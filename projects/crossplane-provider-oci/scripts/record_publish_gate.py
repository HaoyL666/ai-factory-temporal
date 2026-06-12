#!/usr/bin/env python3
from __future__ import annotations

from _shared import context, read_makefile_version, redaction_note, target_version, write_json


ctx = context()
inputs = ctx["inputs"]
payload = {
    "workflow_id": ctx["workflow_id"],
    "step_id": ctx["step_id"],
    "approved": True,
    "gate": "publish",
    "target_version": target_version(inputs),
    "registry_destination": inputs.get("registry_destination", "approval-gate-not-configured"),
    "makefile": read_makefile_version(ctx["workspace_path"]),
    "required_evidence": [
        "approval record",
        "registry destination",
        "image or package references",
        "provenance or checksum data where available",
        "redacted logs",
    ],
    "redaction": redaction_note(),
    "note": "This local harness script records approval evidence only; it does not publish artifacts.",
}
write_json(ctx["artifact_dir"], "publish_gate.json", payload)
