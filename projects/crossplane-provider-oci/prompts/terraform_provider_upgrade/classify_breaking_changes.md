# Classify Breaking Changes

{{include:prompts/_shared/runtime_context.md}}

{{include:prompts/_shared/crossplane_upgrade_guidance.md}}

## Task

Review generated diffs and deterministic evidence from previous steps. Identify
customer-facing risk without treating all generated churn as a breaking change.

Classify meaningful movement in:

- generated API or CRD schemas
- resource additions, removals, and field changes
- example drift
- external-name, import, delete, and reference behavior
- auth or provider configuration behavior

Do not edit files in this step.

## Expected Evidence

Return a breaking-change report with severity, affected service/resource,
owner action, compatibility notes, and whether the workflow should escalate to
install or live OCI validation.

{{include:prompts/_shared/status_contract.md}}
