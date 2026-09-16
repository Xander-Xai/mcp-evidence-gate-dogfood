#!/usr/bin/env python3
"""Derive self-consistent and tampered cases from a real Producer run."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _failed_execution(value: dict[str, Any]) -> dict[str, Any]:
    execution = copy.deepcopy(value)
    execution.update({
        "completed_components": ["scanner_output", "result_sections", "artifact_binding", "result_semantics"],
        "completeness_reason": "scanner_process_failed_after_zero_findings",
        "completeness_status": "failed",
        "exit_code": None,
        "exit_state_valid": False,
        "failed_components": ["scanner_process"],
        "process_completed": False,
        "required_work_completed": False,
    })
    return execution


def _copy_common(source: Path, destination: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    destination.mkdir(parents=True, exist_ok=True)
    receipt = _load(source / "receipt.json")
    evidence = _load(source / "evidence.json")
    raw_source = source / "trivy.raw.json"
    raw_destination = destination / raw_source.name
    shutil.copy2(raw_source, raw_destination)
    return receipt, evidence, raw_destination


def derive(producer_out: Path, destination: Path) -> None:
    base_receipt = _load(producer_out / "receipt.json")
    base_evidence = _load(producer_out / "evidence.json")
    raw = _load(producer_out / "trivy.raw.json")
    findings = sum(len(row.get("Vulnerabilities") or []) for row in raw.get("Results", []))
    if base_receipt.get("verdict") != "clean" or findings != 0:
        raise ValueError("producer output must be a clean zero-finding report")
    execution = base_evidence.get("scanner_execution")
    if not isinstance(execution, dict) or execution.get("completeness_status") != "complete":
        raise ValueError("producer output must contain complete scanner_execution evidence")

    false_dir = destination / "false-clean"
    false_receipt, false_evidence, _ = _copy_common(producer_out, false_dir)
    false_evidence["scanner_execution"] = _failed_execution(execution)
    false_evidence_path = false_dir / "evidence.json"
    _write(false_evidence_path, false_evidence)
    false_receipt["evidence_digest"] = f"sha256:{_digest(false_evidence_path)}"
    _write(false_dir / "receipt.json", false_receipt)

    tampered_dir = destination / "tampered"
    tampered_receipt, tampered_evidence, _ = _copy_common(producer_out, tampered_dir)
    tampered_evidence["scanner_execution"] = _failed_execution(execution)
    _write(tampered_dir / "evidence.json", tampered_evidence)
    # Keep the original receipt evidence_digest deliberately: this is the
    # content-binding tamper case, not a self-consistent semantic mutation.
    _write(tampered_dir / "receipt.json", tampered_receipt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-out", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    derive(args.producer_out, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
