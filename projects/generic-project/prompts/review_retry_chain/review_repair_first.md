# Reviewed Codex Repair E2E Step

{{include:prompts/_shared/runtime_context.md}}

## Required Final State

Open the workspace file named `{{input.review_file_name}}`.

The file must contain both of these exact lines:

```text
{{input.review_initial_marker}}
{{input.review_repair_marker}}
```

Do not remove existing content. Do not edit unrelated files.

## Coder Round Behavior

This step intentionally tests the internal Codex review loop.

If this prompt does not contain a `## Reviewer Feedback` section:

- Append only this line if it is not already present:

```text
{{input.review_initial_marker}}
```

- Do not append `{{input.review_repair_marker}}` yet.
- Return a `SUCCEEDED` contract so the reviewer has to catch the missing final marker.

If this prompt contains a `## Reviewer Feedback` section:

- Append this line if it is not already present:

```text
{{input.review_repair_marker}}
```

- Return a `SUCCEEDED` contract.

## Reviewer Policy

When reviewing this step, reject the coder result with `CHANGES_REQUESTED` if
the current workspace diff or file content does not show `{{input.review_repair_marker}}`.
Approve only when both required marker lines are present.

{{include:prompts/_shared/status_contract.md}}
