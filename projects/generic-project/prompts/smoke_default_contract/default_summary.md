# Default Contract Smoke Step

{{include:prompts/_shared/runtime_context.md}}

## Task

Return a concise smoke-test summary. This prompt is used by a Codex step whose
workflow YAML does not define `output_contract`, so the runtime default contract
must still parse the response.

{{include:prompts/_shared/status_contract.md}}
