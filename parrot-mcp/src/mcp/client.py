"""P0-3: HTTP client for parrot-agent."""

import json
import urllib.request
import urllib.error
from typing import Optional


class AgentClient:
    """HTTP client for parrot-agent API."""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> bool:
        """Check agent connectivity."""
        try:
            self._get("/v1/health")
            return True
        except Exception:
            return False

    def execute(self, skill_yaml: str, params: dict) -> dict:
        """Submit a skill for execution."""
        return self._post("/v1/execute", {
            "skill": skill_yaml,
            "params": params,
        })

    def get_task(self, task_id: str) -> dict:
        """Get task status."""
        return self._get(f"/v1/tasks/{task_id}")

    def resume(self, task_id: str) -> dict:
        return self._post(f"/v1/tasks/{task_id}/resume", {})

    def skip(self, task_id: str) -> dict:
        return self._post(f"/v1/tasks/{task_id}/skip", {})

    def abort(self, task_id: str) -> dict:
        return self._post(f"/v1/tasks/{task_id}/abort", {})

    def interact(self, task_id: str, variable: str, value: str) -> dict:
        return self._post(f"/v1/tasks/{task_id}/interact", {
            "variable": variable, "value": value,
        })

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, data: dict) -> dict:
        return self._request("POST", path, data)

    def _request(self, method: str, path: str, data: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}
