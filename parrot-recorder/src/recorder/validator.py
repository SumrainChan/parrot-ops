"""Skill YAML Schema validator."""

import re
from pathlib import Path

import yaml


REQUIRED_ROOT = ["name", "version", "description", "concurrency", "steps"]
REQUIRED_STEP = ["id", "command", "timeout_seconds", "retry"]
VALID_CONCURRENCY = {"block", "allow"}
VALID_TYPES = {"string", "integer", "boolean"}
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
TEMPLATE_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def validate(path: str) -> tuple[bool, list[str]]:
    """Validate a Skill YAML file. Returns (is_valid, errors)."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        return validate_text(raw)
    except Exception as e:
        return False, [f"Cannot read file: {e}"]


def validate_text(text: str) -> tuple[bool, list[str]]:
    """Validate Skill YAML text. Returns (is_valid, errors)."""
    errors = []

    try:
        skill = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return False, [f"YAML parse error: {e}"]

    if not isinstance(skill, dict):
        return False, ["Root must be a YAML mapping"]

    # Root fields
    for field in REQUIRED_ROOT:
        if field not in skill:
            errors.append(f"Missing required root field: '{field}'")

    name = skill.get("name", "")
    if name and not NAME_PATTERN.match(str(name)):
        errors.append(f"Invalid 'name': '{name}' (must be kebab-case)")

    ver = skill.get("version", "")
    if ver and not SEMVER_PATTERN.match(str(ver)):
        errors.append(f"Invalid 'version': '{ver}' (must be semver)")

    conc = skill.get("concurrency", "")
    if conc and conc not in VALID_CONCURRENCY:
        errors.append(f"Invalid 'concurrency': '{conc}' (must be block or allow)")

    # Parameters
    params = skill.get("parameters") or []
    param_names = set()
    for i, p in enumerate(params):
        pname = p.get("name", "")
        if not pname:
            errors.append(f"Parameter[{i}] missing 'name'")
        elif pname in param_names:
            errors.append(f"Parameter '{pname}' duplicated")
        else:
            param_names.add(pname)
        ptype = p.get("type", "")
        if ptype and ptype not in VALID_TYPES:
            errors.append(f"Parameter '{pname}': invalid type '{ptype}'")

    # Preconditions
    for i, pc in enumerate(skill.get("preconditions") or []):
        if "check" not in pc:
            errors.append(f"Precondition[{i}] missing 'check'")

    # Steps
    steps = skill.get("steps", [])
    if not isinstance(steps, list) or not steps:
        errors.append("'steps' must be a non-empty list")
    else:
        step_ids = set()
        for i, step in enumerate(steps):
            for field in REQUIRED_STEP:
                if field not in step:
                    errors.append(f"Step[{i}] ('{step.get('id', '?')}'): missing '{field}'")

            sid = step.get("id", "")
            if sid in step_ids:
                errors.append(f"Step '{sid}': duplicate id")
            elif sid:
                step_ids.add(sid)

            if step.get("rollback") is None and "rollback_risk" not in step:
                errors.append(f"Step '{sid}': rollback is null, add rollback_risk")

            timeout = step.get("timeout_seconds")
            if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
                errors.append(f"Step '{sid}': timeout_seconds must be > 0")

            retry = step.get("retry")
            if retry is not None and (not isinstance(retry, int) or retry < 0):
                errors.append(f"Step '{sid}': retry must be >= 0")

    # Template variable consistency
    param_names = {p.get("name", "") for p in params}
    all_text = yaml.dump(skill)
    used_vars = set(TEMPLATE_PATTERN.findall(all_text))
    undefined = used_vars - param_names
    unused = param_names - used_vars
    for v in undefined:
        errors.append(f"Template '{{{{{v}}}}}' used but not defined in parameters")
    for v in unused:
        if v:
            errors.append(f"Parameter '{v}' defined but never used")

    return len(errors) == 0, errors
