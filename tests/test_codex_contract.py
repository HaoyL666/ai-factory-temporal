from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_factory_temporal import activities, config
from ai_factory_temporal.activities import _evaluate_codex_output_contract, run_codex_step
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


if __name__ == "__main__":
    unittest.main()
