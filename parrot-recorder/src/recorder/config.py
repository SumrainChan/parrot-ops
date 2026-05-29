"""Configuration loader for Parrot Recorder.

Priority (highest to lowest):
  1. Command-line arguments
  2. .env file (in current directory, then project root)
  3. Environment variables
  4. Built-in defaults
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Config:
    backend: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    output_dir: str = "./skills"

    # Anthropic
    anthropic_api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None

    # OpenAI
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None

    # Derived
    _loaded: bool = field(default=False, repr=False)

    @property
    def api_key(self) -> Optional[str]:
        if self.backend == "anthropic":
            return self.anthropic_api_key
        return self.openai_api_key

    @property
    def base_url(self) -> Optional[str]:
        if self.backend == "anthropic":
            return self.anthropic_base_url
        return self.openai_base_url


_DEFAULTS = {
    "PARROT_LLM_BACKEND": "anthropic",
    "PARROT_MODEL": "claude-sonnet-4-6",
    "PARROT_OUTPUT_DIR": "./skills",
}


def _find_env_file() -> Optional[Path]:
    """Find .env file: current dir first, then project root."""
    cwd = Path.cwd()
    candidates = [cwd]
    # Walk up to find project root
    for parent in cwd.parents:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            candidates.append(parent)
            break
    for d in candidates:
        env = d / ".env"
        if env.exists():
            return env
    return None


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict, ignoring comments and blank lines."""
    result = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val:
                    result[key] = val
    return result


def load_config(
    backend: str = None,
    model: str = None,
    output_dir: str = None,
    anthropic_api_key: str = None,
    anthropic_base_url: str = None,
    openai_api_key: str = None,
    openai_base_url: str = None,
) -> Config:
    """Load configuration from all sources with CLI args taking priority."""

    # Layer 1: defaults
    env = dict(_DEFAULTS)

    # Layer 2: .env file
    env_file = _find_env_file()
    if env_file:
        env.update(_parse_env_file(env_file))

    # Layer 3: OS environment variables
    for key in _DEFAULTS:
        val = os.environ.get(key)
        if val:
            env[key] = val
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        val = os.environ.get(key)
        if val:
            env[key] = val

    # Resolve backend
    resolved_backend = backend or env.get("PARROT_LLM_BACKEND") or "anthropic"

    config = Config(
        backend=resolved_backend,
        model=model or env.get("PARROT_MODEL") or "claude-sonnet-4-6",
        output_dir=output_dir or env.get("PARROT_OUTPUT_DIR") or "./skills",
        anthropic_api_key=anthropic_api_key or env.get("ANTHROPIC_API_KEY"),
        anthropic_base_url=anthropic_base_url or env.get("ANTHROPIC_BASE_URL"),
        openai_api_key=openai_api_key or env.get("OPENAI_API_KEY"),
        openai_base_url=openai_base_url or env.get("OPENAI_BASE_URL"),
        _loaded=True,
    )

    return config
