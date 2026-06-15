from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


WORKSPACE_DIFF_COMMAND = ("git", "diff", "--no-ext-diff", "--no-color", "--", ".")
WORKSPACE_DIFF_MAX_BYTES = 20000
WORKSPACE_DIFF_ERROR_MAX_BYTES = 4000


def stable_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def repair_context(
    *,
    review_round: int,
    previous_review_round: int,
    previous_coder_contract: dict[str, Any],
    reviewer_feedback: dict[str, Any],
) -> dict[str, Any]:
    return {
        "review_round": review_round,
        "previous_review_round": previous_review_round,
        "previous_coder_contract": previous_coder_contract,
        "reviewer_feedback": reviewer_feedback,
    }


def build_reviewer_prompt(
    *,
    original_prompt: str,
    review_round: int,
    coder_prompt_path: Path,
    repair_context: dict[str, Any] | None,
    coder_response: str,
    coder_contract: dict[str, Any],
    workspace_path: str,
    review_history: list[dict[str, Any]],
) -> str:
    round_context = {
        "review_round": review_round,
        "round_type": "repair" if repair_context else "initial",
        "coder_prompt_artifact": str(coder_prompt_path),
    }
    repair_section = reviewer_repair_context_section(repair_context)
    return f"""# Codex Review Step

You are the read-only reviewer for a Codex coding step. Review the coder result,
current workspace diff, and prior review history. Do not edit files.

## Original Task Prompt

{original_prompt}

## Current Coder Round

```json
{stable_json(round_context)}
```

{repair_section}

## Coder Final Response

{coder_response}

## Coder Contract

```json
{stable_json(coder_contract)}
```

## Current Workspace Diff

Generated from the workspace root with `git diff --no-ext-diff --no-color -- .`.
If the workspace is not a Git repository, this section is empty.

```diff
{workspace_diff(Path(workspace_path))}
```

## Prior Review History

```json
{stable_json(review_history_for_prompt(review_history))}
```

## Review Contract

Respond with exactly one JSON object and no surrounding Markdown:

```json
{{
  "status": "SUCCEEDED",
  "verdict": "APPROVED",
  "summary": "Concise review summary.",
  "findings": [],
  "required_fixes": [],
  "error": null
}}
```

Use `"verdict": "CHANGES_REQUESTED"` when the coder must repair the result.
Use `"verdict": "BLOCKED"` when review cannot be completed safely.
"""


def build_repair_prompt(
    *,
    original_prompt: str,
    review_history: list[dict[str, Any]],
    repair_context: dict[str, Any],
) -> str:
    round_context = {
        "review_round": repair_context["review_round"],
        "round_type": "repair",
        "previous_review_round": repair_context["previous_review_round"],
    }
    return f"""# Codex Repair Round

You are the coder for a reviewed Codex step. Continue the original task by
addressing the reviewer feedback. Keep the patch scoped and do not redo
unrelated work.

## Original Task Prompt

{original_prompt}

## Current Coder Round

```json
{stable_json(round_context)}
```

## Repair Context

### Previous Coder Contract

```json
{stable_json(repair_context["previous_coder_contract"])}
```

### Reviewer Feedback Being Addressed

```json
{stable_json(repair_context["reviewer_feedback"])}
```

## Prior Review History

```json
{stable_json(review_history_for_prompt(review_history))}
```

## Output Instruction

Return the same coder output contract required by the original task.
"""


def reviewer_repair_context_section(repair_context: dict[str, Any] | None) -> str:
    if repair_context is None:
        return """## Repair Context

No previous reviewer feedback exists for this coder round."""

    return f"""## Repair Context

### Previous Coder Contract

```json
{stable_json(repair_context["previous_coder_contract"])}
```

### Reviewer Feedback Being Addressed

```json
{stable_json(repair_context["reviewer_feedback"])}
```"""


def review_history_for_prompt(review_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "review_round": entry["review_round"],
            "verdict": entry.get("verdict"),
            "coder_summary": entry.get("coder_summary"),
            "reviewer_summary": entry.get("reviewer_summary"),
            "findings": entry.get("findings") or [],
            "required_fixes": entry.get("required_fixes") or [],
            "coder_contract": entry["coder_contract"],
            "reviewer_contract": entry["reviewer_contract"],
        }
        for entry in review_history
    ]


def workspace_diff(workspace_path: Path) -> str:
    if not (workspace_path / ".git").exists():
        return ""
    try:
        completed = subprocess.run(
            list(WORKSPACE_DIFF_COMMAND),
            cwd=workspace_path,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return completed.stderr[:WORKSPACE_DIFF_ERROR_MAX_BYTES]
    return completed.stdout[:WORKSPACE_DIFF_MAX_BYTES]
