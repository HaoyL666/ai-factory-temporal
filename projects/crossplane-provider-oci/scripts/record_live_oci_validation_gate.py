#!/usr/bin/env python3
from __future__ import annotations

from _shared import context, redaction_note, target_version, validation_level, write_json


ctx = context()
inputs = ctx["inputs"]
selected_resources = inputs.get("live_oci_resources") or []
payload = {
    "workflow_id": ctx["workflow_id"],
    "step_id": ctx["step_id"],
    "approved": True,
    "gate": "live_oci_validation",
    "target_version": target_version(inputs),
    "validation_level": validation_level(inputs),
    "selected_resources": selected_resources,
    "required_evidence": [
        "selected resource list",
        "CRUD/import/delete result",
        "log references",
        "cleanup status",
        "unresolved operational risks",
    ],
    "cleanup_required": True,
    "redaction": redaction_note(),
    "note": "This local harness script records approval evidence only; it does not call live OCI APIs.",
}
write_json(ctx["artifact_dir"], "live_oci_validation_gate.json", payload)
