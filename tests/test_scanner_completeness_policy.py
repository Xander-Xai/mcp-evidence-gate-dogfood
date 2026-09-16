from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_scanner_completeness_cases import build_cases
from scripts.derive_producer_adversarial_cases import derive
from scripts.scanner_completeness_policy import evaluate


ROOT = Path(__file__).parents[1]
ARTIFACT = ROOT / "dist" / "example-artifact.bin"


class ScannerCompletenessPolicyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
