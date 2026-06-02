"""CLI entry point for parrot-mcp."""

import argparse
import sys

from .server import MCPServer


def main():
    parser = argparse.ArgumentParser(
        description="Parrot MCP — MCP server for Parrot Ops Skill execution",
        prog="parrot-mcp",
    )
    parser.add_argument("--skill-dir", "-s", default="./skills",
                        help="Directory containing .skill.yaml files (default: ./skills)")
    parser.add_argument("--agent", "-a", default="http://127.0.0.1:9090",
                        help="parrot-agent base URL (default: http://127.0.0.1:9090)")
    parser.add_argument("--sse", action="store_true",
                        help="Use SSE transport instead of stdio")
    parser.add_argument("--port", "-p", type=int, default=9091,
                        help="SSE listen port (default: 9091)")
    args = parser.parse_args()

    if args.sse:
        _run_sse(args)
    else:
        server = MCPServer(skill_dir=args.skill_dir, agent_url=args.agent)
        server.run()


def _run_sse(args):
    """P1: SSE transport mode (simplified)."""
    print(f"[parrot-mcp] SSE mode not yet implemented. Use stdio mode.",
          file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
