#!/usr/bin/env python3
"""Compare recommendation CPU/read costs against a Git baseline, without serving.

Copies the input database with SQLite backup before any initialization or read
maintenance. Cached embeddings are opened read-only. No provider requests,
live recommendation consumption, or original database writes occur. Outputs
only aggregate timings/coverage; candidate identities and text stay local.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.llm.embedding import decode_embedding_vector_payload
from openbiliclaw.recommendation.curator import PoolCurator
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.storage.database import Database


def load_baseline(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--embedding-cache", type=Path, required=True)
    parser.add_argument("--baseline", default="ac942817")
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("--rounds must be at least 3")
    baseline_commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.baseline],
        text=True,
    ).strip()
    with tempfile.TemporaryDirectory(prefix="obc-recommend-replay-") as directory:
        root = Path(directory)
        for name, source in [
            ("database", "storage/database.py"),
            ("engine", "recommendation/engine.py"),
        ]:
            text = subprocess.check_output(
                ["git", "show", f"{baseline_commit}:src/openbiliclaw/{source}"],
                text=True,
            )
            (root / f"{name}.py").write_text(text)
        before_db = load_baseline("baseline_database", root / "database.py").Database
        before_engine = load_baseline("baseline_engine", root / "engine.py").RecommendationEngine
        path = root / "snapshot.db"
        with (
            sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as live,
            sqlite3.connect(path) as snapshot,
        ):
            live.backup(snapshot)
        a = before_db(path)
        b = Database(path)
        a.initialize()
        b.initialize()
        result = {}
        for name, kwargs in [
            ("load_pool_serve_snapshot", {"limit": 40}),
            ("count_pool_readiness_isolated", {}),
            ("load_pool_platform_availability", {}),
        ]:
            timings = [[], []]
            for i in range(args.rounds):
                outputs = [None, None]
                for j in [i % 2, 1 - i % 2]:
                    db = [a, b][j]
                    start = time.perf_counter()
                    v = getattr(db, name)(**kwargs)
                    timings[j].append((time.perf_counter() - start) * 1000)
                    outputs[j] = dataclasses.asdict(v) if dataclasses.is_dataclass(v) else v
                assert outputs[0] == outputs[1], name
            result[name] = {
                "before_median_ms": round(statistics.median(timings[0][1:]), 2),
                "after_median_ms": round(statistics.median(timings[1][1:]), 2),
                "identical": True,
            }
            print(name, result[name], flush=True)
        # Replay real cached content and vectors without changing live history or pool.
        rows = [
            dict(r)
            for r in b.conn.execute(
                "SELECT * FROM content_cache WHERE coalesce(pool_expression,'')!='' AND coalesce(topic_group,'')!='' AND coalesce(style_key,'')!='' ORDER BY last_scored_at DESC,bvid LIMIT 80"
            )
        ]
        engine = object.__new__(RecommendationEngine)
        candidates = engine._rows_to_discovered(rows)
        embeddings = {}
        with sqlite3.connect(
            args.embedding_cache.resolve().as_uri() + "?mode=ro", uri=True
        ) as cache:
            for item in candidates:
                key = engine._mmr_embedding_text(item).strip().lower()[:200]
                row = cache.execute(
                    "SELECT vector FROM embedding_cache WHERE text_key=? ORDER BY last_accessed_at DESC LIMIT 1",
                    (key,),
                ).fetchone()
                if row:
                    vector = decode_embedding_vector_payload(row[0])
                    if vector:
                        embeddings[item.scoring_key] = vector
        curator = PoolCurator(b)
        context = curator.build_context()
        scores = curator.score_candidates(candidates, context)
        legacy_embeddings = {
            item.bvid: embeddings[item.scoring_key]
            for item in candidates
            if item.scoring_key in embeddings
        }
        legacy_scores = {
            item.bvid: scores[item.scoring_key] for item in candidates if item.scoring_key in scores
        }
        for count in [40, 80]:
            items = candidates[:count]
            timings = [[], []]
            selector_kwargs = [
                dict(
                    limit=10,
                    score_override=legacy_scores,
                    embeddings=legacy_embeddings,
                    amplification_guard=context.over_budget_amplification_keys,
                ),
                dict(
                    limit=10,
                    score_override=scores,
                    embeddings=embeddings,
                    amplification_guard=context.over_budget_amplification_keys,
                ),
            ]
            for i in range(args.rounds):
                picked = [None, None]
                for j in [i % 2, 1 - i % 2]:
                    start = time.perf_counter()
                    batch = [before_engine, RecommendationEngine][j]._select_diversified_batch(
                        items, **selector_kwargs[j]
                    )
                    timings[j].append((time.perf_counter() - start) * 1000)
                    picked[j] = [x.bvid for x in batch]
                assert picked[0] == picked[1]
            result["selector_" + str(count)] = {
                "candidates": len(items),
                "embedding_coverage": sum(x.scoring_key in embeddings for x in items),
                "dimensions": sorted(
                    {len(embeddings[x.scoring_key]) for x in items if x.scoring_key in embeddings}
                ),
                "selected": len(picked[0]),
                "before_median_ms": round(statistics.median(timings[0][1:]), 2),
                "after_median_ms": round(statistics.median(timings[1][1:]), 2),
                "identical_order": True,
            }
            print("selector_" + str(count), result["selector_" + str(count)], flush=True)
        # Deterministic edge scenarios: mixed styles/topics, incomplete vectors, ties,
        # zero vectors, relevance bonuses and amplification caps.
        for seed in range(48):
            rng = random.Random(seed)
            count = [5, 20, 40, 80][seed % 4]
            limit = [1, 5, 10][seed % 3]
            candidates = [
                DiscoveredContent(
                    bvid=f"item-{i}",
                    title=f"Content {i}",
                    source_strategy="search",
                    source_platform=["bilibili", "twitter", "zhihu"][i % 3],
                    topic_group=f"topic-{rng.randrange(7)}",
                    style_key=["deep_focus", "quick_scan", "hands_on", "opinion_sparring", ""][
                        rng.randrange(5)
                    ],
                    relevance_score=rng.random(),
                )
                for i in range(count)
            ]
            vectors_by_bvid = {
                x.bvid: ([0.0] * 32 if i % 9 == 0 else [rng.uniform(-1, 1) for _ in range(32)])
                for i, x in enumerate(candidates)
                if seed % 3 == 0 or i % 4 != 0
            }
            scores_by_bvid = {x.bvid: (0.5 if seed % 5 == 0 else rng.random()) for x in candidates}
            bonuses_by_bvid = {x.bvid: rng.uniform(-0.1, 0.1) for x in candidates}
            legacy_kwargs = dict(
                limit=limit,
                embeddings=vectors_by_bvid,
                score_override=scores_by_bvid,
                relevance_bonus=bonuses_by_bvid,
                amplification_guard={"topic-0", "topic-1"},
            )
            current_kwargs = dict(
                limit=limit,
                embeddings={
                    x.scoring_key: vectors_by_bvid[x.bvid]
                    for x in candidates
                    if x.bvid in vectors_by_bvid
                },
                score_override={x.scoring_key: scores_by_bvid[x.bvid] for x in candidates},
                relevance_bonus={x.scoring_key: bonuses_by_bvid[x.bvid] for x in candidates},
                amplification_guard={"topic-0", "topic-1"},
            )
            x = before_engine._select_diversified_batch(candidates, **legacy_kwargs)
            y = RecommendationEngine._select_diversified_batch(candidates, **current_kwargs)
            assert [r.bvid for r in x] == [r.bvid for r in y], seed
        result["edge_scenarios_identical"] = 48
        result["baseline_commit"] = baseline_commit
        result["mode"] = "offline_replay"
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print("48 edge scenarios identical", flush=True)
        a.close()
        b.close()


if __name__ == "__main__":
    main()
