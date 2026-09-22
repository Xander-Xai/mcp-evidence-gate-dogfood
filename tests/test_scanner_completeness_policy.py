from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_scanner_completeness_cases import build_cases
from scripts.capture_core_evaluation_timestamp import capture, parse_timestamp
from scripts.derive_producer_adversarial_cases import derive
from scripts.scanner_completeness_policy import evaluate


ROOT = Path(__file__).parents[1]
ARTIFACT = ROOT / "dist" / "example-artifact.bin"


def _corpus_clean_eligible(record: dict) -> bool:
    """Apply the shared safety projection to either implementation shape."""
    side = record
    status = side.get("status", side.get("state"))
    if status != "complete" or side.get("semantic_consistency", "ok") != "ok":
        return False
    if side.get("evidence_digest_matches", side.get("digest_matches", True)) is not True:
        return False
    if side.get("failed_components", side.get("failed", 0)):
        return False
    required = side.get("required_components")
    completed = side.get("completed_components")
    if required is not None and (not completed or any(item not in completed for item in required)):
        return False
    if isinstance(side.get("required"), int) and side.get("completed", 0) < side["required"]:
        return False
    counts = side.get("severity_counts", side.get("findings", {}))
    return not any(key not in {"critical", "high", "medium", "low"} and value for key, value in counts.items())


class ScannerCompletenessPolicyTests(unittest.TestCase):
    def test_cross_implementation_corpus_has_same_safety_outcome(self) -> None:
        corpus = json.loads((ROOT / "interop" / "corpus.json").read_text(encoding="utf-8"))
        for name, case in corpus["cases"].items():
            self.assertEqual(_corpus_clean_eligible(case["xander"]), case["clean_eligible"], name)
            self.assertEqual(_corpus_clean_eligible(case["agentgate"]), case["clean_eligible"], name)
    def test_producer_schema_fixture_blocks_failed_zero_findings(self) -> None:
        fixture = ROOT / "evidence" / "scanner-completeness" / "zero-findings-incomplete"
        result = evaluate(fixture / "receipt.json", fixture / "evidence.json", ARTIFACT)
        self.assertEqual(result["integrity"], "pass")
        self.assertEqual(result["receipt_verdict"], "clean")
        self.assertEqual(result["scanner_execution"]["status"], "failed")
        self.assertEqual(result["admission"], "blocked")

    def test_matrix_variants_and_tamper_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dogfood-scanner-completeness-") as directory:
            cases = Path(directory)
            build_cases(cases)

            complete = evaluate(cases / "complete-clean" / "receipt.json", cases / "complete-clean" / "evidence.json", ARTIFACT)
            self.assertEqual(complete["admission"], "eligible_for_gate")

            producer_empty = evaluate(cases / "producer-results-empty" / "receipt.json", cases / "producer-results-empty" / "evidence.json", ARTIFACT)
            self.assertEqual(producer_empty["receipt_verdict"], "inconclusive")
            self.assertEqual(producer_empty["scanner_execution"]["status"], "incomplete")
            self.assertEqual(producer_empty["admission"], "blocked")

            for name in ("incomplete-zero-findings", "failed-zero-findings", "missing-completeness", "malformed-completeness", "contradictory-complete"):
                result = evaluate(cases / name / "receipt.json", cases / name / "evidence.json", ARTIFACT)
                self.assertEqual(result["admission"], "blocked", name)

            tampered = evaluate(cases / "tampered-evidence" / "receipt.json", cases / "tampered-evidence" / "evidence.json", ARTIFACT)
            self.assertEqual(tampered["integrity"], "inconclusive")
            self.assertEqual(tampered["admission"], "blocked")
            self.assertIn("evidence_digest_mismatch", tampered["reason"])

    def test_policy_output_is_machine_readable(self) -> None:
        fixture = ROOT / "evidence" / "scanner-completeness" / "zero-findings-incomplete"
        result = evaluate(fixture / "receipt.json", fixture / "evidence.json", ARTIFACT)
        json.dumps(result, sort_keys=True)

    def test_producer_generated_derivation_preserves_binding_axes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dogfood-producer-derived-") as directory:
            root = Path(directory)
            cases = root / "cases"
            derived = root / "derived"
            build_cases(cases)
            derive(cases / "complete-clean", derived)

            false_clean = evaluate(derived / "false-clean" / "receipt.json", derived / "false-clean" / "evidence.json", ARTIFACT)
            self.assertEqual(false_clean["integrity"], "pass")
            self.assertEqual(false_clean["admission"], "blocked")

            tampered = evaluate(derived / "tampered" / "receipt.json", derived / "tampered" / "evidence.json", ARTIFACT)
            self.assertEqual(tampered["integrity"], "inconclusive")
            self.assertEqual(tampered["admission"], "blocked")

    def test_runtime_evaluation_timestamp_is_timezone_aware_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dogfood-evaluation-time-") as directory:
            receipt = Path(directory) / "receipt.json"
            receipt.write_text(
                json.dumps({"scanned_at": "2026-09-16T13:45:23.123456Z"}),
                encoding="utf-8",
            )
            captured = capture(
                receipt,
                now=datetime(2026, 9, 16, 13, 45, 24, 123456, tzinfo=timezone.utc),
            )
            self.assertEqual(captured, "2026-09-16T13:45:24.123456Z")
            self.assertGreaterEqual(
                parse_timestamp(captured),
                parse_timestamp("2026-09-16T13:45:23.123456Z"),
            )
            with self.assertRaises(ValueError):
                capture(
                    receipt,
                    now=datetime(2026, 9, 16, 13, 45, 22, tzinfo=timezone.utc),
                )

    def test_real_runtime_workflows_use_post_producer_timestamp(self) -> None:
        scanner = (ROOT / ".github" / "workflows" / "scanner-completeness-consumer.yml").read_text(encoding="utf-8")
        deterministic = scanner.split("  producer-generated-acceptance:", 1)[0]
        self.assertIn("--now 2026-09-17T00:00:00Z", deterministic)

        producer_job = scanner.split("  producer-generated-acceptance:", 1)[1].split(
            "  producer-results-empty-acceptance:", 1
        )[0]
        capture_marker = "- name: Capture Core evaluation timestamp after Producer"
        self.assertIn(capture_marker, producer_job)
        self.assertNotIn("--now 2026-09-17T00:00:00Z", producer_job)
        self.assertIn('--now "$CORE_EVALUATION_NOW"', producer_job)
        self.assertIn("EVALUATION_TIMESTAMP_SOURCE=runtime_after_producer", producer_job)
        self.assertLess(
            producer_job.index("- name: Run real Producer on consumer-owned clean candidate"),
            producer_job.index(capture_marker),
        )
        self.assertLess(
            producer_job.index(capture_marker),
            producer_job.index("- name: Verify Producer-generated clean evidence with Core"),
        )

        results_job = scanner.split("  producer-results-empty-acceptance:", 1)[1]
        results_capture_marker = "- name: Capture Core evaluation timestamp after Results=[] Producer"
        self.assertIn(results_capture_marker, results_job)
        self.assertNotIn("--now 2026-09-17T00:00:00Z", results_job)
        self.assertIn('--now "$CORE_EVALUATION_NOW"', results_job)
        self.assertLess(
            results_job.index("- name: Run Producer Results=[] compatibility harness"),
            results_job.index(results_capture_marker),
        )
        self.assertLess(
            results_job.index(results_capture_marker),
            results_job.index("- name: Verify Producer Results=[] with strict Core"),
        )

        osv = (ROOT / ".github" / "workflows" / "real-osv-producer.yml").read_text(encoding="utf-8")
        source_binding_job = osv.split(
            "      - name: Run deterministic OSV source_binding mismatch acceptance", 1
        )[1].split("      - name: Evaluate source_binding mismatch with Dogfood policy", 1)[0]
        self.assertIn("- name: Capture Core evaluation timestamp after Producer", source_binding_job)
        self.assertNotIn("--now 2026-09-17T00:00:00Z", source_binding_job)
        self.assertIn('--now "$CORE_EVALUATION_NOW"', source_binding_job)


if __name__ == "__main__":
    unittest.main()
