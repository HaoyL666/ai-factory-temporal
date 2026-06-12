# Terraform Upgrade Planning Step

{{include:prompts/_shared/runtime_context.md}}

## Task

Inspect the workspace and produce an upgrade plan for target version
`{{input.target_version}}`.

If code changes are needed, make minimal local edits inside the workspace. Focus
on Terraform/provider-version upgrade behavior, compatibility risks, validation
steps, and changed files.

{{include:prompts/_shared/status_contract.md}}
