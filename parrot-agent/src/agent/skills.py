"""Skill library management for parrot-agent."""

import os
from pathlib import Path
from typing import Optional

import yaml


class SkillLibrary:
    """Manage a local directory of Skill YAML files.

    Skills are stored as .skill.yaml files. The library supports
    listing, registering, and removing skills.
    """

    def __init__(self, skill_dir: str = None):
        self.skill_dir = Path(skill_dir or os.path.join(
            os.path.expanduser("~/.parrot"), "skills"))
        self.skill_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self.reload()

    def reload(self):
        """Reload all skills from disk."""
        self._cache = {}
        for f in self.skill_dir.glob("*.skill.yaml"):
            try:
                skill = yaml.safe_load(f.read_text(encoding="utf-8"))
                if isinstance(skill, dict) and "name" in skill:
                    self._cache[skill["name"]] = skill
            except Exception:
                pass

    def list(self, full: bool = False) -> list[dict]:
        """Return all available skills. If full=True, include steps/parameters."""
        if full:
            return list(self._cache.values())
        return [
            {
                "name": s["name"],
                "version": s.get("version", "0.1.0"),
                "description": s.get("description", ""),
                "parameters": s.get("parameters", []),
                "steps": [{"id": st["id"]} for st in s.get("steps", [])],
                "concurrency": s.get("concurrency", "block"),
            }
            for s in self._cache.values()
        ]

    def get(self, name: str) -> Optional[dict]:
        """Get a skill by name."""
        return self._cache.get(name)

    def get_yaml(self, name: str) -> Optional[str]:
        """Get the raw YAML string for a skill."""
        skill = self._cache.get(name)
        if skill:
            return yaml.dump(skill, allow_unicode=True)
        return None

    def all_yaml(self) -> dict[str, str]:
        """Get all skills as name → yaml_string."""
        return {name: yaml.dump(s, allow_unicode=True)
                for name, s in self._cache.items()}

    def register(self, yaml_text: str) -> dict:
        """Register a new skill from YAML text. Saves to disk."""
        try:
            skill = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            return {"error": str(e)}

        if not isinstance(skill, dict) or "name" not in skill:
            return {"error": "Invalid Skill YAML: missing 'name' field"}

        name = skill["name"]
        path = self.skill_dir / f"{name}.skill.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        self._cache[name] = skill
        return {"status": "registered", "name": name, "path": str(path)}

    def remove(self, name: str) -> bool:
        """Remove a skill by name."""
        path = self.skill_dir / f"{name}.skill.yaml"
        if path.exists():
            path.unlink()
        if name in self._cache:
            del self._cache[name]
            return True
        return False
