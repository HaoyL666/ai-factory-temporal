# Bug Fix Prompts

Place bug-fix step prompts here when the project adds a `bug_fix` workflow.

Expected shape:

```text
prompts/bug_fix/
  diagnose.md
  implement_fix.md
  final_summary.md
```

Each prompt should include the shared runtime context and status contract:

```text
{{include:prompts/_shared/runtime_context.md}}
{{include:prompts/_shared/status_contract.md}}
```
