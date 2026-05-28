"""Terminal recording via asciinema."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class Recorder:
    """Wrap asciinema to record terminal sessions with --stdin capture."""

    def __init__(self, cast_path: str = None):
        self.cast_path = cast_path
        self._env_vars = {
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "SYSTEMD_PAGER": "cat",
        }

    def start(self) -> str:
        """Start an interactive recording session. Returns path to .cast file."""
        if not self._check_asciinema():
            print("[parrot] asciinema is required. Install: apt install asciinema")
            print("[parrot] Or: pip install asciinema")
            return None

        if self.cast_path:
            cast_file = self.cast_path
        else:
            ts = time.strftime("%Y%m%d-%H%M%S")
            cast_file = os.path.join(tempfile.gettempdir(), f"parrot-{ts}.cast")

        # Set env vars to prevent accidental TUI triggers
        env = os.environ.copy()
        env.update(self._env_vars)

        print("[parrot] Starting recording...")
        print(f"[parrot] Env: GIT_PAGER=cat PAGER=cat")
        print("[parrot] Type 'exit' or press Ctrl+D to stop.\n")

        try:
            result = subprocess.run(
                ["asciinema", "rec", "--stdin", cast_file],
                env=env,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            if result.returncode != 0:
                print(f"\n[parrot] asciinema exited with code {result.returncode}")
                # Check if the file was still created (partial recording)
                if os.path.exists(cast_file) and os.path.getsize(cast_file) > 0:
                    print(f"[parrot] Partial recording saved: {cast_file}")
                    return cast_file
                return None
        except KeyboardInterrupt:
            print("\n[parrot] Recording interrupted.")

        if os.path.exists(cast_file) and os.path.getsize(cast_file) > 0:
            print(f"\n[parrot] Recording saved: {cast_file}")
            return cast_file

        print("[parrot] No recording data captured.")
        return None

    def _check_asciinema(self) -> bool:
        """Check if asciinema is installed."""
        try:
            result = subprocess.run(
                ["asciinema", "--version"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
