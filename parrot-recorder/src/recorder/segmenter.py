"""Command/output segmentation and sensitive info scanning."""

import re
from dataclasses import dataclass, field

import pyte

from .cleaner import CleanResult, CleanEvent

# Prompt patterns (ordered by specificity)
PROMPT_PATTERNS = [
    re.compile(r"^[^#\$]*@[^#\$]*[#$]( |$)"),    # root@host:~#
    re.compile(r"^[^#\$]*[#$]( |$)"),             # ~$ / /tmp# / / #
    re.compile(r"[#$>]( |$)"),                     # trailing prompt char
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
    interactive_config: dict = field(default_factory=dict)


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
        """Command text from pyte screen echo, output from pyte screen state.

        Strategy:
        1. Feed ALL 'o' events through pyte to reconstruct terminal state
        2. 'i' events with \\r mark command boundaries (Enter pressed)
        3. At each Enter, extract ALL completed commands from the pyte screen
           (the screen shows the echo of the final edited command line)
        4. After the last Enter, use the final screen state to extract
           commands with their outputs
        """
        segments = []
        tui_warnings = []
        secret_warnings = []

        screen = pyte.Screen(200, 1000)
        stream = pyte.Stream(screen)

        in_tui = False
        # Track commands extracted at each Enter press
        executed_commands = []  # list of (timestamp, command_text)

        for ev in clean.events:
            if ev.in_tui and not in_tui:
                tui_warnings.append(f"[{ev.timestamp:.1f}s] TUI mode entered")
                in_tui = True
            elif not ev.in_tui and in_tui:
                tui_warnings.append(f"[{ev.timestamp:.1f}s] TUI mode exited")
                in_tui = False

            if ev.event_type == "o" and not in_tui:
                stream.feed(ev.raw_data)

            elif ev.event_type == "i" and not in_tui:
                if "\r" in ev.data or "\n" in ev.data:
                    # Enter pressed — extract the command from current screen
                    cmd_text = self._extract_command_at_cursor(screen)
                    if cmd_text:
                        executed_commands.append((ev.timestamp, cmd_text))

        # Extract commands from the final pyte screen using prompt splitting
        clean_lines = [l.rstrip() for l in screen.display if l.rstrip()]
        segments = self._split_by_prompts(clean_lines)

        # Merge consecutive duplicates (tab-completion noise)
        segments = self._dedup_consecutive_same_cmd(segments)

        secret_warnings = []
        for seg in segments:
            s = self._scan_secrets(seg.command)
            if s:
                secret_warnings.extend(f"[{seg.command[:60]}] {x}" for x in s)

            # Detect container context
            if seg.command.startswith("docker exec -it "):
                parts = seg.command.split()
                for i, p in enumerate(parts):
                    if p == "-it" and i + 2 < len(parts):
                        seg.container_context = parts[i + 2]
                        break

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
                if cmd:
                    output = "\n".join(output_lines) if output_lines else "(no output)"
                    segments.append(Segment(command=cmd, output=output))
                    output_lines = []
                cmd = self._extract_command_from_prompt(line)
            elif cmd:
                output_lines.append(line)

        if cmd:
            output = "\n".join(output_lines) if output_lines else "(no output)"
            segments.append(Segment(command=cmd, output=output))
        return segments

    def _extract_command_from_prompt(self, prompt_line: str) -> str:
        """Extract the command part from a prompt line."""
        for pat in PROMPT_PATTERNS:
            m = pat.search(prompt_line)
            if m:
                # m.end() may include a trailing space; strip it
                after = prompt_line[m.end():].strip()
                return after
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

    def _extract_command_from_screen(self, prev_lines: list[str],
                                       new_lines: list[str],
                                       prev_prompt: str) -> str:
        """Extract the actual command from pyte screen state.

        When a new prompt appears, the command is the text between the previous
        prompt line and the new prompt line. The shell echo contains the final
        command after all editing (backspace, tab completion, etc.).
        """
        # Strategy: find the line with the previous prompt in prev_lines,
        # then find what comes after it in new_lines

        # Find previous prompt position
        prev_prompt_idx = -1
        for i, line in enumerate(prev_lines):
            if prev_prompt and prev_prompt in line:
                prev_prompt_idx = i
                break
        if not prev_prompt:
            # No previous prompt detected - look for the line just before new prompt
            prev_prompt_idx = len(prev_lines) - 1 if prev_lines else 0

        # Find new prompt position in new_lines
        new_prompt_idx = -1
        for i, line in enumerate(new_lines):
            if self._is_prompt(line):
                new_prompt_idx = i
                break
        if new_prompt_idx < 0:
            return ""

        # Extract command: text between previous prompt and new prompt
        # Typically: [prev prompt + cmd, output..., new prompt]
        # The command is on the line immediately after the previous prompt

        cmd_candidates = []

        for i in range(prev_prompt_idx, min(new_prompt_idx + 1, len(new_lines))):
            line = new_lines[i]
            stripped = line.rstrip()
            if not stripped:
                continue

            # If this line contains the previous prompt, extract text after it
            if prev_prompt and prev_prompt in stripped:
                after = stripped[stripped.rfind(prev_prompt) + len(prev_prompt):].strip()
                if after:
                    cmd_candidates.append(after)
            elif self._is_prompt(stripped):
                # This is the new prompt itself - extract text before the prompt
                for pat in PROMPT_PATTERNS:
                    m = pat.search(stripped)
                    if m:
                        before = stripped[:m.start()].strip()
                        if before:
                            cmd_candidates.append(before)
                        break
            else:
                # Regular line between prompts - might be the command
                cmd_candidates.append(stripped)

        # The first non-empty content after the old prompt is the command
        if cmd_candidates:
            return cmd_candidates[0]

        return ""

    def _extract_command_at_cursor(self, screen) -> str:
        """Extract the command being executed from the current pyte screen.

        Called at the moment of Enter press. Finds the last prompt line
        and extracts the command text after the prompt marker.
        """
        lines = [l.rstrip() for l in screen.display if l.rstrip()]
        if not lines:
            return ""

        # Find the last prompt line (bottom-up search)
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            for pat in PROMPT_PATTERNS:
                m = pat.search(line)
                if m:
                    after = line[m.end():].strip()
                    if after:
                        return after
                    # Check if the next line (if any) contains the command
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if next_line and not self._is_prompt(next_line):
                            return next_line
                    return ""

            # If the last non-empty line doesn't have a prompt, it might
            # be the command being typed (no prompt visible on this screen)
            if i == len(lines) - 1 and line:
                return line

        return ""

    def _dedup_consecutive_same_cmd(self, segments: list) -> list:
        """Merge consecutive segments with identical commands (tab completion).

        When the user types a partial command and presses Tab multiple times,
        the shell re-displays the prompt + command after each Tab. Each display
        creates a segment with the same command and the completion listing as
        'output'. We merge these: keep the last occurrence (which has no output
        from a successful completion, or the actual command output if there is one).
        """
        if len(segments) <= 1:
            return segments

        merged = []
        prev = segments[0]
        for curr in segments[1:]:
            if curr.command == prev.command:
                # Same command — merge: keep the later segment's output
                # (the earlier one's 'output' is tab completion noise)
                prev = curr
            else:
                merged.append(prev)
                prev = curr
        merged.append(prev)
        return merged

    def _extract_command_from_screen_v2(self, screen, prev_prompt: str) -> str:
        """Extract the command from pyte screen state.

        The pyte screen shows what the terminal actually displays — after shell
        line editing (backspace, tab completion, etc.). The command is the text
        between the previous prompt and the current cursor position.
        """
        lines = [l.rstrip() for l in screen.display if l.rstrip()]
        if not lines:
            return ""

        # Find the last prompt line and extract what follows it
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            # Look for known prompt pattern in this line
            for pat in PROMPT_PATTERNS:
                m = pat.search(line)
                if m:
                    after = line[m.end():].strip()
                    # If there's text after the prompt on the same line, it's a command
                    if after:
                        return after
                    # Otherwise, the command might be on the next non-empty line
                    for j in range(i + 1, len(lines)):
                        next_line = lines[j].strip()
                        if next_line and not self._is_prompt(next_line):
                            return next_line
                        elif self._is_prompt(next_line):
                            break
                    return ""

            # Check if this line looks like a command echo (no prompt on this screen)
            # This handles the case when typing hasn't produced a new prompt yet
            if i == len(lines) - 1 and line and not self._is_prompt(line):
                # The last line might be the command being typed
                return line

        return ""

    def _scan_secrets(self, text: str) -> list[str]:
        found = []
        for pat, label in SECRET_PATTERNS:
            if pat.search(text):
                found.append(label)
        return found
