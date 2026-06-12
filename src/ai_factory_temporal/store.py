from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_factory_temporal.models import StepStatus, WorkflowStatus


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_workflow_id() -> str:
    return f"wf-{uuid.uuid4().hex[:12]}"


class SQLiteStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                  id TEXT PRIMARY KEY,
                  temporal_workflow_id TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  task_type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  inputs_json TEXT NOT NULL,
                  workspace_path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workflow_steps (
                  id TEXT PRIMARY KEY,
                  workflow_id TEXT NOT NULL,
                  step_order INTEGER NOT NULL,
                  step_id TEXT NOT NULL,
                  name TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  runner TEXT,
                  status TEXT NOT NULL,
                  config_json TEXT NOT NULL,
                  output_json TEXT,
                  artifact_uri TEXT,
                  error TEXT,
                  retry_count INTEGER NOT NULL DEFAULT 0,
                  started_at TEXT,
                  finished_at TEXT,
                  FOREIGN KEY(workflow_id) REFERENCES workflows(id)
                );

                CREATE TABLE IF NOT EXISTS workflow_feedback (
                  id TEXT PRIMARY KEY,
                  workflow_id TEXT NOT NULL,
                  step_id TEXT NOT NULL,
                  action TEXT NOT NULL,
                  message TEXT,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workflow_approvals (
                  id TEXT PRIMARY KEY,
                  workflow_id TEXT NOT NULL,
                  step_id TEXT NOT NULL,
                  approved INTEGER NOT NULL,
                  comment TEXT,
                  created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_workflow_steps_order
                  ON workflow_steps(workflow_id, step_order);
                """
            )

    def create_workflow(
        self,
        *,
        workflow_id: str,
        project_id: str,
        task_type: str,
        inputs: dict[str, Any],
        workspace_path: str,
        steps: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workflows (
                  id, temporal_workflow_id, project_id, task_type, status,
                  inputs_json, workspace_path, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    workflow_id,
                    project_id,
                    task_type,
                    WorkflowStatus.PENDING.value,
                    json.dumps(inputs, sort_keys=True),
                    workspace_path,
                    now,
                    now,
                ),
            )
            for index, step in enumerate(steps, start=1):
                conn.execute(
                    """
                    INSERT INTO workflow_steps (
                      id, workflow_id, step_order, step_id, name, kind, runner,
                      status, config_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{workflow_id}-{index:02d}-{step['id']}",
                        workflow_id,
                        index,
                        step["id"],
                        step.get("name", step["id"]),
                        step["kind"],
                        step.get("runner"),
                        StepStatus.PENDING.value,
                        json.dumps(step, sort_keys=True),
                    ),
                )

    def set_workflow_status(self, workflow_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), workflow_id),
            )

    def mark_step_running(self, workflow_id: str, step_id: str, retry_count: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE workflow_steps
                SET status = ?, started_at = ?, finished_at = NULL, error = NULL,
                    retry_count = ?
                WHERE workflow_id = ? AND step_id = ?
                """,
                (StepStatus.RUNNING.value, utc_now(), retry_count, workflow_id, step_id),
            )

    def finish_step(
        self,
        *,
        workflow_id: str,
        step_id: str,
        status: str,
        output: dict[str, Any],
        artifact_uri: str | None = None,
        error: str | None = None,
        retry_count: int | None = None,
    ) -> None:
        finished_at = utc_now() if status not in {
            StepStatus.RUNNING.value,
            StepStatus.WAITING_FOR_APPROVAL.value,
            StepStatus.FAILED_WAITING_FOR_FEEDBACK.value,
        } else None
        assignments = [
            "status = ?",
            "output_json = ?",
            "artifact_uri = ?",
            "error = ?",
            "finished_at = ?",
        ]
        params: list[Any] = [
            status,
            json.dumps(output, sort_keys=True),
            artifact_uri,
            error,
            finished_at,
        ]
        if retry_count is not None:
            assignments.append("retry_count = ?")
            params.append(retry_count)
        params.extend([workflow_id, step_id])

        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE workflow_steps
                SET {", ".join(assignments)}
                WHERE workflow_id = ? AND step_id = ?
                """,
                params,
            )

    def record_feedback(self, workflow_id: str, step_id: str, action: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_feedback (id, workflow_id, step_id, action, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, workflow_id, step_id, action, message, utc_now()),
            )

    def record_approval(
        self,
        workflow_id: str,
        step_id: str,
        approved: bool,
        comment: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_approvals (id, workflow_id, step_id, approved, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, workflow_id, step_id, int(approved), comment, utc_now()),
            )

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if row is None:
            raise KeyError(f"Workflow not found: {workflow_id}")
        return {
            "id": row["id"],
            "temporal_workflow_id": row["temporal_workflow_id"],
            "project_id": row["project_id"],
            "task_type": row["task_type"],
            "status": row["status"],
            "inputs": json.loads(row["inputs_json"]),
            "workspace_path": row["workspace_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_steps(self, workflow_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workflow_steps
                WHERE workflow_id = ?
                ORDER BY step_order ASC
                """,
                (workflow_id,),
            ).fetchall()
        return [self._step_from_row(row) for row in rows]

    def workflow_response(self, workflow_id: str) -> dict[str, Any]:
        return {
            "workflow": self.get_workflow(workflow_id),
            "steps": self.list_steps(workflow_id),
        }

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workflow_id": row["workflow_id"],
            "step_order": row["step_order"],
            "step_id": row["step_id"],
            "name": row["name"],
            "kind": row["kind"],
            "runner": row["runner"],
            "status": row["status"],
            "config": json.loads(row["config_json"]),
            "output": json.loads(row["output_json"]) if row["output_json"] else None,
            "artifact_uri": row["artifact_uri"],
            "error": row["error"],
            "retry_count": row["retry_count"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
