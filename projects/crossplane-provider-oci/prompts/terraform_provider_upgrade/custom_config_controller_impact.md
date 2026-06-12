# Check Custom Config and Controller Impact

{{include:prompts/_shared/runtime_context.md}}

{{include:prompts/_shared/crossplane_upgrade_guidance.md}}

## Task

Inspect whether generation success is enough or whether custom provider logic
needs manual follow-up.

Focus on:

- `config/cluster/`
- `config/namespaced/`
- `config/groups.go`
- `config/provider.go`
- `internal/controller/`
- `internal/apis/`
- service-specific customizations touched by generated changes
- custom references, external-name rules, hooks, auth, and overrides

Do not edit files in this step.

## Expected Evidence

Return a custom impact report that separates:

- still valid custom behavior
- required patches
- service-owner input
- blocked or approval-required areas

{{include:prompts/_shared/status_contract.md}}
