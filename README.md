# Local AI Factory With Temporal

Local AI Factory runtime for executing project-specific AI workflows with
Temporal, FastAPI, SQLite, Codex SDK activities, deterministic script steps,
approval gates, and feedback loops.

The runtime shape is:

```text
User / CLI / UI
  -> FastAPI service
      -> creates workflow + step rows in SQLite
      -> starts Temporal workflow
      -> returns workflow_id immediately

Temporal worker
  -> runs durable workflow loop
  -> executes Codex activities
  -> executes deterministic script activities
  -> waits for approval and feedback signals
  -> updates SQLite after each transition

SQLite app DB
  -> product/query state
  -> workflow rows
  -> step rows
  -> feedback and approval rows

var/artifacts
  -> prompts
  -> Codex responses
  -> script logs
  -> validation reports
```

Temporal owns durable execution. SQLite owns product-facing status and queryable
state.

## Install

```bash
cd ~/Desktop/ai-factory-temporal
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

After activation, `ai-factory-api`, `ai-factory-worker`, `pytest`, and the
Python dependencies all come from this project-local virtual environment.

## Start Local Temporal

Install the Temporal CLI if needed, then start the dev server:

```bash
temporal server start-dev
```

The default address used by this project is:

```text
127.0.0.1:7233
```

Override it with:

```bash
export AI_FACTORY_TEMPORAL_ADDRESS=127.0.0.1:7233
```

## Run Worker

In another terminal:

```bash
cd ~/Desktop/ai-factory-temporal
source .venv/bin/activate
ai-factory-worker
```

For local no-Codex smoke tests, run the worker in Codex stub mode:

```bash
source .venv/bin/activate
AI_FACTORY_CODEX_MODE=stub ai-factory-worker
```

For real Codex SDK mode, use the default:

```bash
source .venv/bin/activate
ai-factory-worker
```

Codex settings:

```bash
export AI_FACTORY_CODEX_MODEL=
export AI_FACTORY_CODEX_SANDBOX=workspace-write
export AI_FACTORY_CODEX_APPROVAL_MODE=auto_review
```

## Run API

In another terminal:

```bash
cd ~/Desktop/ai-factory-temporal
source .venv/bin/activate
ai-factory-api
```

Or:

```bash
source .venv/bin/activate
uvicorn ai_factory_temporal.api:create_app --factory --reload
```

## Submit A Workflow

```bash
curl -s -X POST http://127.0.0.1:8000/workflows \
  -H 'content-type: application/json' \
  -d '{
    "project_id": "generic-project",
    "task_type": "upgrade",
    "inputs": {"target_version": "1.2.3"},
    "workspace_path": "."
  }'
```

The API returns immediately:

```json
{
  "workflow_id": "wf-example",
  "status": "PENDING",
  "temporal_workflow_id": "wf-example"
}
```

Crossplane Provider OCI Terraform upgrade example:

```bash
curl -s -X POST http://127.0.0.1:8000/workflows \
  -H 'content-type: application/json' \
  -d '{
    "project_id": "crossplane-provider-oci",
    "task_type": "terraform_provider_upgrade",
    "inputs": {
      "target_version": "8.13.0",
      "validation_level": "L1_BUILD_GENERATE",
      "execution_mode": "plan_only"
    },
    "workspace_path": "/path/to/crossplane-provider-oci"
  }'
```

Check product state:

```bash
curl -s http://127.0.0.1:8000/workflows/wf-example
```

Check Temporal runtime state:

```bash
curl -s http://127.0.0.1:8000/workflows/wf-example/runtime
```

## Approval

When the workflow reaches the approval step:

```bash
curl -s -X POST http://127.0.0.1:8000/workflows/wf-example/approve \
  -H 'content-type: application/json' \
  -d '{
    "step_id": "publish_approval",
    "approved": true,
    "comment": "publish approved"
  }'
```

## Feedback Loop

If a step fails, the workflow moves to `WAITING_FOR_FEEDBACK`.

Retry:

```bash
curl -s -X POST http://127.0.0.1:8000/workflows/wf-example/feedback \
  -H 'content-type: application/json' \
  -d '{
    "step_id": "plan_upgrade",
    "action": "retry",
    "message": "Retry but only touch the version metadata file."
  }'
```

Skip, if the step allows skip:

```bash
curl -s -X POST http://127.0.0.1:8000/workflows/wf-example/feedback \
  -H 'content-type: application/json' \
  -d '{
    "step_id": "optional_step",
    "action": "skip",
    "message": "Skip this optional step."
  }'
```

Abort:

```bash
curl -s -X POST http://127.0.0.1:8000/workflows/wf-example/feedback \
  -H 'content-type: application/json' \
  -d '{
    "step_id": "plan_upgrade",
    "action": "abort",
    "message": "Stop the workflow."
  }'
```

## Failure Classes

The harness separates admission errors, business failures, and system failures.

Admission errors are static project/workflow definition problems caught by the
API before a workflow row or Temporal execution is created:

```text
unsupported task for the selected project
missing script command in workflow YAML
missing prompt template or prompt include
invalid Codex output_contract config
invalid step kind, timeout, feedback retry, or skip configuration
```

Admission errors return HTTP 400. Fix the project pack, workflow YAML, or prompt
files and submit the request again.

Business failures mean the step ran correctly but the task result failed:

```text
script exited non-zero
script timed out
Codex returned status FAILED/ERROR/NEEDS_FEEDBACK
Codex step timed out
Codex returned malformed contract output
```

Business failures move the workflow to `WAITING_FOR_FEEDBACK`, where the user can
retry, skip if allowed, or abort.

System failures mean the admitted workflow hit a harness, worker, dependency, or
runtime bug while executing:

```text
workflow code exception
activity code exception
installed dependency missing or broken
Codex SDK/runtime exception
```

System failures are allowed to throw into Temporal. Temporal keeps the execution
running while workflow-task and activity failures are retried and visible in
Temporal UI as Temporal attempts. This mirrors normal Temporal behavior: deploy
a code/config/dependency fix, then the workflow can continue without asking the
user for business feedback.

## Codex Output Contract

Every Codex step must return a structured status contract. This is the Codex
equivalent of a script exit code: the harness uses it to decide whether the step
succeeded or should pause for feedback.

The default contract requires a JSON object with a `status` field. A workflow can
customize the contract with `output_contract`, for example to require additional
fields:

```yaml
- id: plan_upgrade
  kind: codex
  prompt: prompts/terraform_upgrade/plan_upgrade.md
  output_contract:
    type: status_json
    required_fields:
      - summary
```

The Codex final response must contain a JSON object:

```json
{
  "status": "SUCCEEDED",
  "summary": "What changed.",
  "changed_files": ["relative/path"],
  "checks": ["What was verified"],
  "error": null
}
```

The harness treats these as success statuses:

```text
SUCCEEDED, SUCCESS, OK
```

The harness treats these as failed statuses and pauses the workflow at
`WAITING_FOR_FEEDBACK`:

```text
FAILED, FAILURE, ERROR, NEEDS_FEEDBACK
```

Malformed contract output also fails the step. This means Codex task-level
failure does not depend only on SDK exceptions. Codex timeouts and
contract-declared failures are business failures and move into the feedback path;
Codex SDK/runtime exceptions are system failures and surface through Temporal's
task/activity failure retry behavior.

## Deterministic Step Artifacts

Script steps receive a harness-created artifact directory:

```text
AI_FACTORY_ARTIFACT_DIR=var/artifacts/<workflow_id>/<step_id>/step-run-N
```

The script can write any files there. The harness always captures `stdout.log`
and `stderr.log`, scans the artifact directory, writes `artifact-manifest.json`,
and writes a normalized `step-result.json`.

If the script also writes `result.json`, the harness includes it as
`script_result` in the normalized step output. This is optional; simple scripts
can just exit `0` or nonzero and write whatever evidence files they have.

Later Codex steps receive previous step outputs with the artifact directory,
manifest path, and discovered artifact list, so prompts do not need to hardcode
every deterministic output file name.

## Codex Review Loop

Codex steps can opt into an internal coder/reviewer loop:

```yaml
- id: implement_change
  kind: codex
  prompt: prompts/task/implement_change.md
  review: true
```

Use `review: { enabled: true, max_review_rounds: 3 }` when a step needs a
custom repair cap. When review is enabled, one outer Temporal step can run
multiple internal review rounds:

```text
step-run-1/
  review-round-1/
    coder-prompt.md
    coder-final-response.md
    coder-contract.json
    reviewer-prompt.md
    reviewer-final-response.md
    reviewer-contract.json
  review-round-2/
    ...
  step-result.json
```

The coder still returns the normal Codex output contract. The reviewer runs in
read-only mode by default and returns:

```json
{
  "status": "SUCCEEDED",
  "verdict": "APPROVED",
  "summary": "Concise review summary.",
  "findings": [],
  "required_fixes": [],
  "error": null
}
```

`APPROVED` finishes the Codex step. `CHANGES_REQUESTED` starts another coder
repair round until `max_review_rounds` is reached. A final rejection or
blocked review makes the step fail and moves the workflow to the normal feedback
path. The DB stores one synthesized final step contract; the artifacts preserve
every coder and reviewer contract.

Reviewer prompts use one generic structure for every project and task: original
task prompt, current coder round metadata, optional repair context, coder final
response, parsed coder contract, current workspace diff, compact prior review
history, and the fixed review output contract. Full coder prompts are kept as
artifacts but are not embedded into reviewer prompts.

## E2E Smoke Workflows

The sample `generic-project` includes a few local smoke workflows:

```text
codex_edit_e2e
  Runs a real Codex SDK edit, then verifies the edited file with a script.

fail_once_retry_e2e
  Fails once, waits for feedback, then succeeds after a retry signal.

fail_once_skip_e2e
  Fails an optional step, waits for feedback, skips it, then continues.

codex_timeout_e2e
  Forces a Codex timeout and waits for feedback.

review_retry_chain_e2e
  Runs a reviewed Codex repair loop, then a failed Codex step with feedback retry.

approval_first
  Pauses immediately at an approval gate.
```

## Add Another Project

Create:

```text
projects/<project-id>/project_pack.yaml
projects/<project-id>/workflows/<task-type>.yaml
projects/<project-id>/prompts/_shared/*.md
projects/<project-id>/prompts/<task-type>/*.md
projects/<project-id>/scripts/*.py
```

The Temporal workflow does not know project-specific logic. It only reads the
project pack, workflow YAML, step kind, prompt, runner config, inputs, and
previous outputs.

The sample project uses this layout:

```text
projects/generic-project/
  project_pack.yaml
  workflows/
    upgrade.yaml
    codex_edit_e2e.yaml
    ...
  prompts/
    _shared/
      runtime_context.md
      status_contract.md
      edit_file_contract.md
    terraform_upgrade/
      plan_upgrade.md
      final_summary.md
    bug_fix/
      README.md
    smoke_codex_edit/
      codex_edit_file.md
  scripts/
    validate_workspace.py
    mock_publish.py
    ...
```

The `crossplane-provider-oci` project follows the same generated-file style:

```text
projects/crossplane-provider-oci/
  project_pack.yaml
  workflows/
    terraform_provider_upgrade.yaml
  prompts/
    _shared/
    terraform_provider_upgrade/
  scripts/
    collect_generation_evidence.py
    collect_build_package_evidence.py
    record_publish_gate.py
    record_install_validation_gate.py
    record_live_oci_validation_gate.py
```

Its Terraform provider upgrade workflow models a provider-owner review process:
AI-owned scoping/version edits/risk analysis, deterministic evidence collection,
approval-gated publish/install/live OCI validation, and final review packet
assembly. The included deterministic scripts are local-safe by default and write
evidence artifacts instead of publishing, mutating clusters, or calling live OCI
APIs.

Workflow YAML references task-specific prompts:

```yaml
steps:
  - id: plan_upgrade
    kind: codex
    prompt: prompts/terraform_upgrade/plan_upgrade.md
```

Step prompts can reuse shared template sections:

```text
{{include:prompts/_shared/runtime_context.md}}
{{include:prompts/_shared/status_contract.md}}
```

## Validate

These tests do not require a running Temporal server:

```bash
source .venv/bin/activate
python -m pytest -q
```
