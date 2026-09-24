#!/usr/bin/env python3
"""Fail-closed GitHub Actions pin validation with structural YAML analysis.

Dependency refs are checked in their workflow/job/step environment scope.
Shell parsing is intentionally bounded to git checkout/fetch forms documented
in ``_checkout_target`` and ``_fetch_targets``; unknown option syntax fails.
"""

from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SHA = re.compile(r"^[0-9a-fA-F]{40}$")
EXTERNAL = re.compile(r"^[^/@\s]+/[^@\s]+@(.+)$")
ACTION_REF_EXPRESSION = re.compile(r"^\$\{\{\s*env\.([A-Z][A-Z0-9_]*)\s*\}\}$")
SHELL_ENV = re.compile(r"^\$\{?([A-Z][A-Z0-9_]*)\}?$")


def _yaml_value(value: Any) -> str:
    """Convert a YAML scalar to text without discarding quotes' content."""
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _shell_word(token: str) -> tuple[str, bool]:
    """Unquote one shell word and report whether variable expansion applies."""
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] == "'":
        return token[1:-1], False
    if len(token) >= 2 and token[0] == token[-1] == '"':
        return token[1:-1], True
    return token, True


def validate_ref(ref: str, location: str) -> str | None:
    ref = ref.strip()
    if ref.startswith("./") or ref.startswith("../"):
        return None
    if ref.startswith("docker://"):
        return f"{location}: docker references are unsupported"
    match = EXTERNAL.fullmatch(ref)
    if not match or not SHA.fullmatch(match.group(1)):
        return f"{location}: external Action must use a full 40-character commit SHA"
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _workflow(text: str, source: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [f"{source}: invalid workflow YAML: {exc}"]
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        return None, [f"{source}: jobs mapping is required"]
    return document, []


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps", [])
    if not isinstance(steps, list):
        return []
    return [_mapping(step) for step in steps]


def _effective_env(*scopes: Any) -> dict[str, str]:
    """Merge outer-to-inner workflow, job, and step env mappings."""
    effective: dict[str, str] = {}
    for scope in scopes:
        for key, value in _mapping(scope).items():
            effective[str(key)] = _yaml_value(value)
    return effective


def _resolved_sha(value: str, env: dict[str, str]) -> bool:
    value = value.strip()
    if SHA.fullmatch(value):
        return True
    match = ACTION_REF_EXPRESSION.fullmatch(value)
    if match:
        return bool(SHA.fullmatch(env.get(match.group(1), "")))
    shell_match = SHELL_ENV.fullmatch(value)
    if shell_match:
        return bool(SHA.fullmatch(env.get(shell_match.group(1), "")))
    return False


@dataclass
class ShellState:
    """Bounded static shell environment and subshell scope stack."""

    persistent_env: dict[str, str]
    scopes: list[dict[str, str]] = field(default_factory=list)

    @property
    def env(self) -> dict[str, str]:
        return self.scopes[-1] if self.scopes else self.persistent_env

    def enter_group(self) -> None:
        self.scopes.append(self.env.copy())

    def leave_group(self) -> bool:
        if not self.scopes:
            return False
        self.scopes.pop()
        return True


def _mask_command_substitutions(line: str) -> tuple[str, bool]:
    """Mask command substitutions, flagging nested protected Git operations."""
    output: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    found_unsafe_git = False
    while index < len(line):
        char = line[index]
        if escaped:
            output.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            output.append(char)
            escaped = True
            index += 1
            continue
        if char in "'\"":
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            output.append(char)
            index += 1
            continue
        substitution_end: int | None = None
        if char == "`" and quote != "'":
            end = index + 1
            while end < len(line) and line[end] != "`":
                if line[end] == "\\":
                    end += 1
                end += 1
            if end < len(line):
                substitution_end = end + 1
                inner = line[index + 1:end]
            else:
                found_unsafe_git = True
                substitution_end = len(line)
                inner = line[index + 1:]
        elif line.startswith("$(", index) and quote != "'":
            depth = 1
            end = index + 2
            inner_quote: str | None = None
            inner_escaped = False
            while end < len(line) and depth:
                current = line[end]
                if inner_escaped:
                    inner_escaped = False
                elif current == "\\" and inner_quote != "'":
                    inner_escaped = True
                elif inner_quote:
                    if current == inner_quote:
                        inner_quote = None
                elif current in "'\"":
                    inner_quote = current
                elif current == "(":
                    depth += 1
                elif current == ")":
                    depth -= 1
                end += 1
            if depth:
                found_unsafe_git = True
                substitution_end = len(line)
                inner = line[index + 2:]
            else:
                substitution_end = end
                inner = line[index + 2:end - 1]
        if substitution_end is not None:
            if _contains_git_dependency_operation(inner):
                found_unsafe_git = True
            output.append("__CODEX_DYNAMIC_SUBSTITUTION__")
            index = substitution_end
            continue
        output.append(char)
        index += 1
    return "".join(output), found_unsafe_git


def _shell_events(script: str) -> list[tuple[str, list[str]]]:
    """Return shell commands and explicit group events from a bounded lexer.

    Newlines, ``;``, ``&&``, ``||``, and pipes end commands. Unquoted
    parentheses are group delimiters; quoted parentheses remain ordinary
    argument data because shlex handles quoting before punctuation.
    """
    events: list[tuple[str, list[str]]] = []
    pending = ""
    heredoc: str | None = None
    case_depth = 0
    for raw_line in script.splitlines():
        if heredoc is not None:
            if raw_line.strip() == heredoc:
                heredoc = None
            continue
        line = pending + raw_line.strip()
        if re.match(r"^case\s", line):
            case_depth += 1
        elif line == "esac" and case_depth:
            case_depth -= 1
        if line.endswith("\\"):
            pending = line[:-1] + " "
            continue
        pending = ""
        line, unsafe_substitution = _mask_command_substitutions(line)
        if unsafe_substitution:
            events.append(("invalid", ["git", "__ambiguous_git_checkout_fetch__"]))
        lexer = shlex.shlex(line, posix=False, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        current: list[str] = []
        try:
            tokens = list(lexer)
        except ValueError:
            # Shell blocks routinely contain multiline quoting and template
            # syntax unrelated to Git. Only fail closed when the malformed
            # line itself appears to contain a protected Git operation.
            if re.search(r"(?:^|\s)(?:[^\s;&|()]*/)?git\b.*\b(checkout|fetch)\b", script):
                events.append(("invalid", ["git", "__ambiguous_git_checkout_fetch__"]))
            continue
        for token in tokens:
            if token and all(char in ";&|()" for char in token):
                for char in token:
                    if char in "()":
                        if case_depth and char == ")":
                            continue
                        if current:
                            events.append(("command", current))
                            current = []
                        events.append(("open" if char == "(" else "close", []))
                    else:
                        if current:
                            events.append(("command", current))
                            current = []
            elif token and any(char in ";&|()" for char in token):
                # shlex keeps punctuation inside a quoted word as data (for
                # example 'feature/(mutable)'). Unquoted delimiters arrive as
                # punctuation-only tokens and are handled above.
                current.append(token)
            else:
                current.append(token)
        if current:
            events.append(("command", current))
            heredoc_match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
            if heredoc_match:
                heredoc = heredoc_match.group(1)
    if pending and re.search(r"(?:^|\s)(?:[^\s;&|()]*/)?git\b.*\b(checkout|fetch)\b", pending):
        events.append(("invalid", ["git", "__ambiguous_git_checkout_fetch__"]))
    return events


def _shell_commands(script: str) -> list[list[str]]:
    """Compatibility projection of parsed commands, including grouped ones."""
    return [tokens for kind, tokens in _shell_events(script) if kind in {"command", "invalid"}]


def _git_subcommand(tokens: list[str]) -> tuple[str | None, int]:
    """Locate checkout/fetch after supported global git options."""
    value_options = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"checkout", "fetch"}:
            return token, index + 1
        if token in value_options:
            if index + 1 >= len(tokens):
                raise ValueError(f"git {token} requires an argument")
            index += 2
            continue
        if token.startswith(("-C", "-c", "--git-dir=", "--work-tree=", "--namespace=", "--config-env=")):
            index += 1
            continue
        if token.startswith("-"):
            # Support ordinary no-argument global switches, but do not guess
            # at unfamiliar options that may consume the following operand.
            if token in {"--no-pager", "--no-optional-locks", "--bare", "--literal-pathspecs"}:
                index += 1
                continue
            raise ValueError(f"unsupported git global option {token!r}")
        return None, index
    return None, index


def _is_git_executable(token: str) -> bool:
    """Recognize Git by executable basename, including explicit paths."""
    return token.replace("\\", "/").rsplit("/", 1)[-1] == "git"


def _git_executable_index(tokens: list[str]) -> int | None:
    return next((index for index, token in enumerate(tokens) if _is_git_executable(token)), None)


def _contains_git_dependency_operation(script: str) -> bool:
    """Use the normal shell and Git parsers for nested substitution content."""
    for tokens in _shell_commands(script):
        index = _git_executable_index(tokens)
        if index is None:
            continue
        try:
            subcommand, _ = _git_subcommand(tokens[index:])
        except ValueError:
            if any(token in {"checkout", "fetch"} for token in tokens[index + 1:]):
                return True
            continue
        if subcommand in {"checkout", "fetch"}:
            return True
    return False


def _checkout_target(args: list[str]) -> str | None:
    """Return checkout's revision operand; support -b/-B and --orphan arity.

    Supported options: --detach, -b/--branch, -B, --orphan, --force/-f,
    --quiet/-q, --guess/--no-guess, --overlay/--no-overlay,
    --recurse-submodules[=<pathspec>], and --no-recurse-submodules. Unknown
    or malformed options fail closed. ``--`` ends options; pathspec checkout
    (two positional operands) is rejected as ambiguous for dependency pinning.
    """
    index = 0
    positional: list[str] = []
    branch_option = False
    while index < len(args):
        token = args[index]
        if token == "--":
            positional.extend(args[index + 1:])
            break
        if token in {"-b", "-B", "--branch", "--orphan"}:
            if index + 1 >= len(args):
                raise ValueError(f"{token} requires a branch name")
            if token != "--orphan":
                branch_option = True
            index += 2
            continue
        if token.startswith("--branch="):
            branch_option = True
            index += 1
            continue
        if token.startswith("--orphan="):
            index += 1
            continue
        if token in {"--detach", "-d", "--force", "-f", "--quiet", "-q", "--guess", "--no-guess", "--overlay", "--no-overlay", "--no-recurse-submodules"}:
            index += 1
            continue
        if token == "--recurse-submodules":
            index += 1
            if index < len(args) and not args[index].startswith("-"):
                # This option's optional pathspec is only consumed when
                # explicitly attached by Git; a separate token is a ref.
                pass
            continue
        if token.startswith("--recurse-submodules="):
            index += 1
            continue
        if token.startswith("-"):
            raise ValueError(f"unsupported git checkout option {token!r}")
        positional.append(token)
        index += 1
    if branch_option:
        if len(positional) != 1:
            raise ValueError("git checkout -b/-B requires exactly one start-point revision")
        return positional[0]
    if len(positional) > 1:
        raise ValueError("ambiguous git checkout with multiple positional operands")
    return positional[0] if positional else None


def _fetch_targets(args: list[str]) -> list[str]:
    """Parse bounded ``git fetch [options] remote [refspec ...]`` syntax."""
    value_options = {"--depth", "--deepen", "--shallow-since", "--shallow-exclude", "--filter", "--server-option", "-j"}
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        if token in value_options:
            if index + 1 >= len(args):
                raise ValueError(f"git fetch {token} requires an argument")
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(args):
        return []
    return [token for token in args[index + 1:] if not token.startswith("-")]


def _git_dependency_targets(script: str) -> list[tuple[str, str]]:
    """Extract bounded Git checkout/fetch targets from a shell script."""
    targets: list[tuple[str, str]] = []
    for command_tokens in _shell_commands(script):
        if "__ambiguous_git_checkout_fetch__" in command_tokens:
            targets.append(("invalid", ""))
            continue
        git_index = _git_executable_index(command_tokens)
        if git_index is None:
            continue
        try:
            subcommand, args_index = _git_subcommand(command_tokens[git_index:])
            if subcommand == "checkout":
                target = _checkout_target(command_tokens[git_index + args_index:])
                if target is not None:
                    targets.append((subcommand, target))
            elif subcommand == "fetch":
                targets.extend((subcommand, target) for target in _fetch_targets(command_tokens[git_index + args_index:]))
        except ValueError:
            # A malformed git checkout/fetch line is not silently ignored.
            if re.search(r"(?:^|\s)(?:[^\s;&|()]*/)?git\b.*\b(checkout|fetch)\b", " ".join(command_tokens)):
                targets.append(("invalid", ""))
    return targets


ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
VARIABLE_VALUE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


def _assignment_value(value: str, env: dict[str, str]) -> str:
    """Resolve literal SHA or a simple already-resolved variable reference."""
    value, expandable = _shell_word(value)
    if SHA.fullmatch(value):
        return value
    if not expandable:
        return value
    reference = VARIABLE_VALUE.fullmatch(value)
    if reference:
        resolved = env.get(reference.group(1), "")
        return resolved if SHA.fullmatch(resolved) else value
    # Retain all other values as explicitly non-SHA so they shadow YAML env.
    # This covers mutable refs, empty assignments, expressions, substitutions,
    # and any complex value the bounded analyzer cannot safely evaluate.
    return value


def _analyze_shell_script(
    script: str,
    initial_env: dict[str, str],
    location: str,
    protected_keys: set[str],
) -> list[str]:
    """Validate Git refs while applying simple shell assignments in order."""
    errors: list[str] = []
    state = ShellState(initial_env.copy())
    for kind, tokens in _shell_events(script):
        if kind == "open":
            state.enter_group()
            continue
        if kind == "close":
            if not state.leave_group():
                errors.append(f"{location}: unmatched shell group close")
            continue
        if kind == "invalid":
            errors.append(f"{location}: unsupported or ambiguous Git shell syntax")
            continue
        if not tokens:
            continue

        # Commands that can mutate arbitrary shell state are outside the
        # supported static grammar. If protected names are in scope, fail
        # closed rather than carrying forward a possibly stale SHA.
        control_words = {"if", "then", "elif", "else", "while", "until", "for", "do"}
        command_offset = 0
        while command_offset < len(tokens) and tokens[command_offset] in control_words:
            command_offset += 1
        if command_offset == len(tokens):
            continue
        tokens = tokens[command_offset:]
        command_name = tokens[0]
        source_is_venv_activation = (
            command_name == "source"
            and len(tokens) == 2
            and _shell_word(tokens[1])[0].replace("\\", "/").endswith("/venv/bin/activate")
        )
        if command_name in {"eval", "source", ".", "read"} and protected_keys and not source_is_venv_activation:
            errors.append(f"{location}: unsupported shell state mutation via {command_name}")
            for key in protected_keys:
                state.env[key] = ""
            continue
        if command_name == "unset":
            for key in tokens[1:]:
                if key in protected_keys:
                    state.env[key] = ""
            continue

        assignment_tokens: list[str] = []
        offset = 0
        if command_name == "export":
            offset = 1
            while offset < len(tokens) and ASSIGNMENT.fullmatch(tokens[offset]):
                assignment_tokens.append(tokens[offset])
                offset += 1
            if offset == len(tokens):
                for assignment in assignment_tokens:
                    match = ASSIGNMENT.fullmatch(assignment)
                    assert match is not None
                    state.env[match.group(1)] = _assignment_value(match.group(2), state.env)
                continue
            # `export NAME` changes only export metadata, not the value.
            continue

        while offset < len(tokens) and ASSIGNMENT.fullmatch(tokens[offset]):
            assignment_tokens.append(tokens[offset])
            offset += 1
        if assignment_tokens:
            if offset == len(tokens):
                # A bare assignment command persists in the current scope.
                for assignment in assignment_tokens:
                    match = ASSIGNMENT.fullmatch(assignment)
                    assert match is not None
                    state.env[match.group(1)] = _assignment_value(match.group(2), state.env)
                continue
            # Leading assignments are local to this command only.
            command_env = state.env.copy()
            for assignment in assignment_tokens:
                match = ASSIGNMENT.fullmatch(assignment)
                assert match is not None
                command_env[match.group(1)] = _assignment_value(match.group(2), command_env)
            command_tokens = tokens[offset:]
        else:
            command_env = state.env
            command_tokens = tokens

        git_index = _git_executable_index(command_tokens)
        if git_index is None:
            # Unknown declarations of protected values are not accepted.
            if command_name in {"declare", "typeset", "local", "readonly"}:
                for token in tokens[1:]:
                    match = ASSIGNMENT.fullmatch(token)
                    if match and match.group(1) in protected_keys:
                        state.env[match.group(1)] = ""
            continue
        try:
            subcommand, args_index = _git_subcommand(command_tokens[git_index:])
            if subcommand == "checkout":
                target = _checkout_target(command_tokens[git_index + args_index:])
                if target is not None:
                    error = _validate_target(target, command_env, location, subcommand)
                    if error:
                        errors.append(error)
            elif subcommand == "fetch":
                for target in _fetch_targets(command_tokens[git_index + args_index:]):
                    error = _validate_target(target, command_env, location, subcommand)
                    if error:
                        errors.append(error)
        except ValueError:
            errors.append(f"{location}: unsupported or ambiguous git checkout/fetch syntax")
    if state.scopes and (protected_keys or _git_dependency_targets(script)):
        errors.append(f"{location}: unclosed shell group")
    return errors


def _extract_env_reference(value: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.fullmatch(value.strip())
    return match.group(1) if match else None


def dependency_identity_keys(text: str) -> set[str]:
    """Find environment keys that feed shell or actions/checkout refs."""
    document, errors = _workflow(text, "workflow")
    if errors or document is None:
        return set()
    keys: set[str] = set()
    workflow_env = _mapping(document.get("env"))
    for job in _mapping(document.get("jobs")).values():
        job = _mapping(job)
        for step in _steps(job):
            for _, target in _git_dependency_targets(_yaml_value(step.get("run"))):
                value, expandable = _shell_word(target)
                name = _extract_env_reference(value, SHELL_ENV) if expandable else None
                if name:
                    keys.add(name)
            with_values = _mapping(step.get("with"))
            name = _extract_env_reference(_yaml_value(with_values.get("ref")), ACTION_REF_EXPRESSION)
            if name:
                keys.add(name)
    return keys


def semantic_action_refs(text: str, source: str = "workflow") -> list[tuple[str, str]]:
    """Return reusable-job and step action ``uses`` values structurally."""
    document, _ = _workflow(text, source)
    if document is None:
        return []
    refs: list[tuple[str, str]] = []
    for job_name, raw_job in _mapping(document.get("jobs")).items():
        job = _mapping(raw_job)
        job_uses = job.get("uses")
        if isinstance(job_uses, str):
            refs.append((f"{source}:jobs.{job_name}.uses", job_uses.strip()))
        for index, step in enumerate(_steps(job)):
            uses = step.get("uses")
            if isinstance(uses, str):
                refs.append((f"{source}:jobs.{job_name}.steps[{index}].uses", uses.strip()))
    return refs


def _validate_target(target: str, env: dict[str, str], location: str, command: str) -> str | None:
    target, expandable = _shell_word(target)
    if SHA.fullmatch(target) or (expandable and _resolved_sha(target, env)):
        return None
    if expandable and (SHELL_ENV.fullmatch(target) or ACTION_REF_EXPRESSION.fullmatch(target)):
        name = SHELL_ENV.fullmatch(target) or ACTION_REF_EXPRESSION.fullmatch(target)
        key = name.group(1) if name else target
        return f"{location}: {command} dependency identity {key} is undefined or not a full 40-character commit SHA"
    if not SHA.fullmatch(target):
        return f"{location}: {command} dependency ref {target!r} must use a full 40-character commit SHA"
    return None


def validate_workflow_text(text: str, source: str = "workflow") -> list[str]:
    document, errors = _workflow(text, source)
    if errors or document is None:
        return errors
    for location, ref in semantic_action_refs(text, source):
        error = validate_ref(ref, location)
        if error:
            errors.append(error)

    workflow_env = _mapping(document.get("env"))
    for job_name, raw_job in _mapping(document.get("jobs")).items():
        job = _mapping(raw_job)
        job_env = _mapping(job.get("env"))
        # Reusable workflow jobs are also actions and were validated above.
        for index, step in enumerate(_steps(job)):
            location = f"{source}:jobs.{job_name}.steps[{index}]"
            uses = _yaml_value(step.get("uses"))
            if uses.startswith("actions/checkout@"):
                checkout_with = _mapping(step.get("with"))
                repository = _yaml_value(checkout_with.get("repository"))
                if repository and not _yaml_value(checkout_with.get("ref")):
                    errors.append(f"{location}: dependency checkout repository requires an immutable ref")
                if "ref" in checkout_with:
                    env = _effective_env(workflow_env, job_env, step.get("env"))
                    ref = _yaml_value(checkout_with.get("ref"))
                    if not _resolved_sha(ref, env):
                        errors.append(f"{location}: actions/checkout ref must resolve in this scope to a full 40-character commit SHA")
            script = _yaml_value(step.get("run"))
            env = _effective_env(workflow_env, job_env, step.get("env"))
            protected: set[str] = set()
            for _, target in _git_dependency_targets(script):
                word, expandable = _shell_word(target)
                name = _extract_env_reference(word, SHELL_ENV) if expandable else None
                if name:
                    protected.add(name)
            errors.extend(_analyze_shell_script(script, env, location, protected))
    return errors


def validate_tree(root: Path) -> list[str]:
    errors: list[str] = []
    workflow_root = root / ".github" / "workflows"
    for path in sorted(workflow_root.glob("*.y*ml")):
        errors.extend(validate_workflow_text(path.read_text(encoding="utf-8"), str(path.relative_to(root))))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_tree(root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Dogfood CI action pin validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
