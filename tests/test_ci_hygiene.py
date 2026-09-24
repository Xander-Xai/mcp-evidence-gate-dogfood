import json
import unittest
from pathlib import Path

from scripts.check_action_pins import dependency_identity_keys, semantic_action_refs, validate_workflow_text, validate_tree


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

    def test_full_dependency_identity_sha_is_accepted(self):
        text = f"""
jobs:
  test:
    env:
      PRODUCER_SHA: {SHA}
      GATE_SHA: {SHA}
      PRODUCER_PR_HEAD_SHA: {SHA}
      PRODUCER_REVIEWED_SHA: {SHA}
      CORE_CANDIDATE_SHA: {SHA}
    steps:
      - run: |
          git checkout --detach "$PRODUCER_SHA"
          git checkout "$GATE_SHA"
          git fetch origin "$PRODUCER_PR_HEAD_SHA"
          git checkout --detach "$PRODUCER_REVIEWED_SHA"
          git checkout "$CORE_CANDIDATE_SHA"
"""
        self.assertEqual(validate_workflow_text(text), [])

    def test_abbreviated_dependency_identity_sha_is_rejected(self):
        text = """
jobs:
  test:
    env:
      PRODUCER_SHA: 2aeeb6c
      GATE_SHA: 12345678
      PRODUCER_PR_HEAD_SHA: 1234567890ab
      PRODUCER_REVIEWED_SHA: main
      CORE_CANDIDATE_SHA: v1.2.3
    steps:
      - run: |
          git checkout --detach "$PRODUCER_SHA"
          git checkout "$GATE_SHA"
          git fetch origin "$PRODUCER_PR_HEAD_SHA"
          git checkout --detach "$PRODUCER_REVIEWED_SHA"
          git checkout "$CORE_CANDIDATE_SHA"
"""
        errors = validate_workflow_text(text)
        self.assertEqual(len(errors), 5)
        self.assertTrue(all("full 40-character commit SHA" in error for error in errors))

    def test_expression_empty_and_whitespace_identity_values_fail_closed(self):
        invalid_values = (
            "${{ vars.PRODUCER_SHA }}",
            '"${{ vars.PRODUCER_SHA }}"',
            "main",
            "v1.2.3",
            "1234567",
            "",
        )
        for value in invalid_values:
            with self.subTest(value=value):
                text = f"""
jobs:
  test:
    env:
      PRODUCER_SHA: {value}
    steps:
      - run: git checkout --detach "$PRODUCER_SHA"
"""
                self.assertTrue(validate_workflow_text(text))

    def test_actions_checkout_ref_requires_sha_or_validated_identity(self):
        template = """
jobs:
  test:
    env:
      CORE_SHA: {identity}
    steps:
      - uses: actions/checkout@{action_sha}
        with:
          repository: owner/dependency
          ref: {ref}
"""
        valid_refs = (SHA, "${{ env.CORE_SHA }}", '"${{ env.CORE_SHA }}"')
        for ref in valid_refs:
            with self.subTest(ref=ref):
                self.assertEqual(validate_workflow_text(template.format(identity=SHA, action_sha=SHA, ref=ref)), [])
        for ref in ("main", "v1.2.3", "${{ vars.CORE_SHA }}", '"${{ vars.CORE_SHA }}"', ""):
            with self.subTest(ref=ref):
                errors = validate_workflow_text(template.format(identity=SHA, action_sha=SHA, ref=ref))
                self.assertTrue(any("actions/checkout ref" in error for error in errors))

    def test_git_checkout_literal_refs_and_quoted_checkout_actions_fail_closed(self):
        command_template = f"""
jobs:
  test:
    steps:
      - run: git checkout {{ref}}
"""
        for ref in ("main", "v1.2.3", "1234567", "12345678", "1234567890ab"):
            with self.subTest(ref=ref):
                self.assertTrue(validate_workflow_text(command_template.format(ref=ref)))
        self.assertEqual(validate_workflow_text(command_template.format(ref=SHA)), [])

        multiple_commands = f"""
jobs:
  test:
    env:
      GOOD_SHA: {SHA}
    steps:
      - run: git checkout "$GOOD_SHA"; git checkout main
"""
        errors = validate_workflow_text(multiple_commands)
        self.assertTrue(any("dependency ref 'main'" in error for error in errors))

        quoted_action = f"""
jobs:
  test:
    steps:
      - uses: 'actions/checkout@{SHA}'
        with:
          repository: owner/dependency
          ref: main
"""
        self.assertTrue(any("actions/checkout ref" in error for error in validate_workflow_text(quoted_action)))

    def test_actions_checkout_inputs_are_independent_of_yaml_key_order(self):
        text = f"""
jobs:
  test:
    steps:
      - with:
          repository: owner/dependency
          ref: main
        uses: actions/checkout@{SHA}
"""
        self.assertTrue(any("actions/checkout ref" in error for error in validate_workflow_text(text)))

    def test_checkout_identity_discovery_covers_current_workflows(self):
        root = Path(__file__).resolve().parents[1]
        discovered: set[str] = set()
        for workflow in (root / ".github" / "workflows").glob("*.y*ml"):
            discovered.update(dependency_identity_keys(workflow.read_text(encoding="utf-8")))
        self.assertTrue({"GATE_SHA", "PRODUCER_PR_HEAD_SHA", "PRODUCER_REVIEWED_SHA", "CORE_CANDIDATE_SHA"}.issubset(discovered))

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


if __name__ == "__main__":
    unittest.main()
