"""Skill YAML generation via LLM or template."""

import re

from .config import Config
from .segmenter import SegmentationResult, Segment

# ── Skill YAML Schema ─────────────────────────────────────────────

SKILL_SCHEMA = """## Skill YAML Schema v0.1

```yaml
name: <kebab-case-skill-name>
version: 1.0.0
description: <one-line description>

parameters:  # optional
  - name: <param_name>
    type: string|integer|boolean
    required: true|false
    default: <value>
    description: <what this controls>

preconditions:  # optional
  - check: <shell command>
    expected_pattern: <regex or empty for exit_code==0>
    description: <what this checks>

concurrency: block  # block | allow

steps:
  - id: <unique-step-id>
    command: <shell command with {{param}} templates>
    working_dir: <optional>
    timeout_seconds: <int>
    retry: <int, 0 for no retry>
    rollback: <rollback command or null>
    rollback_risk: <description if rollback is null>
    expected_output_pattern: <regex or null>
    continue_on_failure: false
```
"""

# ── Few-shot examples ─────────────────────────────────────────────

FEWSHOT_DEPLOY = """## Example 1: Docker deploy

### Input:
Task: Build and deploy user-api container
Commands:
  1. cd /home/deploy/user-api
  2. docker build -t user-api:latest .
     |  [+] Building 12.3s (5/5) FINISHED
     |  => => writing image sha256:abc123... 0.0s
  3. docker stop user-api && docker rm user-api
     |  user-api
  4. docker run -d -p 8080:8080 --name user-api --restart unless-stopped user-api:latest
     |  7f83a2c1d9e5...

### Output:
```yaml
name: deploy-user-api
version: 1.0.0
description: Build and deploy user-api service container

parameters:
  - name: service_name
    type: string
    required: false
    default: user-api
    description: Service/container name
  - name: port
    type: integer
    required: false
    default: 8080
    description: Service listen port

preconditions:
  - check: docker ps
    expected_pattern: ".+"
    description: Docker daemon must be running

concurrency: block

steps:
  - id: build
    # original: docker build -t user-api:latest .
    command: docker build -t {{service_name}}:latest .
    working_dir: /home/deploy/{{service_name}}
    timeout_seconds: 120
    retry: 2
    rollback: null
    rollback_risk: "Build failure only consumes disk space"
    expected_output_pattern: "sha256:[0-9a-f]{64}"
  - id: stop-old
    # original: docker stop user-api && docker rm user-api
    command: docker stop {{service_name}} && docker rm {{service_name}}
    timeout_seconds: 30
    retry: 1
    rollback: null
    rollback_risk: "Old container deleted. Ensure build was successful first"
    expected_output_pattern: "{{service_name}}"
    continue_on_failure: true
  - id: start
    # original: docker run -d -p 8080:8080 --name user-api --restart unless-stopped user-api:latest
    command: docker run -d -p {{port}}:{{port}} --name {{service_name}} --restart unless-stopped {{service_name}}:latest
    working_dir: /home/deploy/{{service_name}}
    timeout_seconds: 30
    retry: 1
    rollback: docker stop {{service_name}} && docker rm {{service_name}}
    rollback_risk: "Low - can stop and remove new container"
    expected_output_pattern: "[0-9a-f]{12,}"
```
"""

FEWSHOT_CONFIG = """## Example 2: Config reload

### Input:
Task: Test and reload nginx configuration
Commands:
  1. docker exec web-nginx nginx -t
     |  nginx: configuration file /etc/nginx/nginx.conf syntax is ok
     |  nginx: configuration file /etc/nginx/nginx.conf test is successful
  2. docker exec web-nginx nginx -s reload
     |  2026/05/28 08:18:18 [notice] signal process started

### Output:
```yaml
name: reload-nginx-config
version: 1.0.0
description: Test and reload nginx configuration

parameters:
  - name: container_name
    type: string
    required: true
    description: Name of the nginx container

preconditions:
  - check: docker ps --filter name={{container_name}} --format '{{.Names}}'
    expected_pattern: "{{container_name}}"
    description: Nginx container must be running

concurrency: block

steps:
  - id: test-config
    # original: docker exec web-nginx nginx -t
    command: docker exec {{container_name}} nginx -t
    timeout_seconds: 10
    retry: 0
    rollback: null
    rollback_risk: "Read-only test, does not change state"
    expected_output_pattern: "syntax is ok"
  - id: reload
    # original: docker exec web-nginx nginx -s reload
    command: docker exec {{container_name}} nginx -s reload
    timeout_seconds: 10
    retry: 1
    rollback: docker exec {{container_name}} nginx -s reload
    rollback_risk: "Cannot undo reload, but previous config can be reapplied"
    expected_output_pattern: "signal process started"
```
"""

FEWSHOT_CHECK = """## Example 3: Health check

### Input:
Task: Check system health metrics
Commands:
  1. df -h / | tail -1
     |  /dev/vda3  40G  33G  4.6G  88% /
  2. free -h | grep Mem
     |  Mem: 7.6G  2.1G  3.2G  234M  2.3G  5.0G
  3. docker ps --format "{{.Names}} {{.Status}}"
     |  web-nginx Up 2 weeks
     |  api-service Up 3 days

### Output:
```yaml
name: system-health-check
version: 1.0.0
description: Collect system health metrics (disk, memory, services)

concurrency: allow

steps:
  - id: check-disk
    # original: df -h / | tail -1
    command: df -h / | tail -1
    timeout_seconds: 5
    retry: 0
    rollback: null
    rollback_risk: "Read-only check"
    expected_output_pattern: "\\d+%"
  - id: check-memory
    # original: free -h | grep Mem
    command: free -h | grep Mem
    timeout_seconds: 5
    retry: 0
    rollback: null
    rollback_risk: "Read-only check"
  - id: check-services
    # original: docker ps --format "{{.Names}} {{.Status}}"
    command: docker ps --format '{{.Names}}'
    timeout_seconds: 5
    retry: 0
    rollback: null
    rollback_risk: "Read-only check"
```
"""

SYSTEM_PROMPT = f"""You are an operations automation expert. Analyze terminal recordings and generate reusable Skill YAML files for AI agents.

## Rules:
- Only include commands that changed state or produced useful output
- Parameterize variable values: service names, ports, file paths, env names
- Do NOT parameterize: package names, system paths (/etc, /var), protocol names
- Skip exploration-only commands (ls, cat, echo) unless they verify state
- Add '# original: <command>' comment above each parameterized command
- Set timeout_seconds: build=120s, deploy=30s, simple=10s
- Set retry: read-only=0, stop/rm=1, build=2
- concurrency: block for state-changing, allow for read-only checks
- Exclude TUI operations (vim, less, htop) from steps

{SKILL_SCHEMA}

{FEWSHOT_DEPLOY}

{FEWSHOT_CONFIG}

{FEWSHOT_CHECK}

Now analyze the session below. Output ONLY the YAML block (```yaml ... ```), nothing else.
"""


class SkillGenerator:
    """Generate Skill YAML from segmented recordings."""

    def __init__(self, config: Config):
        self.config = config

    def generate(self, task: str, seg_result: SegmentationResult) -> str:
        """Generate Skill YAML via LLM, with annotated context and post-fix."""
        from .annotator import format_annotated_segments
        commands_text = format_annotated_segments(seg_result.segments)
        user_prompt = f"Task: {task}\n\nCommands:\n{commands_text}"
        full_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
        yaml_text = self._call_llm(full_prompt)
        if yaml_text and not yaml_text.startswith("[ERROR]"):
            yaml_text = self._post_process(yaml_text)
        return yaml_text

    def _post_process(self, yaml_text: str) -> str:
        """Layer 2: Fix common LLM omissions in generated YAML."""
        yaml_text = self._ensure_parameters_declared(yaml_text)
        yaml_text = self._fix_missing_rollback(yaml_text)
        yaml_text = self._fix_timeout(yaml_text)
        yaml_text = self._fix_concurrency(yaml_text)
        return yaml_text

    def _fix_missing_rollback(self, yaml_text: str) -> str:
        """Ensure destructive commands have rollback_risk declared."""
        import re
        lines = yaml_text.split("\n")
        state_keywords = ["docker stop", "docker rm", "docker run", "docker build",
                        "rm ", "mv ", "kill ", "systemctl stop", "systemctl restart",
                        "apt install", "apt remove", "pip install", "pip uninstall",
                        "npm install", "npm uninstall"]

        in_step = False
        has_null_rollback = False  # rollback: null (missing)
        has_risk = False
        step_cmd = ""
        step_idx = -1

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("- id:"):
                # Flush previous step
                if in_step and has_null_rollback and not has_risk:
                    if any(kw in step_cmd for kw in state_keywords):
                        lines.insert(i, f"    rollback_risk: \"Destructive — ensure backup before execution\"")
                    else:
                        lines.insert(i, f"    rollback_risk: \"Read-only — no state change\"")
                in_step = True
                has_null_rollback = False
                has_risk = False
                step_cmd = ""
                step_idx = i
            elif in_step:
                if "rollback:" in stripped:
                    # Check if it's explicitly null (missing rollback)
                    has_null_rollback = stripped.endswith("null")
                if "rollback_risk:" in stripped:
                    has_risk = True
                if stripped.startswith("command:"):
                    step_cmd = stripped

        # Flush last step
        if in_step and has_null_rollback and not has_risk:
            if any(kw in step_cmd for kw in state_keywords):
                lines.append("    rollback_risk: \"Destructive — ensure backup before execution\"")
            else:
                lines.append("    rollback_risk: \"Read-only — no state change\"")

        return "\n".join(lines)

    def _fix_timeout(self, yaml_text: str) -> str:
        """Set reasonable timeout defaults based on command type."""
        import re
        lines = yaml_text.split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"(\s+)timeout_seconds:\s*(\d+)", line)
            if m and int(m.group(2)) < 5:
                # Find the command for this step
                for j in range(i - 1, max(i - 20, 0), -1):
                    cmd_m = re.match(r"\s+command:\s*(.+)", lines[j])
                    if cmd_m:
                        cmd = cmd_m.group(1)
                        indent = m.group(1)
                        if any(kw in cmd for kw in ["docker build", "apt install",
                            "pip install", "npm install", "git clone"]):
                            lines[i] = f"{indent}timeout_seconds: 120"
                        elif any(kw in cmd for kw in ["docker run", "docker stop",
                            "docker rm", "systemctl"]):
                            lines[i] = f"{indent}timeout_seconds: 30"
                        else:
                            lines[i] = f"{indent}timeout_seconds: 10"
                        break
        return "\n".join(lines)

    def _fix_concurrency(self, yaml_text: str) -> str:
        """Auto-set concurrency: allow for read-only skills."""
        import re
        lines = yaml_text.split("\n")

        # Check if all steps are read-only
        ro_patterns = [r"^\s+command:\s*(ls|cat|grep|df|free|du|ps|docker ps|curl|wget|echo|whoami|pwd|hostname|date|uname)(\s|$)",
                      r"status$", r" is-", r" list", r"^git log", r"^git status",
                      r"^git diff", r"^git branch", r"^docker logs", r"^docker inspect"]

        all_ro = True
        for line in lines:
            if line.strip().startswith("command:"):
                if not any(re.search(p, line) for p in ro_patterns):
                    all_ro = False
                    break

        if all_ro:
            for i, line in enumerate(lines):
                if line.strip().startswith("concurrency:") and "allow" not in line:
                    lines[i] = "concurrency: allow"
                    break

        return "\n".join(lines)

    def _ensure_parameters_declared(self, yaml_text: str) -> str:
        """Auto-declare template variables used in steps but missing from parameters."""
        import re as _re
        import yaml as _yaml

        # Find all {{var}} references in the YAML
        used_vars = set(_re.findall(r"\{\{(\w+)\}\}", yaml_text))

        # Find already-declared parameter names
        declared = set(_re.findall(r"^\s+- name: (\w+)", yaml_text, _re.MULTILINE))

        missing = used_vars - declared
        if not missing:
            return yaml_text

        # Build the new parameter entries
        new_entries = []
        for var in sorted(missing):
            new_entries.append(f"  - name: {var}")
            new_entries.append(f"    type: string")
            new_entries.append(f"    required: true")
            new_entries.append(f"    description: \"{var} (auto-detected)\"")

        # Insert after existing parameters section, or after description if none
        if declared:
            # Find the last parameter definition and insert after it
            last_param_idx = -1
            lines = yaml_text.split("\n")
            for i, line in enumerate(lines):
                if _re.match(r"^\s+- name: ", line):
                    # Go to the end of this param block
                    j = i + 1
                    while j < len(lines) and (lines[j].startswith("    ") or not lines[j].strip()):
                        j += 1
                    last_param_idx = j
            if last_param_idx > 0:
                lines = lines[:last_param_idx] + new_entries + lines[last_param_idx:]
                return "\n".join(lines)

        # No existing parameters section — add one after description
        lines = yaml_text.split("\n")
        insert_at = 3  # name, version, description = 3 lines
        if insert_at < len(lines):
            blank_line = "" if not lines[insert_at].strip() else ""
            new_section = ["", "parameters:"] + new_entries + [""]
            lines = lines[:insert_at] + [blank_line] + new_section + lines[insert_at + (0 if blank_line else 0):]
            return "\n".join(lines)

        return yaml_text

    def generate_template(self, task: str, seg_result: SegmentationResult) -> str:
        """Generate a template YAML without LLM."""
        name = self._slugify(task)
        steps = []

        for i, seg in enumerate(seg_result.segments):
            if seg.in_tui:
                continue
            sid = f"{name.replace('-', '_')}_step{i + 1}"
            step = {
                "id": sid,
                "# original": seg.command,
                "command": seg.command,
                "timeout_seconds": 30,
                "retry": 0,
                "rollback": "null",
                "rollback_risk": "TODO: describe risk if rollback is null",
                "expected_output_pattern": "null",
            }
            # Try to detect container context
            if seg.container_context:
                step["# context"] = f"inside container: {seg.container_context}"

            steps.append(step)

        return self._render_yaml(name, task, [], "block", steps)

    def build_yaml(self, name: str, desc: str, params: list,
                   concurrency: str, steps: list[dict]) -> str:
        """Build a Skill YAML from structured input (parrot new)."""
        return self._render_yaml(name, desc, params, concurrency, steps)

    def _format_segments(self, segments: list[Segment]) -> str:
        """Format segments for the LLM prompt."""
        lines = []
        for i, seg in enumerate(segments):
            if seg.in_tui:
                continue
            cmd = seg.command.strip()
            output = seg.output.strip()

            if len(output) > 500:
                output = output[:200] + f"\n  ... ({len(output)} chars total) ...\n" + output[-200:]

            output_indented = "\n  |  ".join(output.split("\n")) if output else ""

            ctx = f" [in container: {seg.container_context}]" if seg.container_context else ""
            lines.append(f"  {i+1}. {cmd}{ctx}")
            if output_indented:
                lines.append(f"     |  {output_indented}")

        return "\n".join(lines)

    def _render_yaml(self, name: str, desc: str, params: list,
                     concurrency: str, steps: list[dict]) -> str:
        """Render a complete Skill YAML string."""
        lines = [
            f"name: {name}",
            "version: 1.0.0",
            f"description: {desc}",
            "",
        ]

        if params:
            lines.append("parameters:")
            for p in params:
                pname = p.get("name", "param")
                ptype = p.get("type", "string")
                required = p.get("required", False)
                lines.append(f"  - name: {pname}")
                lines.append(f"    type: {ptype}")
                lines.append(f"    required: {str(required).lower()}")
                if "default" in p:
                    val = p["default"]
                    if ptype == "string":
                        val = f'"{val}"' if not val.startswith('"') else val
                    lines.append(f"    default: {val}")
                if "description" in p:
                    desc = p['description']
                    if self._needs_quoting(str(desc)):
                        desc = self._quote_yaml(str(desc))
                    lines.append(f"    description: {desc}")
            lines.append("")

        if concurrency:
            lines.append(f"concurrency: {concurrency}")
            lines.append("")

        lines.append("steps:")
        for step in steps:
            sid = step.get("id", "step")
            lines.append(f"  - id: {sid}")

            for key in ("# original", "# context"):
                if key in step:
                    lines.append(f"    {key}: {step[key]}")

            for key in ("command", "working_dir", "timeout_seconds", "retry",
                        "rollback", "rollback_risk", "expected_output_pattern",
                        "continue_on_failure"):
                if key in step:
                    val = step[key]
                    if val is None:
                        val = "null"
                    elif isinstance(val, bool):
                        val = str(val).lower()
                    elif isinstance(val, str) and self._needs_quoting(val):
                        val = self._quote_yaml(val)
                    lines.append(f"    {key}: {val}")
            lines.append("")

        return "\n".join(lines)

    def _call_llm(self, full_prompt: str) -> str:
        """Call the LLM API."""
        if self.config.backend == "anthropic":
            return self._call_claude(full_prompt)
        else:
            return self._call_openai(full_prompt)

    def _call_claude(self, full_prompt: str) -> str:
        try:
            import anthropic
            kwargs = {"api_key": self.config.anthropic_api_key}
            if self.config.anthropic_base_url:
                kwargs["base_url"] = self.config.anthropic_base_url
            client = anthropic.Anthropic(**kwargs)
            response = client.messages.create(
                model=self.config.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": full_prompt}],
                thinking={"type": "disabled"},
            )
            for block in response.content:
                text = getattr(block, "text", None)
                if text:
                    return self._extract_yaml(text)
            return "[ERROR] No text in Claude response"
        except ImportError:
            return "[ERROR] anthropic package not installed"
        except Exception as e:
            return f"[ERROR] Claude API call failed: {e}"

    def _call_openai(self, full_prompt: str) -> str:
        try:
            import openai
            kwargs = {"api_key": self.config.openai_api_key}
            if self.config.openai_base_url:
                kwargs["base_url"] = self.config.openai_base_url
            client = openai.OpenAI(**kwargs)
            response = client.chat.completions.create(
                model=self.config.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": full_prompt}],
                stream=False,
            )
            if hasattr(response, "choices") and response.choices:
                return self._extract_yaml(response.choices[0].message.content)
            if isinstance(response, dict):
                choices = response.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    if isinstance(msg, dict):
                        return self._extract_yaml(msg.get("content", str(response)))
                    return str(msg)
            return str(response)
        except ImportError:
            return "[ERROR] openai package not installed"
        except Exception as e:
            return f"[ERROR] OpenAI API call failed: {e}"

    def _extract_yaml(self, text: str) -> str:
        # 1. Fenced yaml block
        m = re.search(r"```yaml\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # 2. Any fenced block containing YAML
        m = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            content = m.group(1).strip()
            if "name:" in content and "steps:" in content:
                return content
        # 3. YAML embedded in free text — find 'name: <skill>' line
        yaml_start = re.search(r"^name:\s*\S+", text, re.MULTILINE)
        if yaml_start:
            lines = text.split("\n")
            start_idx = 0
            for i, line in enumerate(lines):
                if re.match(r"^name:\s*\S+", line.strip()):
                    start_idx = i
                    break
            yaml_lines = []
            for line in lines[start_idx:]:
                stripped = line.strip()
                if not stripped:
                    yaml_lines.append(line)
                    continue
                is_yaml_key = bool(re.match(r"^[a-z][\w-]*:\s*$|^[a-z][\w-]*:\s", stripped))
                is_indented = line[0] in (" ", "\t")
                is_comment = stripped.startswith("#")
                is_list_item = stripped.startswith("- ")
                if is_yaml_key or is_indented or is_comment or is_list_item:
                    yaml_lines.append(line)
                else:
                    break
            result = "\n".join(yaml_lines).strip()
            if "name:" in result and "steps:" in result:
                return result
        # 4. Fallback: return raw text
        return text.strip()

    def _needs_quoting(self, val: str) -> bool:
        """Check if a string value needs YAML quoting."""
        return (
            ": " in val
            or val.startswith(("{", "[", "'", '"', "&", "*", "!", "|", ">", "%", "@", "`"))
            or val in ("true", "false", "null", "yes", "no", "on", "off")
            or "#" in val
        )

    def _quote_yaml(self, val: str) -> str:
        """Quote a string value for safe YAML output. Prefers single quotes."""
        if "'" not in val:
            return f"'{val}'"
        # Fall back to double quotes with escaping
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _slugify(self, text: str) -> str:
        name = text.lower().strip()
        name = re.sub(r"[^a-z0-9\s-]", "", name)
        name = re.sub(r"\s+", "-", name)
        return name[:50] if name else "untitled"
