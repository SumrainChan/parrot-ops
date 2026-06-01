"""P0-3: Skill execution engine.

Core logic: parse Skill YAML → execute steps sequentially → validate output →
retry on failure → rollback if needed → return structured result.
"""

import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

import yaml


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
    status: str = "queued"          # queued|running|completed|blocked|failed|aborted
    steps: list[StepResult] = field(default_factory=list)
    current_step: int = 0
    total_steps: int = 0
    error: str = ""
    started_at: str = ""
    completed_at: str = ""


class SkillExecutor:
    """Execute a Skill YAML on the local machine."""

    def __init__(self, task_id: str, skill_yaml: str, params: dict = None):
        self.task_id = task_id
        self.params = params or {}
        self.skill = yaml.safe_load(skill_yaml)
        self.result = ExecutionResult(
            task_id=task_id,
            skill_name=self.skill.get("name", "unknown"),
            total_steps=len(self.skill.get("steps", [])),
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._lock = None           # set by agent for concurrency control

    def execute(self) -> ExecutionResult:
        """Run all steps. Returns immediately — caller polls result.steps for progress."""
        self.result.status = "running"
        steps = self.skill.get("steps", [])

        for i, step in enumerate(steps):
            self.result.current_step = i + 1
            step_result = self._execute_step(step, i)
            self.result.steps.append(step_result)

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
        """Retry the current blocked step and continue."""
        if self.result.status != "blocked":
            return self.result

        self.result.status = "running"
        steps = self.skill.get("steps", [])
        start_idx = self.result.current_step - 1

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
        cmd = self._apply_params(step["command"])
        wd = self._apply_params(step.get("working_dir", ""))
        timeout = step.get("timeout_seconds", 30)
        max_retries = step.get("retry", 0)
        expected = step.get("expected_output_pattern", "")

        result = StepResult(
            step_id=step["id"],
            command=cmd,
            working_dir=wd,
        )

        start = time.time()
        for attempt in range(max_retries + 1):
            result.retries_used = attempt
            ok, output, exit_code = self._run(cmd, wd, timeout)

            result.exit_code = exit_code
            result.output = output.strip()

            if ok and expected:
                ok = bool(re.search(expected, output))

            if ok:
                result.status = "completed"
                result.duration_ms = int((time.time() - start) * 1000)
                return result

        result.status = "failed"
        result.duration_ms = int((time.time() - start) * 1000)

        # continue_on_failure
        if step.get("continue_on_failure"):
            result.status = "skipped"

        # health_check on success
        if result.status == "completed":
            hc = step.get("health_check")
            if hc:
                self._health_check(hc)

        return result

    def _rollback(self, step: dict, step_result: StepResult) -> bool:
        """Execute rollback. Returns True if rollback succeeded."""
        rollback_cmd = step.get("rollback")
        if not rollback_cmd:
            return False
        cmd = self._apply_params(rollback_cmd)
        wd = self._apply_params(step.get("working_dir", ""))
        ok, _, _ = self._run(cmd, wd, timeout=30)
        return ok

    def _health_check(self, hc: dict):
        cmd = self._apply_params(hc["command"])
        pattern = hc.get("expected_pattern", "")
        interval = hc.get("interval_seconds", 5)
        max_attempts = hc.get("max_attempts", 6)
        for _ in range(max_attempts):
            time.sleep(interval)
            ok, output, _ = self._run(cmd, "", timeout=10)
            if ok and re.search(pattern, output):
                return

    def _run(self, command: str, working_dir: str, timeout: int
             ) -> tuple[bool, str, int]:
        """Execute a shell command locally. Returns (ok, output, exit_code)."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=working_dir if working_dir else None,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            return result.returncode == 0, output, result.returncode
        except subprocess.TimeoutExpired:
            return False, f"Command timed out after {timeout}s", -1
        except Exception as e:
            return False, str(e), -1

    def _apply_params(self, text: str) -> str:
        """Replace {{var}} templates with parameter values."""
        result = text
        for key, val in self.params.items():
            result = result.replace(f"{{{{{key}}}}}", str(val))
        return result
