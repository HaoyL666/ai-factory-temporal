from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ARTIFACT_MANIFEST_FILENAME = "artifact-manifest.json"
STEP_RESULT_FILENAME = "step-result.json"
SCRIPT_RESULT_FILENAME = "result.json"


def build_artifact_manifest(
    artifact_dir: Path,
    *,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    """Build a generic index for files produced by a step.

    The manifest is intentionally semantic-light: it tells later steps what
    exists and where to read it, without requiring project-specific schemas.
    """
    excluded = set(exclude or set())
    files: list[dict[str, Any]] = []
    if not artifact_dir.exists():
        return {"artifact_dir": str(artifact_dir), "files": files}

    for path in sorted(artifact_dir.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue

        relative_path = path.relative_to(artifact_dir).as_posix()
        if relative_path in excluded:
            continue

        try:
            files.append(_artifact_file_entry(artifact_dir, path, relative_path))
        except OSError as exc:
            files.append(
                {
                    "path": relative_path,
                    "kind": "unreadable",
                    "error": str(exc),
                }
            )

    return {
        "artifact_dir": str(artifact_dir),
        "files": files,
    }


def load_optional_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(parsed, dict):
        return None, "expected a JSON object"
    return parsed, None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact_file_entry(
    artifact_dir: Path,
    path: Path,
    relative_path: str,
) -> dict[str, Any]:
    if path.is_symlink():
        return {
            "path": relative_path,
            "kind": "symlink",
            "target": os.readlink(path),
        }

    stat = path.stat()
    return {
        "path": relative_path,
        "absolute_path": str(path),
        "kind": _artifact_kind(path),
        "size_bytes": stat.st_size,
        "sha256": _sha256(path),
    }


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".diff", ".patch"}:
        return "diff"
    if suffix in {".log", ".out", ".err"}:
        return "log"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".txt", ".text"}:
        return "text"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".xml":
        return "xml"
    if suffix == ".html":
        return "html"
    return "file"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
