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

    def test_checkout_action_identity_is_case_insensitive(self):
        for uses, ref in (
            (f"Actions/Checkout@{SHA}", "main"),
            (f"ACTIONS/CHECKOUT@{SHA}", "abcdef1"),
            (f"actions/Checkout@{SHA}", None),
        ):
            with self.subTest(uses=uses, ref=ref):
                fields = f"repository: owner/dependency\n          ref: {ref}\n" if ref is not None else "repository: owner/dependency\n"
                text = f"jobs:\n  test:\n    steps:\n      - uses: {uses}\n        with:\n          {fields}"
                self.assertTrue(validate_workflow_text(text), text)

        flow_mutable = f"jobs:\n  test:\n    steps:\n      - uses: Actions/Checkout@{SHA}\n        with: {{repository: owner/dependency, ref: main}}\n"
        self.assertTrue(validate_workflow_text(flow_mutable), flow_mutable)

        valid_templates = (
            f"uses: Actions/Checkout@{SHA}\n        with: {{repository: owner/dependency, ref: {SHA}}}",
            f"env:\n  CORE_SHA: {SHA}\njobs:\n  test:\n    steps:\n      - uses: Actions/Checkout@{SHA}\n        with:\n          repository: owner/dependency\n          ref: ${{{{ env.CORE_SHA }}}}",
        )
        self.assertEqual(validate_workflow_text("jobs:\n  test:\n    steps:\n      - " + valid_templates[0] + "\n"), [])
        self.assertEqual(validate_workflow_text(valid_templates[1]), [])
        action_tag = f"jobs:\n  test:\n    steps:\n      - uses: Actions/Checkout@v4\n        with: {{repository: owner/dependency, ref: {SHA}}}\n"
        self.assertTrue(any("full 40-character commit SHA" in error for error in validate_workflow_text(action_tag)))

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
            f"git checkout --orphan temporary {SHA}",
        ):
            with self.subTest(command=command):
                text = f"jobs:\n  test:\n    steps:\n      - run: {command}\n"
                self.assertEqual(validate_workflow_text(text), [])
        for command in (
            "git checkout -B temporary main", "git checkout -b temporary abcdef1", "git checkout main",
            "git checkout --orphan temporary",
        ):
            with self.subTest(command=command):
                text = f"jobs:\n  test:\n    steps:\n      - run: {command}\n"
                self.assertTrue(validate_workflow_text(text))

    def test_git_checkout_requires_explicit_revision(self):
        for command in (
            "git checkout", "git checkout --detach", "git checkout -f", "git checkout --quiet",
            "git checkout --detach --force", "/usr/bin/git checkout --detach",
            '"/usr/bin/git" checkout --detach', "(git checkout --detach)",
            '("/usr/bin/git" -C repo checkout --detach)', "git -C repo checkout --detach",
        ):
            with self.subTest(command=command):
                text = f"jobs:\n  test:\n    steps:\n      - run: {command}\n"
                self.assertTrue(validate_workflow_text(text), command)
        for command in (f"git checkout {SHA}", f"git checkout --detach {SHA}", f"git -C repo checkout {SHA}"):
            with self.subTest(command=command):
                text = f"jobs:\n  test:\n    steps:\n      - run: {command}\n"
                self.assertEqual(validate_workflow_text(text), [])

    def test_quoted_and_dynamic_git_executables(self):
        invalid = (
            '"/usr/bin/git" checkout main', "'/usr/bin/git' checkout main", '"git" checkout main',
            '"/usr/bin/git" -C repo checkout main', '"/usr/bin/git" fetch origin main',
            '"$GIT_BIN" checkout ' + SHA, '${GIT_BIN} checkout ' + SHA,
            '$(which git) checkout ' + SHA, '`which git` checkout ' + SHA,
            '"/usr/bin/git" checkout --detach',
            'ignored=$("/usr/bin/git" -C repo checkout main)',
            '"C:\\tools\\git" checkout main',
        )
        for command in invalid:
            with self.subTest(command=command):
                text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in command.splitlines())
                self.assertTrue(validate_workflow_text(text), command)

        valid = (
            f'"/usr/bin/git" checkout {SHA}', f"'git' -C repo checkout {SHA}",
            f'"/usr/bin/git" fetch origin {SHA}', f'("/usr/bin/git" -C repo checkout {SHA})',
        )
        for command in valid:
            with self.subTest(command=command):
                text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in command.splitlines())
                self.assertEqual(validate_workflow_text(text), [], command)

    def test_git_subcommands_are_shell_word_normalized(self):
        invalid = (
            'git "checkout" main', "git 'checkout' main", '/usr/bin/git "checkout" main',
            '"/usr/bin/git" \'checkout\' main', 'git -C repo "checkout" main',
            'git "fetch" origin main', 'SUBCOMMAND=checkout\ngit "$SUBCOMMAND" main',
            'git "$(echo checkout)" main',
        )
        for command in invalid:
            with self.subTest(command=command):
                text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in command.splitlines())
                self.assertTrue(validate_workflow_text(text), command)

        valid = (
            f'git "checkout" {SHA}', f'"/usr/bin/git" \'checkout\' {SHA}',
            f'git "-C" repo "checkout" {SHA}', f'git "fetch" origin {SHA}',
            f'command git "checkout" {SHA}', f'exec "/usr/bin/git" \'checkout\' {SHA}',
        )
        for command in valid:
            with self.subTest(command=command):
                text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in command.splitlines())
                self.assertEqual(validate_workflow_text(text), [], command)

    def test_env_launcher_uses_command_local_identity(self):
        invalid = (
            "env git checkout main", "env /usr/bin/git checkout main", 'env git "checkout" main',
            "env -i git checkout main", "env -C repo git checkout main", "env --chdir=repo git checkout main",
            "env -S 'git checkout main'",
            f"env CORE_SHA=main git checkout \"$CORE_SHA\"",
            f"env -u CORE_SHA git checkout \"$CORE_SHA\"",
            f"env --unset=CORE_SHA git checkout \"$CORE_SHA\"",
            'env "/usr/bin/git" "checkout" main',
            f"CORE_SHA: {SHA}\nenv --ignore-environment git checkout \"$CORE_SHA\"",
            f"CORE_SHA: {SHA}\nenv -i git checkout \"$CORE_SHA\"",
        )
        for script in invalid:
            with self.subTest(script=script):
                env = "      CORE_SHA: " + SHA + "\n" if "CORE_SHA:" not in script else ""
                content = script.replace(f"CORE_SHA: {SHA}\n", "")
                text = "jobs:\n  test:\n    env:\n" + env
                text += "    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in content.splitlines())
                self.assertTrue(validate_workflow_text(text), script)

    def test_command_builtin_options_are_parsed_before_the_executable(self):
        invalid = (
            "command -p git checkout main",
            "command -- git checkout main",
            "command -p -- git checkout main",
            'command -p "/usr/bin/git" checkout main',
            "command -p git -C repo checkout main",
            'command "-p" git checkout main',
            'command -p "git" checkout main',
            'command -p "/usr/bin/git" "checkout" main',
        )
        for script in invalid:
            with self.subTest(script=script):
                text = "jobs:\n  test:\n    steps:\n      - run: " + script + "\n"
                self.assertTrue(validate_workflow_text(text), script)

        valid = (
            f"command -p git checkout {SHA}",
            f"command -- git checkout {SHA}",
            f'command -p "/usr/bin/git" -C repo checkout {SHA}',
            f"command -p env CORE_SHA={SHA} git checkout \"$CORE_SHA\"",
            f"exec env -i CORE_SHA={SHA} git checkout \"$CORE_SHA\"",
            f"command -p env -C repo git checkout {SHA}",
            "command -v git",
            "command -V git",
        )
        for script in valid:
            with self.subTest(script=script):
                text = "jobs:\n  test:\n    steps:\n      - run: " + script + "\n"
                self.assertEqual(validate_workflow_text(text), [], script)

        unknown = "command -x git checkout main"
        text = "jobs:\n  test:\n    steps:\n      - run: " + unknown + "\n"
        self.assertTrue(validate_workflow_text(text), unknown)

    def test_dependency_git_operations_fail_closed_in_conditional_chains_and_pipelines(self):
        sha_b = SHA[::-1]

        def check(script):
            text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
            return validate_workflow_text(text)

        invalid = (
            f"git clone repo dep\nfalse && git -C dep checkout {SHA}",
            f"git clone repo dep\ncondition && git -C dep checkout {SHA}",
            f"git clone repo dep\ngit -C dep checkout {SHA} && git -C dep checkout main",
            "git clone repo dep\ntrue || git -C dep checkout " + SHA,
            "git clone repo dep\ncondition || git -C dep checkout " + SHA,
            f"git clone repo dep\ncondition && git -C dep checkout {sha_b}",
            f"git clone repo dep\ncondition || git -C dep checkout {sha_b}",
            "git clone repo dep | cat",
            f"echo x | git -C dep checkout {SHA}",
            f"git -C dep checkout {SHA} | cat",
            f"git clone repo dep; false && git -C dep checkout {SHA}; git -C dep checkout \"$OTHER\"",
            f"CORE_SHA={SHA}\ncondition && CORE_SHA=main\ngit checkout \"$CORE_SHA\"",
            f"git clone repo dep\nfalse && command -p git -C dep checkout {SHA}",
            f"git clone repo dep\ncondition || command -- git -C dep checkout {SHA}",
            "{\ngit clone repo dep\ncondition && command -p git -C dep checkout " + SHA + "\n}",
            "(\ngit clone repo dep\ncondition && command -p git -C dep checkout " + SHA + "\n)",
        )
        for script in invalid:
            with self.subTest(script=script):
                self.assertTrue(check(script), script)

        valid = f"git clone repo dep; git -C dep checkout {SHA}"
        self.assertEqual(check(valid), [])

        valid = (
            f"env git checkout {SHA}", f"env /usr/bin/git checkout {SHA}",
            f"env CORE_SHA={SHA} git checkout \"$CORE_SHA\"",
            f"env -i CORE_SHA={SHA} git checkout \"$CORE_SHA\"",
            f"env --ignore-environment CORE_SHA={SHA} git checkout \"$CORE_SHA\"",
            f"env --unset=UNUSED CORE_SHA={SHA} git checkout \"$CORE_SHA\"",
            f"env -u UNUSED env CORE_SHA={SHA} \"/usr/bin/git\" \"checkout\" \"$CORE_SHA\"",
            f"command env CORE_SHA={SHA} git checkout \"$CORE_SHA\"",
            f"exec env -i CORE_SHA={SHA} \"/usr/bin/git\" \"checkout\" \"$CORE_SHA\"",
        )
        for script in valid:
            with self.subTest(script=script):
                text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
                self.assertEqual(validate_workflow_text(text), [], script)

        for script in (
            f"CORE_SHA: {SHA}\nenv -i git checkout \"$CORE_SHA\"",
            f"CORE_SHA={SHA}\nif condition; then\nCORE_SHA=main\nfi\nenv \"/usr/bin/git\" \"checkout\" \"$CORE_SHA\"",
            f"if false; then\nCORE_SHA={SHA}\nfi\nenv git checkout \"$CORE_SHA\"",
        ):
            with self.subTest(script=script):
                if script.startswith("CORE_SHA:"):
                    content = script.replace(f"CORE_SHA: {SHA}\n", "")
                    yaml_env = f"      CORE_SHA: {SHA}\n"
                else:
                    content, yaml_env = script, ""
                text = "jobs:\n  test:\n    env:\n" + yaml_env + "    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in content.splitlines())
                self.assertTrue(validate_workflow_text(text), script)

    def test_shell_control_flow_merges_branch_and_loop_states(self):
        sha_a = SHA
        sha_b = SHA[::-1]
        invalid = (
            ("", f"CORE_SHA=main\nif false; then\nCORE_SHA={sha_a}\nfi\ngit checkout \"$CORE_SHA\""),
            ("", f"CORE_SHA={sha_a}\nif false; then\nCORE_SHA=main\nfi\ngit checkout \"$CORE_SHA\""),
            ("CORE_SHA: main\n", f"if false; then\nCORE_SHA={sha_a}\nfi\ngit checkout \"$CORE_SHA\""),
            (f"CORE_SHA: {sha_a}\n", "if false; then\nCORE_SHA=main\nfi\ngit checkout \"$CORE_SHA\""),
            (f"CORE_SHA: {sha_a}\n", f"if condition; then\nCORE_SHA={sha_b}\nfi\ngit checkout \"$CORE_SHA\""),
            ("", f"CORE_SHA={sha_a}\nif condition; then\nCORE_SHA={sha_b}\nfi\ngit checkout \"$CORE_SHA\""),
            ("", f"if condition; then\nCORE_SHA={sha_a}\nelse\nCORE_SHA={sha_b}\nfi\ngit checkout \"$CORE_SHA\""),
            ("CORE_SHA: main\n", f"while false; do\nCORE_SHA={sha_a}\ndone\ngit checkout \"$CORE_SHA\""),
            (f"CORE_SHA: {sha_a}\n", "for x in 1 2; do\nCORE_SHA=main\ndone\ngit checkout \"$CORE_SHA\""),
            (f"CORE_SHA: {sha_a}\n", "(\nif condition; then\nCORE_SHA=main\nfi\ngit checkout \"$CORE_SHA\"\n)"),
            (f"CORE_SHA: {sha_a}\n", f"if false; then\nCORE_SHA={sha_a}\nelif CORE_SHA=main; then\nCORE_SHA={sha_a}\nfi\ngit checkout \"$CORE_SHA\""),
        )
        for env, script in invalid:
            with self.subTest(env=env, script=script):
                text = "jobs:\n  test:\n    env:\n" + "".join(f"      {line}\n" for line in env.splitlines())
                text += "    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
                self.assertTrue(validate_workflow_text(text), script)

        identical_branches = f"if condition; then\nCORE_SHA={sha_a}\nelse\nCORE_SHA={sha_a}\nfi\ngit checkout \"$CORE_SHA\""
        text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in identical_branches.splitlines())
        self.assertEqual(validate_workflow_text(text), [])

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
            f"if true; then CORE_SHA={SHA}; else CORE_SHA={SHA}; fi\ngit checkout \"$CORE_SHA\"",
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

    def test_cloned_dependency_must_be_pinned_to_selected_commit(self):
        sha_a = SHA
        sha_b = SHA[::-1]

        def check(script):
            text = "jobs:\n  test:\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
            return validate_workflow_text(text)

        invalid = (
            "git clone https://github.com/owner/dependency.git dep",
            "git clone --branch main https://github.com/owner/dependency.git dep",
            "git clone repo dep\ngit -C dep checkout main",
            f"git clone repo dep\ngit -C dep fetch origin {sha_a}",
            f"git clone repo-a dep-a\ngit clone repo-b dep-b\ngit -C dep-a checkout {sha_a}",
            f"git clone repo dep\ngit -C other checkout {sha_a}",
            f"env -C parent git clone repo dep\ngit -C dep checkout {sha_a}",
            "if condition; then\ngit clone repo dep\nfi",
            f"if condition; then\ngit clone repo dep\ngit -C dep checkout {sha_a}\nelse\ngit clone repo dep\ngit -C dep checkout {sha_b}\nfi",
            'git clone "https://example.invalid/repo.git" "$UNKNOWN_DEST"',
            "git clone repo",
            "ignored=$(git clone repo dep)",
        )
        for script in invalid:
            with self.subTest(script=script):
                self.assertTrue(check(script), script)

        valid = (
            f"git clone repo dep\ngit -C dep checkout {sha_a}",
            f"git clone --no-checkout repo dep\ngit -C dep checkout --detach {sha_a}",
            f'"/usr/bin/git" clone repo dep\ngit -C dep checkout {sha_a}',
            f"env git clone repo dep\ncommand git -C dep checkout {sha_a}",
            f"command git clone repo dep\nexec git -C dep checkout {sha_a}",
            f"git -C parent clone repo dep\ngit -C parent/dep checkout {sha_a}",
            f"env -C parent git clone repo dep\ngit -C parent/dep checkout {sha_a}",
            f"env --chdir=parent git clone repo dep\ngit -C parent/dep checkout {sha_a}",
            f"git clone repo-a dep-a\ngit clone repo-b dep-b\ngit -C dep-a checkout {sha_a}\ngit -C dep-b checkout {sha_b}",
            f"(git clone repo dep)\ngit -C dep checkout {sha_a}",
            f"git clone --depth 1 repo dep\ngit -C dep checkout {sha_a}",
            f"git clone repo \"$RUNNER_TEMP/core\"\ngit -C \"$RUNNER_TEMP/core\" checkout --detach \"$CORE_SHA\"",
            f"if condition; then\ngit clone repo dep\ngit -C dep checkout {sha_a}\nfi",
            f"{{ git clone repo dep; git -C dep checkout {sha_a}; }}",
        )
        for script in valid:
            with self.subTest(script=script):
                if '"$CORE_SHA"' in script:
                    text = "jobs:\n  test:\n    env:\n      CORE_SHA: " + sha_a + "\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
                    self.assertEqual(validate_workflow_text(text), [], script)
                else:
                    self.assertEqual(check(script), [], script)

    def test_brace_groups_persist_shell_assignments_but_subshells_do_not(self):
        sha = SHA

        def check(script):
            text = f"jobs:\n  test:\n    env:\n      CORE_SHA: {sha}\n    steps:\n      - run: |\n" + "".join(f"          {line}\n" for line in script.splitlines())
            return validate_workflow_text(text)

        invalid = (
            f"{{ CORE_SHA=main; }}\ngit checkout \"$CORE_SHA\"",
            f"{{\nCORE_SHA=main\n}}\ngit checkout \"$CORE_SHA\"",
            f"{{ CORE_SHA=main; git checkout \"$CORE_SHA\"; }}",
            f"{{ CORE_SHA=main; }}\nif condition; then\ntrue\nfi\ngit checkout \"$CORE_SHA\"",
            f"{{\nif condition; then\nCORE_SHA=main\nfi\n}}\ngit checkout \"$CORE_SHA\"",
            f"{{\n( true )\nCORE_SHA=main\n}}\ngit checkout \"$CORE_SHA\"",
        )
        for script in invalid:
            with self.subTest(script=script):
                self.assertTrue(check(script), script)

        valid = (
            f"{{ CORE_SHA={sha}; }}\ngit checkout \"$CORE_SHA\"",
            f"{{\nCORE_SHA={sha}\n}}\ngit checkout \"$CORE_SHA\"",
            f"( CORE_SHA=main )\ngit checkout \"$CORE_SHA\"",
            f"{{\n( CORE_SHA=main )\n}}\ngit checkout \"$CORE_SHA\"",
            f"(\n{{ CORE_SHA=main; }}\n)\ngit checkout \"$CORE_SHA\"",
            f"{{\n( true )\nCORE_SHA={sha}\n}}\ngit checkout \"$CORE_SHA\"",
        )
        for script in valid:
            with self.subTest(script=script):
                self.assertEqual(check(script), [], script)

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
