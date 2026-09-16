#!/usr/bin/env python3
"""Materialize deterministic scanner-completeness cases from a Producer fixture."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


PROFILE = "registry-pr-1404@20747d3253ba8638161dd95f1cec70df02993c22"
COMPONENTS = [
    "scanner_process",
    "scanner_output",
    "result_sections",
    "artifact_binding",
    "result_semantics",
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _complete_execution() -> dict[str, Any]:
    return {
        "completed_components": COMPONENTS,
        "completeness_reason": "all_required_scanner_work_completed",
        "completeness_status": "complete",
        "exit_code": 0,
        "exit_state_valid": True,
        "failed_components": [],
        "invocation_started": True,
        "output_exists": True,
        "output_parseable": True,
        "output_present": True,
        "output_size": 245,
        "process_completed": True,
        "required_components": COMPONENTS,
        "required_work_completed": True,
        "result_semantics_consistent": True,
        "scanner_contract": "trivy-fs-json-v1",
        "schema_version": "project-defined-scanner-execution-v1",
    }


def _receipt(base: dict[str, Any], evidence: Path) -> dict[str, Any]:
    value = copy.deepcopy(base)
    value["evidence_digest"] = f"sha256:{_digest(evidence)}"
    value["policy_profile"] = PROFILE
    return value


def _materialize_case(
    destination: Path,
    base_receipt: dict[str, Any],
    base_evidence: dict[str, Any],
    execution: dict[str, Any] | None,
    *,
    verdict: str = "clean",
    inconclusive_reason: str | None = None,
    raw_bytes: bytes,
    tamper_after_binding: bool = False,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    evidence = copy.deepcopy(base_evidence)
    evidence["raw_report"]["sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    evidence["raw_report"]["size"] = len(raw_bytes)
    if execution is None:
        evidence.pop("scanner_execution", None)
    else:
        evidence["scanner_execution"] = execution
    if isinstance(evidence.get("scanner_execution"), dict):
        evidence["scanner_execution"]["output_size"] = len(raw_bytes)
    evidence_path = destination / "evidence.json"
    _write_json(evidence_path, evidence)
    receipt = _receipt(base_receipt, evidence_path)
    receipt["verdict"] = verdict
    if verdict == "inconclusive":
        receipt["inconclusive_reason"] = inconclusive_reason or "evidence_unavailable"
    receipt_path = destination / "receipt.json"
    _write_json(receipt_path, receipt)
    if tamper_after_binding:
        evidence["scanner_execution"]["completeness_status"] = "incomplete"
        _write_json(evidence_path, evidence)
    (destination / "trivy.raw.json").write_bytes(raw_bytes)


def build_cases(destination: Path) -> None:
    source = Path(__file__).parents[1] / "evidence" / "scanner-completeness" / "zero-findings-incomplete"
    base_receipt = json.loads((source / "receipt.json").read_text(encoding="utf-8"))
    base_evidence = json.loads((source / "evidence.json").read_text(encoding="utf-8"))
    raw_bytes = (source / "trivy.raw.json").read_bytes()
    _materialize_case(destination / "complete-clean", base_receipt, base_evidence, _complete_execution(), raw_bytes=raw_bytes)
    findings_raw = json.loads(raw_bytes.decode("utf-8"))
    findings_raw["Results"][0]["Vulnerabilities"] = [{"VulnerabilityID": "CVE-DOGFOOD"}]
    findings_bytes = json.dumps(findings_raw, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    _materialize_case(destination / "complete-findings", base_receipt, base_evidence, _complete_execution(), verdict="findings", raw_bytes=findings_bytes)
    empty_results_raw = copy.deepcopy(json.loads(raw_bytes.decode("utf-8")))
    empty_results_raw["Results"] = []
    empty_results_bytes = json.dumps(empty_results_raw, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    empty_results_execution = {
        **copy.deepcopy(base_evidence["scanner_execution"]),
        "completed_components": [],
        "completeness_reason": "trivy_result_sections_missing",
        "completeness_status": "incomplete",
        "failed_components": ["result_sections"],
        "required_work_completed": False,
        "result_semantics_consistent": False,
    }
    _materialize_case(
        destination / "producer-results-empty",
        base_receipt,
        base_evidence,
        empty_results_execution,
        verdict="inconclusive",
        inconclusive_reason="evidence_unavailable",
        raw_bytes=empty_results_bytes,
    )
    _materialize_case(destination / "incomplete-zero-findings", base_receipt, base_evidence, {
        **copy.deepcopy(base_evidence["scanner_execution"]),
        "completeness_status": "incomplete",
        "completeness_reason": "required_scanner_work_not_completed",
        "process_completed": True,
        "exit_code": 0,
        "exit_state_valid": True,
        "failed_components": ["result_sections"],
        "completed_components": ["scanner_process", "scanner_output"],
        "required_work_completed": False,
        "result_semantics_consistent": False,
    }, raw_bytes=raw_bytes)
    _materialize_case(destination / "failed-zero-findings", base_receipt, base_evidence, copy.deepcopy(base_evidence["scanner_execution"]), raw_bytes=raw_bytes)
    _materialize_case(destination / "missing-completeness", base_receipt, base_evidence, None, raw_bytes=raw_bytes)
    _materialize_case(destination / "malformed-completeness", base_receipt, base_evidence, {
        **_complete_execution(),
        "completeness_status": "not-a-valid-status",
    }, raw_bytes=raw_bytes)
    contradictory = _complete_execution()
    contradictory["process_completed"] = False
    _materialize_case(destination / "contradictory-complete", base_receipt, base_evidence, contradictory, raw_bytes=raw_bytes)
    _materialize_case(destination / "tampered-evidence", base_receipt, base_evidence, _complete_execution(), raw_bytes=raw_bytes, tamper_after_binding=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build_cases(args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
