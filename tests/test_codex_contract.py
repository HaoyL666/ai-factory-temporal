from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_factory_temporal import activities, config
from ai_factory_temporal.activities import (
    _evaluate_codex_output_contract,
    _evaluate_codex_review_contract,
    run_codex_step,
)
from ai_factory_temporal.catalog import ProjectCatalog
from ai_factory_temporal.models import StepStatus
from ai_factory_temporal.store import SQLiteStore, new_workflow_id


class CodexContractTest(unittest.TestCase):
    def test_contract_accepts_success_json(self) -> None:
        result = _evaluate_codex_output_contract(
            {"output_contract": {"type": "status_json", "required_fields": ["summary"]}},
            '{"status":"SUCCEEDED","summary":"done"}',
        )

        self.assertIsNotNone(result)
        self.assertIsNone(result["error"])
        self.assertEqual(result["status"], "SUCCEEDED")

    def test_contract_is_required_by_default(self) -> None:
        result = _evaluate_codex_output_contract(
            {},
            '{"status":"SUCCEEDED","summary":"done"}',
        )

        self.assertIsNone(result["error"])
        self.assertEqual(result["status"], "SUCCEEDED")

    def test_default_contract_fails_malformed_output(self) -> None:
        result = _evaluate_codex_output_contract(
            {},
            "I could not complete this.",
        )

        self.assertIn("Codex output contract violation", result["error"])

    def test_contract_cannot_be_disabled(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Codex output contracts are required and cannot be disabled",
        ):
            _evaluate_codex_output_contract(
                {"output_contract": False},
                '{"status":"SUCCEEDED","summary":"done"}',
            )

    def test_unsupported_contract_type_is_system_config_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported Codex output contract type"):
            _evaluate_codex_output_contract(
                {"output_contract": "xml_status"},
                '{"status":"SUCCEEDED","summary":"done"}',
            )

    def test_contract_accepts_fenced_json(self) -> None:
        result = _evaluate_codex_output_contract(
            {"output_contract": "status_json"},
            '```json\n{"status":"OK","summary":"done"}\n```',
        )

        self.assertIsNotNone(result)
        self.assertIsNone(result["error"])
        self.assertEqual(result["status"], "OK")

    def test_contract_fails_model_declared_failure(self) -> None:
        result = _evaluate_codex_output_contract(
            {"output_contract": "status_json"},
            '{"status":"FAILED","error":"target file missing"}',
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error"], "target file missing")

    def test_contract_fails_malformed_output(self) -> None:
        result = _evaluate_codex_output_contract(
            {"output_contract": "status_json"},
            "I could not complete this.",
        )

        self.assertIsNotNone(result)
        self.assertIn("Codex output contract violation", result["error"])

    def test_review_contract_accepts_changes_requested(self) -> None:
        result = _evaluate_codex_review_contract(
            """
            {
              "status": "SUCCEEDED",
              "verdict": "CHANGES_REQUESTED",
              "summary": "Needs one repair.",
              "findings": [],
              "required_fixes": [],
              "error": null
            }
            """
        )

        self.assertIsNone(result["error"])
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["verdict"], "CHANGES_REQUESTED")

    def test_stub_codex_step_returns_contract_output_without_explicit_contract(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        pack = catalog.load_project_pack("generic-project")
        workflow = catalog.load_workflow("generic-project", "codex_edit_e2e")
        step = dict(workflow["steps"][0])
        step.pop("output_contract", None)
        old_mode = activities.config.CODEX_MODE
        activities.config.CODEX_MODE = "stub"

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                target = tmp_path / "target.txt"
                target.write_text("PENDING\n", encoding="utf-8")
                store = SQLiteStore(tmp_path / "test.db")
                workflow_id = new_workflow_id()
                store.create_workflow(
                    workflow_id=workflow_id,
                    project_id="generic-project",
                    task_type="codex_edit_e2e",
                    inputs={
                        "file_name": "target.txt",
                        "old_text": "PENDING",
                        "new_text": "DONE",
                    },
                    workspace_path=str(tmp_path),
                    steps=workflow["steps"],
                )

                result = run_codex_step(
                    {
                        "workflow_id": workflow_id,
                        "project_id": "generic-project",
                        "task_type": "codex_edit_e2e",
                        "inputs": {
                            "file_name": "target.txt",
                            "old_text": "PENDING",
                            "new_text": "DONE",
                        },
                        "workspace_path": str(tmp_path),
                        "project_pack": pack,
                        "project_dir": str(config.PROJECTS_DIR / "generic-project"),
                        "db_path": str(tmp_path / "test.db"),
                        "artifacts_path": str(tmp_path / "artifacts"),
                        "step": step,
                        "previous_outputs": [],
                        "retry_count": 0,
                    }
                )

                self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
                self.assertEqual(result["output"]["contract_status"], "SUCCEEDED")
                self.assertEqual(result["output"]["contract"]["status"], "SUCCEEDED")
        finally:
            activities.config.CODEX_MODE = old_mode

    def test_stub_codex_step_review_approves_first_iteration(self) -> None:
        result, store, workflow_id, artifact_uri = self._run_stub_reviewed_codex_step(
            {
                "enabled": True,
                "max_review_iterations": 2,
                "stub_verdicts": ["APPROVED"],
            }
        )

        self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
        self.assertEqual(result["output"]["contract_status"], "SUCCEEDED")
        self.assertEqual(result["output"]["review"]["final_verdict"], "APPROVED")
        self.assertEqual(result["output"]["review"]["review_iterations"], 1)
        self.assertTrue((artifact_uri / "review-1" / "coder-contract.json").exists())
        self.assertTrue((artifact_uri / "review-1" / "reviewer-contract.json").exists())
        self.assertTrue((artifact_uri / "step-result.json").exists())
        self.assertEqual(store.list_steps(workflow_id)[0]["status"], StepStatus.SUCCEEDED.value)

    def test_stub_codex_step_review_repairs_then_approves(self) -> None:
        result, _store, _workflow_id, artifact_uri = self._run_stub_reviewed_codex_step(
            {
                "enabled": True,
                "max_review_iterations": 2,
                "stub_verdicts": ["CHANGES_REQUESTED", "APPROVED"],
            }
        )

        self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
        self.assertEqual(result["output"]["review"]["final_verdict"], "APPROVED")
        self.assertEqual(result["output"]["review"]["review_iterations"], 2)
        self.assertEqual(result["output"]["review"]["repair_attempts"], 1)
        self.assertTrue((artifact_uri / "review-2" / "coder-prompt.md").exists())

    def test_stub_codex_step_review_rejection_fails_after_max_iterations(self) -> None:
        result, store, workflow_id, artifact_uri = self._run_stub_reviewed_codex_step(
            {
                "enabled": True,
                "max_review_iterations": 1,
                "stub_verdicts": ["CHANGES_REQUESTED"],
            }
        )

        self.assertEqual(result["status"], StepStatus.FAILED.value)
        self.assertIn("Codex review rejected final attempt", result["error"])
        self.assertEqual(result["output"]["contract_status"], "FAILED")
        self.assertEqual(result["output"]["review"]["final_verdict"], "CHANGES_REQUESTED")
        self.assertTrue((artifact_uri / "step-result.json").exists())
        stored = store.list_steps(workflow_id)[0]
        self.assertEqual(stored["status"], StepStatus.FAILED.value)
        self.assertIn("Codex review rejected final attempt", stored["error"])

    def _run_stub_reviewed_codex_step(
        self,
        review_config: dict[str, object],
    ) -> tuple[dict[str, object], SQLiteStore, str, Path]:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        pack = catalog.load_project_pack("generic-project")
        workflow = catalog.load_workflow("generic-project", "codex_edit_e2e")
        step = dict(workflow["steps"][0])
        step["review"] = review_config
        old_mode = activities.config.CODEX_MODE
        activities.config.CODEX_MODE = "stub"

        try:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            tmp_path = Path(tmp.name)
            (tmp_path / "target.txt").write_text("PENDING\n", encoding="utf-8")
            store = SQLiteStore(tmp_path / "test.db")
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="codex_edit_e2e",
                inputs={
                    "file_name": "target.txt",
                    "old_text": "PENDING",
                    "new_text": "DONE",
                },
                workspace_path=str(tmp_path),
                steps=[step],
            )

            result = run_codex_step(
                {
                    "workflow_id": workflow_id,
                    "project_id": "generic-project",
                    "task_type": "codex_edit_e2e",
                    "inputs": {
                        "file_name": "target.txt",
                        "old_text": "PENDING",
                        "new_text": "DONE",
                    },
                    "workspace_path": str(tmp_path),
                    "project_pack": pack,
                    "project_dir": str(config.PROJECTS_DIR / "generic-project"),
                    "db_path": str(tmp_path / "test.db"),
                    "artifacts_path": str(tmp_path / "artifacts"),
                    "step": step,
                    "previous_outputs": [],
                    "retry_count": 0,
                }
            )
            return result, store, workflow_id, Path(result["artifact_uri"])
        finally:
            activities.config.CODEX_MODE = old_mode


if __name__ == "__main__":
    unittest.main()
