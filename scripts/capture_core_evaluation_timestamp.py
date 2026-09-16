#!/usr/bin/env python3
"""Capture a UTC Core evaluation timestamp after Producer output exists.

The caller must invoke this helper after the Producer has completed.  The
receipt timestamp is parsed as a timezone-aware datetime and the helper fails
closed if the captured evaluation instant is earlier than ``scanned_at``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC."""

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def capture(receipt_path: Path, *, now: datetime | None = None) -> str:
    """Return a runtime UTC timestamp that is not before receipt ``scanned_at``."""

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    scanned_at_raw = receipt.get("scanned_at")
    if not isinstance(scanned_at_raw, str):
        raise ValueError("receipt.scanned_at must be an ISO-8601 string")

    scanned_at = parse_timestamp(scanned_at_raw)
    evaluation_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if evaluation_now < scanned_at:
        raise ValueError(
            "evaluation timestamp precedes Producer scanned_at: "
            f"{evaluation_now.isoformat()} < {scanned_at.isoformat()}"
        )

    rendered = evaluation_now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    print(f"producer_scanned_at={scanned_at_raw}", file=sys.stderr)
    print(f"core_evaluation_now={rendered}", file=sys.stderr)
    print("evaluation_timestamp_source=runtime_after_producer", file=sys.stderr)
    print("evaluation_now_gte_scanned_at=PASS", file=sys.stderr)
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        print(capture(args.receipt))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"capture_core_evaluation_timestamp: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
