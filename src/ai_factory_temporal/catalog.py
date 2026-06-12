from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ai_factory_temporal.prompting import validate_prompt_template


class CatalogError(ValueError):
    """Raised when a project pack or workflow definition cannot be found."""


class ProjectCatalog:
    def __init__(self, projects_dir: Path):
        self.projects_dir = projects_dir

    def project_dir(self, project_id: str) -> Path:
        path = self.projects_dir / project_id
        if not path.exists():
            raise CatalogError(f"Unknown project id: {project_id}")
        return path

    def load_project_pack(self, project_id: str) -> dict[str, Any]:
        project_dir = self.project_dir(project_id)
        return self._load_yaml(project_dir / "project_pack.yaml")

    def load_workflow(self, project_id: str, task_type: str) -> dict[str, Any]:
        project_dir = self.project_dir(project_id)
        path = project_dir / "workflows" / f"{task_type}.yaml"
        if not path.exists():
            raise CatalogError(f"Unknown task type for project {project_id}: {task_type}")
        workflow = self._load_yaml(path)
        self._validate_workflow(project_dir, project_id, task_type, workflow)
        return workflow

    def validate_task_supported(self, project_pack: dict[str, Any], project_id: str, task_type: str) -> None:
        supported_tasks = project_pack.get("supported_tasks")
        if supported_tasks is None:
            return
        if not isinstance(supported_tasks, list) or not all(
            isinstance(task, str) for task in supported_tasks
        ):
            raise CatalogError(f"Project {project_id} has invalid supported_tasks; expected a list of strings")
        if task_type not in supported_tasks:
            raise CatalogError(f"Task {task_type} is not supported by project {project_id}")

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise CatalogError(f"Missing catalog file: {path}")
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise CatalogError(f"Catalog file must contain a mapping: {path}")
        return data

    def _validate_workflow(
        self,
        project_dir: Path,
        project_id: str,
        task_type: str,
        workflow: dict[str, Any],
    ) -> None:
        workflow_id = workflow.get("id")
        if workflow_id is not None and not _is_non_empty_string(workflow_id):
            raise CatalogError(f"Workflow {project_id}/{task_type} id must be a non-empty string")

        steps = workflow.get("steps")
        if not isinstance(steps, list) or not steps:
            raise CatalogError(f"Workflow {project_id}/{task_type} has no steps")

        seen_step_ids: set[str] = set()
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise CatalogError(f"Workflow {project_id}/{task_type} step {index} must be a mapping")

            label = f"Workflow {project_id}/{task_type} step {index}"
            step_id = _required_string(step, "id", label)
            if step_id in seen_step_ids:
                raise CatalogError(f"{label} has duplicate step id: {step_id}")
            seen_step_ids.add(step_id)

            kind = _required_string(step, "kind", f"{label} ({step_id})")
            if kind == "deterministic":
                self._validate_script_step(step, f"{label} ({step_id})")
            elif kind == "codex":
                self._validate_codex_step(project_dir, step, f"{label} ({step_id})")
            elif kind == "approval":
                self._validate_approval_step(step, f"{label} ({step_id})")
            else:
                raise CatalogError(
                    f"{label} ({step_id}) has unsupported kind `{kind}`; "
                    "expected codex, deterministic, or approval"
                )

            _optional_positive_int(step, "timeout_seconds", f"{label} ({step_id})")
            _optional_non_negative_int(step, "max_feedback_retries", f"{label} ({step_id})")
            _optional_bool(step, "allow_skip", f"{label} ({step_id})")

    def _validate_script_step(self, step: dict[str, Any], label: str) -> None:
        runner = step.get("runner", "script")
        if runner != "script":
            raise CatalogError(f"{label} has unsupported deterministic runner `{runner}`; expected script")

        command = step.get("command")
        if not isinstance(command, list) or not command:
            raise CatalogError(f"{label} script step must define a non-empty command list")
        if not all(isinstance(part, str) and part for part in command):
            raise CatalogError(f"{label} command entries must be non-empty strings")

        cwd = step.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise CatalogError(f"{label} cwd must be a string")

    def _validate_codex_step(self, project_dir: Path, step: dict[str, Any], label: str) -> None:
        prompt = _required_string(step, "prompt", label)
        try:
            validate_prompt_template(project_dir=project_dir, prompt_path=prompt)
        except (OSError, ValueError) as exc:
            raise CatalogError(f"{label} has invalid prompt `{prompt}`: {exc}") from exc

        self._validate_codex_output_contract(step, label)

    def _validate_approval_step(self, step: dict[str, Any], label: str) -> None:
        message = step.get("message")
        if message is not None and not isinstance(message, str):
            raise CatalogError(f"{label} approval message must be a string")

        approved_step = step.get("approved_step")
        if approved_step is None:
            return
        if not isinstance(approved_step, dict):
            raise CatalogError(f"{label} approved_step must be a mapping")
        self._validate_script_step(approved_step, f"{label} approved_step")
        _optional_positive_int(approved_step, "timeout_seconds", f"{label} approved_step")

    @staticmethod
    def _validate_codex_output_contract(step: dict[str, Any], label: str) -> None:
        contract = step.get("output_contract", {})
        if contract is None or contract is True or contract == {}:
            return
        if contract is False:
            raise CatalogError(f"{label} Codex output_contract cannot be disabled")
        if isinstance(contract, str):
            contract = {"type": contract}
        elif isinstance(contract, dict):
            contract = {"type": "status_json", **contract}
        else:
            raise CatalogError(f"{label} output_contract must be a boolean, string, or mapping")

        contract_type = str(contract.get("type", "status_json"))
        if contract_type not in {"status_json", "json_status"}:
            raise CatalogError(f"{label} has unsupported Codex output_contract type `{contract_type}`")

        status_field = contract.get("status_field")
        if status_field is not None and not _is_non_empty_string(status_field):
            raise CatalogError(f"{label} output_contract status_field must be a non-empty string")

        for field_name in ("required_fields", "success_statuses", "failure_statuses"):
            values = contract.get(field_name)
            if values is None:
                continue
            if not isinstance(values, list) or not all(_is_non_empty_string(value) for value in values):
                raise CatalogError(f"{label} output_contract {field_name} must be a list of strings")


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _required_string(step: dict[str, Any], field: str, label: str) -> str:
    value = step.get(field)
    if not _is_non_empty_string(value):
        raise CatalogError(f"{label} must define a non-empty `{field}`")
    return value.strip()


def _optional_positive_int(step: dict[str, Any], field: str, label: str) -> None:
    value = step.get(field)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CatalogError(f"{label} {field} must be a positive integer")


def _optional_non_negative_int(step: dict[str, Any], field: str, label: str) -> None:
    value = step.get(field)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogError(f"{label} {field} must be a non-negative integer")


def _optional_bool(step: dict[str, Any], field: str, label: str) -> None:
    value = step.get(field)
    if value is not None and not isinstance(value, bool):
        raise CatalogError(f"{label} {field} must be a boolean")
