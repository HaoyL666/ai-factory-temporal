#!/usr/bin/env python3
from __future__ import annotations

from _shared import context, redaction_note, target_version, validation_level, write_json


ctx = context()
inputs = ctx["inputs"]
payload = {
    "workflow_id": ctx["workflow_id"],
    "step_id": ctx["step_id"],
    "approved": True,
    "gate": "install_validation",
    "target_version": target_version(inputs),
    "validation_level": validation_level(inputs),
    "cluster_class": inputs.get("cluster_class", "local-or-oke-not-configured"),
    "package_reference": inputs.get("package_reference", "approval-gate-not-configured"),
    "checks_to_collect": [
        "Provider installed",
        "ProviderRevision healthy",
        "controller deployment available",
        "package pull secret path validated",
        "ProviderConfig path validated",
    ],
    "redaction": redaction_note(),
    "note": "This local harness script records approval evidence only; it does not mutate a cluster.",
}
write_json(ctx["artifact_dir"], "install_validation_gate.json", payload)
