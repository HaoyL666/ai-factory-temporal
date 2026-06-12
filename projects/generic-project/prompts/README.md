# Prompt Layout

Prompts are grouped by supported task:

```text
prompts/_shared/
  Reusable prompt sections such as runtime context and output contracts.

prompts/terraform_upgrade/
  Step prompts used by the upgrade workflow.

prompts/smoke_codex_edit/
  Step prompts used by Codex edit smoke workflows.

prompts/smoke_default_contract/
  Step prompts used to verify default Codex contract behavior.
```

Workflow YAML points each Codex step at the task-specific step prompt. Step
prompts can reuse shared fragments with:

```text
{{include:prompts/_shared/runtime_context.md}}
```
