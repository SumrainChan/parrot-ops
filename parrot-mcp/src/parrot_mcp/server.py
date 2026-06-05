"""P0-1 + P0-4: MCP server with parrot-agent routing and error forwarding."""

import json
import sys

from .converter import (
    skill_to_tool, build_list_skills_tool, build_skill_search,
)
from .client import AgentClient


class MCPServer:
    """MCP stdio server — JSON-RPC 2.0 over stdin/stdout.

    Pulls skill list from parrot-agent on startup. Falls back to local
    --skill-dir if agent doesn't have skills API.
    """

    def __init__(self, agent_url: str, skill_dir: str = ""):
        self.agent = AgentClient(agent_url)
        if not self.agent.health():
            print(f"[parrot-mcp] Agent not reachable at {agent_url}", file=sys.stderr)
            sys.exit(1)

        # Pull skills from agent
        self.skills = self.agent.list_skills()
        if not self.skills and skill_dir:
            # Fallback: load from local dir
            from .converter import load_skills
            self.skills = load_skills(skill_dir)

        self.tools = self._build_tools()
        print(f"[parrot-mcp] Loaded {len(self.skills)} skills, agent OK", file=sys.stderr)

    def run(self):
        """Main stdio loop."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self._handle(request)
                if response is not None:  # notifications return None
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"[parrot-mcp] error: {e}", file=sys.stderr)

    def _handle(self, request: dict) -> dict | None:
        """Route JSON-RPC request to handler."""
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            return self._response(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "parrot-mcp", "version": "0.1.0"},
            })

        if method == "notifications/initialized":
            return None  # no response for notifications

        if method == "tools/list":
            # Re-pull skills each time for live updates
            skills = self.agent.list_skills()
            if skills:
                self.skills = skills
                self.tools = self._build_tools()
            return self._response(req_id, {"tools": list(self.tools.values())})

        if method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = self._call_tool(tool_name, arguments)
            return self._response(req_id, result)

        return self._error(req_id, -32601, f"Unknown method: {method}")

    def _call_tool(self, name: str, args: dict) -> dict:
        """Execute a tool call."""
        # Skill-named tools are shortcuts to execute_skill
        known_skills = {s["name"] for s in self.skills}
        if name in known_skills:
            args["skill_name"] = name
            return self._execute_skill(args)

        if name == "list_skills":
            query = args.get("query", "")
            results = build_skill_search(self.skills, query)
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps([{
                        "name": s["name"],
                        "description": s.get("description", ""),
                        "parameters": {
                            p["name"]: {
                                "type": p.get("type", "string"),
                                "required": p.get("required", False),
                            }
                            for p in s.get("parameters", [])
                        },
                    } for s in results], ensure_ascii=False, indent=2),
                }],
            }

        if name == "execute_skill":
            return self._execute_skill(args)

        if name == "get_task_status":
            if "task_id" not in args:
                return self._text_result("Error: missing task_id")
            result = self.agent.get_task(args["task_id"])
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}

        if name == "resume_skill":
            if "task_id" not in args:
                return self._text_result("Error: missing task_id")
            result = self.agent.resume(args["task_id"])
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

        if name == "skip_step":
            if "task_id" not in args:
                return self._text_result("Error: missing task_id")
            result = self.agent.skip(args["task_id"])
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

        if name == "abort_skill":
            if "task_id" not in args:
                return self._text_result("Error: missing task_id")
            result = self.agent.abort(args["task_id"])
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

        return self._text_result(f"Unknown tool: {name}")

    def _execute_skill(self, args: dict) -> dict:
        """Execute a skill via parrot-agent."""
        skill_name = args.get("skill_name", "")
        if not skill_name:
            return self._text_result("Error: skill_name is required")

        # Validate skill exists in agent
        known = {s["name"] for s in self.skills}
        if skill_name not in known:
            return self._text_result(
                f"Skill '{skill_name}' not found. Available: "
                + ", ".join(sorted(known))
            )

        params = args.get("params", {})
        result = self.agent.execute_by_name(skill_name, params)

        # P0-4: Forward error context
        status = result.get("status", "unknown")
        if status == "blocked":
            error = result.get("error", "")
            text = f"Task blocked: {error}\n\nTask ID: {result.get('task_id', '?')}\nUse resume_skill, skip_step, or abort_skill after investigating."
            return {"content": [{"type": "text", "text": text}]}

        if status == "completed":
            steps_str = ", ".join(
                f"{s.get('step', '?')} {'OK' if s.get('status') == 'completed' else s.get('status', '?')}"
                for s in result.get("steps", [])
            )
            text = f"Task completed successfully.\nSteps: {steps_str}"
            return {"content": [{"type": "text", "text": text}]}

        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}

    def _build_tools(self) -> dict:
        """Build MCP tool definitions from loaded skills."""
        tools = {
            "list_skills": build_list_skills_tool(self.skills),
        }
        for s in self.skills:
            tools[s["name"]] = skill_to_tool(s)
        # Additional built-in tools
        tools["execute_skill"] = {
            "name": "execute_skill",
            "description": "执行一个运维 Skill。Skill 是经过人类审核的确定性操作序列。可用: list_skills 查看。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "Skill 名称"},
                    "params": {"type": "object", "description": "Skill 参数"},
                },
                "required": ["skill_name"],
            },
        }
        tools["get_task_status"] = {
            "name": "get_task_status",
            "description": "查询异步 Skill 任务的当前状态",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务 ID"},
                },
                "required": ["task_id"],
            },
        }
        tools["resume_skill"] = {
            "name": "resume_skill",
            "description": "恢复一个被阻塞的 Skill 任务",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务 ID"},
                },
                "required": ["task_id"],
            },
        }
        tools["skip_step"] = {
            "name": "skip_step",
            "description": "跳过当前失败的步骤继续执行",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务 ID"},
                },
                "required": ["task_id"],
            },
        }
        tools["abort_skill"] = {
            "name": "abort_skill",
            "description": "终止任务并执行回滚",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务 ID"},
                },
                "required": ["task_id"],
            },
        }
        return tools

    def _response(self, req_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def _text_result(self, text: str) -> dict:
        return {"content": [{"type": "text", "text": text}]}
