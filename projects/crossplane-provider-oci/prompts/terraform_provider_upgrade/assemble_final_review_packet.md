# Assemble Final Review Packet

{{include:prompts/_shared/runtime_context.md}}

{{include:prompts/_shared/crossplane_upgrade_guidance.md}}

## Task

Assemble the final provider-owner review packet from the workspace and previous
step outputs. Do not make additional code changes.

The packet should include:

- current and target Terraform provider versions
- source-of-truth diff summary
- generated diff summary
- breaking-change report
- custom config/controller impact report
- generation diff evidence
- build, test, package, publish, install, and live OCI gate outcomes
- redaction status
- rollback notes
- final recommendation: approve, reject, or block with diagnostics

## Expected Evidence

Return concise review evidence that a provider owner can inspect without reading
raw agent logs.

{{include:prompts/_shared/status_contract.md}}
