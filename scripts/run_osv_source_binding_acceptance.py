#!/usr/bin/env python3
"""Exercise Producer PR #4's OSV source_binding classification."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*, producer_root: Path, binary: Path, artifact: Path, wrong_source: Path, output: Path, producer_sha: str, core_sha: str) -> dict[str, object]:
    sys.path.insert(0, str(producer_root.resolve()))
    from src import osv_producer  # type: ignore[import-not-found]

    artifact = artifact.resolve()
    wrong_source = wrong_source.resolve()
    binary = binary.resolve()
    output = output.resolve()
    osv_producer._platform_key = lambda: "linux"
    osv_producer.PINNED_OSV_SHA256["linux"] = _digest(binary)
    osv_producer.osv_version = lambda _binary: "2.5.1"
    report = json.dumps({
        "results": [{
            "source": {"path": str(wrong_source), "type": "lockfile"},
            "packages": [{
                "package": {"name": "typing_extensions", "version": "4.12.2", "ecosystem": "PyPI"},
                "vulnerabilities": [],
            }],
        }]
    })

    osv_producer.subprocess.run = lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout=report, stderr=""
    )
    producer_exit = osv_producer.run(str(binary), artifact, output)
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    execution = evidence["scanner_execution"]

    assert producer_exit == 1
    assert receipt["verdict"] == "inconclusive"
    assert execution["schema_version"] == "project-defined-scanner-execution-v1"
    assert execution["completeness_status"] == "incomplete"
    assert execution["failed_components"] == ["source_binding"]
    assert execution["completeness_reason"] == "artifact_ref_mismatch"

    manifest = {
        "schema_version": "dogfood-osv-source-binding-runtime-v1",
        "producer_pr_head": producer_sha,
        "core_sha": core_sha,
        "artifact": str(artifact),
        "wrong_source": str(wrong_source),
        "producer_exit_code": producer_exit,
        "producer_receipt_digest": "sha256:" + _digest(output / "receipt.json"),
        "producer_evidence_digest": "sha256:" + _digest(output / "evidence.json"),
        "receipt_verdict": receipt["verdict"],
        "scanner_execution": execution,
    }
    (output / "source-binding-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--wrong-source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--producer-sha", required=True)
    parser.add_argument("--core-sha", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(
        producer_root=args.producer_root,
        binary=args.binary,
        artifact=args.artifact,
        wrong_source=args.wrong_source,
        output=args.out,
        producer_sha=args.producer_sha,
        core_sha=args.core_sha,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
