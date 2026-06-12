from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def render_prompt(
    *,
    project_dir: Path,
    workflow_id: str,
    project_id: str,
    task_type: str,
    workspace_path: str,
    step: dict[str, Any],
    inputs: dict[str, Any],
    project_pack: dict[str, Any],
    previous_outputs: list[dict[str, Any]],
    retry_feedback: dict[str, Any] | None = None,
    step_run_number: int = 1,
) -> str:
    prompt_path = step.get("prompt")
    if prompt_path:
        template_path = project_dir / prompt_path
        template = _load_prompt_template(
            project_dir=project_dir,
            template_path=template_path,
            seen=set(),
        )
    else:
        template = "# Task\n\nExecute step `{{step_id}}`.\n"

    replacements = {
        "workflow_id": workflow_id,
        "project_id": project_id,
        "task_type": task_type,
        "step_id": step["id"],
        "step_name": step.get("name", step["id"]),
        "workspace_path": workspace_path,
        "step_run": str(step_run_number),
        "step_run_number": str(step_run_number),
        "inputs_json": json.dumps(inputs, indent=2, sort_keys=True),
        "project_pack_json": json.dumps(project_pack, indent=2, sort_keys=True),
        "previous_outputs_json": json.dumps(previous_outputs, indent=2, sort_keys=True),
        "retry_feedback_json": json.dumps(retry_feedback or {}, indent=2, sort_keys=True),
    }

    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{{" + key + "}}", value)

    for key, value in inputs.items():
        rendered = rendered.replace("{{input." + key + "}}", str(value))

    return rendered


def validate_prompt_template(*, project_dir: Path, prompt_path: str) -> None:
    _load_prompt_template(
        project_dir=project_dir,
        template_path=project_dir / prompt_path,
        seen=set(),
    )


def _load_prompt_template(
    *,
    project_dir: Path,
    template_path: Path,
    seen: set[Path],
) -> str:
    resolved = template_path.resolve()
    project_root = project_dir.resolve()
    if project_root not in resolved.parents and resolved != project_root:
        raise ValueError(f"Prompt include escapes project directory: {template_path}")
    if resolved in seen:
        raise ValueError(f"Prompt include cycle detected: {template_path}")

    seen.add(resolved)
    template = resolved.read_text(encoding="utf-8")

    def replace_include(match: re.Match[str]) -> str:
        include_ref = match.group(1).strip()
        include_path = Path(include_ref)
        if include_path.is_absolute():
            target = include_path
        elif include_ref.startswith("."):
            target = resolved.parent / include_path
        else:
            target = project_dir / include_path
        return _load_prompt_template(
            project_dir=project_dir,
            template_path=target,
            seen=seen.copy(),
        )

    return re.sub(r"\{\{include:([^}]+)\}\}", replace_include, template)
