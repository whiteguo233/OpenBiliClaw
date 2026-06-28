"""Regression tests for cache-friendly structured LLM profile-context calls."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


PROFILE_CONTEXT_CALL_SITES = [
    ("src/openbiliclaw/discovery/engine.py", 'caller="discovery.evaluate_single"'),
    ("src/openbiliclaw/recommendation/engine.py", 'caller="recommendation.evaluate_batch"'),
    ("src/openbiliclaw/recommendation/delight.py", 'caller="recommendation.delight_score"'),
    ("src/openbiliclaw/runtime/keyword_planner.py", 'caller="discovery.keyword_planner"'),
    (
        "src/openbiliclaw/runtime/bilibili_producer.py",
        'caller="runtime.bilibili_extension_search.queries"',
    ),
    ("src/openbiliclaw/sources/xhs_keyword_gen.py", 'caller="sources.xhs.keyword_gen"'),
    ("src/openbiliclaw/discovery/strategies/youtube.py", 'caller="yt_search.generate_queries"'),
    ("src/openbiliclaw/discovery/strategies/search.py", 'caller="discovery.search.queries"'),
    ("src/openbiliclaw/discovery/strategies/x.py", 'caller="discovery.x.keyword_gen"'),
    (
        "src/openbiliclaw/discovery/strategies/douyin_direct.py",
        'caller="discovery.douyin.keyword_gen"',
    ),
    ("src/openbiliclaw/discovery/strategies/explore.py", 'caller="discovery.explore.queries"'),
    ("src/openbiliclaw/soul/awareness_analyzer.py", 'caller="soul.awareness"'),
    ("src/openbiliclaw/soul/insight_analyzer.py", 'caller="soul.insight"'),
    ("src/openbiliclaw/soul/speculator.py", 'caller="soul.speculate"'),
    ("src/openbiliclaw/soul/avoidance_speculator.py", 'caller="soul.avoidance_speculate"'),
    ("src/openbiliclaw/soul/profile_builder.py", 'caller="soul.profile_build"'),
]


def test_profile_context_structured_calls_opt_out_of_extra_core_memory() -> None:
    """Profile-bearing prompts should not append the same context into system text.

    These call sites already place the profile/soul/preference context in the
    user prompt. Letting ``LLMService`` append core memory again makes the
    system prompt user-specific and hurts provider prompt-cache prefix reuse.
    """

    missing: list[str] = []
    for relative_path, marker in PROFILE_CONTEXT_CALL_SITES:
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        marker_index = source.index(marker)
        window = source[max(0, marker_index - 700) : marker_index + 700]
        if "inject_core_memory" not in window and "without_core_memory_kwargs" not in window:
            missing.append(f"{relative_path}: {marker}")

    assert not missing, "missing core-memory opt-out near:\n" + "\n".join(missing)
