from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_factory_temporal import activities, config
from ai_factory_temporal.activities import run_codex_step
from ai_factory_temporal.codex_contracts import (
    evaluate_codex_output_contract as _evaluate_codex_output_contract,
    evaluate_codex_review_contract as _evaluate_codex_review_contract,
)
from ai_factory_temporal.review_prompts import (
    build_repair_prompt as _build_repair_prompt,
    build_reviewer_prompt as _build_reviewer_prompt,
    repair_context as _repair_context,
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

    def test_codex_step_declared_failure_writes_failed_step_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = SQLiteStore(tmp_path / "test.db")
            workflow_id = new_workflow_id()
            step = {
                "id": "codex_failed_contract",
                "name": "Codex Failed Contract",
                "kind": "codex",
                "prompt": "prompts/task.md",
                "output_contract": "status_json",
            }
            project_dir = tmp_path / "project"
            (project_dir / "prompts").mkdir(parents=True)
            (project_dir / "prompts" / "task.md").write_text("# Task\n", encoding="utf-8")
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="failed-contract",
                inputs={},
                workspace_path=str(tmp_path),
                steps=[step],
            )

            final_response = json.dumps(
                {
                    "status": "FAILED",
                    "summary": "Codex reported a business failure.",
                    "error": "intentional failed contract",
                }
            )

            def fake_codex_turn(**kwargs):
                kwargs["prompt_path"].write_text(kwargs["prompt"], encoding="utf-8")
                kwargs["output_path"].write_text(final_response, encoding="utf-8")
                metadata = {
                    "mode": "sdk",
                    "thread_id": "thread-failed",
                    "turn_id": "turn-failed",
                    "usage": None,
                }
                kwargs["metadata_path"].write_text(
                    json.dumps(metadata, sort_keys=True),
                    encoding="utf-8",
                )
                return {"final_response": final_response, "metadata": metadata}

            with patch("ai_factory_temporal.activities._run_codex_turn", fake_codex_turn):
                result = run_codex_step(
                    {
                        "workflow_id": workflow_id,
                        "project_id": "generic-project",
                        "task_type": "failed-contract",
                        "inputs": {},
                        "workspace_path": str(tmp_path),
                        "project_pack": {},
                        "project_dir": str(project_dir),
                        "db_path": str(tmp_path / "test.db"),
                        "artifacts_path": str(tmp_path / "artifacts"),
                        "step": step,
                        "previous_outputs": [],
                        "feedback_retry_count": 0,
                    }
                )

            step_result = json.loads(
                (Path(result["artifact_uri"]) / "step-result.json").read_text(encoding="utf-8")
            )
            stored = store.list_steps(workflow_id)[0]
            self.assertEqual(result["status"], StepStatus.FAILED.value)
            self.assertEqual(result["output"]["status"], StepStatus.FAILED.value)
            self.assertEqual(step_result["status"], StepStatus.FAILED.value)
            self.assertEqual(stored["status"], StepStatus.FAILED.value)
            self.assertEqual(stored["output"]["status"], StepStatus.FAILED.value)
            self.assertEqual(stored["output"]["contract_status"], "FAILED")

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
                        "feedback_retry_count": 0,
                    }
                )

                self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
                self.assertEqual(result["output"]["kind"], "codex")
                self.assertEqual(result["output"]["status"], StepStatus.SUCCEEDED.value)
                self.assertEqual(
                    result["output"]["summary"],
                    "CODEX_STUB_DONE for codex_edit_file",
                )
                self.assertEqual(result["output"]["contract_status"], "SUCCEEDED")
                self.assertEqual(result["output"]["contract"]["status"], "SUCCEEDED")
                artifact_uri = Path(result["artifact_uri"])
                self.assertTrue((artifact_uri / "step-result.json").exists())
                self.assertEqual(
                    result["output"]["step_result"],
                    str(artifact_uri / "step-result.json"),
                )
                usage_records = store.list_usage_records(workflow_id)
                self.assertEqual(len(usage_records), 1)
                self.assertEqual(usage_records[0]["step_id"], "codex_edit_file")
                self.assertEqual(usage_records[0]["role"], "coder")
                self.assertEqual(usage_records[0]["mode"], "stub")
                self.assertEqual(usage_records[0]["usage"]["total_tokens"], 0)
        finally:
            activities.config.CODEX_MODE = old_mode

    def test_stub_codex_step_review_approves_first_round(self) -> None:
        result, store, workflow_id, artifact_uri = self._run_stub_reviewed_codex_step(
            {
                "enabled": True,
                "max_review_rounds": 2,
                "stub_verdicts": ["APPROVED"],
            }
        )

        self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
        self.assertEqual(result["output"]["kind"], "codex")
        self.assertEqual(result["output"]["status"], StepStatus.SUCCEEDED.value)
        self.assertEqual(result["output"]["contract_status"], "SUCCEEDED")
        self.assertEqual(result["output"]["review"]["final_verdict"], "APPROVED")
        self.assertEqual(result["output"]["review"]["review_rounds"], 1)
        self.assertTrue((artifact_uri / "review-round-1" / "coder-contract.json").exists())
        self.assertTrue((artifact_uri / "review-round-1" / "reviewer-contract.json").exists())
        self.assertTrue((artifact_uri / "step-result.json").exists())
        self.assertEqual(store.list_steps(workflow_id)[0]["status"], StepStatus.SUCCEEDED.value)
        usage_records = store.list_usage_records(workflow_id)
        self.assertEqual([record["role"] for record in usage_records], ["coder", "reviewer"])
        self.assertEqual([record["review_round"] for record in usage_records], [1, 1])

    def test_stub_codex_step_review_repairs_then_approves(self) -> None:
        result, store, workflow_id, artifact_uri = self._run_stub_reviewed_codex_step(
            {
                "enabled": True,
                "max_review_rounds": 2,
                "stub_verdicts": ["CHANGES_REQUESTED", "APPROVED"],
            }
        )

        self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
        self.assertEqual(result["output"]["review"]["final_verdict"], "APPROVED")
        self.assertEqual(result["output"]["review"]["review_rounds"], 2)
        self.assertEqual(result["output"]["review"]["repair_rounds"], 1)
        self.assertTrue((artifact_uri / "review-round-2" / "coder-prompt.md").exists())
        reviewer_prompt = (
            artifact_uri / "review-round-2" / "reviewer-prompt.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## Current Coder Round", reviewer_prompt)
        self.assertIn('"round_type": "repair"', reviewer_prompt)
        self.assertIn("## Reviewer Feedback Being Addressed", reviewer_prompt)
        self.assertNotIn("## Coder Prompt For This Review Round", reviewer_prompt)
        usage_records = store.list_usage_records(workflow_id)
        self.assertEqual(
            [(record["role"], record["review_round"]) for record in usage_records],
            [("coder", 1), ("reviewer", 1), ("coder", 2), ("reviewer", 2)],
        )

    def test_stub_codex_step_review_rejection_fails_after_max_rounds(self) -> None:
        result, store, workflow_id, artifact_uri = self._run_stub_reviewed_codex_step(
            {
                "enabled": True,
                "max_review_rounds": 1,
                "stub_verdicts": ["CHANGES_REQUESTED"],
            }
        )

        self.assertEqual(result["status"], StepStatus.FAILED.value)
        self.assertIn("Codex review rejected final round", result["error"])
        self.assertEqual(result["output"]["contract_status"], "FAILED")
        self.assertEqual(result["output"]["review"]["final_verdict"], "CHANGES_REQUESTED")
        self.assertTrue((artifact_uri / "step-result.json").exists())
        stored = store.list_steps(workflow_id)[0]
        self.assertEqual(stored["status"], StepStatus.FAILED.value)
        self.assertIn("Codex review rejected final round", stored["error"])

    def test_reviewer_prompt_uses_compact_current_round_context(self) -> None:
        repair_context = _repair_context(
            review_round=2,
            previous_review_round=1,
            previous_coder_contract={
                "status": "SUCCEEDED",
                "summary": "initial edit done",
                "changed_files": ["target.txt"],
            },
            reviewer_feedback={
                "status": "SUCCEEDED",
                "verdict": "CHANGES_REQUESTED",
                "summary": "missing required validation",
                "required_fixes": ["add validation output"],
            },
        )
        review_history = [
            {
                "review_round": 1,
                "verdict": "CHANGES_REQUESTED",
                "coder_summary": "initial edit done",
                "reviewer_summary": "missing required validation",
                "findings": ["validation missing"],
                "required_fixes": ["add validation output"],
                "coder_contract": "/artifacts/review-round-1/coder-contract.json",
                "reviewer_contract": "/artifacts/review-round-1/reviewer-contract.json",
                "coder_prompt": "/artifacts/review-round-1/coder-prompt.md",
                "coder_response": "/artifacts/review-round-1/coder-final-response.md",
                "reviewer_prompt": "/artifacts/review-round-1/reviewer-prompt.md",
                "reviewer_response": "/artifacts/review-round-1/reviewer-final-response.md",
            }
        ]

        prompt = _build_reviewer_prompt(
            original_prompt="# Original Task\n\nDo the task.",
            review_round=2,
            coder_prompt_path=Path("/artifacts/review-round-2/coder-prompt.md"),
            repair_context=repair_context,
            coder_response='{"status":"SUCCEEDED"}',
            coder_contract={"status": "SUCCEEDED", "summary": "repair done"},
            workspace_path="/tmp/not-a-git-workspace",
            review_history=review_history,
        )

        self.assertIn("## Original Task Prompt", prompt)
        self.assertIn("## Current Coder Round", prompt)
        self.assertIn('"round_type": "repair"', prompt)
        self.assertIn(
            '"coder_prompt_artifact": "/artifacts/review-round-2/coder-prompt.md"',
            prompt,
        )
        self.assertIn("## Repair Context", prompt)
        self.assertIn("## Reviewer Feedback Being Addressed", prompt)
        self.assertIn("## Current Workspace Diff", prompt)
        self.assertIn("git diff --no-ext-diff --no-color -- .", prompt)
        self.assertIn("## Prior Review History", prompt)
        self.assertIn('"reviewer_summary": "missing required validation"', prompt)
        self.assertNotIn("## Coder Prompt For This Review Round", prompt)

        self.assertEqual(
            prompt,
            _build_reviewer_prompt(
                original_prompt="# Original Task\n\nDo the task.",
                review_round=2,
                coder_prompt_path=Path("/artifacts/review-round-2/coder-prompt.md"),
                repair_context=repair_context,
                coder_response='{"status":"SUCCEEDED"}',
                coder_contract={"status": "SUCCEEDED", "summary": "repair done"},
                workspace_path="/tmp/not-a-git-workspace",
                review_history=review_history,
            ),
        )

    def test_repair_prompt_uses_stable_repair_context(self) -> None:
        repair_context = _repair_context(
            review_round=2,
            previous_review_round=1,
            previous_coder_contract={"summary": "initial edit done", "status": "SUCCEEDED"},
            reviewer_feedback={
                "verdict": "CHANGES_REQUESTED",
                "required_fixes": ["add validation output"],
            },
        )
        review_history = [
            {
                "review_round": 1,
                "verdict": "CHANGES_REQUESTED",
                "coder_summary": "initial edit done",
                "reviewer_summary": "missing required validation",
                "findings": [],
                "required_fixes": ["add validation output"],
                "coder_contract": "/artifacts/review-round-1/coder-contract.json",
                "reviewer_contract": "/artifacts/review-round-1/reviewer-contract.json",
            }
        ]

        prompt = _build_repair_prompt(
            original_prompt="# Original Task\n\nDo the task.",
            review_history=review_history,
            repair_context=repair_context,
        )

        self.assertIn("# Codex Repair Round", prompt)
        self.assertIn("## Original Task Prompt", prompt)
        self.assertIn("## Current Coder Round", prompt)
        self.assertIn('"review_round": 2', prompt)
        self.assertIn("## Repair Context", prompt)
        self.assertIn("## Reviewer Feedback Being Addressed", prompt)
        self.assertIn("## Prior Review History", prompt)
        self.assertIn("## Output Instruction", prompt)
        self.assertNotIn("## Coder Prompt For This Review Round", prompt)

        self.assertEqual(
            prompt,
            _build_repair_prompt(
                original_prompt="# Original Task\n\nDo the task.",
                review_history=review_history,
                repair_context=repair_context,
            ),
        )

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
                    "feedback_retry_count": 0,
                }
            )
            return result, store, workflow_id, Path(result["artifact_uri"])
        finally:
            activities.config.CODEX_MODE = old_mode


if __name__ == "__main__":
    unittest.main()
