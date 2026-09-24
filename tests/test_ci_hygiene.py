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

    def test_checkout_env_resolution_uses_workflow_job_and_step_scope(self):
        workflow_visible = f"""
env:
  CORE_SHA: {SHA}
jobs:
  test:
    steps:
      - uses: actions/checkout@{SHA}
        with: {{repository: owner/dependency, ref: "${{{{ env.CORE_SHA }}}}"}}
"""
        self.assertEqual(validate_workflow_text(workflow_visible), [])

        job_visible = f"""
jobs:
  test:
    env:
      CORE_SHA: {SHA}
    steps:
      - uses: actions/checkout@{SHA}
        with:
          repository: owner/dependency
          ref: ${{{{ env.CORE_SHA }}}}
"""
        self.assertEqual(validate_workflow_text(job_visible), [])

        unrelated_job = f"""
jobs:
  job_a:
    env:
      CORE_SHA: {SHA}
    steps:
      - run: echo unrelated
  job_b:
    steps:
      - uses: actions/checkout@{SHA}
        with:
          repository: owner/dependency
          ref: ${{{{ env.CORE_SHA }}}}
"""
        self.assertTrue(any("must resolve in this scope" in error for error in validate_workflow_text(unrelated_job)))

    def test_step_env_overrides_outer_identity_and_invalid_override_fails(self):
        valid = f"""
env:
  CORE_SHA: {SHA}
jobs:
  test:
    steps:
      - uses: actions/checkout@{SHA}
        env:
          CORE_SHA: {SHA[::-1]}
        with:
          repository: owner/dependency
          ref: ${{{{ env.CORE_SHA }}}}
"""
        self.assertEqual(validate_workflow_text(valid), [])
        invalid = valid.replace(f"CORE_SHA: {SHA[::-1]}", "CORE_SHA: main")
        self.assertTrue(any("must resolve in this scope" in error for error in validate_workflow_text(invalid)))

    def test_flow_style_checkout_with_is_structurally_validated(self):
        template = """
jobs:
  test:
    steps:
      - uses: actions/checkout@ACTION_SHA
        with: {repository: owner/dependency, ref: REF}
"""
        self.assertTrue(validate_workflow_text(template.replace("ACTION_SHA", SHA).replace("REF", "main")))
        self.assertTrue(validate_workflow_text(template.replace("ACTION_SHA", SHA).replace("REF", "abcdef1")))
        self.assertEqual(validate_workflow_text(template.replace("ACTION_SHA", SHA).replace("REF", f'"{SHA}"')), [])
        reordered = f"""
jobs:
  test:
    steps:
      - with: {{ref: main, repository: owner/dependency}}
        uses: actions/checkout@{SHA}
"""
        self.assertTrue(validate_workflow_text(reordered))

    def test_git_checkout_option_arity_and_orphan(self):
        for command in (
            f"git checkout --detach {SHA}",
            f"git checkout {SHA}",
            f"git checkout -B temporary {SHA}",
            f"git checkout -b temporary {SHA}",
            "git checkout --orphan temporary",
        ):
            with self.subTest(command=command):
                text = f"jobs:\n  test:\n    steps:\n      - run: {command}\n"
                self.assertEqual(validate_workflow_text(text), [])
        for command in ("git checkout -B temporary main", "git checkout -b temporary abcdef1", "git checkout main"):
            with self.subTest(command=command):
                text = f"jobs:\n  test:\n    steps:\n      - run: {command}\n"
                self.assertTrue(validate_workflow_text(text))

    def test_shell_assignments_are_tracked_in_execution_order(self):
        cases = (
            (f"CORE_SHA: {SHA}\n", "CORE_SHA=main\ngit checkout \"$CORE_SHA\"", False),
            ("", f"CORE_SHA={SHA}\ngit checkout \"$CORE_SHA\"", True),
            ("", "export CORE_SHA=main\ngit checkout \"$CORE_SHA\"", False),
            ("", f"export CORE_SHA={SHA}\ngit checkout \"$CORE_SHA\"", True),
            (f"CORE_SHA: {SHA}\n", "CORE_SHA=main git checkout \"$CORE_SHA\"", False),
            ("", f"CORE_SHA={SHA} git checkout \"$CORE_SHA\"", True),
            ("", "CORE_SHA=$(git rev-parse HEAD)\ngit checkout \"$CORE_SHA\"", False),
            ("", "CORE_SHA=\ngit checkout \"$CORE_SHA\"", False),
            ("", f"PINNED_SHA={SHA}\nCORE_SHA=\"$PINNED_SHA\"\ngit checkout \"$CORE_SHA\"", True),
            ("", "PINNED_SHA=main\nCORE_SHA=\"$PINNED_SHA\"\ngit checkout \"$CORE_SHA\"", False),
            ("", "CORE_SHA=\"${SOME_MUTABLE_VALUE}\"\ngit checkout \"$CORE_SHA\"", False),
            ("", "CORE_SHA='${LITERAL_TEXT}'\ngit checkout \"$CORE_SHA\"", False),
            ("", "CORE_SHA=`git rev-parse HEAD`\ngit checkout \"$CORE_SHA\"", False),
            ("", "CORE_SHA=\"${{ vars.CORE_SHA }}\"\ngit checkout \"$CORE_SHA\"", False),
        )
        for env, script, expected_pass in cases:
            with self.subTest(env=env, script=script):
                env_yaml = env or "UNRELATED: value\n"
                text = "jobs:\n  test:\n    env:\n" + "".join(f"      {line}" for line in env_yaml.splitlines(keepends=True))
                text += "    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
                errors = validate_workflow_text(text)
                self.assertEqual(not errors, expected_pass, errors)

    def test_shell_command_local_assignments_do_not_persist(self):
        valid_inline_only = f"""
jobs:
  test:
    steps:
      - run: |
          CORE_SHA={SHA} git checkout "$CORE_SHA"
"""
        self.assertEqual(validate_workflow_text(valid_inline_only), [])
        invalid_after_inline = f"""
jobs:
  test:
    steps:
      - run: |
          CORE_SHA={SHA} git checkout "$CORE_SHA"
          git checkout "$CORE_SHA"
"""
        self.assertTrue(validate_workflow_text(invalid_after_inline))

    def test_shell_control_clauses_and_git_executable_paths_compose(self):
        invalid_scripts = (
            f"if true; then CORE_SHA=main; fi\ngit checkout \"$CORE_SHA\"",
            f"if true\nthen\nCORE_SHA=main\nfi\ngit checkout \"$CORE_SHA\"",
            f"if CORE_SHA=main; then\ntrue\nfi\ngit checkout \"$CORE_SHA\"",
            f"while true; do\nCORE_SHA=main\nbreak\ndone\ngit checkout \"$CORE_SHA\"",
            f"for x in 1; do CORE_SHA=main; done\ngit checkout \"$CORE_SHA\"",
            f"until false; do CORE_SHA=main; break; done\ngit checkout \"$CORE_SHA\"",
            f"if true; then export CORE_SHA=main; fi\ngit checkout \"$CORE_SHA\"",
            f"if true; then CORE_SHA=main; fi\n/usr/bin/git checkout \"$CORE_SHA\"",
            f"CORE_SHA={SHA}\n(\nif true; then CORE_SHA=main; fi\n/usr/bin/git checkout \"$CORE_SHA\"\n)",
            "ignored=$(git checkout main)",
            f"ignored=$(git checkout {SHA})",
            "ignored=$(/usr/bin/git fetch origin main)",
            "ignored=`git checkout main`",
            f"ignored=$(/usr/bin/git -C repo checkout main)",
            "if true; then\nignored=$(git -C repo checkout main)\nfi",
            "/usr/bin/git checkout main",
            "/usr/bin/git checkout abcdef1",
            "/usr/bin/git -C repo checkout main",
            "/usr/local/bin/git fetch origin main",
            "./git checkout main",
        )
        for script in invalid_scripts:
            with self.subTest(script=script):
                env = "CORE_SHA: " + SHA + "\n" if "CORE_SHA:" not in script else ""
                text = "jobs:\n  test:\n    env:\n" + "".join(f"      {line}\n" for line in env.splitlines())
                text += "    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
                self.assertTrue(validate_workflow_text(text), script)

        valid_scripts = (
            f"if true; then CORE_SHA={SHA}; fi\ngit checkout \"$CORE_SHA\"",
            f"/usr/bin/git checkout {SHA}",
            f"/usr/bin/git -C repo checkout {SHA}",
            f"/usr/bin/git fetch origin {SHA}",
            f"CORE_SHA={SHA}\n(\n/usr/bin/git checkout \"$CORE_SHA\"\n)",
        )
        for script in valid_scripts:
            with self.subTest(script=script):
                text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
                self.assertEqual(validate_workflow_text(text), [], script)

    def test_shell_grouping_and_subshell_environment(self):
        invalid_scripts = (
            "(git checkout main)",
            "(git checkout abcdef1)",
            "(git fetch origin main)",
            "(git checkout main; echo done)",
            "(echo start; git checkout main)",
            "(\n  CORE_SHA=main\n  git checkout \"$CORE_SHA\"\n)",
            f"CORE_SHA={SHA}\n(\n  CORE_SHA=main\n  git checkout \"$CORE_SHA\"\n)",
        )
        for script in invalid_scripts:
            with self.subTest(script=script):
                text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
                self.assertTrue(validate_workflow_text(text))

        valid_scripts = (
            f"(git checkout {SHA})",
            f"(git fetch origin {SHA})",
            f"(\n  git checkout {SHA}\n)",
            f"(\n  CORE_SHA={SHA}\n  git checkout \"$CORE_SHA\"\n)",
        )
        for script in valid_scripts:
            with self.subTest(script=script):
                text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
                self.assertEqual(validate_workflow_text(text), [])

    def test_quoted_parentheses_remain_argument_data(self):
        text = """
jobs:
  test:
    steps:
      - run: git checkout 'feature/(mutable)'
"""
        errors = validate_workflow_text(text)
        self.assertTrue(any("feature/(mutable)" in error for error in errors), errors)

    def test_single_quoted_shell_variable_is_literal_not_yaml_env_expansion(self):
        text = f"""
jobs:
  test:
    env:
      CORE_SHA: {SHA}
    steps:
      - run: git checkout '$CORE_SHA'
"""
        errors = validate_workflow_text(text)
        self.assertTrue(errors)
        self.assertTrue(any("'$CORE_SHA'" in error for error in errors), errors)

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
