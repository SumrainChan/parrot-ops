"""CLI entry point for parrot-recorder."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .config import load_config


def _reset_terminal():
    """Restore terminal to sane state (after asciinema raw mode)."""
    if sys.stdin.isatty():
        subprocess.run(["stty", "sane"], capture_output=True)


_real_input = input

def _safe_input(prompt: str = "") -> str:
    """Reset terminal before reading input (fix backspace after asciinema)."""
    _reset_terminal()
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    return sys.stdin.readline().rstrip("\n")


def cmd_learn(args):
    """Record terminal operations, then generate Skill YAML (or save intermediate with --skip-llm)."""
    from .recorder import Recorder
    from .cleaner import Cleaner
    from .segmenter import Segmenter
    from .generator import SkillGenerator
    from .validator import validate_text

    config = load_config()

    # Phase 1: Record
    recorder = Recorder()
    cast_path = recorder.start()

    if cast_path is None:
        print("[parrot] Recording failed or was cancelled.")
        sys.exit(1)

    # Phase 2: Clean + Segment
    print("[parrot] Analyzing recording...")
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

    if seg_result.secret_warnings:
        print("\n[!!!] SECRETS DETECTED:")
        for w in seg_result.secret_warnings:
            print(f"  {w}")

    if seg_result.tui_warnings:
        print("\n[!] TUI operations detected (excluded):")
        for w in seg_result.tui_warnings:
            print(f"  {w[:120]}")

    # Phase 3: Generate or save intermediate
    if args.skip_llm:
        parrot_path = _get_output_path(args.output, args.task, "parrot.json", config)
        _save_parrot_json(parrot_path, seg_result, cast_path)
        print(f"[parrot] Saved: {parrot_path}")
        print(f"\n[parrot] Next: parrot compose {parrot_path} -t \"<task description>\"\n")
        return

    # Phase 4: Task description
    task = args.task
    if not task:
        task = _safe_input("Task description (one sentence): ").strip()
        if not task:
            print("[parrot] Task description required. Aborting.")
            sys.exit(1)

    # Phase 5: Generate Skill YAML
    generator = SkillGenerator(config)

    if not config.api_key:
        print("[parrot] No API key configured. Using template mode.")
        yaml_text = generator.generate_template(task, seg_result)
    else:
        print("[parrot] Composing Skill YAML via LLM...")
        yaml_text = generator.generate(task, seg_result)

        if yaml_text.startswith("[ERROR]"):
            print(yaml_text)
            print("[parrot] Falling back to template mode...")
            yaml_text = generator.generate_template(task, seg_result)

    # Phase 6: Preview, confirm, save, validate
    print()
    print("-" * 60)
    print(yaml_text)
    print("-" * 60)

    output_path = _get_output_path(args.output, task, "skill.yaml", config)

    if not sys.stdin.isatty():
        print(f"[parrot] Non-interactive mode: saving to {output_path}")
    else:
        while True:
            choice = _safe_input(f"\nSave to {output_path}? [Y/n/e edit path]: ").strip().lower()
            if choice in ("", "y", "yes"):
                break
            elif choice == "n":
                print("[parrot] Discarded.")
                sys.exit(0)
            elif choice == "e":
                new_path = _safe_input("  Path: ").strip()
                if new_path:
                    output_path = Path(new_path)
            else:
                print("  Please answer Y (save), n (discard), or e (edit path)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml_text, encoding="utf-8")

    valid, errors = validate_text(yaml_text)
    if valid:
        print(f"[parrot] Saved and validated: {output_path}")
    else:
        print(f"[parrot] Saved: {output_path}")
        print(f"[parrot] Validation warnings ({len(errors)}):")
        for e in errors:
            print(f"  [!] {e}")


def cmd_compose(args):
    """Generate Skill YAML from a .parrot.json or .cast file."""
    from .cleaner import Cleaner
    from .segmenter import Segmenter, SegmentationResult
    from .generator import SkillGenerator
    from .validator import validate_text

    config = load_config()
    input_path = Path(args.file)

    if not input_path.exists():
        print(f"[parrot] File not found: {input_path}")
        sys.exit(1)

    # Phase 1: Load segments from file
    if input_path.suffix == ".json":
        seg_result = _load_parrot_json(str(input_path))
    elif input_path.suffix == ".cast":
        print("[parrot] Processing .cast file...")
        cleaner = Cleaner()
        segmenter = Segmenter()
        seg_result = segmenter.process(cleaner.process(str(input_path)))
    else:
        print(f"[parrot] Unsupported file type: {input_path.suffix}")
        print("[parrot] Expected .parrot.json or .cast")
        sys.exit(1)

    if not seg_result.segments:
        print("[parrot] No commands found.")
        sys.exit(1)

    print(f"[parrot] Loaded {len(seg_result.segments)} command(s)" +
          (f", {len(seg_result.secret_warnings)} secret warning(s)" if seg_result.secret_warnings else ""))

    # Phase 2: Task description
    task = args.task
    if not task:
        task = _safe_input("Task description (one sentence): ").strip()
        if not task:
            print("[parrot] Task description required. Aborting.")
            sys.exit(1)

    # Phase 3: Generate Skill YAML
    generator = SkillGenerator(config)

    if args.skip_llm:
        print("[parrot] Generating template YAML (--skip-llm)...")
        yaml_text = generator.generate_template(task, seg_result)
    elif not config.api_key:
        print("[parrot] No API key configured. Using template mode.")
        yaml_text = generator.generate_template(task, seg_result)
    else:
        print("[parrot] Composing Skill YAML via LLM...")
        yaml_text = generator.generate(task, seg_result)

        if yaml_text.startswith("[ERROR]"):
            print(yaml_text)
            print("[parrot] Falling back to template mode...")
            yaml_text = generator.generate_template(task, seg_result)

    # Phase 4: Preview, confirm, save, validate
    print()
    print("-" * 60)
    print(yaml_text)
    print("-" * 60)

    output_path = _get_output_path(args.output, task, "skill.yaml", config)

    # Non-interactive: both -t and -o specified → save immediately
    if args.task and args.output:
        pass  # skip confirmation
    elif not sys.stdin.isatty():
        print(f"[parrot] Non-interactive mode: saving to {output_path}")
    else:
        while True:
            choice = _safe_input(f"\nSave to {output_path}? [Y/n/e edit path]: ").strip().lower()
            if choice in ("", "y", "yes"):
                break
            elif choice == "n":
                print("[parrot] Discarded.")
                sys.exit(0)
            elif choice == "e":
                new_path = _safe_input("  Path: ").strip()
                if new_path:
                    output_path = Path(new_path)
            else:
                print("  Please answer Y (save), n (discard), or e (edit path)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml_text, encoding="utf-8")

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
    from .validator import validate_text

    config = load_config()
    generator = SkillGenerator(config)

    print("Parrot — Create a new Skill\n")

    name = _safe_input("Skill name (kebab-case): ").strip()
    while not name:
        name = _safe_input("Skill name (kebab-case): ").strip()

    desc = _safe_input("Description: ").strip()
    while not desc:
        desc = _safe_input("Description: ").strip()

    print("\nParameters (name=default_value, empty to finish):")
    params = []
    while True:
        entry = _safe_input(f"  param [{len(params) + 1}]: ").strip()
        if not entry:
            break
        if "=" in entry:
            pname, _, pdefault = entry.partition("=")
            params.append({"name": pname.strip(), "default": pdefault.strip(),
                           "type": _guess_type(pdefault.strip())})
        else:
            params.append({"name": entry, "type": "string"})

    conc = _safe_input("\nConcurrency [block/allow] (default block): ").strip()
    concurrency = conc if conc in ("block", "allow") else "block"

    print("\nSteps (empty command to finish):")
    steps = []
    sid = 1
    while True:
        cmd = _safe_input(f"  step {sid} command (empty to finish): ").strip()
        if not cmd:
            break
        timeout_str = _safe_input(f"    timeout_seconds (default 30): ").strip()
        timeout = int(timeout_str) if timeout_str else 30

        retry_str = _safe_input(f"    retry (default 0): ").strip()
        retry = int(retry_str) if retry_str else 0

        expected = _safe_input(f"    expected_output_pattern (optional): ").strip()
        rollback = _safe_input(f"    rollback command (optional): ").strip()

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
        description="Parrot — learn terminal operations and compose reusable Skill YAML",
        prog="parrot",
    )
    sub = parser.add_subparsers(dest="command")

    # parrot learn
    p_learn = sub.add_parser("learn", help="Record operations and generate Skill YAML")
    p_learn.add_argument("-t", "--task", help="Task description")
    p_learn.add_argument("-o", "--output", help="Output path for Skill YAML (.skill.yaml or .parrot.json)")
    p_learn.add_argument("--skip-llm", action="store_true", help="Skip LLM, save .parrot.json for later compose")

    # parrot compose
    p_compose = sub.add_parser("compose", help="Generate Skill YAML from .parrot.json or .cast file")
    p_compose.add_argument("file", help="Path to .parrot.json or .cast file")
    p_compose.add_argument("-t", "--task", help="Task description")
    p_compose.add_argument("-o", "--output", help="Output path for Skill YAML")
    p_compose.add_argument("--skip-llm", action="store_true", help="Skip LLM, output template YAML")

    # parrot new
    sub.add_parser("new", help="Create a Skill YAML interactively without recording")

    # parrot validate
    p_validate = sub.add_parser("validate", help="Validate an existing Skill YAML file")
    p_validate.add_argument("skill_file", help="Path to .skill.yaml file")

    args = parser.parse_args()

    if args.command == "learn":
        cmd_learn(args)
    elif args.command == "compose":
        cmd_compose(args)
    elif args.command == "new":
        cmd_new(args)
    elif args.command == "validate":
        cmd_validate(args)
    else:
        parser.print_help()


# ── Helpers ───────────────────────────────────────────────────────

def _save_parrot_json(path: Path, seg_result, cast_path: str):
    """Save segmentation result as .parrot.json intermediate format."""
    data = {
        "version": "0.1.0",
        "source": os.path.basename(cast_path),
        "has_stdin_events": seg_result.has_stdin_events,
        "tui_warnings": seg_result.tui_warnings,
        "secret_warnings": seg_result.secret_warnings,
        "segments": [
            {
                "command": s.command,
                "output": s.output,
                "prompt": s.prompt,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "container_context": s.container_context,
                "in_tui": s.in_tui,
            }
            for s in seg_result.segments
        ],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_parrot_json(path: str):
    """Load .parrot.json back into a SegmentationResult."""
    from .segmenter import Segment, SegmentationResult

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    segments = [
        Segment(
            command=s["command"],
            output=s["output"],
            prompt=s.get("prompt", ""),
            start_time=s.get("start_time", 0.0),
            end_time=s.get("end_time", 0.0),
            container_context=s.get("container_context", ""),
            in_tui=s.get("in_tui", False),
        )
        for s in data["segments"]
    ]

    return SegmentationResult(
        segments=segments,
        tui_warnings=data.get("tui_warnings", []),
        secret_warnings=data.get("secret_warnings", []),
        has_stdin_events=data.get("has_stdin_events", False),
    )


def _get_output_path(explicit: str, task: str, suffix: str, config) -> Path:
    """Determine output path from explicit arg, task name, or default."""
    if explicit:
        return Path(explicit)
    task_slug = _guess_skill_name(task) if task else "recording"
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{task_slug}.{suffix}"


def _guess_skill_name(task: str) -> str:
    import re
    name = task.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"\s+", "-", name)
    return name[:50] if name else "untitled"


def _guess_type(value: str) -> str:
    if value.isdigit():
        return "integer"
    if value.lower() in ("true", "false"):
        return "boolean"
    return "string"


if __name__ == "__main__":
    main()
