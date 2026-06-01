"""P0-4: Persistence & audit logging.

SQLite for task/step state. JSON files for completed task audit logs.
7-day TTL auto-cleanup.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .executor import ExecutionResult, StepResult


class Persistence:
    """Manage task state and audit logs."""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.parrot"))
        self.db_path = self.data_dir / "agent.db"
        self.audit_dir = self.data_dir / "audit"
        self._init_dirs()
        self._init_db()

    def _init_dirs(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def _init_db(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    skill_name TEXT,
                    status TEXT DEFAULT 'queued',
                    params TEXT DEFAULT '{}',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    step_id TEXT,
                    command TEXT,
                    working_dir TEXT DEFAULT '',
                    exit_code INTEGER DEFAULT -1,
                    output TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    retries_used INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    rolled_back INTEGER DEFAULT 0,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                )
            """)
            conn.commit()

    # ── Task CRUD ──────────────────────────────────────────────

    def create_task(self, task_id: str, skill_name: str, params: dict) -> str:
        now = _now()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, skill_name, params, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, skill_name, json.dumps(params), now, now),
            )
            conn.commit()
        return task_id

    def update_task_status(self, task_id: str, status: str):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, _now(), task_id),
            )
            conn.commit()

    def get_task(self, task_id: str) -> Optional[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "task_id": row[0], "skill_name": row[1], "status": row[2],
            "params": row[3], "created_at": row[4], "updated_at": row[5],
        }

    def list_active_tasks(self) -> list[str]:
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT task_id FROM tasks WHERE status IN ('queued', 'running', 'blocked')"
            ).fetchall()
        return [r[0] for r in rows]

    # ── Steps ──────────────────────────────────────────────────

    def save_step(self, task_id: str, step: StepResult):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO steps (task_id, step_id, command, working_dir, "
                "exit_code, output, duration_ms, retries_used, status, rolled_back) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, step.step_id, step.command, step.working_dir,
                 step.exit_code, step.output[:10000], step.duration_ms,
                 step.retries_used, step.status, int(step.rolled_back)),
            )
            conn.commit()

    def get_steps(self, task_id: str) -> list[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [
            {
                "step": r[2], "command": r[3], "working_dir": r[4],
                "exit_code": r[5], "output": r[6], "duration_ms": r[7],
                "retries_used": r[8], "status": r[9], "rolled_back": bool(r[10]),
            }
            for r in rows
        ]

    # ── Audit ──────────────────────────────────────────────────

    def write_audit(self, result: ExecutionResult):
        """Write completed/failed/aborted task audit to JSON file."""
        log = {
            "task_id": result.task_id,
            "skill_name": result.skill_name,
            "status": result.status,
            "error": result.error,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "steps": [
                {
                    "step": s.step_id,
                    "command": s.command,
                    "working_dir": s.working_dir,
                    "exit_code": s.exit_code,
                    "output": s.output[:5000],
                    "duration_ms": s.duration_ms,
                    "retries_used": s.retries_used,
                    "status": s.status,
                    "rolled_back": s.rolled_back,
                }
                for s in result.steps
            ],
        }
        path = self.audit_dir / f"{result.task_id}.json"
        path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_audit_log(self, task_id: str) -> Optional[list]:
        """Read audit log for a completed task."""
        path = self.audit_dir / f"{task_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8")).get("steps", [])

    def cleanup_old_audit(self, ttl_days: int = 7):
        """Remove audit logs older than TTL."""
        cutoff = time.time() - ttl_days * 86400
        for f in self.audit_dir.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
