"""Terminal output cleaning via pyte."""

import json
import re
from dataclasses import dataclass, field

import pyte


TUI_ENTER = "\033[?1049h"
TUI_EXIT = "\033[?1049l"


@dataclass
class CleanEvent:
    """A single event after pyte cleaning."""
    timestamp: float
    event_type: str          # "i" or "o"
    data: str                 # Clean text (ANSI stripped)
    raw_data: str             # Original data
    in_tui: bool = False


@dataclass
class CleanResult:
    events: list[CleanEvent]
    width: int
    height: int
    env: dict

    @property
    def input_events(self) -> list[CleanEvent]:
        return [e for e in self.events if e.event_type == "i"]

    @property
    def output_events(self) -> list[CleanEvent]:
        return [e for e in self.events if e.event_type == "o"]

    @property
    def tui_regions(self) -> list[tuple[float, float]]:
        """Return (start_ts, end_ts) tuples for TUI regions."""
        regions = []
        tui_start = None
        for e in self.events:
            if e.in_tui and tui_start is None:
                tui_start = e.timestamp
            elif not e.in_tui and tui_start is not None:
                regions.append((tui_start, e.timestamp))
                tui_start = None
        if tui_start is not None:
            regions.append((tui_start, self.events[-1].timestamp))
        return regions


class Cleaner:
    """Clean asciinema recordings using pyte terminal emulation."""

    def process(self, cast_path: str) -> CleanResult:
        """Load and clean a .cast file."""
        header, raw_events = self._load(cast_path)
        width = header.get("width", 80)
        height = header.get("height", 24)

        screen = pyte.Screen(width, height)
        stream = pyte.Stream(screen)

        events = []
        in_tui = False

        for ts, etype, data in raw_events:
            # Track TUI state
            if etype == "o":
                if TUI_ENTER in data:
                    in_tui = True
                elif TUI_EXIT in data:
                    in_tui = False

                # Feed through pyte to get clean text
                stream.feed(data)

            # For "i" events, check if it's terminal query noise
            if etype == "i" and self._is_terminal_noise(data):
                continue

            # Don't strip 'i' events — they contain control chars (\r, \x7f, \t)
            # that the segmenter needs for Enter detection and editing analysis
            if etype == "i":
                clean_data = data
            else:
                clean_data = data.strip()

            events.append(CleanEvent(
                timestamp=ts,
                event_type=etype,
                data=clean_data,
                raw_data=data,
                in_tui=in_tui,
            ))

        return CleanResult(
            events=events,
            width=width,
            height=height,
            env=header.get("env", {}),
        )

    def _load(self, path: str) -> tuple[dict, list]:
        with open(path, encoding="utf-8") as f:
            header = json.loads(f.readline())
            raw = [json.loads(l) for l in f if l.strip()]
        return header, raw

    def _is_terminal_noise(self, data: str) -> bool:
        """Filter out terminal query responses misidentified as input."""
        # Cursor position report: \033[4;5R
        if re.match(r"^\033\[\d+;\d+R$", data):
            return True
        # Device attributes: \033[>84;0;0c
        if re.match(r"^\033\[>\d+(;\d+)*c$", data):
            return True
        return False
