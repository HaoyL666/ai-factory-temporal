# Apply Terraform Provider Version Bump

{{include:prompts/_shared/runtime_context.md}}

{{include:prompts/_shared/crossplane_upgrade_guidance.md}}

## Task

Make the smallest source-of-truth edit for the requested Terraform provider
version. This is the only normal source-editing AI step in the workflow.

Primary edit:

- update `TERRAFORM_PROVIDER_VERSION` in `Makefile`

Avoid hand-editing generated files. Provider binary names, download URLs,
schema generation, docs pulls, resolver output, builds, packages, and local
deploy targets should be handled by deterministic steps unless a source-of-truth
file genuinely requires a manual patch.

## Expected Evidence

Return changed files, old and new version values, and assumptions that must be
verified by generation.

{{include:prompts/_shared/status_contract.md}}
