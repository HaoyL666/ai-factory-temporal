# Codex Review Exhaustion E2E Step

{{include:prompts/_shared/runtime_context.md}}

## Required Final State

Open the workspace file named `{{input.exhaust_file_name}}`.

The reviewer may approve only when the file contains this exact required marker:

```text
{{input.exhaust_required_marker}}
```

## Coder Behavior

This step intentionally tests review exhaustion and the outer feedback gate.

- Append only this incomplete marker if it is not already present:

```text
{{input.exhaust_incomplete_marker}}
```

- Do not append `{{input.exhaust_required_marker}}`.
- Do not edit unrelated files.
- Return a `SUCCEEDED` coder contract after writing the incomplete marker.

## Reviewer Policy

Return `CHANGES_REQUESTED` if `{{input.exhaust_required_marker}}` is missing
from the current workspace diff or file content. Approve only when the required
marker is present.

{{include:prompts/_shared/status_contract.md}}
