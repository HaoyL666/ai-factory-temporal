# Codex Edit Smoke Step

{{include:prompts/_shared/runtime_context.md}}

## Required Edit

Open the file named `{{input.file_name}}` in the workspace.

Replace the token:

```text
{{input.old_text}}
```

with:

```text
{{input.new_text}}
```

Do not change unrelated files.

{{include:prompts/_shared/edit_file_contract.md}}
