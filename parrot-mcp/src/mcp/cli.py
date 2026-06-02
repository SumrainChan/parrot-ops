"""CLI entry point for parrot-mcp."""

import argparse
import sys

from .server import MCPServer


def main():
    parser = argparse.ArgumentParser(
        description="Parrot MCP — MCP server for Parrot Ops Skill execution",
        prog="parrot-mcp",
    )
    parser.add_argument("--agent", "-a", default="http://127.0.0.1:9090",
                        help="parrot-agent base URL (default: http://127.0.0.1:9090)")
    parser.add_argument("--skill-dir", "-s", default="",
                        help="Local skill directory (fallback if agent has no skills API)")
    parser.add_argument("--sse", action="store_true",
                        help="Use SSE transport instead of stdio")
    parser.add_argument("--port", "-p", type=int, default=9091,
                        help="SSE listen port (default: 9091)")
    args = parser.parse_args()

    if args.sse:
        print(f"[parrot-mcp] SSE mode not yet implemented.", file=sys.stderr)
        sys.exit(1)

    server = MCPServer(agent_url=args.agent, skill_dir=args.skill_dir)
    server.run()


if __name__ == "__main__":
    main()
