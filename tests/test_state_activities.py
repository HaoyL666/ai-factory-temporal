from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_factory_temporal.activities import (
    record_approval_wait,
    record_step_waiting_for_feedback,
    set_workflow_status,
)
from ai_factory_temporal.models import StepStatus, WorkflowStatus
from ai_factory_temporal.store import SQLiteStore, new_workflow_id


class StateActivitiesTest(unittest.TestCase):
    def test_approval_wait_records_step_without_hiding_workflow_status_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            store = SQLiteStore(db_path)
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="approval",
                inputs={},
                workspace_path=tmp,
                steps=[{"id": "approval_gate", "name": "Approval Gate", "kind": "approval"}],
            )
            set_workflow_status(
                {
                    "db_path": str(db_path),
                    "workflow_id": workflow_id,
                    "status": WorkflowStatus.RUNNING.value,
                }
            )

            record_approval_wait(
                {
                    "db_path": str(db_path),
                    "workflow_id": workflow_id,
                    "step_id": "approval_gate",
                    "message": "Approve this step.",
                }
            )

            self.assertEqual(store.get_workflow(workflow_id)["status"], WorkflowStatus.RUNNING.value)
            self.assertEqual(
                store.list_steps(workflow_id)[0]["status"],
                StepStatus.WAITING_FOR_APPROVAL.value,
            )

            set_workflow_status(
                {
                    "db_path": str(db_path),
                    "workflow_id": workflow_id,
                    "status": WorkflowStatus.WAITING_FOR_APPROVAL.value,
                }
            )
            self.assertEqual(
                store.get_workflow(workflow_id)["status"],
                WorkflowStatus.WAITING_FOR_APPROVAL.value,
            )

    def test_feedback_wait_records_step_without_hiding_workflow_status_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            store = SQLiteStore(db_path)
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="feedback",
                inputs={},
                workspace_path=tmp,
                steps=[
                    {
                        "id": "failing_step",
                        "name": "Failing Step",
                        "kind": "deterministic",
                        "runner": "script",
                    }
                ],
            )
            set_workflow_status(
                {
                    "db_path": str(db_path),
                    "workflow_id": workflow_id,
                    "status": WorkflowStatus.RUNNING.value,
                }
            )

            record_step_waiting_for_feedback(
                {
                    "db_path": str(db_path),
                    "workflow_id": workflow_id,
                    "step_id": "failing_step",
                    "output": {"returncode": 40},
                    "artifact_uri": "/tmp/artifact",
                    "error": "script failed with exit code 40",
                    "feedback_retry_count": 0,
                }
            )

            step = store.list_steps(workflow_id)[0]
            self.assertEqual(store.get_workflow(workflow_id)["status"], WorkflowStatus.RUNNING.value)
            self.assertEqual(step["status"], StepStatus.FAILED_WAITING_FOR_FEEDBACK.value)
            self.assertEqual(step["error"], "script failed with exit code 40")

            set_workflow_status(
                {
                    "db_path": str(db_path),
                    "workflow_id": workflow_id,
                    "status": WorkflowStatus.WAITING_FOR_FEEDBACK.value,
                }
            )
            self.assertEqual(
                store.get_workflow(workflow_id)["status"],
                WorkflowStatus.WAITING_FOR_FEEDBACK.value,
            )


if __name__ == "__main__":
    unittest.main()
