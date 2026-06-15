# Local AI Factory With Temporal

Local AI Factory is a Temporal-backed workflow harness for running predefined
AI-assisted engineering workflows. A user submits a project task to the API, the
API creates a workflow instance, Temporal runs the durable execution loop, and
workers execute each step as either a Codex SDK activity, a deterministic script,
or a human approval gate.

The harness is project-agnostic. Each project contributes a project pack,
workflow YAML, prompts, and optional scripts. The runtime owns the common
execution behavior: isolated worktrees, step state, feedback loops, approval
signals, artifacts, Codex review loops, usage tracking, and durable progression
through Temporal.

## Architecture

```text
User / CLI / UI
  -> FastAPI service
      -> validates project pack and workflow YAML
      -> creates an isolated target-repo worktree
      -> creates workflow and step rows in SQLite
      -> starts a Temporal workflow
      -> returns workflow_id immediately

Temporal workflow
  -> runs the ordered step loop durably
  -> waits for approval and feedback signals
  -> asks activities to update product-facing SQLite state

Temporal worker
  -> executes Codex SDK activities
  -> executes deterministic script activities
  -> records step artifacts, usage, checkpoints, approvals, and feedback

SQLite app DB
  -> workflows
  -> workflow_steps
  -> workflow_feedback
  -> workflow_approvals
  -> workflow_usage

var/artifacts
  -> rendered prompts
  -> Codex responses and SDK metadata
  -> review-round contracts
  -> script stdout/stderr
  -> artifact manifests
  -> normalized step-result.json files
```

Temporal owns durable execution. SQLite owns product-facing query state.
Artifacts own the audit trail for prompts, outputs, scripts, and evidence.

## Install

```bash
cd ~/Desktop/ai-factory-temporal
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

After activation, `ai-factory-api`, `ai-factory-worker`, `pytest`, and the
Python dependencies come from this project-local virtual environment.

## Run Locally

Start Temporal:

```bash
temporal server start-dev
```

The default Temporal address is `127.0.0.1:7233`. Override it with:

```bash
export AI_FACTORY_TEMPORAL_ADDRESS=127.0.0.1:7233
```

Start a worker in another terminal:

```bash
cd ~/Desktop/ai-factory-temporal
source .venv/bin/activate
ai-factory-worker
```

For local no-Codex smoke tests, use stub mode:

```bash
AI_FACTORY_CODEX_MODE=stub ai-factory-worker
```

For real Codex SDK mode, the worker must run with normal user access to Codex
local state, usually `~/.codex`. If the worker is launched from a restricted
sandbox that can only read `~/.codex`, Codex SDK initialization can fail before
the model runs.

Start the API in another terminal:

```bash
cd ~/Desktop/ai-factory-temporal
source .venv/bin/activate
ai-factory-api
```

Or run Uvicorn directly:

```bash
uvicorn ai_factory_temporal.api:create_app --factory --reload
```

## Configuration

Common runtime settings:

```bash
export AI_FACTORY_TEMPORAL_ADDRESS=127.0.0.1:7233
export AI_FACTORY_TASK_QUEUE=ai-factory-local
export AI_FACTORY_DB=var/ai_factory.db
export AI_FACTORY_ARTIFACTS=var/artifacts
export AI_FACTORY_CODEX_MODE=real
export AI_FACTORY_CODEX_MODEL=
export AI_FACTORY_CODEX_SANDBOX=workspace-write
export AI_FACTORY_CODEX_APPROVAL_MODE=auto_review
```

Optional cost settings use prices per 1M tokens. If unset, the harness records
tokens and leaves estimated cost as `null`.

```bash
export AI_FACTORY_COST_CURRENCY=USD
export AI_FACTORY_INPUT_TOKEN_PRICE_PER_1M=
export AI_FACTORY_OUTPUT_TOKEN_PRICE_PER_1M=
export AI_FACTORY_CACHE_READ_TOKEN_PRICE_PER_1M=
export AI_FACTORY_CACHE_CREATION_TOKEN_PRICE_PER_1M=
```

## Submit A Workflow

Most requests should pass a target repository. The API creates an isolated Git
worktree and runs the workflow there.

```bash
curl -s -X POST http://127.0.0.1:8000/workflows \
  -H 'content-type: application/json' \
  -d '{
    "project_id": "generic-project",
    "task_type": "upgrade",
    "inputs": {"target_version": "1.2.3"},
    "target_repo_path": "/path/to/target-repo",
    "base_ref": "main"
  }'
```

Example response:

```json
{
  "workflow_id": "wf-example",
  "status": "PENDING",
  "temporal_workflow_id": "wf-example",
  "workspace": {
    "mode": "managed_worktree",
    "workspace_path": "/path/to/.ai-factory-worktrees/wf-example-generic-project",
    "branch": "ai-factory/wf-example/upgrade",
    "base_ref": "main"
  }
}
```

For local/manual testing, callers may pass an already prepared `workspace_path`
instead of `target_repo_path`. In that mode the API does not create a worktree.

Check product state:

```bash
curl -s http://127.0.0.1:8000/workflows/wf-example
```

Check Temporal runtime state:

```bash
curl -s http://127.0.0.1:8000/workflows/wf-example/runtime
```

Check Codex token and cost tracking:

```bash
curl -s http://127.0.0.1:8000/workflows/wf-example/usage
```

## Crossplane Provider OCI Example

```bash
curl -s -X POST http://127.0.0.1:8000/workflows \
  -H 'content-type: application/json' \
  -d '{
    "project_id": "crossplane-provider-oci",
    "task_type": "terraform_provider_upgrade",
    "inputs": {
      "target_version": "8.13.0",
      "execution_mode": "plan_only"
    },
    "target_repo_path": "/path/to/crossplane-provider-oci",
    "base_ref": "main"
  }'
```

`execution_mode: "plan_only"` keeps deterministic scripts local-safe: they write
evidence artifacts without publishing, mutating clusters, or calling live OCI
APIs. With `execution_mode: "execute"`, generation/build steps run configured
commands, and approval-gated publish/install/live validation steps run only
explicitly provided commands.

## Approval

When a workflow reaches an approval step, it moves to `WAITING_FOR_APPROVAL`.
Approve it with:

```bash
curl -s -X POST http://127.0.0.1:8000/workflows/wf-example/approve \
  -H 'content-type: application/json' \
  -d '{
    "step_id": "publish_approval",
    "approved": true,
    "comment": "publish approved"
  }'
```

If an approval step has an `approved_step`, the nested deterministic step runs
after approval. If approval is denied, the approval step is marked skipped.

## Feedback Loop

When a business failure occurs, the workflow moves to `WAITING_FOR_FEEDBACK`.

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

Retry and skip reset the workspace to the last successful checkpoint before
continuing. Abort leaves the failed workspace state available for inspection and
marks the workflow failed.

## Workflow Semantics

### Step Kinds

Workflows are sequential and predefined in YAML. Each step is one of:

```text
codex         -> run a Codex SDK turn, optionally with an internal review loop
deterministic -> run a configured script command
approval      -> wait for a user approval signal, then optionally run a script
```

### Checkpoints

Successful Codex and deterministic steps commit source changes in the managed
worktree as step checkpoints. Harness DB and artifact paths are excluded from
checkpoint commits.

On feedback:

```text
retry -> reset workspace to the last checkpoint, clean untracked source files, rerun the step
skip  -> reset workspace to the last checkpoint, mark the step skipped, continue
abort -> leave the failed workspace as-is, mark the workflow failed
```

### Failure Classes

Admission errors are caught by the API before a DB row or Temporal workflow is
created. Examples: unsupported task, invalid workflow YAML, missing prompt, or
invalid step config.

Business failures mean the step ran correctly but the task result failed.
Examples: script nonzero exit, script timeout, Codex timeout, Codex malformed
contract, or Codex contract status `FAILED`. These move to
`WAITING_FOR_FEEDBACK`.

System failures are worker, dependency, harness, or runtime failures. Examples:
activity code exception, missing dependency, or Codex SDK initialization failure.
These throw into Temporal and are retried/visible as Temporal failures. Fix the
code or worker environment and let Temporal continue.

## Codex Output Contract

Every Codex step returns a structured status contract. This is the Codex
equivalent of a script exit code.

```json
{
  "status": "SUCCEEDED",
  "summary": "What changed.",
  "changed_files": ["relative/path"],
  "checks": ["What was verified"],
  "error": null
}
```

Success statuses:

```text
SUCCEEDED, SUCCESS, OK
```

Failure statuses:

```text
FAILED, FAILURE, ERROR, NEEDS_FEEDBACK
```

Workflow YAML can require additional fields:

```yaml
- id: plan_upgrade
  kind: codex
  prompt: prompts/terraform_upgrade/plan_upgrade.md
  output_contract:
    type: status_json
    required_fields:
      - summary
      - changed_files
```

## Codex Review Loop

Codex steps can opt into an internal coder/reviewer loop:

```yaml
- id: implement_change
  kind: codex
  prompt: prompts/task/implement_change.md
  review:
    enabled: true
    max_review_rounds: 3
```

When review is enabled, one Temporal step can run multiple internal review
rounds:

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

The coder returns the normal Codex output contract. The reviewer runs read-only
by default and returns:

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
repair round until `max_review_rounds` is reached. A final rejection fails the
step and moves the workflow to the normal feedback path.

## Deterministic Step Artifacts

Script steps receive these environment variables:

```text
AI_FACTORY_WORKFLOW_ID
AI_FACTORY_STEP_ID
AI_FACTORY_ARTIFACT_DIR
AI_FACTORY_INPUTS_JSON
AI_FACTORY_WORKSPACE_PATH
AI_FACTORY_FEEDBACK_RETRY_COUNT
AI_FACTORY_RETRY_FEEDBACK_JSON
```

The harness captures:

```text
stdout.log
stderr.log
artifact-manifest.json
step-result.json
```

If a script writes `result.json`, the harness includes it as `script_result` in
the normalized output. Later Codex steps receive previous step outputs,
including artifact directories, manifest paths, and discovered artifact lists.

## Token And Cost Tracking

Every Codex turn records one `workflow_usage` row with:

```text
workflow id
step id
step run number
role: coder or reviewer
review round
mode
model
thread id / turn id
raw SDK usage
normalized token counts
optional estimated cost
```

Cost tracking is observational only. The harness does not enforce budgets or
stop workflows based on cost.

## Project Layout

Add a project by creating:

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

The sample generic project:

```text
projects/generic-project/
  project_pack.yaml
  workflows/
    upgrade.yaml
    codex_edit_e2e.yaml
    review_retry_chain_e2e.yaml
    ...
  prompts/
    _shared/
      runtime_context.md
      status_contract.md
      edit_file_contract.md
    terraform_upgrade/
      plan_upgrade.md
      final_summary.md
    smoke_codex_edit/
      codex_edit_file.md
  scripts/
    validate_workspace.py
    mock_publish.py
    ...
```

The Crossplane Provider OCI project follows the same generated-file style:

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

Step prompts can reuse shared template sections:

```text
{{include:prompts/_shared/runtime_context.md}}
{{include:prompts/_shared/status_contract.md}}
```

## E2E Smoke Workflows

The sample `generic-project` includes local smoke workflows:

```text
codex_edit_e2e
  Runs a real Codex SDK edit, then verifies the edited file with a script.

codex_fail_retry_e2e
  Forces a Codex business failure, waits for feedback, retries, then verifies.

fail_once_retry_e2e
  Fails a script once, waits for feedback, then succeeds after retry.

fail_once_skip_e2e
  Fails an optional script step, skips it, then continues.

review_retry_chain_e2e
  Runs a reviewed Codex repair loop, then a failed reviewed Codex step with retry.

review_exhaust_abort_e2e
  Exhausts review rounds, waits for feedback, then supports abort.

approval_first
  Pauses immediately at an approval gate.
```

## Validate

These tests do not require a running Temporal server:

```bash
source .venv/bin/activate
python -m pytest -q
```

For live validation, start Temporal, the worker, and the API, then submit one of
the smoke workflows above.

## Future Work

These are intentionally not part of the current minimal runtime, but they are
the next design areas to harden before using this as a broader AI Factory
runtime.

### Failed Workflow Orchestrator

Add a coordinator process that scans recent failed workflows, reads artifacts and
Temporal failure history, classifies whether the failure is retryable, and
suggests or launches a new workflow attempt with a refined plan. This should be
separate from the main workflow loop so normal step execution stays simple.

### Idempotent Activity Side Effects

Make Codex and script activities safer under Temporal activity retries by using
operation ids, resumable artifact writes, explicit side-effect checkpoints, and
clear replay/duplicate handling. The goal is for a worker crash or activity
retry to avoid duplicate commits, duplicate usage rows, or inconsistent
artifacts.

### Durable Loop Memory Layer

Add a durable run-memory model that stores compact decisions, observations,
failure analysis, review outcomes, and retry rationale. This would give later
steps and future workflow attempts a cleaner context source than passing full
previous outputs forever.

### Workflow Event Timeline

Add a first-class `workflow_events` table for product-facing timeline queries:
step started, Codex turn started, review verdict, approval requested, feedback
received, retry reset, checkpoint committed, and workflow completed/failed.
Temporal remains the execution source of truth; this table would support UI and
analytics.

### Generator Layer

Add a project/task graph generator that can traverse project definitions and
supported tasks to produce project packs, workflows, prompts, and script
contracts. For now these files are hand-authored.

### Worktree Lifecycle And Promotion

Add commands or API endpoints to list managed worktrees, clean abandoned
worktrees, promote a successful workflow branch, and optionally open a PR in the
target repository.

### Policy, Budget, And Governance

Extend token/cost tracking into optional budget policy, approval policy,
project-level safety rules, and audit exports. The current implementation only
records usage.
