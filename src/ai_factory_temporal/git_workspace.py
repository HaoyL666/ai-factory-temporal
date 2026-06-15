from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


CHECKPOINT_GIT_AUTHOR_NAME = "AI Factory"
CHECKPOINT_GIT_AUTHOR_EMAIL = "ai-factory@example.invalid"


def create_isolated_worktree(
    *,
    target_repo_path: str | Path,
    workflow_id: str,
    project_id: str,
    task_type: str,
    base_ref: str | None = None,
    worktrees_dir: str | Path | None = None,
) -> dict[str, Any]:
    target_repo = Path(target_repo_path).resolve()
    target_root_result = _git(target_repo, ["rev-parse", "--show-toplevel"], check=False)
    if target_root_result.returncode != 0:
        raise ValueError(f"target_repo_path is not a Git repository: {target_repo}")

    target_root = Path(target_root_result.stdout.strip()).resolve()
    selected_base_ref = base_ref or "HEAD"
    base_commit = _git(
        target_root,
        ["rev-parse", "--verify", f"{selected_base_ref}^{{commit}}"],
        check=True,
    ).stdout.strip()
    branch = f"ai-factory/{workflow_id}/{_safe_ref_segment(task_type)}"
    parent_dir = (
        Path(worktrees_dir).resolve()
        if worktrees_dir is not None
        else target_root.parent / ".ai-factory-worktrees"
    )
    workspace_path = parent_dir / f"{workflow_id}-{_safe_path_segment(project_id)}"

    if workspace_path.exists():
        raise ValueError(f"worktree path already exists: {workspace_path}")

    parent_dir.mkdir(parents=True, exist_ok=True)
    try:
        _git(
            target_root,
            [
                "worktree",
                "add",
                "-b",
                branch,
                str(workspace_path),
                base_commit,
            ],
            check=True,
        )
    except Exception:
        _best_effort_git(target_root, ["worktree", "remove", "--force", str(workspace_path)])
        _best_effort_git(target_root, ["branch", "-D", branch])
        raise

    return {
        "mode": "managed_worktree",
        "target_repo_path": str(target_repo),
        "target_repo_root": str(target_root),
        "workspace_path": str(workspace_path),
        "base_ref": selected_base_ref,
        "base_commit": base_commit,
        "branch": branch,
    }


def remove_isolated_worktree(worktree: dict[str, Any]) -> None:
    workspace_path = worktree.get("workspace_path")
    target_repo_root = worktree.get("target_repo_root")
    branch = worktree.get("branch")
    if workspace_path and target_repo_root:
        _best_effort_git(
            Path(target_repo_root),
            ["worktree", "remove", "--force", str(workspace_path)],
        )
    if branch and target_repo_root:
        _best_effort_git(Path(target_repo_root), ["branch", "-D", str(branch)])


def workspace_checkpoint(workspace_path: str | Path) -> dict[str, Any]:
    workspace = Path(workspace_path)
    git_check = _git(
        workspace,
        ["rev-parse", "--is-inside-work-tree"],
        check=False,
    )
    if git_check.returncode != 0 or git_check.stdout.strip() != "true":
        return {
            "enabled": False,
            "status": "not_git",
            "commit": None,
            "reason": "workspace is not a Git worktree",
        }

    head = _git(workspace, ["rev-parse", "HEAD"], check=False)
    if head.returncode != 0:
        return {
            "enabled": False,
            "status": "no_head",
            "commit": None,
            "reason": head.stderr.strip() or "workspace has no HEAD commit",
        }

    branch = _git(workspace, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    return {
        "enabled": True,
        "status": "captured",
        "commit": head.stdout.strip(),
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
    }


def commit_step_checkpoint(
    *,
    workspace_path: str | Path,
    workflow_id: str,
    step_id: str,
    step_run_number: int,
    runner: str,
    artifact_uri: str | None,
    exclude_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_path)
    before = workspace_checkpoint(workspace)
    if not before.get("enabled"):
        return before

    status_before = _git(workspace, ["status", "--porcelain=v1"], check=True).stdout
    if not status_before.strip():
        return {
            **before,
            "status": "no_changes",
            "changed": False,
            "previous_commit": before.get("commit"),
        }

    _git(workspace, ["add", "-A"], check=True)
    _unstage_excluded_paths(workspace, exclude_paths or [])

    staged = _git(workspace, ["diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        return {
            **before,
            "status": "no_source_changes",
            "changed": False,
            "previous_commit": before.get("commit"),
        }
    if staged.returncode not in {0, 1}:
        raise RuntimeError(staged.stderr.strip() or "failed to inspect staged Git diff")

    message = _checkpoint_message(
        workflow_id=workflow_id,
        step_id=step_id,
        step_run_number=step_run_number,
        runner=runner,
        artifact_uri=artifact_uri,
    )
    _git(
        workspace,
        [
            "-c",
            f"user.name={CHECKPOINT_GIT_AUTHOR_NAME}",
            "-c",
            f"user.email={CHECKPOINT_GIT_AUTHOR_EMAIL}",
            "commit",
            "--no-verify",
            "-m",
            message,
        ],
        check=True,
    )
    after = workspace_checkpoint(workspace)
    return {
        **after,
        "status": "committed",
        "changed": True,
        "previous_commit": before.get("commit"),
        "message": message,
    }


def reset_workspace_to_checkpoint(
    *,
    workspace_path: str | Path,
    checkpoint_commit: str | None,
    preserve_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_path)
    current = workspace_checkpoint(workspace)
    if not current.get("enabled"):
        return current

    target_commit = checkpoint_commit or current.get("commit")
    if not target_commit:
        return {
            **current,
            "status": "no_checkpoint",
            "reason": "no checkpoint commit is available",
        }

    _git(workspace, ["reset", "--hard", str(target_commit)], check=True)
    clean_args = ["clean", "-fd"]
    clean_args.extend(_clean_excludes(workspace, preserve_paths or []))
    _git(workspace, clean_args, check=True)
    after = workspace_checkpoint(workspace)
    return {
        **after,
        "status": "reset",
        "target_commit": str(target_commit),
        "previous_commit": current.get("commit"),
    }


def _checkpoint_message(
    *,
    workflow_id: str,
    step_id: str,
    step_run_number: int,
    runner: str,
    artifact_uri: str | None,
) -> str:
    lines = [
        f"ai-factory: {workflow_id} {step_id}",
        "",
        f"Workflow: {workflow_id}",
        f"Step: {step_id}",
        f"Step Run: {step_run_number}",
        f"Runner: {runner}",
    ]
    if artifact_uri:
        lines.append(f"Artifacts: {artifact_uri}")
    return "\n".join(lines)


def _unstage_excluded_paths(workspace: Path, paths: list[str | Path]) -> None:
    for path in _paths_inside_workspace(workspace, paths):
        _git(workspace, ["reset", "--quiet", "--", path], check=False)
        for suffix in ("-journal", "-wal", "-shm"):
            _git(workspace, ["reset", "--quiet", "--", f"{path}{suffix}"], check=False)


def _clean_excludes(workspace: Path, paths: list[str | Path]) -> list[str]:
    args: list[str] = []
    for path in _paths_inside_workspace(workspace, paths):
        args.extend(["-e", path])
        args.extend(["-e", f"{path}/"])
        args.extend(["-e", f"{path}/**"])
        for suffix in ("-journal", "-wal", "-shm"):
            args.extend(["-e", f"{path}{suffix}"])
    return args


def _paths_inside_workspace(workspace: Path, paths: list[str | Path]) -> list[str]:
    workspace_resolved = workspace.resolve()
    relative_paths: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(workspace_resolved)
        except (OSError, ValueError):
            continue
        if str(relative) in {"", "."}:
            continue
        relative_paths.append(relative.as_posix())
    return relative_paths


def _safe_path_segment(value: str) -> str:
    return _safe_segment(value).strip("-") or "project"


def _safe_ref_segment(value: str) -> str:
    return _safe_segment(value).strip(".-/") or "task"


def _safe_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)


def _best_effort_git(workspace: Path, args: list[str]) -> None:
    try:
        _git(workspace, args, check=False)
    except OSError:
        return


def _git(workspace: Path, args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RuntimeError(message)
    return completed
