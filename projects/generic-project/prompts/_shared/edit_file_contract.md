## Output Contract

Respond with exactly one JSON object and no surrounding Markdown:

```json
{
  "status": "SUCCEEDED",
  "summary": "Concise summary of what changed, including CODEX_EDIT_TEST_DONE.",
  "changed_files": ["{{input.file_name}}"],
  "checks": ["How you verified the edit"],
  "error": null
}
```

If you cannot complete the edit, do not pretend it succeeded. Respond with:

```json
{
  "status": "FAILED",
  "summary": "What blocked the edit.",
  "changed_files": [],
  "checks": [],
  "error": "Actionable failure reason"
}
```
