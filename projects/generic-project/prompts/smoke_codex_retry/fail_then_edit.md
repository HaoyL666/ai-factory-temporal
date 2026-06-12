# Codex Retry Smoke

{{include:prompts/_shared/runtime_context.md}}

## Required Behavior

This step intentionally tests the workflow feedback loop.

If `Attempt` is `1`:

- Do not edit any files.
- Return exactly one JSON object with `"status": "FAILED"`.
- Use this exact error string: `Intentional Codex retry smoke failure`.

If `Attempt` is greater than `1`:

- Open the workspace file named `{{input.retry_file_name}}`.
- Append this exact line if it is not already present:

```text
{{input.retry_success_marker}}
```

- Do not edit unrelated files.
- Return exactly one JSON object with `"status": "SUCCEEDED"`.

{{include:prompts/_shared/status_contract.md}}
