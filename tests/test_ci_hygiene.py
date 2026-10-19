import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.check_action_pins import semantic_action_refs, validate_workflow_text, validate_tree


SHA = "0123456789abcdef0123456789abcdef01234567"


class CiHygieneTests(unittest.TestCase):
    def test_full_sha_and_local_refs_pass(self):
        text = f"""
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      uses: ordinary-env-data
    steps:
      - run: echo ok
        env:
          uses: ordinary-step-env-data
      - uses: ./local-action
        with:
          uses: ordinary-input-data
  reusable:
    uses: owner/repo/.github/workflows/test.yml@{SHA}
    with:
      uses: normal-input
"""
        self.assertEqual(validate_workflow_text(text), [])

    def test_mutable_refs_fail(self):
        text = """
jobs:
  test:
    steps:
      - uses: actions/checkout@v4
  reusable:
    uses: owner/repo/.github/workflows/test.yml@main
"""
        errors = validate_workflow_text(text)
        self.assertEqual(len(errors), 2)

    def test_multifield_step_refs_are_validated(self):
        text = f"""
jobs:
  test:
    steps:
      - name: vulnerable
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: evidence
      - name: pinned
        uses: actions/upload-artifact@{SHA}
      - id: identified
        uses: actions/checkout@{SHA}
"""
        errors = validate_workflow_text(text)
        self.assertEqual(len(errors), 1)
        self.assertIn("external Action must use", errors[0])

    def test_real_multifield_action_is_discovered(self):
        root = Path(__file__).resolve().parents[1]
        workflow = root / ".github" / "workflows" / "real-multi-receipt-composition.yml"
        refs = semantic_action_refs(workflow.read_text(encoding="utf-8"), str(workflow))
        upload_refs = [(location, ref) for location, ref in refs if "actions/upload-artifact@" in ref]
        self.assertTrue(upload_refs)
        self.assertTrue(any(ref.startswith("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02") for _, ref in upload_refs))

    def test_mutated_real_multifield_action_is_rejected(self):
        root = Path(__file__).resolve().parents[1]
        workflow = root / ".github" / "workflows" / "real-multi-receipt-composition.yml"
        text = workflow.read_text(encoding="utf-8")
        mutated = text.replace("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", "actions/upload-artifact@v4", 1)
        self.assertTrue(validate_workflow_text(mutated, "mutated-workflow"))

    def test_false_positive_data_keys_are_ignored(self):
        text = """
jobs:
  test:
    env:
      uses: anything
    with:
      uses: another-input
    strategy:
      matrix:
        uses: matrix-data
    steps:
      - run: echo ok
        env:
          uses: step-data
        with:
          uses: input-data
"""
        self.assertEqual(validate_workflow_text(text), [])

    def test_real_tree_has_no_mutable_external_refs(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_tree(root), [])

    def test_governance_and_composition_json_parse(self):
        root = Path(__file__).resolve().parents[1]
        for relative in ("governance/promotion-gates.json", "evidence/consumer-composition-set.json"):
            with (root / relative).open(encoding="utf-8") as handle:
                json.load(handle)


    def test_digest_pinned_evidence_is_byte_stable(self):
        root = Path(__file__).resolve().parents[1]
        clean_receipt = json.loads(
            (root / "evidence/clean/evidence-match-receipt.json").read_text(encoding="utf-8")
        )
        scanner_fixture = root / "evidence/scanner-completeness/zero-findings-incomplete"
        scanner_receipt = json.loads((scanner_fixture / "receipt.json").read_text(encoding="utf-8"))
        scanner_evidence = json.loads((scanner_fixture / "evidence.json").read_text(encoding="utf-8"))
        pinned = {
            "evidence/clean/evidence-report.json": clean_receipt["evidence_digest"],
            "evidence/scanner-completeness/zero-findings-incomplete/evidence.json":
                scanner_receipt["evidence_digest"],
            "evidence/scanner-completeness/zero-findings-incomplete/trivy.raw.json":
                "sha256:" + scanner_evidence["raw_report"]["sha256"],
        }
        for relative, expected in pinned.items():
            with self.subTest(path=relative):
                actual = "sha256:" + hashlib.sha256((root / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)
                attributes = subprocess.check_output(
                    ["git", "check-attr", "text", "eol", "--", relative],
                    cwd=root,
                    text=True,
                ).splitlines()
                # Both -text and an explicit LF checkout prevent CRLF hash drift.
                self.assertTrue(
                    f"{relative}: text: unset" in attributes
                    or f"{relative}: eol: lf" in attributes,
                    f"Digest-pinned evidence lacks cross-platform byte protection: {relative}",
                )


        # Materialize the real pinned fixture bytes into a separate Git repo,
        # then verify actual checkout results with both autocrlf settings.
        with tempfile.TemporaryDirectory(prefix="dogfood-evidence-checkout-") as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / ".gitattributes").write_bytes((root / ".gitattributes").read_bytes())
            for relative in pinned:
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((root / relative).read_bytes())

            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Fixture Test", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "record digest-pinned fixtures"],
                cwd=source,
                check=True,
            )
            for setting in ("true", "false"):
                checkout = Path(temporary) / f"checkout-{setting}"
                subprocess.run(
                    ["git", "-c", f"core.autocrlf={setting}", "clone", "-q", str(source), str(checkout)],
                    check=True,
                )
                for relative, expected in pinned.items():
                    with self.subTest(path=relative, autocrlf=setting):
                        digest = "sha256:" + hashlib.sha256((checkout / relative).read_bytes()).hexdigest()
                        self.assertEqual(digest, expected)


if __name__ == "__main__":
    unittest.main()
