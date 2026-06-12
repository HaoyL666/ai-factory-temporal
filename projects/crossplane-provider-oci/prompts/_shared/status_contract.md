## Output Contract

Respond with exactly one JSON object and no surrounding Markdown.

For success:

```json
{
  "status": "SUCCEEDED",
  "summary": "Concise summary of the completed step.",
  "changed_files": [],
  "checks": [],
  "evidence": [],
  "risks": [],
  "error": null
}
```

For a blocked or failed step, use `FAILED` and include an actionable error:

```json
{
  "status": "FAILED",
  "summary": "What blocked the step.",
  "changed_files": [],
  "checks": [],
  "evidence": [],
  "risks": [],
  "error": "Actionable failure reason"
}
```

Do not mark the step `SUCCEEDED` if required repository evidence is missing,
the target version is unclear, validation cannot be interpreted, or approval is
needed before continuing.
