# Parrot Ops

Demonstrate once, execute anywhere — structured skills for AI agents.

## Modules

| Module | Status | Description | Install |
|---|---|---|---|
| `parrot-recorder` | Phase 1 | Record terminal operations, generate Skill YAML | `pip install ./parrot-recorder` |
| `parrot-agent` | Phase 2 (planned) | Remote execution engine with rollback & audit | - |
| `parrot-mcp` | Phase 2 (planned) | MCP plugin for standardized AI agent integration | - |

## Quick Start

```bash
# 1. Configure
cp .env.example .env
# Edit .env → set ANTHROPIC_API_KEY

# 2. Install recorder
pip install ./parrot-recorder

# 3. Record and generate a Skill
parrot learn

# Or record offline (skip LLM), compose later
parrot learn --skip-llm                    # saves .parrot.json
parrot compose <output>.parrot.json        # generates Skill YAML

# Or create one interactively (no recording needed)
parrot new
```

## CLI Reference

```
parrot learn               Record + generate Skill YAML (--skip-llm for offline)
parrot compose <file>      Generate Skill YAML from .parrot.json or .cast
parrot new                 Interactive Skill creation (no recording)
parrot validate <file>     Validate a Skill YAML file
```

## Skill YAML

Skills are the core abstraction of Parrot Ops — structured YAML files that AI agents can read and execute. See `docs/` for the format specification and examples.
