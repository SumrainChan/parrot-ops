"""P0-2: HTTP REST API server.

Wraps SkillExecutor and Persistence behind a simple HTTP API.
Uses standard library http.server for zero external dependencies.
"""

import json
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from .executor import SkillExecutor, ExecutionResult
from .persistence import Persistence


class AgentServer:
    """HTTP API server for parrot-agent."""

    def __init__(self, bind: str = "127.0.0.1", port: int = 9090,
                 data_dir: str = None):
        self.bind = bind
        self.port = port
        self.persistence = Persistence(data_dir)
        self.executors: dict[str, SkillExecutor] = {}  # task_id -> executor
        self.lock = threading.Lock()  # concurrency control (P0-5)
        self.httpd: HTTPServer = None

    def start(self):
        handler = self._make_handler()
        self.httpd = HTTPServer((self.bind, self.port), handler)
        print(f"[parrot-agent] listening on {self.bind}:{self.port}")
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        if self.httpd:
            self.httpd.shutdown()

    def _make_handler(self):
        agent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == "/v1/execute":
                    agent._handle_execute(self)
                elif self.path.startswith("/v1/tasks/") and self.path.endswith("/resume"):
                    task_id = self.path.split("/")[3]
                    agent._handle_resume(self, task_id)
                elif self.path.startswith("/v1/tasks/") and self.path.endswith("/skip"):
                    task_id = self.path.split("/")[3]
                    agent._handle_skip(self, task_id)
                elif self.path.startswith("/v1/tasks/") and self.path.endswith("/abort"):
                    task_id = self.path.split("/")[3]
                    agent._handle_abort(self, task_id)
                elif self.path.startswith("/v1/tasks/") and self.path.endswith("/interact"):
                    task_id = self.path.split("/")[3]
                    agent._handle_interact(self, task_id)
                else:
                    self._send_json(404, {"error": "not_found"})

            def do_GET(self):
                if self.path.startswith("/v1/tasks/") and self.path.endswith("/log"):
                    task_id = self.path.split("/")[3]
                    agent._handle_get_log(self, task_id)
                elif self.path.startswith("/v1/tasks/"):
                    task_id = self.path.split("/")[3]
                    agent._handle_get_task(self, task_id)
                elif self.path == "/v1/health":
                    agent._handle_health(self)
                elif self.path == "/v1/tasks":
                    agent._handle_list_tasks(self)
                else:
                    self._send_json(404, {"error": "not_found"})

            def _send_json(self, status, data):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self):
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length)) if length else {}

            def log_message(self, *args):
                pass  # quiet

        return Handler

    # ── Handlers ────────────────────────────────────────────────

    def _handle_execute(self, handler):
        body = handler._read_body()
        skill_yaml = body.get("skill", "")
        params = body.get("params", {})

        if not skill_yaml:
            handler._send_json(400, {"error": "missing 'skill' field"})
            return

        # Concurrency check
        with self.lock:
            active = self.persistence.list_active_tasks()
            if active:
                handler._send_json(409, {
                    "error": "concurrency_blocked",
                    "active_tasks": active,
                })
                return

            task_id = uuid.uuid4().hex[:8]
            self.persistence.create_task(task_id, "skill", params)
            self.persistence.update_task_status(task_id, "running")

        # Execute in background thread
        executor = SkillExecutor(task_id, skill_yaml, params)
        self.executors[task_id] = executor

        def run():
            result = executor.execute()
            for s in result.steps:
                self.persistence.save_step(task_id, s)
            self.persistence.update_task_status(task_id, result.status)
            if result.status in ("completed", "failed", "aborted"):
                self.persistence.write_audit(result)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        handler._send_json(200, {"task_id": task_id, "status": "running"})

    def _handle_get_task(self, handler, task_id: str):
        task = self.persistence.get_task(task_id)
        if not task:
            handler._send_json(404, {"error": "task_not_found"})
            return

        executor = self.executors.get(task_id)
        steps = self.persistence.get_steps(task_id)
        result = {
            "task_id": task_id,
            "status": task["status"],
            "current_step": executor.result.current_step if executor else 0,
            "total_steps": executor.result.total_steps if executor else len(steps),
            "steps": steps,
            "error": executor.result.error if executor else "",
        }
        handler._send_json(200, result)

    def _handle_get_log(self, handler, task_id: str):
        log = self.persistence.get_audit_log(task_id)
        if log is None:
            handler._send_json(404, {"error": "no_log_found"})
            return
        handler._send_json(200, log)

    def _handle_health(self, handler):
        active = len(self.persistence.list_active_tasks())
        completed = len(list(self.persistence.audit_dir.glob("*.json")))
        handler._send_json(200, {
            "status": "ok",
            "active_tasks": active,
            "completed_tasks": completed,
        })

    def _handle_list_tasks(self, handler):
        active = self.persistence.list_active_tasks()
        handler._send_json(200, {"active": active})

    def _handle_resume(self, handler, task_id: str):
        executor = self.executors.get(task_id)
        if not executor:
            handler._send_json(404, {"error": "task_not_found"})
            return
        self.persistence.update_task_status(task_id, "running")

        def run():
            result = executor.resume()
            for s in result.steps:
                self.persistence.save_step(task_id, s)
            self.persistence.update_task_status(task_id, result.status)
            if result.status in ("completed", "failed", "aborted"):
                self.persistence.write_audit(result)

        threading.Thread(target=run, daemon=True).start()
        handler._send_json(200, {"status": "resumed"})

    def _handle_skip(self, handler, task_id: str):
        executor = self.executors.get(task_id)
        if not executor:
            handler._send_json(404, {"error": "task_not_found"})
            return
        self.persistence.update_task_status(task_id, "running")

        def run():
            result = executor.skip()
            for s in result.steps:
                self.persistence.save_step(task_id, s)
            self.persistence.update_task_status(task_id, result.status)
            if result.status in ("completed", "failed", "aborted"):
                self.persistence.write_audit(result)

        threading.Thread(target=run, daemon=True).start()
        handler._send_json(200, {"status": "skipped"})

    def _handle_abort(self, handler, task_id: str):
        executor = self.executors.get(task_id)
        if not executor:
            handler._send_json(404, {"error": "task_not_found"})
            return

        def run():
            result = executor.abort()
            self.persistence.update_task_status(task_id, result.status)
            self.persistence.write_audit(result)

        threading.Thread(target=run, daemon=True).start()
        handler._send_json(200, {"status": "aborted"})

    def _handle_interact(self, handler, task_id: str):
        executor = self.executors.get(task_id)
        if not executor:
            handler._send_json(404, {"error": "task_not_found"})
            return
        body = handler._read_body()
        var_name = body.get("variable", "")
        value = body.get("value", "")
        if var_name:
            executor.params[var_name] = value

        def run():
            result = executor.resume()
            for s in result.steps:
                self.persistence.save_step(task_id, s)
            self.persistence.update_task_status(task_id, result.status)

        threading.Thread(target=run, daemon=True).start()
        handler._send_json(200, {"status": "resumed"})
