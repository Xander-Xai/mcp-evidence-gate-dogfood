#!/usr/bin/env python3
"""Dogfood consumer policy for Producer scanner-execution evidence.

This is a project-defined policy layer.  It does not extend or reinterpret the
MCP Registry SecurityScanReceipt v1 schema and it does not replace Core Gate
verification.  It prevents a producer-specific incomplete execution from
becoming a downstream PASS candidate, even when the immutable Core verifier
only sees a digest-bound evidence file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


SCANNER_EXECUTION_SCHEMA = "project-defined-scanner-execution-v1"
VALID_STATUSES = {"complete", "incomplete", "failed"}
PROFILE = "registry-pr-1404@20747d3253ba8638161dd95f1cec70df02993c22"


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_complete(execution: Mapping[str, Any]) -> tuple[bool, str]:
    if execution.get("schema_version") != SCANNER_EXECUTION_SCHEMA:
        return False, "scanner_execution_schema_invalid"
    status = execution.get("completeness_status")
    if status not in VALID_STATUSES:
        return False, "scanner_execution_status_invalid"

    required = execution.get("required_components")
    completed = execution.get("completed_components")
    failed = execution.get("failed_components")
    if not all(isinstance(value, list) for value in (required, completed, failed)) or not required:
        return False, "scanner_execution_components_invalid"

    if status != "complete":
        return False, f"scanner_execution_{status}"

    required_flags = (
        "invocation_started",
        "process_completed",
        "exit_state_valid",
        "output_present",
        "output_parseable",
        "required_work_completed",
        "result_semantics_consistent",
    )
    if any(execution.get(flag) is not True for flag in required_flags):
        return False, "scanner_execution_complete_claim_not_proven"
    if type(execution.get("exit_code")) is not int:
        return False, "scanner_execution_exit_code_invalid"
    if execution.get("output_exists") is not True:
        return False, "scanner_execution_output_missing"
    if not isinstance(execution.get("output_size"), int) or execution["output_size"] <= 0:
        return False, "scanner_execution_output_empty"
    if failed or any(component not in completed for component in required):
        return False, "scanner_execution_required_component_incomplete"
    return True, "all_required_scanner_work_completed"


def evaluate(receipt_path: Path, evidence_path: Path, artifact_path: Path) -> dict[str, Any]:
    receipt = _load_json(receipt_path)
    evidence = _load_json(evidence_path)
    artifact_digest = f"sha256:{_sha256(artifact_path)}"
    evidence_digest = f"sha256:{_sha256(evidence_path)}"

    reasons: list[str] = []
    integrity = "pass"
    if not isinstance(receipt, dict) or not isinstance(evidence, dict):
        return {
            "schema_version": "dogfood-scanner-completeness-policy-v1",
            "profile": PROFILE,
            "integrity": "invalid",
            "admission": "blocked",
            "reason": "receipt_or_evidence_not_object",
            "receipt_verdict": "unknown",
            "scanner_execution": {"status": "invalid", "reason": "receipt_or_evidence_not_object"},
        }

    if receipt.get("scanned_artifact_digest") != artifact_digest:
        integrity = "inconclusive"
        reasons.append("artifact_digest_mismatch")
    artifact_record = evidence.get("artifact")
    if not isinstance(artifact_record, Mapping):
        integrity = "inconclusive"
        reasons.append("evidence_artifact_metadata_invalid")
    elif artifact_record.get("sha256") != artifact_digest.removeprefix("sha256:"):
        integrity = "inconclusive"
        reasons.append("evidence_artifact_digest_mismatch")
    if receipt.get("evidence_digest") != evidence_digest:
        integrity = "inconclusive"
        reasons.append("evidence_digest_mismatch")

    execution = evidence.get("scanner_execution")
    if not isinstance(execution, Mapping):
        execution_status = "invalid"
        execution_reason = "scanner_execution_missing"
        complete = False
    else:
        execution_status = execution.get("completeness_status", "invalid")
        complete, execution_reason = _is_complete(execution)
        if execution_status not in VALID_STATUSES:
            execution_status = "invalid"

    verdict = receipt.get("verdict", "unknown")
    if not complete:
        reasons.append(execution_reason)
    admission = "eligible_for_gate" if integrity == "pass" and complete else "blocked"
    return {
        "schema_version": "dogfood-scanner-completeness-policy-v1",
        "profile": PROFILE,
        "integrity": integrity,
        "admission": admission,
        "reason": ";".join(dict.fromkeys(reasons)) or "completeness_and_bindings_verified",
        "receipt_verdict": verdict,
        "scanner_execution": {"status": execution_status, "reason": execution_reason},
        "artifact_digest": artifact_digest,
        "evidence_digest": evidence_digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.receipt, args.evidence, args.artifact)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({
            "schema_version": "dogfood-scanner-completeness-policy-v1",
            "integrity": "invalid",
            "admission": "blocked",
            "reason": f"policy_input_error:{error}",
        }, indent=2, sort_keys=True))
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["admission"] == "eligible_for_gate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
