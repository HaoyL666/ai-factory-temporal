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
                  feedback_retry_count INTEGER NOT NULL DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS workflow_usage (
                  id TEXT PRIMARY KEY,
                  workflow_id TEXT NOT NULL,
                  step_id TEXT NOT NULL,
                  step_run_number INTEGER NOT NULL,
                  role TEXT NOT NULL,
                  review_round INTEGER NOT NULL,
                  mode TEXT NOT NULL,
                  model TEXT,
                  thread_id TEXT,
                  turn_id TEXT,
                  input_tokens INTEGER NOT NULL DEFAULT 0,
                  output_tokens INTEGER NOT NULL DEFAULT 0,
                  cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
                  cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
                  billable_input_tokens INTEGER NOT NULL DEFAULT 0,
                  total_tokens INTEGER NOT NULL DEFAULT 0,
                  estimated_cost REAL,
                  cost_currency TEXT NOT NULL DEFAULT 'USD',
                  raw_usage_json TEXT NOT NULL,
                  metadata_json TEXT NOT NULL,
                  artifact_uri TEXT,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(workflow_id) REFERENCES workflows(id)
                );

                CREATE INDEX IF NOT EXISTS idx_workflow_steps_order
                  ON workflow_steps(workflow_id, step_order);
                CREATE INDEX IF NOT EXISTS idx_workflow_usage_workflow
                  ON workflow_usage(workflow_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_workflow_usage_step
                  ON workflow_usage(workflow_id, step_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_usage_turn_id
                  ON workflow_usage(turn_id)
                  WHERE turn_id IS NOT NULL AND turn_id != '';
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(workflow_steps)").fetchall()
            }
            if "feedback_retry_count" not in columns:
                conn.execute(
                    "ALTER TABLE workflow_steps "
                    "ADD COLUMN feedback_retry_count INTEGER NOT NULL DEFAULT 0"
                )
                if "retry_count" in columns:
                    conn.execute(
                        "UPDATE workflow_steps "
                        "SET feedback_retry_count = retry_count"
                    )
            conn.execute(
                """
                UPDATE workflow_steps
                SET runner = 'codex'
                WHERE kind = 'codex' AND (runner IS NULL OR runner = '')
                """
            )
            conn.execute(
                """
                UPDATE workflow_steps
                SET runner = 'script'
                WHERE kind = 'deterministic' AND (runner IS NULL OR runner = '')
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
                        _step_runner(step),
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

    def mark_step_running(
        self,
        workflow_id: str,
        step_id: str,
        feedback_retry_count: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE workflow_steps
                SET status = ?, started_at = ?, finished_at = NULL, error = NULL,
                    feedback_retry_count = ?
                WHERE workflow_id = ? AND step_id = ?
                """,
                (
                    StepStatus.RUNNING.value,
                    utc_now(),
                    feedback_retry_count,
                    workflow_id,
                    step_id,
                ),
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
        feedback_retry_count: int | None = None,
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
        if feedback_retry_count is not None:
            assignments.append("feedback_retry_count = ?")
            params.append(feedback_retry_count)
        if output.get("runner"):
            assignments.append("runner = ?")
            params.append(str(output["runner"]))
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

    def record_codex_usage(
        self,
        *,
        workflow_id: str,
        step_id: str,
        step_run_number: int,
        role: str,
        review_round: int,
        mode: str,
        model: str | None,
        thread_id: str | None,
        turn_id: str | None,
        usage: dict[str, int],
        raw_usage: Any,
        metadata: dict[str, Any],
        estimated_cost: float | None,
        cost_currency: str,
        artifact_uri: str,
    ) -> dict[str, Any]:
        record_id = uuid.uuid4().hex
        created_at = utc_now()
        raw_usage_json = json.dumps(raw_usage or {}, sort_keys=True)
        metadata_json = json.dumps(metadata or {}, sort_keys=True)

        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO workflow_usage (
                  id, workflow_id, step_id, step_run_number, role, review_round,
                  mode, model, thread_id, turn_id, input_tokens, output_tokens,
                  cache_read_input_tokens, cache_creation_input_tokens,
                  billable_input_tokens, total_tokens, estimated_cost,
                  cost_currency, raw_usage_json, metadata_json, artifact_uri,
                  created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    workflow_id,
                    step_id,
                    step_run_number,
                    role,
                    review_round,
                    mode,
                    model,
                    thread_id,
                    turn_id,
                    int(usage.get("input_tokens", 0)),
                    int(usage.get("output_tokens", 0)),
                    int(usage.get("cache_read_input_tokens", 0)),
                    int(usage.get("cache_creation_input_tokens", 0)),
                    int(usage.get("billable_input_tokens", 0)),
                    int(usage.get("total_tokens", 0)),
                    estimated_cost,
                    cost_currency,
                    raw_usage_json,
                    metadata_json,
                    artifact_uri,
                    created_at,
                ),
            )
            if cursor.rowcount:
                row = conn.execute(
                    "SELECT * FROM workflow_usage WHERE id = ?",
                    (record_id,),
                ).fetchone()
            elif turn_id:
                row = conn.execute(
                    "SELECT * FROM workflow_usage WHERE turn_id = ?",
                    (turn_id,),
                ).fetchone()
            else:
                row = None

        if row is None:
            raise RuntimeError("Failed to record Codex usage")
        return self._usage_from_row(row)

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

    def list_usage_records(self, workflow_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workflow_usage
                WHERE workflow_id = ?
                ORDER BY created_at ASC
                """,
                (workflow_id,),
            ).fetchall()
        return [self._usage_from_row(row) for row in rows]

    def workflow_response(self, workflow_id: str) -> dict[str, Any]:
        usage_records = self.list_usage_records(workflow_id)
        return {
            "workflow": self.get_workflow(workflow_id),
            "steps": self.list_steps(workflow_id),
            "usage": {
                "summary": usage_summary(usage_records),
                "records": usage_records,
            },
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
            "feedback_retry_count": row["feedback_retry_count"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _usage_from_row(row: sqlite3.Row) -> dict[str, Any]:
        usage = {
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cache_read_input_tokens": row["cache_read_input_tokens"],
            "cache_creation_input_tokens": row["cache_creation_input_tokens"],
            "billable_input_tokens": row["billable_input_tokens"],
            "total_tokens": row["total_tokens"],
        }
        return {
            "id": row["id"],
            "workflow_id": row["workflow_id"],
            "step_id": row["step_id"],
            "step_run_number": row["step_run_number"],
            "role": row["role"],
            "review_round": row["review_round"],
            "mode": row["mode"],
            "model": row["model"],
            "thread_id": row["thread_id"],
            "turn_id": row["turn_id"],
            "usage": usage,
            "estimated_cost": row["estimated_cost"],
            "cost_currency": row["cost_currency"],
            "raw_usage": json.loads(row["raw_usage_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "artifact_uri": row["artifact_uri"],
            "created_at": row["created_at"],
        }


def _step_runner(step: dict[str, Any]) -> str | None:
    if step.get("runner"):
        return str(step["runner"])
    if step.get("kind") == "codex":
        return "codex"
    if step.get("kind") == "deterministic":
        return "script"
    return None


def usage_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "billable_input_tokens": 0,
        "total_tokens": 0,
    }
    estimated_cost = 0.0
    priced_turns = 0
    currency = "USD"
    for record in records:
        for key in totals:
            totals[key] += int(record["usage"].get(key, 0))
        if record.get("estimated_cost") is not None:
            estimated_cost += float(record["estimated_cost"])
            priced_turns += 1
            currency = str(record.get("cost_currency") or currency)

    return {
        "turn_count": len(records),
        "priced_turn_count": priced_turns,
        "usage": totals,
        "estimated_cost": estimated_cost if priced_turns else None,
        "cost_currency": currency,
    }
