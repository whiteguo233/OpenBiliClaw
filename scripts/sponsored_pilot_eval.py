#!/usr/bin/env python3
"""Run the Sponsored pilot eval against a runtime command.

Examples::

    # Rust mock runtime (no key needed)
    python scripts/sponsored_pilot_eval.py \
      --runtime "python -m openbiliclaw.llm.sponsored_mock_runtime"

    # Official runtime built with a wrapped key and signed policy
    python scripts/sponsored_pilot_eval.py \
      --runtime "/path/to/obc-sponsored-runtime --policy dist/sponsored-policy.json \
        --signature dist/sponsored-policy.sig --data-dir /tmp/obc-sponsored-eval" \
      --fixtures eval/fixtures.json --output eval/report.json

The harness validates JSON shape only; quality comparison against the legacy
prompt should be a separate human/eval review before enabling the feature.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbiliclaw.llm.sponsored_pilot_eval import (  # noqa: E402
    load_fixtures,
    run_pilot_eval,
)
from openbiliclaw.llm.sponsored_provider import SponsoredProvider  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        required=True,
        help="runtime command line, including --policy/--signature/--data-dir",
    )
    parser.add_argument("--fixtures", default="", help="JSON fixture array; default synthetic set")
    parser.add_argument("--output", default="", help="write the JSON report to this path")
    parser.add_argument("--request-timeout", type=float, default=180.0)
    return parser


async def _run(runtime_command: str, fixtures_path: str, timeout: float) -> dict[str, Any]:
    provider = SponsoredProvider(
        shlex.split(runtime_command),
        request_timeout=timeout,
    )
    try:
        fixtures = load_fixtures(fixtures_path) if fixtures_path else None
        return await run_pilot_eval(provider, fixtures)
    finally:
        await provider.aclose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = asyncio.run(_run(args.runtime, args.fixtures, args.request_timeout))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
