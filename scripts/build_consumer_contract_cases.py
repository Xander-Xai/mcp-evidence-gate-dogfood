#!/usr/bin/env python3
"""Build independent, digest-correct Core v2 consumer-contract attack fixtures."""
from __future__ import annotations
import argparse, copy, hashlib, json
from pathlib import Path

TRIVY = ["scanner_process", "scanner_output", "result_sections", "artifact_binding", "result_semantics"]
OSV = ["scanner_process", "scanner_output", "result_sections", "source_binding", "result_semantics"]
CORE = "771bd2871147fe56a6ea911546ee0ddcaca01e9e"
PROFILE = "registry-pr-1404@20747d3253ba8638161dd95f1cec70df02993c22"

def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("destination", type=Path); args = ap.parse_args()
    root = Path(__file__).parents[1]; artifact = root / "dist" / "example-artifact.bin"
    adigest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    specs = {
      "trivy-valid-complete": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 0, "clean", "complete", None),
      "narrowed-required-components": ("trivy", "0.74.0", "trivy-fs-json-v1", ["scanner_process"], 0, "clean", "complete", "scanner_execution_required_components_mismatch"),
      "omitted-required-component": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY[:-1], 0, "clean", "complete", "scanner_execution_required_components_mismatch"),
      "required-present-not-completed": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 0, "clean", "complete", "scanner_execution_contradictory"),
      "unknown-contract": ("trivy", "0.74.0", "attacker-defined-contract-v1", TRIVY, 0, "clean", "complete", "scanner_execution_contract_unsupported"),
      "wrong-contract-for-scanner": ("trivy", "0.74.0", "osv-scanner-v2-lockfile-json-v1", OSV, 0, "clean", "complete", "scanner_execution_contract_mismatch"),
      "evidence-scanner-name-mismatch": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 0, "clean", "complete", "scanner_identity_mismatch"),
      "missing-receipt-version": ("trivy", None, "trivy-fs-json-v1", TRIVY, 0, "clean", "complete", "scanner_version_missing"),
      "empty-receipt-version": ("trivy", "", "trivy-fs-json-v1", TRIVY, 0, "clean", "complete", "scanner_version_missing"),
      "missing-evidence-version": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 0, "clean", "complete", "scanner_version_missing"),
      "empty-evidence-version": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 0, "clean", "complete", "scanner_version_missing"),
      "version-mismatch": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 0, "clean", "complete", "scanner_version_mismatch"),
      "trivy-complete-exit-1": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 1, "clean", "complete", "scanner_execution_exit_code_invalid"),
      "trivy-complete-exit-999": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 999, "clean", "complete", "scanner_execution_exit_code_invalid"),
      "trivy-failed-exit-1": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 1, "clean", "failed", "scanner_execution_failed"),
      "trivy-failed-exit-999": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 999, "clean", "failed", "scanner_execution_failed"),
      "incomplete-illegal-exit": ("trivy", "0.74.0", "trivy-fs-json-v1", TRIVY, 999, "clean", "incomplete", "scanner_execution_incomplete"),
      "osv-valid-complete-exit-0": ("osv-scanner", "2.5.1", "osv-scanner-v2-lockfile-json-v1", OSV, 0, "clean", "complete", None),
      "osv-valid-complete-exit-1": ("osv-scanner", "2.5.1", "osv-scanner-v2-lockfile-json-v1", OSV, 1, "findings", "complete", None),
      "osv-failed-exit-2": ("osv-scanner", "2.5.1", "osv-scanner-v2-lockfile-json-v1", OSV, 2, "clean", "failed", "scanner_execution_failed"),
    }
    for name, (scanner, version, contract, components, exit_code, verdict, status, reason) in specs.items():
        evidence_scanner = {"name": scanner, "version": version}
        if name == "evidence-scanner-name-mismatch": evidence_scanner["name"] = "osv-scanner"
        if name == "missing-evidence-version": evidence_scanner.pop("version")
        if name == "empty-evidence-version": evidence_scanner["version"] = ""
        if name == "version-mismatch": evidence_scanner["version"] = "0.73.0"
        completed = list(components)
        if name == "required-present-not-completed": completed = completed[:-1]
        if status == "failed": completed = []
        execution = {"schema_version":"project-defined-scanner-execution-v1","scanner_contract":contract,"invocation_started":True,"process_completed":status != "failed","exit_code":exit_code,"exit_state_valid":True,"output_present":True,"output_exists":True,"output_size":1,"output_parseable":True,"required_components":list(components),"completed_components":completed,"failed_components":([] if status != "failed" else ["scanner_process"]),"completeness_status":status,"completeness_reason":"all_required_scanner_work_completed" if status=="complete" else "scanner_work_not_completed","required_work_completed":status=="complete","result_semantics_consistent":status=="complete"}
        evidence = {"scanner": evidence_scanner, "scanner_execution": execution, "raw_report": {"exists":True,"present":True,"size":1}}
        evidence_path = args.destination / name / "evidence.json"; write(evidence_path, evidence)
        receipt = {"scanner":scanner,"scanned_artifact_digest":f"sha256:{adigest}","scan_scope":["package"],"verdict":verdict,"scanned_at":"2026-09-17T00:00:00Z","attestation":"publisher-asserted","evidence_digest":f"sha256:{hashlib.sha256(evidence_path.read_bytes()).hexdigest()}"}
        if version is not None: receipt["scanner_version"] = version
        write(args.destination / name / "receipt.json", receipt)
        expected_status = "complete" if status == "complete" and reason is None else (status if status != "complete" else ("unverified" if reason == "scanner_version_missing" else ("contradictory" if reason == "scanner_execution_required_components_mismatch" else "malformed")))
        write(args.destination / name / "expected.json", {"case":name,"core_sha":CORE,"profile":PROFILE,"expected_pass":reason is None and status=="complete" and verdict=="clean","expected_scanner_status":expected_status,"expected_reason":reason})
    write(args.destination / "consumer-contract-adversarial-aggregate.json", {"schema_version":"dogfood-consumer-contract-adversarial-v1","core_candidate":CORE,"producer":"3b4862245ce1778d52d6a3b58f8b1b8cb4906dfb","registry_profile":PROFILE,"case_count":len(specs),"cases":sorted(specs)})

if __name__ == "__main__": main()
