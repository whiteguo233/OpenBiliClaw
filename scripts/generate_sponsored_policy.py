#!/usr/bin/env python3
"""Generate the unsigned Sponsored Runtime policy from the public registry.

Release CI runs this, then a protected pipeline injects wrapped keys and signs
the result. This script must never read, write, or log key material.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbiliclaw.llm.sponsored_contracts import build_unsigned_policy  # noqa: E402
from openbiliclaw.llm.sponsored_tasks import (  # noqa: E402
    DEFAULT_SPONSORED_REGISTRY,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "dist" / "sponsored-policy.json"
DEFAULT_POLICY_LIFETIME_DAYS = 90
_VOLATILE_POLICY_KEYS = frozenset({"generated_at", "expires_at"})


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _add_days(timestamp: str, days: int) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (parsed + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _render(policy: dict[str, Any]) -> str:
    return json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _check_matches(existing: str, generated: str) -> bool:
    try:
        existing_policy = json.loads(existing)
        generated_policy = json.loads(generated)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"existing policy is not valid JSON: {exc}") from exc
    if not isinstance(existing_policy, dict) or not isinstance(generated_policy, dict):
        return False
    for key in _VOLATILE_POLICY_KEYS:
        existing_policy.pop(key, None)
        generated_policy.pop(key, None)
    return existing_policy == generated_policy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--policy-version", type=int, default=1)
    parser.add_argument("--generated-at", default="", help="ISO-8601 UTC; default: now")
    parser.add_argument("--expires-at", default="", help="ISO-8601 UTC; default: +90 days")
    parser.add_argument("--core-min", default="")
    parser.add_argument("--core-max", default="")
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against --output, ignoring generated_at/expires_at",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = args.generated_at or _utc_now()
    expires_at = args.expires_at or _add_days(generated_at, DEFAULT_POLICY_LIFETIME_DAYS)

    policy = build_unsigned_policy(
        DEFAULT_SPONSORED_REGISTRY,
        policy_version=args.policy_version,
        generated_at=generated_at,
        expires_at=expires_at,
        core_min=args.core_min,
        core_max=args.core_max,
    )
    rendered = _render(policy)

    if args.check:
        if not args.output.exists():
            print(f"missing policy: {args.output}", file=sys.stderr)
            return 2
        if not _check_matches(args.output.read_text(encoding="utf-8"), rendered):
            print(f"policy is out of date: {args.output}", file=sys.stderr)
            return 2
        print(f"policy is up to date: {args.output} ({len(policy['tasks'])} tasks)")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output} ({len(policy['tasks'])} tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
