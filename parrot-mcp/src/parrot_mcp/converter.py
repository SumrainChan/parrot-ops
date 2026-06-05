"""P0-2: Skill YAML → MCP Tool conversion.

Scans local skill directory, reads .skill.yaml files, generates
MCP Tool definitions with JSON Schema input.
"""

import json
import re
from pathlib import Path
from typing import Optional

import yaml


def load_skills(skill_dir: str) -> list[dict]:
    """Scan a directory for .skill.yaml files and load them."""
    skills = []
    base = Path(skill_dir)
    if not base.exists():
        return skills
    for f in sorted(base.glob("*.skill.yaml")):
        try:
            skill = yaml.safe_load(f.read_text(encoding="utf-8"))
            if isinstance(skill, dict) and "name" in skill:
                skills.append(skill)
        except Exception:
            pass
    return skills


def skill_to_tool(skill: dict) -> dict:
    """Convert a single Skill YAML dict to an MCP Tool definition."""
    name = skill["name"]
    desc = _build_description(skill)
    props = {}
    required = []

    for p in skill.get("parameters", []):
        props[p["name"]] = {
            "type": p.get("type", "string"),
            "description": p.get("description", p["name"]),
        }
        if "default" in p:
            props[p["name"]]["default"] = p["default"]
        if p.get("required"):
            required.append(p["name"])

    return {
        "name": name,
        "description": desc,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required,
        } if props else {"type": "object", "properties": {}},
    }


def build_list_skills_tool(skills: list[dict]) -> dict:
    """Generate the list_skills tool from available skills."""
    names = ", ".join(s["name"] for s in skills)
    return {
        "name": "list_skills",
        "description": (
            "列出所有可用的运维 Skill。每个 Skill 是经过人类审核的、"
            "可复用的运维操作。可用 Skill: " + (names or "(none)")
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "按名称或描述搜索 Skill（可选）",
                }
            },
        },
    }


def build_skill_search(skills: list[dict], query: str = "") -> list[dict]:
    """Search skills by name or description."""
    if not query:
        return skills
    q = query.lower()
    return [
        s for s in skills
        if q in s["name"].lower() or q in s.get("description", "").lower()
    ]


def _build_description(skill: dict) -> str:
    """Build enhanced tool description from Skill YAML."""
    base = skill.get("description", skill["name"])
    steps = skill.get("steps", [])
    if steps:
        step_ids = " → ".join(s["id"] for s in steps)
        base += f"。步骤: {step_ids}"
    if any(s.get("rollback") for s in steps if s.get("rollback")):
        base += "。失败时自动回滚"
    return base
