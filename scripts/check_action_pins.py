#!/usr/bin/env python3
"""Fail-closed semantic GitHub Actions pin validation using only stdlib."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SHA = re.compile(r"^[0-9a-fA-F]{40}$")
EXTERNAL = re.compile(r"^[^/@\s]+/[^@\s]+@(.+)$")
DEPENDENCY_COMMAND = re.compile(r"\bgit\b[^\n]*(?:\bcheckout\b|\bfetch\b)[^\n]*")
SHELL_VARIABLE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\}?")
ACTION_REF_EXPRESSION = re.compile(r"^\$\{\{\s*env\.([A-Z][A-Z0-9_]*)\s*\}\}$")
KEY_DEFINITION = re.compile(r"^\s*([A-Z][A-Z0-9_]*):(?:\s+(.*?))?\s*$")


def _value(raw: str) -> str:
    value = raw.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def validate_ref(ref: str, location: str) -> str | None:
    ref = _value(ref)
    if ref.startswith("./") or ref.startswith("../"):
        return None
    if ref.startswith("docker://"):
        return f"{location}: docker references are unsupported"
    match = EXTERNAL.fullmatch(ref)
    if not match or not SHA.fullmatch(match.group(1)):
        return f"{location}: external Action must use a full 40-character commit SHA"
    return None


def semantic_action_refs(text: str, source: str = "workflow") -> list[tuple[str, str]]:
    """Return only jobs.<id>.uses and jobs.<id>.steps[*].uses references."""
    refs: list[tuple[str, str]] = []
    in_jobs = False
    current_job: str | None = None
    in_steps = False
    step_indent: int | None = None
    step_active = False
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if stripped == "jobs:":
            in_jobs = True
            current_job = None
            in_steps = False
            step_indent = None
            step_active = False
            continue
        if not in_jobs:
            continue
        if indent <= 1 and stripped and not stripped.startswith("jobs:"):
            in_jobs = False
            current_job = None
            in_steps = False
            step_indent = None
            step_active = False
            continue
        if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            current_job = stripped[:-1].strip(" '\"")
            in_steps = False
            step_indent = None
            step_active = False
            continue
        if current_job is None:
            continue
        if indent == 4 and stripped.startswith("uses:"):
            refs.append((f"{source}:jobs.{current_job}.uses:{number}", _value(stripped[6:].strip())))
            continue
        if indent == 4 and stripped == "steps:":
            in_steps = True
            step_indent = None
            step_active = False
            continue
        if indent == 4 and stripped and not stripped.startswith("#"):
            in_steps = False
            step_indent = None
            step_active = False
            continue
        if not in_steps:
            continue
        if step_indent is None and indent > 4 and stripped.startswith("-"):
            step_indent = indent
            step_active = True
        elif step_indent is not None and indent == step_indent and stripped.startswith("-"):
            step_active = True
        elif step_indent is not None and indent < step_indent:
            in_steps = False
            step_indent = None
            step_active = False
            continue
        if not step_active or step_indent is None:
            continue
        if stripped.startswith("- uses:") and indent == step_indent:
            refs.append((f"{source}:jobs.{current_job}.steps:{number}", _value(stripped[8:].strip())))
        elif indent == step_indent + 2 and stripped.startswith("uses:"):
            refs.append((f"{source}:jobs.{current_job}.steps:{number}", _value(stripped[6:].strip())))
    return refs


def _without_yaml_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in "'\"":
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            continue
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def dependency_identity_keys(text: str) -> set[str]:
    """Find env keys that feed git checkout/fetch or actions/checkout refs."""
    keys: set[str] = set()
    for command in DEPENDENCY_COMMAND.findall(text):
        keys.update(SHELL_VARIABLE.findall(command))
    lines = text.splitlines()
    step_indent: int | None = None
    uses_checkout = False
    for number, raw in enumerate(lines):
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if stripped == "steps:":
            step_indent = indent
            uses_checkout = False
            continue
        if step_indent is None:
            continue
        if indent <= step_indent and stripped:
            step_indent = None
            uses_checkout = False
            continue
        if indent == step_indent + 2 and stripped.startswith("-"):
            uses_checkout = False
            inline = re.match(r"-\s*uses:\s*actions/checkout@", stripped)
            if inline:
                uses_checkout = True
            continue
        if indent == step_indent + 4 and stripped.startswith("uses:"):
            uses_checkout = stripped[6:].strip().startswith("actions/checkout@")
            continue
        if uses_checkout and stripped.startswith("ref:"):
            value = _without_yaml_comment(stripped[4:].strip())
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            match = ACTION_REF_EXPRESSION.fullmatch(value)
            if match:
                keys.add(match.group(1))
    return keys


def _checkout_action_refs(text: str) -> list[tuple[int, str]]:
    refs: list[tuple[int, str]] = []
    lines = text.splitlines()
    step_indent: int | None = None
    uses_checkout = False
    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if stripped == "steps:":
            step_indent = indent
            uses_checkout = False
            continue
        if step_indent is None:
            continue
        if indent <= step_indent and stripped:
            step_indent = None
            uses_checkout = False
            continue
        if indent == step_indent + 2 and stripped.startswith("-"):
            uses_checkout = bool(re.match(r"-\s*uses:\s*actions/checkout@", stripped))
            continue
        if indent == step_indent + 4 and stripped.startswith("uses:"):
            uses_checkout = stripped[6:].strip().startswith("actions/checkout@")
            continue
        if uses_checkout and stripped.startswith("ref:"):
            refs.append((number, _without_yaml_comment(stripped[4:].strip())))
    return refs


def _identity_values(text: str, keys: set[str]) -> list[tuple[int, str, str]]:
    values: list[tuple[int, str, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        match = KEY_DEFINITION.match(raw)
        if match and match.group(1) in keys:
            value = _without_yaml_comment(match.group(2) or "")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values.append((number, match.group(1), value))
    return values


def validate_workflow_text(text: str, source: str = "workflow") -> list[str]:
    errors = []
    if not re.search(r"(?m)^jobs:\s*$", text):
        return [f"{source}: jobs mapping is required"]
    # Validate only values that flow into dependency checkout operations, so
    # ordinary artifact and binary digests remain outside this Git identity gate.
    keys = dependency_identity_keys(text)
    for number, key, value in _identity_values(text, keys):
        if not SHA.fullmatch(value):
            errors.append(f"{source}:line {number}: {key} must use a full 40-character commit SHA")
    for number, raw_value in _checkout_action_refs(text):
        value = raw_value
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if SHA.fullmatch(value):
            continue
        indirection = ACTION_REF_EXPRESSION.fullmatch(value)
        if indirection and any(key == indirection.group(1) and SHA.fullmatch(identity) for _, key, identity in _identity_values(text, keys)):
            continue
        errors.append(f"{source}:line {number}: actions/checkout ref must be a full 40-character commit SHA or validated env identity")
    for location, ref in semantic_action_refs(text, source):
        error = validate_ref(ref, location)
        if error:
            errors.append(error)
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
