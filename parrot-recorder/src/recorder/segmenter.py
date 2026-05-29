"""Command/output segmentation and sensitive info scanning."""

import re
from dataclasses import dataclass, field

import pyte

from .cleaner import CleanResult, CleanEvent

# Prompt patterns (ordered by specificity)
PROMPT_PATTERNS = [
    re.compile(r"^[^#\$]*@[^#\$]*[#$] "),     # root@host:~#
    re.compile(r"^[^#\$]*[#$] "),               # ~$ / /tmp# / / #
    re.compile(r"[#$>] $"),                      # trailing prompt char
]

# Sensitive info patterns
SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
    (re.compile(r"ghp_[0-9a-zA-Z]{36}"), "GitHub Personal Token"),
    (re.compile(r"gho_[0-9a-zA-Z]{36}"), "GitHub OAuth Token"),
    (re.compile(r"(?:password|passwd|pwd|secret|token)\s*[=:]\s*\S+", re.I),
     "Password/Token in command"),
    (re.compile(r"mysql://[^:]+:[^@]+@"), "DB URL with password"),
    (re.compile(r"Authorization:\s*Bearer\s+\S+", re.I), "Auth header Bearer token"),
]


@dataclass
class Segment:
    """A single command-execution unit."""
    command: str
    output: str
    prompt: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    in_tui: bool = False
    container_context: str = ""       # e.g. "exif-nginx" if inside docker exec
    secrets: list[str] = field(default_factory=list)


@dataclass
class SegmentationResult:
    segments: list[Segment]
    tui_warnings: list[str]
    secret_warnings: list[str]
    has_stdin_events: bool


class Segmenter:
    """Split cleaned recording into (command, output) pairs."""

    def process(self, clean: CleanResult) -> SegmentationResult:
        has_i = len(clean.input_events) > 0
        if has_i:
            return self._segment_with_stdin(clean)
        else:
            return self._segment_without_stdin(clean)

    def _segment_with_stdin(self, clean: CleanResult) -> SegmentationResult:
        """Exact segmentation using 'i' events."""
        segments = []
        tui_warnings = []
        secret_warnings = []

        pending_cmd = None
        pending_cmd_ts = 0.0
        pending_raw = ""
        in_tui = False
        last_prompt_ts = 0.0
        current_prompt = ""
        container_context = ""

        for ev in clean.events:
            # TUI tracking
            if ev.in_tui and not in_tui:
                tui_warnings.append(f"[{ev.timestamp:.1f}s] TUI mode entered")
                in_tui = True
            elif not ev.in_tui and in_tui:
                tui_warnings.append(f"[{ev.timestamp:.1f}s] TUI mode exited")
                in_tui = False

            if ev.event_type == "i" and not in_tui:
                # Flush previous command
                if pending_cmd is not None:
                    output = self._extract_output(pending_raw, pending_cmd)
                    secrets = self._scan_secrets(pending_cmd)
                    if secrets:
                        secret_warnings.extend(
                            f"[{pending_cmd[:60]}] {s}" for s in secrets)
                    segments.append(Segment(
                        command=pending_cmd,
                        output=output,
                        prompt=current_prompt,
                        start_time=pending_cmd_ts,
                        end_time=ev.timestamp,
                        in_tui=False,
                        container_context=container_context,
                        secrets=secrets,
                    ))

                pending_cmd = ev.data.strip().rstrip("\r")
                pending_cmd_ts = ev.timestamp
                pending_raw = ""

                # Detect container context changes
                if pending_cmd.startswith("docker exec -it "):
                    parts = pending_cmd.split()
                    for i, p in enumerate(parts):
                        if p == "-it" and i + 2 < len(parts):
                            container_context = parts[i + 2]  # container name
                            break

                if pending_cmd in ("exit", "exit\r") and container_context:
                    container_context = ""  # Back to host

            elif ev.event_type == "o" and not in_tui:
                pending_raw += ev.raw_data
                # Detect prompt
                for line in self._get_display_lines(ev.raw_data):
                    if self._is_prompt(line.rstrip()):
                        current_prompt = line.rstrip()
                        last_prompt_ts = ev.timestamp

        # Flush last command
        if pending_cmd is not None:
            output = self._extract_output(pending_raw, pending_cmd)
            secrets = self._scan_secrets(pending_cmd)
            if secrets:
                secret_warnings.extend(
                    f"[{pending_cmd[:60]}] {s}" for s in secrets)
            segments.append(Segment(
                command=pending_cmd,
                output=output,
                prompt=current_prompt,
                start_time=pending_cmd_ts,
                end_time=last_prompt_ts,
                container_context=container_context,
                secrets=secrets,
            ))

        return SegmentationResult(
            segments=segments,
            tui_warnings=tui_warnings,
            secret_warnings=secret_warnings,
            has_stdin_events=True,
        )

    def _segment_without_stdin(self, clean: CleanResult) -> SegmentationResult:
        """Fallback: prompt regex segmentation."""
        screen = pyte.Screen(200, 1000)  # tall screen to capture all output
        stream = pyte.Stream(screen)
        tui_warnings = []
        in_tui = False

        for ev in clean.events:
            if ev.event_type != "o":
                continue
            if ev.in_tui and not in_tui:
                tui_warnings.append(f"[{ev.timestamp:.1f}s] TUI mode entered")
                in_tui = True
            elif not ev.in_tui and in_tui:
                tui_warnings.append(f"[{ev.timestamp:.1f}s] TUI mode exited")
                in_tui = False
            if not in_tui:
                stream.feed(ev.raw_data)

        clean_lines = [l.rstrip() for l in screen.display if l.rstrip()]
        segments = self._split_by_prompts(clean_lines)
        secret_warnings = []
        for seg in segments:
            s = self._scan_secrets(seg.command)
            if s:
                secret_warnings.extend(f"[{seg.command[:60]}] {x}" for x in s)

        return SegmentationResult(
            segments=segments,
            tui_warnings=tui_warnings,
            secret_warnings=secret_warnings,
            has_stdin_events=False,
        )

    def _extract_output(self, raw_data: str, command: str) -> str:
        """Extract clean output using pyte on raw accumulator."""
        screen = pyte.Screen(200, 1000)
        stream = pyte.Stream(screen)
        stream.feed(raw_data)

        lines = [l.rstrip() for l in screen.display if l.rstrip()]

        collecting = False
        output_lines = []

        for line in lines:
            if not collecting and command in line:
                collecting = True
                after = line[line.find(command) + len(command):].strip()
                if after and not self._is_prompt(after):
                    output_lines.append(after)
                continue
            if collecting:
                if self._is_prompt(line):
                    break
                if line.strip():
                    output_lines.append(line.strip())

        # Dedup consecutive identical lines (spinner/progress artifacts)
        deduped = []
        for line in output_lines:
            if deduped and line == deduped[-1]:
                continue
            deduped.append(line)

        return "\n".join(deduped) if deduped else "(no visible output)"

    def _split_by_prompts(self, lines: list[str]) -> list[Segment]:
        """Split clean lines into segments by prompt boundaries (fallback)."""
        segments = []
        cmd = ""
        output_lines = []

        for line in lines:
            if self._is_prompt(line):
                if cmd and output_lines:
                    segments.append(Segment(
                        command=cmd, output="\n".join(output_lines)))
                    output_lines = []
                cmd = self._extract_command_from_prompt(line)
            elif cmd:
                output_lines.append(line)

        if cmd and output_lines:
            segments.append(Segment(
                command=cmd, output="\n".join(output_lines)))
        return segments

    def _extract_command_from_prompt(self, prompt_line: str) -> str:
        """Extract the command part from a prompt line."""
        for pat in PROMPT_PATTERNS:
            m = pat.search(prompt_line)
            if m:
                return prompt_line[m.end():].strip()
        return prompt_line

    def _is_prompt(self, text: str) -> bool:
        if not text or len(text) < 2:
            return False
        for pat in PROMPT_PATTERNS:
            if pat.search(text):
                return True
        return False

    def _get_display_lines(self, raw_data: str) -> list[str]:
        """Quick pyte pass to get display lines from raw data."""
        screen = pyte.Screen(200, 50)
        stream = pyte.Stream(screen)
        stream.feed(raw_data)
        return [l.rstrip() for l in screen.display if l.rstrip()]

    def _scan_secrets(self, text: str) -> list[str]:
        found = []
        for pat, label in SECRET_PATTERNS:
            if pat.search(text):
                found.append(label)
        return found
