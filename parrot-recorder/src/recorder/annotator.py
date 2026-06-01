"""Layer 1: Pre-processing — annotate segments with rich context for LLM prompt."""

import re
from dataclasses import dataclass, field

from .segmenter import Segment


# ── Command classification ───────────────────────────────────────

STATE_CHANGING_PATTERNS = [
    "docker (build|run|stop|rm|kill|restart|push|tag|commit|exec|cp)",
    "(apt|apt-get|yum|dnf|pip|npm|cargo) (install|remove|uninstall|upgrade|update)",
    "(systemctl|service) (start|stop|restart|enable|disable|reload)",
    "(mv|cp|rm|dd|rsync|scp|tar|unzip)\\s",
    "(chmod|chown|chgrp)\\s",
    "(mount|umount)\\s",
    "(useradd|usermod|userdel|groupadd)\\s",
    "(kill|pkill|killall)\\s",
    "(sed|awk|tee)\\s.*[>|]",
    "echo\\s.*>",
    "git (commit|push|merge|checkout|reset|stash)\\s",
    "mkdir\\s",
    "ln\\s",
]

READ_ONLY_PATTERNS = [
    r"^(ls|cat|less|head|tail|grep|find|locate|which|type|file|stat|wc|du|df|free)(\s|$)",
    r"^(ps|top|htop|iotop|vmstat|iostat|netstat|ss)(\s|$)",
    r"^(docker|kubectl) (ps|logs|inspect|images|info|version|stats|top|port)(\s|$)",
    r"^(git|svn) (log|status|diff|show|branch|tag|remote)(\s|$)",
    r"^(curl|wget)(\s|$)",
    r"^(echo|printf|date|whoami|id|hostname|pwd|env|printenv|uname|uptime)(\s|$)",
    r"^(systemctl|service) (status|list|is-enabled|is-active)(\s|$)",
    r"^(apt|apt-get|yum|dnf|pip) (list|search|info|show)(\s|$)",
]

NAVIGATION_PATTERNS = [
    r"^cd\s",
    r"^pushd\s",
    r"^popd",
]

# ── Parameterizable value detection ──────────────────────────────

# Patterns that suggest a value should be parameterized
PARAM_PATTERNS = [
    # Port numbers
    (re.compile(r"(?:-p|--port|:)\s*(\d{2,5})\b"), "port", "integer"),
    # Service/container names (after common flags and docker exec)
    (re.compile(r"--name\s+(\S+)"), "service_name", "string"),
    (re.compile(r"docker\s+(?:exec|stop|rm|start|restart|logs|inspect)\s+(?:-it\s+)?(\S+)"), "container_name", "string"),
    # Image tags
    (re.compile(r"(\S+):(latest|v?\d+\.\d+)"), "image_tag", "string"),
    # File paths that are not system paths
    (re.compile(r"(/home/\S+|/opt/\S+|/var/www/\S+|/srv/\S+)"), "working_dir", "string"),
    # URLs
    (re.compile(r"(https?://\S+)"), "endpoint_url", "string"),
    # Environment values
    (re.compile(r"--env\s+(\S+=\S+)"), "env_var", "string"),
    # Version numbers
    (re.compile(r"\b(v?\d+\.\d+\.\d+)\b"), "version", "string"),
]


# ── Rollback suggestion ──────────────────────────────────────────

ROLLBACK_RULES = [
    (re.compile(r"docker stop (\S+)"), "docker start {0}"),
    (re.compile(r"docker rm (\S+)"), "Cannot rollback — container deleted"),
    (re.compile(r"docker run .* --name (\S+)"), "docker stop {0} && docker rm {0}"),
    (re.compile(r"mv (\S+) (\S+)"), "mv {1} {0}"),
    (re.compile(r"cp (\S+) (\S+)"), "rm {1}"),
    (re.compile(r"rm (\S+)"), "Cannot rollback — file deleted"),
    (re.compile(r"systemctl stop (\S+)"), "systemctl start {0}"),
    (re.compile(r"systemctl restart (\S+)"), "systemctl restart {0}"),
]


@dataclass
class CommandAnnotation:
    command_type: str = ""            # state-changing | read-only | navigation
    parameterizable: list[dict] = field(default_factory=list)
    rollback_suggestion: str = ""
    container_context: str = ""
    is_destructive: bool = False
    notes: list[str] = field(default_factory=list)


class CommandAnnotator:
    """Analyze a command segment and produce rich annotations for the LLM."""

    def annotate(self, segment: Segment) -> CommandAnnotation:
        cmd = segment.command.strip()
        ann = CommandAnnotation()

        # 1. Classify command type
        ann.command_type = self._classify(cmd)

        # 2. Detect parameterizable values
        ann.parameterizable = self._detect_params(cmd)

        # 3. Check if destructive
        ann.is_destructive = self._is_destructive(cmd)

        # 4. Suggest rollback
        ann.rollback_suggestion = self._suggest_rollback(cmd)

        # 5. Container context
        ann.container_context = segment.container_context

        # 6. Notes
        ann.notes = self._add_notes(segment, ann)

        return ann

    def _classify(self, cmd: str) -> str:
        for pat in NAVIGATION_PATTERNS:
            if re.search(pat, cmd):
                return "navigation"
        for pat in STATE_CHANGING_PATTERNS:
            if re.search(pat, cmd):
                return "state-changing"
        for pat in READ_ONLY_PATTERNS:
            if re.search(pat, cmd):
                return "read-only"
        return "state-changing"  # assume state-changing if unknown

    def _detect_params(self, cmd: str) -> list[dict]:
        found = []
        seen_names = set()
        for pat, suggested_name, ptype in PARAM_PATTERNS:
            for m in pat.finditer(cmd):
                value = m.group(1)
                if value not in seen_names and not self._is_system_value(value):
                    found.append({
                        "value": value,
                        "suggested_name": suggested_name,
                        "type": ptype,
                    })
                    seen_names.add(value)
        return found

    def _is_system_value(self, value: str) -> bool:
        """Don't parameterize system paths or well-known values."""
        system_prefixes = ("/etc/", "/usr/", "/bin/", "/sbin/", "/lib/",
                          "/dev/", "/proc/", "/sys/", "/run/", "/tmp/")
        return any(value.startswith(p) for p in system_prefixes)

    def _is_destructive(self, cmd: str) -> bool:
        destructive = [
            r"\brm\b", r"\bdd\b", r"\bkill\b", r"\brmdir\b",
            r"docker\s+rm\b", r"docker\s+rmi\b", r"docker\s+system\s+prune",
            r"\bdrop\b", r"\bdelete\b", r"\btruncate\b",
            r"\bchmod\s+777\b", r"\bchmod\s+-R\b",
        ]
        return any(re.search(p, cmd) for p in destructive)

    def _suggest_rollback(self, cmd: str) -> str:
        for pat, template in ROLLBACK_RULES:
            m = pat.search(cmd)
            if m:
                return template.format(*m.groups())
        return ""

    def _add_notes(self, segment: Segment, ann: CommandAnnotation) -> list[str]:
        notes = []
        if ann.is_destructive:
            notes.append("destructive: user confirmation recommended")
        if ann.command_type == "navigation":
            notes.append("navigation: consider using working_dir instead of cd step")
        if segment.container_context:
            notes.append(f"runs inside container: {segment.container_context}")
        if len(segment.output) > 1000:
            notes.append(f"long output ({len(segment.output)} chars): consider truncating")
        return notes


@dataclass
class InteractiveHint:
    step_index: int           # which step should be interactive
    command: str               # the original command
    param_value: str           # the value that might be user-chosen
    param_name: str            # suggested variable name
    source_step: int           # which step produced the "query" output
    source_output: str         # the output containing the value


def detect_interactive_hints(segments: list[Segment]) -> list[InteractiveHint]:
    """Detect 'query → select' patterns across consecutive segments.

    Pattern: step N is read-only (ss, docker ps, ls), step N+1 uses
    a value that appears in step N's output — likely a user selection.
    """
    annotator = CommandAnnotator()
    hints = []

    for i in range(1, len(segments)):
        prev = segments[i - 1]
        curr = segments[i]

        if prev.in_tui or curr.in_tui:
            continue

        prev_ann = annotator.annotate(prev)
        curr_ann = annotator.annotate(curr)

        # Previous step must be read-only (a "query")
        if prev_ann.command_type != "read-only":
            continue

        # Current step must be state-changing (a "select + act")
        if curr_ann.command_type not in ("state-changing", "navigation"):
            continue

        # Check if any parameterizable value in current step appears in previous output
        for param in curr_ann.parameterizable:
            if param["value"] in prev.output:
                hints.append(InteractiveHint(
                    step_index=i,
                    command=curr.command,
                    param_value=param["value"],
                    param_name=param["suggested_name"],
                    source_step=i - 1,
                    source_output=prev.output,
                ))

    return hints


def format_annotated_segments(segments: list[Segment],
                              interactive_hints: list[InteractiveHint] = None) -> str:
    """Format segments with annotations for the LLM prompt."""
    annotator = CommandAnnotator()
    lines = []

    for i, seg in enumerate(segments):
        if seg.in_tui:
            continue

        ann = annotator.annotate(seg)
        cmd = seg.command.strip()
        output = seg.output.strip()

        # Truncate long output
        if len(output) > 500:
            output = output[:200] + f"\n  ... ({len(output)} chars) ...\n" + output[-200:]

        output_indented = "\n  |  ".join(output.split("\n")) if output else ""

        # Build annotation line
        annotations = []
        annotations.append(f"type: {ann.command_type}")
        if ann.parameterizable:
            params_str = ", ".join(
                f"{p['value']}→{{{{{p['suggested_name']}}}}}" for p in ann.parameterizable
            )
            annotations.append(f"parameterizable: {params_str}")
        if ann.rollback_suggestion:
            annotations.append(f"rollback: {ann.rollback_suggestion}")
        if ann.container_context:
            annotations.append(f"context: inside {ann.container_context}")
        if ann.notes:
            annotations.extend(ann.notes)

        # Interactive step hint
        if seg.interactive_config:
            ic = seg.interactive_config
            annotations.append(f"INTERACTIVE: prompt=\"{ic['prompt']}\" variable={{{{{ic['variable']}}}}}")

        lines.append(f"  {i+1}. {cmd}")
        if annotations:
            lines.append(f"     [{' | '.join(annotations)}]")
        if output_indented:
            lines.append(f"     |  {output_indented}")

    return "\n".join(lines)
