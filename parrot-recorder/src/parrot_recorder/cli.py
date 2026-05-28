"""CLI entry point for parrot-recorder."""

import argparse
import sys
from pathlib import Path

from .config import load_config


def cmd_record(args):
    """Start a recording session, auto-clean and generate Skill YAML."""
    from .recorder import Recorder
    from .cleaner import Cleaner
    from .segmenter import Segmenter
    from .generator import SkillGenerator

    config = load_config(output_dir=args.output if args.output else None)

    # Phase 1: Record
    recorder = Recorder()
    cast_path = recorder.start()

    if cast_path is None:
        print("[parrot] Recording failed or was cancelled.")
        sys.exit(1)

    # Phase 2: Clean + Segment
    print("[parrot] Cleaning and analyzing recording...")
    cleaner = Cleaner()
    clean_result = cleaner.process(cast_path)

    segmenter = Segmenter()
    seg_result = segmenter.process(clean_result)

    if not seg_result.segments:
        print("[parrot] No commands detected in recording.")
        sys.exit(1)

    print(f"[parrot] Detected {len(seg_result.segments)} command(s)" +
          (f", {len(seg_result.tui_warnings)} TUI region(s)" if seg_result.tui_warnings else "") +
          (f", {len(seg_result.secret_warnings)} SECRET warning(s)" if seg_result.secret_warnings else ""))

    # Show warnings
    if seg_result.secret_warnings:
        print("\n[!!!] SECRETS DETECTED:")
        for w in seg_result.secret_warnings:
            print(f"  {w}")

    if seg_result.tui_warnings:
        print("\n[!] TUI operations detected (excluded from Skill):")
        for w in seg_result.tui_warnings:
            print(f"  {w[:120]}")

    # Phase 3: Task description
    task = args.task
    if not task:
        task = input("\nTask description (one sentence): ").strip()
        if not task:
            print("[parrot] Task description required. Aborting.")
            sys.exit(1)

    # Phase 4: Generate Skill YAML
    if args.skip_llm:
        print("[parrot] Generating template YAML (--skip-llm)...")
        generator = SkillGenerator(config)
        yaml_text = generator.generate_template(task, seg_result)
    else:
        if not config.api_key:
            print("[parrot] No API key configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env")
            print("[parrot] Falling back to template mode. Use --skip-llm to skip this warning.")
            generator = SkillGenerator(config)
            yaml_text = generator.generate_template(task, seg_result)
        else:
            print("[parrot] Generating Skill YAML via LLM...")
            generator = SkillGenerator(config)
            yaml_text = generator.generate(task, seg_result)

    if yaml_text.startswith("[ERROR]"):
        print(yaml_text)
        print("[parrot] Falling back to template mode...")
        generator = SkillGenerator(config)
        yaml_text = generator.generate_template(task, seg_result)

    # Phase 5: Preview and confirm
    print()
    print("─" * 60)
    print(yaml_text)
    print("─" * 60)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        skill_name = _guess_skill_name(task)
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{skill_name}.skill.yaml"

    while True:
        choice = input(f"\nSave to {output_path}? [Y/n/e edit path]: ").strip().lower()
        if choice in ("", "y", "yes"):
            break
        elif choice == "n":
            print("[parrot] Discarded.")
            sys.exit(0)
        elif choice == "e":
            new_path = input("  Path: ").strip()
            if new_path:
                output_path = Path(new_path)
        else:
            print("  Please answer Y (save), n (discard), or e (edit path)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml_text, encoding="utf-8")

    # Validate
    from .validator import validate_text
    valid, errors = validate_text(yaml_text)
    if valid:
        print(f"[parrot] Saved and validated: {output_path}")
    else:
        print(f"[parrot] Saved: {output_path}")
        print(f"[parrot] Validation warnings ({len(errors)}):")
        for e in errors:
            print(f"  [!] {e}")


def cmd_new(args):
    """Interactive Skill creation without recording."""
    from .generator import SkillGenerator
    from .config import load_config

    config = load_config()
    generator = SkillGenerator(config)

    print("Parrot Recorder — Create a new Skill\n")

    name = input("Skill name (kebab-case): ").strip()
    while not name:
        name = input("Skill name (kebab-case): ").strip()

    desc = input("Description: ").strip()
    while not desc:
        desc = input("Description: ").strip()

    print("\nParameters (name=default_value, empty to finish):")
    params = []
    while True:
        entry = input(f"  param [{len(params)+1}]: ").strip()
        if not entry:
            break
        if "=" in entry:
            pname, _, pdefault = entry.partition("=")
            params.append({"name": pname.strip(), "default": pdefault.strip(),
                           "type": _guess_type(pdefault.strip())})
        else:
            params.append({"name": entry, "type": "string"})

    conc = input("\nConcurrency [block/allow] (default block): ").strip()
    concurrency = conc if conc in ("block", "allow") else "block"

    print("\nSteps (empty command to finish):")
    steps = []
    sid = 1
    while True:
        cmd = input(f"  step {sid} command (empty to finish): ").strip()
        if not cmd:
            break
        timeout_str = input(f"    timeout_seconds (default 30): ").strip()
        timeout = int(timeout_str) if timeout_str else 30

        retry_str = input(f"    retry (default 0): ").strip()
        retry = int(retry_str) if retry_str else 0

        expected = input(f"    expected_output_pattern (optional): ").strip()
        rollback = input(f"    rollback command (optional): ").strip()

        steps.append({
            "id": name.replace("-", "_") + f"_step{sid}",
            "command": cmd,
            "timeout_seconds": timeout,
            "retry": retry,
            "expected_output_pattern": expected or None,
            "rollback": rollback or None,
        })
        sid += 1

    if not steps:
        print("[parrot] No steps defined. Aborting.")
        sys.exit(1)

    yaml_text = generator.build_yaml(name, desc, params, concurrency, steps)

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{name}.skill.yaml"
    output_path.write_text(yaml_text, encoding="utf-8")

    from .validator import validate_text
    valid, errors = validate_text(yaml_text)
    if valid:
        print(f"\n[parrot] Saved: {output_path}")
    else:
        print(f"\n[parrot] Saved: {output_path} (validation warnings: {len(errors)})")

    print(yaml_text)


def cmd_validate(args):
    """Validate an existing Skill YAML file."""
    from .validator import validate

    path = args.skill_file
    valid, errors = validate(str(path))
    if valid:
        print(f"[parrot] {path} — PASSED")
    else:
        print(f"[parrot] {path} — FAILED ({len(errors)} error(s))")
        for e in errors:
            print(f"  [!] {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Parrot Recorder — record terminal operations and generate Skill YAML",
        prog="parrot",
    )
    sub = parser.add_subparsers(dest="command")

    # parrot record
    p_record = sub.add_parser("record", help="Start a terminal recording session")
    p_record.add_argument("-t", "--task", help="Task description (skip interactive prompt)")
    p_record.add_argument("-o", "--output", help="Output path for Skill YAML")
    p_record.add_argument("--skip-llm", action="store_true", help="Skip LLM, output template YAML only")

    # parrot new
    sub.add_parser("new", help="Create a Skill YAML interactively without recording")

    # parrot validate
    p_validate = sub.add_parser("validate", help="Validate an existing Skill YAML file")
    p_validate.add_argument("skill_file", help="Path to .skill.yaml file")

    args = parser.parse_args()

    if args.command == "record":
        cmd_record(args)
    elif args.command == "new":
        cmd_new(args)
    elif args.command == "validate":
        cmd_validate(args)
    else:
        parser.print_help()


def _guess_skill_name(task: str) -> str:
    """Derive a kebab-case skill name from the task description."""
    import re
    name = task.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"\s+", "-", name)
    return name[:50] if name else "untitled"


def _guess_type(value: str) -> str:
    """Guess YAML type from a default value string."""
    if value.isdigit():
        return "integer"
    if value.lower() in ("true", "false"):
        return "boolean"
    return "string"


if __name__ == "__main__":
    main()
