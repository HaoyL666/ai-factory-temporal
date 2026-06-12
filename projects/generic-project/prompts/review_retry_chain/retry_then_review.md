# Codex Business Failure Then Reviewed Retry E2E Step

{{include:prompts/_shared/runtime_context.md}}

## Required Final State

Open the workspace file named `{{input.retry_review_file_name}}`.

The file must contain this exact line:

```text
{{input.retry_review_marker}}
```

Do not remove existing content. Do not edit unrelated files.

## Coder Step Run Behavior

This step intentionally tests the outer human feedback loop before the internal
review loop.

If `Step run` is `1`:

- Do not edit any files.
- Return exactly one JSON object with `"status": "FAILED"`.
- Use this exact error string: `Intentional reviewed retry business failure`.

If `Step run` is greater than `1`:

- Append `{{input.retry_review_marker}}` if it is not already present.
- Return a `SUCCEEDED` contract.

## Reviewer Policy

When reviewing this step, approve only when `{{input.retry_review_marker}}` is
present in the current workspace diff or file content. Otherwise return
`CHANGES_REQUESTED`.

{{include:prompts/_shared/status_contract.md}}
