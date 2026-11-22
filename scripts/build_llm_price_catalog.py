#!/usr/bin/env python3
"""Regenerate the bundled LLM price catalog from downloaded datasets.

Inputs (download first; this script never fetches by itself):

- ``models.dev``  ``https://models.dev/api.json``
- ``openrouter``  ``https://openrouter.ai/api/v1/models``

Usage:

    curl -sS https://models.dev/api.json -o /tmp/modelsdev.json
    curl -sS https://openrouter.ai/api/v1/models -o /tmp/openrouter.json
    python scripts/build_llm_price_catalog.py \
        --models-dev /tmp/modelsdev.json \
        --openrouter /tmp/openrouter.json \
        --out src/openbiliclaw/llm/pricing_catalog.json

For an end-user refresh (network fetch + atomic write into the data dir) use
``openbiliclaw price --sync`` instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openbiliclaw.llm.price_catalog import build_catalog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dev", required=True, type=Path)
    parser.add_argument("--openrouter", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--usd-cny", type=float, default=7.2)
    args = parser.parse_args()

    models_dev = json.loads(args.models_dev.read_text(encoding="utf-8"))
    openrouter = json.loads(args.openrouter.read_text(encoding="utf-8"))
    catalog = build_catalog(models_dev, openrouter, usd_cny_rate=args.usd_cny)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            catalog,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    size_kb = args.out.stat().st_size / 1024
    print(
        f"wrote {args.out} ({size_kb:.0f} KB): "
        f"{len(catalog['providers'])} providers, "
        f"{len(catalog['offers'])} models with offers, "
        f"{len(catalog['tariffs'])} models with tariffs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
