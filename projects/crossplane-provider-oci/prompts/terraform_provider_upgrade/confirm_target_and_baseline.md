# Confirm Target and Baseline

{{include:prompts/_shared/runtime_context.md}}

{{include:prompts/_shared/crossplane_upgrade_guidance.md}}

## Task

Turn the user request into a concrete Terraform provider upgrade manifest.

Inspect the repository and identify:

- target Terraform provider version from inputs
- current `TERRAFORM_PROVIDER_VERSION` value in `Makefile`
- requested execution mode, defaulting to `plan_only`
- protected actions that must stay behind approval gates
- evidence destinations and artifact expectations
- likely generated surfaces that may move

Do not edit files in this step. This step is read-only scoping and baseline
confirmation.

## Expected Evidence

Include the source-of-truth files found, current version, requested target
version, execution mode, and initial risk classification.

{{include:prompts/_shared/status_contract.md}}
