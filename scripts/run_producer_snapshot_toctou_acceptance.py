"""External Dogfood acceptance for Producer artifact snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess as host_subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace


PROFILE = "registry-pr-1404@20747d3253ba8638161dd95f1cec70df02993c22"
PRODUCER_CANDIDATE = "3462989a9f52a14e680e0c63b402beb84649f922"
PRODUCER_MAIN = "12dc756ddda7789081d58854b9b2c45587457513"
CORE_MAIN = "8c6d19b9ad90d6f066fa6fd1f41d0a8da3b017f3"
DOGFOOD_BASE = "655a707737631d2b9b84f9c5d0a6ab26756bd5eb"
HOST_RUN = host_subprocess.run


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def trivy_report(target: Path) -> str:
    return json.dumps({
        "Trivy": {"Version": "0.74.0"},
        "ArtifactName": str(target.resolve()),
        "Results": [{"Target": target.name, "Class": "lang-pkgs", "Type": "python", "Vulnerabilities": []}],
    })


def osv_report(target: Path) -> str:
    return json.dumps({
        "results": [{"source": {"path": str(target.resolve()), "type": "lockfile"},
                      "packages": [{"package": {"name": "requests", "version": "2.31.0", "ecosystem": "PyPI"},
                                     "vulnerabilities": []}]}],
    })


def core_verify(core: Path, receipt: Path, artifact: Path, evidence: Path, out: Path) -> dict:
    command = ["node", str(core / "dist/cli.js"), "verify", "--receipt", str(receipt),
               "--artifact", str(artifact), "--evidence", str(evidence),
               "--policy", "strict-scanner-completeness", "--format", "json"]
    proc = HOST_RUN(command, capture_output=True, text=True)
    out.write_text(proc.stdout, encoding="utf-8")
    result = json.loads(proc.stdout)
    result["cli_exit_code"] = proc.returncode
    return result


def assert_identity(evidence: dict, receipt: dict, subject: Path, original_digest: str, size: int, target: Path) -> None:
    assert receipt["scanned_artifact_ref"] == str(subject)
    assert evidence["artifact"]["ref"] == str(subject)
    assert evidence["scan_input"]["source_ref"] == str(subject)
    assert receipt["scanned_artifact_ref"] != str(target)
    assert receipt["scanned_artifact_digest"] == "sha256:" + original_digest
    assert evidence["artifact"]["sha256"] == original_digest
    assert evidence["scan_input"]["sha256"] == original_digest
    assert evidence["artifact"]["size"] == size
    assert evidence["scan_input"]["size"] == size
    assert evidence["invocation"]["argv"][-1] == str(target)
    assert str(target) != str(subject)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--core", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.producer))
    from src import osv_producer, producer

    artifact_a = b"A\n"
    artifact_b = b"B-replaced\n"
    manifest = {"producer_candidate": PRODUCER_CANDIDATE, "producer_main": PRODUCER_MAIN,
                "core_promoted_main": CORE_MAIN, "dogfood_base": DOGFOOD_BASE, "registry_profile": PROFILE}

    with tempfile.TemporaryDirectory(prefix="dogfood-b2-") as temp:
        root = Path(temp)
        binary = root / "trivy"
        binary.write_bytes(b"fake-trivy")
        producer.PINNED_TRIVY_SHA256[sys.platform] = sha_bytes(binary.read_bytes())
        producer.trivy_version = lambda _: "0.74.0"
        subject = root / "artifact.txt"
        subject.write_bytes(artifact_a)
        target_meta = {}
        original_run = producer.subprocess.run

        def trivy_attack(argv, **_kwargs):
            target = Path(argv[-1])
            target_meta.update({"original_subject_path": str(subject), "scanner_argv": argv,
                                "scanner_target_path": str(target), "scanner_target_is_original": target == subject,
                                "scanner_target_bytes_sha256": sha_bytes(target.read_bytes())})
            subject.write_bytes(artifact_b)
            target_meta["scanner_target_bytes_sha256"] = sha_bytes(target.read_bytes())
            Path(argv[argv.index("--output") + 1]).write_text(trivy_report(target), encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="")

        producer.subprocess.run = trivy_attack
        trivy_out = args.out / "trivy-a-to-b"
        assert producer.run(str(binary), subject, trivy_out) == 0
        trivy_receipt, trivy_evidence = load(trivy_out / "receipt.json"), load(trivy_out / "evidence.json")
        assert_identity(trivy_evidence, trivy_receipt, subject, sha_bytes(artifact_a), len(artifact_a), Path(trivy_evidence["invocation"]["argv"][-1]))
        assert subject.read_bytes() == artifact_b
        assert trivy_evidence["scanner_execution"]["completeness_status"] == "complete"
        manifest["trivy_a_to_b"] = {**target_meta, "receipt_digest": trivy_receipt["scanned_artifact_digest"],
                                     "evidence_artifact_digest": trivy_evidence["artifact"]["sha256"],
                                     "scan_input_digest": trivy_evidence["scan_input"]["sha256"],
                                     "subject_after": "B"}

        subject.write_bytes(artifact_a)
        if args.core:
            core_a = core_verify(args.core, trivy_out / "receipt.json", subject, trivy_out / "evidence.json", args.out / "core-on-A.json")
            subject.write_bytes(artifact_b)
            core_b = core_verify(args.core, trivy_out / "receipt.json", subject, trivy_out / "evidence.json", args.out / "core-on-B.json")
            assert core_a["decision"] == "pass"
            assert core_b["decision"] != "pass"
            manifest["core_on_A"] = core_a
            manifest["core_on_B"] = core_b

        osv_binary = root / "osv-scanner"
        osv_binary.write_bytes(b"fake-osv")
        osv_producer._platform_key = lambda: "linux"
        osv_producer.PINNED_OSV_SHA256["linux"] = sha_bytes(osv_binary.read_bytes())
        osv_producer.osv_version = lambda _: "2.5.1"
        subject.write_bytes(artifact_a)
        osv_meta = {}
        original_osv_run = osv_producer.subprocess.run

        def osv_attack(argv, **_kwargs):
            target = Path(argv[-1])
            osv_meta.update({"original_subject_path": str(subject), "scanner_argv": argv,
                             "scanner_target_path": str(target), "scanner_target_is_original": target == subject,
                             "scanner_target_bytes_sha256": sha_bytes(target.read_bytes())})
            subject.write_bytes(artifact_b)
            osv_meta["scanner_target_bytes_sha256"] = sha_bytes(target.read_bytes())
            return SimpleNamespace(returncode=0, stdout=osv_report(target), stderr="")

        osv_producer.subprocess.run = osv_attack
        osv_out = args.out / "osv-a-to-b"
        assert osv_producer.run(str(osv_binary), subject, osv_out) == 0
        osv_receipt, osv_evidence = load(osv_out / "receipt.json"), load(osv_out / "evidence.json")
        assert_identity(osv_evidence, osv_receipt, subject, sha_bytes(artifact_a), len(artifact_a), Path(osv_evidence["invocation"]["argv"][-1]))
        assert osv_evidence["scanner_execution"]["completeness_status"] == "complete"
        manifest["osv_a_to_b"] = {**osv_meta, "receipt_digest": osv_receipt["scanned_artifact_digest"],
                                   "evidence_artifact_digest": osv_evidence["artifact"]["sha256"],
                                   "scan_input_digest": osv_evidence["scan_input"]["sha256"]}

        # Snapshot drift: scanner returns a valid report, then mutates its own target.
        subject.write_bytes(artifact_a)
        def drift(argv, **_kwargs):
            target = Path(argv[-1])
            Path(argv[argv.index("--output") + 1]).write_text(trivy_report(target), encoding="utf-8")
            target.write_bytes(b"DRIFT")
            return SimpleNamespace(returncode=0, stderr="")
        producer.subprocess.run = drift
        drift_out = args.out / "snapshot-drift"
        assert producer.run(str(binary), subject, drift_out) == 1
        drift_evidence = load(drift_out / "evidence.json")
        assert drift_evidence["scanner_execution"]["completeness_reason"] == "artifact_snapshot_changed"
        assert "artifact_binding" in drift_evidence["scanner_execution"]["failed_components"]
        assert drift_evidence["scanner_execution"]["invocation_started"] is True
        assert drift_evidence["scanner_execution"]["process_completed"] is True
        manifest["snapshot_drift"] = drift_evidence["scanner_execution"]

        # Missing source must not fabricate an empty digest receipt.
        missing_out = args.out / "snapshot-failure"
        assert producer.run(str(binary), root / "missing.txt", missing_out) == 1
        assert not (missing_out / "receipt.json").exists()
        manifest["snapshot_failure"] = {"receipt_present": False}

        # Concurrent Trivy invocations use distinct ContextVar metadata.
        barrier = threading.Barrier(2)
        concurrent_results = {}
        concurrent_errors = {}
        def concurrent_run(argv, **_kwargs):
            target = Path(argv[-1])
            barrier.wait(timeout=10)
            Path(argv[argv.index("--output") + 1]).write_text(trivy_report(target), encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="")
        producer.subprocess.run = concurrent_run
        sources = [(root / "concurrent-a.txt", b"AAAA"), (root / "concurrent-b.txt", b"BBBBBBBB")]
        for path, data in sources: path.write_bytes(data)
        def invoke(name, path):
            try:
                code = producer.run(str(binary), path, args.out / name)
                concurrent_results[name] = (code, load(args.out / name / "evidence.json"))
            except BaseException as error:
                concurrent_errors[name] = repr(error)
        threads = [threading.Thread(target=invoke, args=(f"concurrent-{i}", p)) for i, (p, _) in enumerate(sources)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=20)
        assert all(not thread.is_alive() for thread in threads)
        assert not concurrent_errors, concurrent_errors
        assert set(concurrent_results) == {"concurrent-0", "concurrent-1"}
        assert all(code == 0 for code, _ in concurrent_results.values())
        for name, (_, evidence) in concurrent_results.items():
            expected = sources[int(name.rsplit("-", 1)[1])][1]
            assert evidence["artifact"]["sha256"] == sha_bytes(expected)
            assert evidence["artifact"]["size"] == len(expected)
        manifest["concurrency"] = {name: {"digest": evidence["artifact"]["sha256"], "size": evidence["artifact"]["size"]}
                                    for name, (_, evidence) in concurrent_results.items()}

        producer.subprocess.run = original_run
        osv_producer.subprocess.run = original_osv_run

    manifest["cleanup"] = "EVENTUAL"
    manifest["real_trivy"] = "executed by workflow"
    manifest["real_osv"] = "executed by workflow"
    manifest["oci"] = "candidate non-regression job"
    manifest["multi_receipt"] = "candidate composition job"
    manifest["aggregate_acceptance"] = "PASS"
    write(args.out / "producer-snapshot-toctou-acceptance.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
