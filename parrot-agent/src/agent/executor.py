"""P0-3 + P1-9 + P1-10: Skill execution engine.

Core logic: parse Skill YAML → precheck → execute steps sequentially →
validate output → retry on failure → rollback → return structured result.
Supports interactive steps and dual executors (Local/Docker).
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

import yaml

from .executors import BaseExecutor, LocalExecutor, get_executor


@dataclass
class StepResult:
    step_id: str
    command: str
    working_dir: str = ""
    exit_code: int = -1
    output: str = ""
    duration_ms: int = 0
    status: str = "pending"        # pending|running|completed|failed|skipped|rolled_back
    rolled_back: bool = False
    retries_used: int = 0


@dataclass
class ExecutionResult:
    task_id: str
    skill_name: str
    status: str = "queued"          # queued|running|completed|blocked|failed|aborted|awaiting_input
    steps: list[StepResult] = field(default_factory=list)
    current_step: int = 0
    total_steps: int = 0
    error: str = ""
    blocked_step: Optional[dict] = None  # P1-9: interactive step prompt
    started_at: str = ""
    completed_at: str = ""


class SkillExecutor:
    """Execute a Skill YAML on the local machine."""

    def __init__(self, task_id: str, skill_yaml: str, params: dict = None,
                 default_executor: BaseExecutor = None):
        self.task_id = task_id
        self.params = params or {}
        self.skill = yaml.safe_load(skill_yaml)
        self.default_executor = default_executor or LocalExecutor()
        self.result = ExecutionResult(
            task_id=task_id,
            skill_name=self.skill.get("name", "unknown"),
            total_steps=len(self.skill.get("steps", [])),
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._lock = None

    def precheck(self) -> Optional[str]:
        """P1-10: Run precheck validations. Returns error message or None if OK."""
        # Check required params
        for p in self.skill.get("parameters", []):
            if p.get("required") and p["name"] not in self.params:
                return f"Missing required parameter: {p['name']}"

        # Check disk space (> 100MB in /tmp and ~/)
        import shutil
        for path in ["/tmp", "."]:
            try:
                usage = shutil.disk_usage(path)
                if usage.free < 100 * 1024 * 1024:
                    return f"Low disk space: {path} has {usage.free // 1024 // 1024}MB free"
            except Exception:
                pass

        # Check Docker if any step uses docker
        for step in self.skill.get("steps", []):
            cmd = step.get("command", "")
            if "docker " in cmd or "docker\n" in cmd:
                ok, out, _ = self.default_executor.run("docker ps", "", 5)
                if not ok:
                    return f"Docker not available: {out[:200]}"
                break

        return None

    def execute(self) -> ExecutionResult:
        """Run all steps. Stops on interactive step (awaiting_input)."""
        # Precheck
        err = self.precheck()
        if err:
            self.result.status = "failed"
            self.result.error = f"Precheck failed: {err}"
            return self.result

        self.result.status = "running"
        steps = self.skill.get("steps", [])

        for i, step in enumerate(steps):
            self.result.current_step = i + 1
            step_result = self._execute_step(step, i)
            self.result.steps.append(step_result)

            # P1-9: Stop on interactive step
            if self.result.status == "awaiting_input":
                return self.result

            if step_result.status == "failed":
                rolled_back = self._rollback(step, step_result)
                if rolled_back:
                    step_result.rolled_back = True
                    step_result.status = "rolled_back"
                else:
                    self.result.status = "blocked"
                    self.result.error = (
                        f"Step '{step['id']}': exit_code={step_result.exit_code}, "
                        f"retries={step_result.retries_used}, rollback_failed"
                    )
                    return self.result

            elif step_result.status == "skipped":
                continue

        self.result.status = "completed"
        self.result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return self.result

    def resume(self) -> ExecutionResult:
        """Retry the current blocked/interactive step and continue."""
        if self.result.status not in ("blocked", "awaiting_input"):
            return self.result

        self.result.status = "running"
        steps = self.skill.get("steps", [])
        start_idx = self.result.current_step - 1

        # P1-9: If resuming from interactive step, skip it (input already provided)
        if start_idx < len(steps):
            step = steps[start_idx]
            if step.get("interactive") and step.get("variable") in self.params:
                self.result.steps[-1].status = "completed"
                start_idx += 1  # skip the interactive step

        for i in range(start_idx, len(steps)):
            step = steps[i]
            self.result.current_step = i + 1
            step_result = self._execute_step(step, i)
            # Replace existing step result or append
            if i < len(self.result.steps):
                self.result.steps[i] = step_result
            else:
                self.result.steps.append(step_result)

            if step_result.status == "failed":
                self.result.status = "blocked"
                return self.result

        self.result.status = "completed"
        return self.result

    def skip(self) -> ExecutionResult:
        """Skip the blocked step and continue."""
        if self.result.status != "blocked":
            return self.result

        # Mark current step as skipped
        idx = self.result.current_step - 1
        if idx < len(self.result.steps):
            self.result.steps[idx].status = "skipped"

        self.result.status = "running"
        steps = self.skill.get("steps", [])
        start_idx = idx + 1

        for i in range(start_idx, len(steps)):
            step = steps[i]
            self.result.current_step = i + 1
            step_result = self._execute_step(step, i)
            self.result.steps.append(step_result)

            if step_result.status == "failed":
                self.result.status = "blocked"
                return self.result

        self.result.status = "completed"
        return self.result

    def abort(self) -> ExecutionResult:
        """Abort and reverse-rollback completed steps."""
        completed = [r for r in self.result.steps if r.status == "completed"]
        steps = self.skill.get("steps", [])

        for r in reversed(completed):
            for step in steps:
                if step["id"] == r.step_id and step.get("rollback"):
                    self._run(step["rollback"], step.get("working_dir", ""),
                             timeout=30)

        self.result.status = "aborted"
        self.result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return self.result

    def _execute_step(self, step: dict, index: int) -> StepResult:
        cmd = self._apply_params(step.get("command", ""))
        wd = self._apply_params(step.get("working_dir", ""))
        timeout = step.get("timeout_seconds", 30)
        max_retries = step.get("retry", 0)
        expected = step.get("expected_output_pattern", "")

        # P1-9: Interactive step — pause and return prompt
        if step.get("interactive"):
            self.result.status = "awaiting_input"
            self.result.blocked_step = {
                "id": step["id"],
                "prompt": step.get("prompt", "Input required"),
                "variable": step.get("variable", "user_input"),
                "default": step.get("default", ""),
            }
            return StepResult(
                step_id=step["id"],
                command="(awaiting user input)",
                status="pending",
            )

        # Use the appropriate executor
        executor = get_executor(step, self.skill)

        result = StepResult(
            step_id=step["id"],
            command=cmd,
            working_dir=wd,
        )

        start = time.time()
        for attempt in range(max_retries + 1):
            result.retries_used = attempt
            ok, output, exit_code = executor.run(cmd, wd, timeout)

            result.exit_code = exit_code
            result.output = output.strip()

            if ok and expected:
                ok = bool(re.search(expected, output))

            if ok:
                result.status = "completed"
                result.duration_ms = int((time.time() - start) * 1000)
                # health_check on success
                hc = step.get("health_check")
                if hc:
                    self._health_check(hc)
                return result

        result.status = "failed"
        result.duration_ms = int((time.time() - start) * 1000)

        if step.get("continue_on_failure"):
            result.status = "skipped"

        return result

    def _rollback(self, step: dict, step_result: StepResult) -> bool:
        """Execute rollback. Returns True if rollback succeeded."""
        rollback_cmd = step.get("rollback")
        if not rollback_cmd:
            return False
        cmd = self._apply_params(rollback_cmd)
        wd = self._apply_params(step.get("working_dir", ""))
        executor = get_executor(step, self.skill)
        ok, _, _ = executor.run(cmd, wd, timeout=30)
        return ok

    def _health_check(self, hc: dict):
        cmd = self._apply_params(hc["command"])
        pattern = hc.get("expected_pattern", "")
        interval = hc.get("interval_seconds", 5)
        max_attempts = hc.get("max_attempts", 6)
        for _ in range(max_attempts):
            time.sleep(interval)
            ok, output, _ = self.default_executor.run(cmd, "", timeout=10)
            if ok and re.search(pattern, output):
                return

    def _apply_params(self, text: str) -> str:
        """Replace {{var}} templates with parameter values."""
        result = text
        for key, val in self.params.items():
            result = result.replace(f"{{{{{key}}}}}", str(val))
        return result
