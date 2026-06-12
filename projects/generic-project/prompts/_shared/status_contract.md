## Output Contract

Respond with exactly one JSON object and no surrounding Markdown:

```json
{
  "status": "SUCCEEDED",
  "summary": "Concise summary of the completed step.",
  "changed_files": [],
  "checks": [],
  "error": null
}
```

If you cannot complete the step, do not pretend it succeeded. Respond with:

```json
{
  "status": "FAILED",
  "summary": "What blocked the step.",
  "changed_files": [],
  "checks": [],
  "error": "Actionable failure reason"
}
```
