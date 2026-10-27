"""Replay discovery candidates through two evaluation prompt/input arms.

Usage:
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b compact --sample 100 --repeats 3 \
        --output data/eval/profile-diet-compact.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b reason-diet --sample 100 --repeats 3 \
        --output data/eval/reason-diet.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b reason-off --sample 100 --repeats 3 \
        --output data/eval/reason-off.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b json-minify --sample 100 --repeats 3 \
        --output data/eval/json-minify.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b sparse-json --sample 100 --repeats 3 \
        --output data/eval/sparse-json.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b row-wire-v1 --sample 100 --repeats 3 \
        --output data/eval/row-wire-v1.json
    .venv/bin/python scripts/run_profile_diet_ab.py \
        --arm-b model=<instance-id> --sample 100 --repeats 3 \
        --output data/eval/model-route.json

For ``--arm-b compact``, arm A forces the legacy full-profile/no-recall prompt
shape and arm B uses current production inputs: compact profile plus per-item
``related_interests`` recall when an embedding service is configured. For
``reason-diet``, ``reason-off``, ``json-minify``, and ``model=...`` arms, both sides use
production profile/recall shape so the requested arm remains the only
intentional difference. ``reason-off`` leaves arm A on the production reason
contract and temporarily removes the ``reason`` output field from arm B's
evaluation prompts only. ``json-minify`` leaves arm A on production pretty JSON
and removes whitespace only from arm B's profile, negative-example, and content
JSON blocks. The gate uses the production 4096-token output ceiling and fails on
missing evaluation responses instead of treating gateway/parse failures as
genuine zero scores. ``sparse-json`` independently compares production candidate
JSON/global IDs with canonical sparse JSON/local IDs. ``row-wire-v1`` then
independently compares that same sparse JSON with its exact row-wire encoding;
neither arm substitutes for the other's quality and token gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import secrets
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("eval.profile_diet_ab")

FLIP_RATE_MAX = 0.03
SPEARMAN_MIN = 0.95
CHUNK_TIMEOUT_SECONDS = 900.0
RATE_LIMIT_RETRY_DELAYS_SECONDS = (65.0, 130.0, 260.0, 520.0)

_REPLAY_STATUSES = frozenset({"evaluated", "cached", "rejected_low_score"})
_DEFAULT_BATCH_SIZE = 30
_RELATIVE_ADMISSION_SHRINK_MAX = 0.03
_JSON_MINIFY_PROMPT_TOKEN_SAVINGS_MIN = 0.10
_JSON_MINIFY_TOTAL_TOKEN_SAVINGS_MIN = 0.08
_CANDIDATE_TRANSPORT_EXPERIMENTS: Mapping[str, Mapping[str, object]] = {
    "sparse-json": {
        "arm_a_transport": "production-json",
        "arm_b_transport": "sparse-json",
        "prompt_token_savings_min": 0.20,
        "total_token_savings_min": 0.15,
        "total_savings_strict": False,
    },
    "row-wire-v1": {
        "arm_a_transport": "sparse-json",
        "arm_b_transport": "row-wire-v1",
        "prompt_token_savings_min": 0.05,
        "total_token_savings_min": 0.0,
        "total_savings_strict": True,
    },
}
_ENGINE_CANDIDATE_TRANSPORTS = {
    "production-json": "production",
    "sparse-json": "sparse-json",
    "row-wire-v1": "row-wire-v1",
}
_REPLAY_SOURCE_CONTEXT = "mixed"
_REPLAY_EVALUATION_OUTPUT_FIELDS = (
    "relevance_score",
    "relevance_reason",
    "topic_key",
    "topic_group",
    "style_key",
    "franchise_key",
    "pool_expression",
    "pool_topic_label",
)
_REPLAY_CLASSIFICATION_FIELDS = ("topic_group", "style_key", "franchise_key")
_NON_RETRYABLE_PROVIDER_LIMIT_MARKERS = (
    "http 402",
    "payment required",
    "insufficient balance",
    "insufficient_quota",
    "billing",
    "out of credit",
    "credit exhausted",
    "余额不足",
    "账户余额",
)
_EMPTY_REPLAY_ATTRIBUTION: Mapping[str, object] = {
    "pair_kind": "",
    "repeat": 0,
    "logical_run": "",
    "arm": "",
}
_REPLAY_ATTRIBUTION: ContextVar[Mapping[str, object]] = ContextVar(
    "openbiliclaw_profile_diet_replay_attribution",
    default=_EMPTY_REPLAY_ATTRIBUTION,
)
_REPLAY_PROVIDER_CALL_ID: ContextVar[str] = ContextVar(
    "openbiliclaw_profile_diet_provider_call_id",
    default="",
)
_REPLAY_EVAL_REQUEST_COUNTER: ContextVar[dict[str, int] | None] = ContextVar(
    "openbiliclaw_profile_diet_eval_request_counter",
    default=None,
)
_EVALUATION_JSON_TAGS = (
    "profile_core",
    "profile_life_context",
    "profile_interests",
    "profile_style_context",
    "profile_recent_context",
    "profile_extra",
    "profile_summary",
    "negative_examples",
    "evaluation_context",
    "content_batch",
)
_REPLAY_PRIVACY_DIGEST_SALT = secrets.token_bytes(32)
_CACHE_USAGE_SEMANTICS_BY_PROVIDER = {
    "claude": "prompt_excludes_cached",
    "deepseek": "prompt_includes_cached",
    "gemini": "prompt_includes_cached",
    "openai": "prompt_includes_cached",
    "openai_compatible": "prompt_includes_cached",
    "openrouter": "prompt_includes_cached",
    "orcarouter": "prompt_includes_cached",
    "requesty": "prompt_includes_cached",
    "api_route": "prompt_includes_cached",
    "cheaperinference": "prompt_includes_cached",
}


def _normalized_provider_limit_messages(exc: BaseException) -> str:
    """Return messages through the first normalized provider-limit error.

    Provider adapters deliberately translate SDK exceptions into
    ``LLMRateLimitError``. Raw SDK causes can contain incidental response
    metadata such as a ``billing`` field even for a transient HTTP 429; once
    the adapter has classified the error, replay policy must not reinterpret
    those lower-level implementation details.
    """

    from openbiliclaw.llm.base import LLMRateLimitError

    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current).lower())
        if isinstance(current, LLMRateLimitError):
            break
        current = current.__cause__ or current.__context__
    return " ".join(messages)


def _is_retryable_replay_rate_limit(exc: BaseException) -> bool:
    """Return whether one failed replay call is safe to retry after cooldown."""

    from openbiliclaw.llm.service import is_llm_rate_limit_error

    messages = _normalized_provider_limit_messages(exc)
    if any(marker in messages for marker in _NON_RETRYABLE_PROVIDER_LIMIT_MARKERS):
        return False
    return is_llm_rate_limit_error(exc)


@dataclass(frozen=True)
class ReplayCandidate:
    """Prompt-replay metadata aligned with one candidate score."""

    candidate_id: int
    title: str
    source_strategy: str
    source_platform: str = ""
    content_id: str = ""
    content_url: str = ""
    score_threshold: float = 0.0


@dataclass(frozen=True)
class ScoreDeltaSummary:
    """Aggregate absolute score-delta metrics."""

    mean_abs_delta: float
    p95_abs_delta: float


@dataclass(frozen=True)
class AdmissionFlipSummary:
    """Admission-threshold flip metrics."""

    flip_count: int
    item_count: int
    flip_rate: float
    per_strategy: dict[str, int]


@dataclass(frozen=True)
class ModelOverride:
    """Parsed model arm override (instance ID in v2, provider:model in legacy)."""

    provider: str
    model: str


@dataclass(frozen=True)
class ReplayMetrics:
    """Metrics for one aligned pair of replay scores."""

    mean_abs_delta: float
    p95_abs_delta: float
    spearman: float
    flip_rate: float
    flip_count: int
    admitted_a: int
    admitted_b: int
    admission_rate_delta: float


@dataclass(frozen=True)
class ReplayPair:
    """One control or treatment replay pair."""

    repeat: int
    kind: str
    first_arm: str
    scores_a: tuple[float, ...]
    scores_b: tuple[float, ...]
    metrics: ReplayMetrics


@dataclass(frozen=True)
class ReplayProfileSnapshot:
    """Frozen raw/effective profile identities used by every replay arm."""

    raw_profile: object
    effective_profile: object
    raw_digest: str
    effective_digest: str
    overrides_present: bool
    active_speculation_count: int


class ReplayEmbeddingValidationError(RuntimeError):
    """Raised when one replay embedding result is not usable evidence."""


class ReplayEmbeddingAudit:
    """Fail-closed wrapper and privacy-safe audit for replay embeddings."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.namespace = _embedding_namespace(inner)
        self.calls: list[dict[str, object]] = []
        self.errors: list[str] = []
        self._dimension: int | None = None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def embed(self, text: str) -> list[float]:
        attribution = dict(_REPLAY_ATTRIBUTION.get())
        request: dict[str, object] = {
            **attribution,
            "request_digest": _privacy_digest({"embedding_request": str(text)}),
            "namespace": self.namespace,
        }
        try:
            raw_vector = await self._inner.embed(text)
            vector = _validated_embedding_vector(raw_vector)
            dimension = len(vector)
            if self._dimension is None:
                self._dimension = dimension
            elif dimension != self._dimension:
                raise ReplayEmbeddingValidationError(
                    f"embedding dimension drift: expected {self._dimension}, got {dimension}"
                )
        except Exception as exc:
            reason = _embedding_error_reason(exc)
            self.errors.append(reason)
            self.calls.append({**request, "status": "error", "dimension": 0, "error": reason})
            if isinstance(exc, ReplayEmbeddingValidationError):
                raise
            raise ReplayEmbeddingValidationError(reason) from exc
        self.calls.append({**request, "status": "ok", "dimension": dimension})
        return vector

    def summary(
        self,
        *,
        eligible_tail_count: int,
        recall_audit: ReplayRecallAudit,
        expected_runs: set[tuple[str, int, str]] | None = None,
    ) -> dict[str, object]:
        blocking_reasons = list(dict.fromkeys(self.errors))
        production_recall_batches = recall_audit.production_batch_count
        if eligible_tail_count > 0 and production_recall_batches <= 0:
            blocking_reasons.append(
                "eligible tail interests existed but production recall was never invoked"
            )
        if eligible_tail_count > 0 and not self.calls:
            blocking_reasons.append(
                "eligible tail interests existed but no embedding request was audited"
            )
        if eligible_tail_count > 0 and expected_runs:
            observed_runs = {
                (
                    str(call.get("pair_kind") or ""),
                    _to_int(call.get("repeat")),
                    str(call.get("logical_run") or ""),
                )
                for call in self.calls
            }
            for missing in sorted(expected_runs - observed_runs):
                blocking_reasons.append(
                    f"production recall run {missing!r} emitted no embedding request"
                )
        return {
            "passed": not blocking_reasons,
            "degraded": False,
            "namespace": self.namespace,
            "call_count": len(self.calls),
            "successful_call_count": sum(call.get("status") == "ok" for call in self.calls),
            "dimension": self._dimension or 0,
            "eligible_tail_count": eligible_tail_count,
            "blocking_reasons": blocking_reasons,
            "calls": [dict(call) for call in self.calls],
        }


class ReplayRecallAudit:
    """Record whether production recall ran and injected labels per logical run."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    @property
    def production_batch_count(self) -> int:
        return sum(event.get("scope") == "batch" for event in self.events)

    def record_batch(
        self,
        result: Mapping[int, Sequence[object]],
        *,
        candidate_count: int,
        complete_candidate_count: int | None = None,
    ) -> None:
        injected = sum(len(labels) for labels in result.values())
        completed = (
            candidate_count if complete_candidate_count is None else complete_candidate_count
        )
        self.events.append(
            {
                **dict(_REPLAY_ATTRIBUTION.get()),
                "scope": "batch",
                "candidate_count": candidate_count,
                "complete_candidate_count": completed,
                "candidates_with_injection": len(result),
                "injected_label_count": injected,
            }
        )

    def record_single(self, result: Sequence[object], *, complete: bool = True) -> None:
        self.events.append(
            {
                **dict(_REPLAY_ATTRIBUTION.get()),
                "scope": "single",
                "candidate_count": 1,
                "complete_candidate_count": int(complete),
                "candidates_with_injection": int(bool(result)),
                "injected_label_count": len(result),
            }
        )

    def payload(self) -> dict[str, object]:
        return {
            "production_batch_count": self.production_batch_count,
            "injected_label_count": sum(
                int(event["injected_label_count"]) for event in self.events
            ),
            "candidates_with_injection": sum(
                int(event["candidates_with_injection"]) for event in self.events
            ),
            "candidate_count": sum(int(event["candidate_count"]) for event in self.events),
            "complete_candidate_count": sum(
                int(event["complete_candidate_count"]) for event in self.events
            ),
            "events": [dict(event) for event in self.events],
        }

    def validate(
        self,
        *,
        expected_runs: set[tuple[str, int, str]],
        minimum_batches_per_run: int,
        expected_candidate_count: int,
    ) -> dict[str, object]:
        grouped: Counter[tuple[str, int, str]] = Counter()
        candidate_counts: Counter[tuple[str, int, str]] = Counter()
        blocking_reasons: list[str] = []
        for event in self.events:
            if event.get("scope") != "batch":
                continue
            key = (
                str(event.get("pair_kind") or ""),
                _to_int(event.get("repeat")),
                str(event.get("logical_run") or ""),
            )
            grouped[key] += 1
            candidate_counts[key] += int(event.get("candidate_count") or 0)
            if int(event.get("complete_candidate_count") or 0) != int(
                event.get("candidate_count") or 0
            ):
                blocking_reasons.append(f"production recall batch {key!r} was incomplete")
        for expected in sorted(expected_runs):
            if grouped[expected] < minimum_batches_per_run:
                blocking_reasons.append(
                    f"production recall run {expected!r} emitted "
                    f"{grouped[expected]} batch audit(s), expected at least "
                    f"{minimum_batches_per_run}"
                )
            if candidate_counts[expected] < expected_candidate_count:
                blocking_reasons.append(
                    f"production recall run {expected!r} covered "
                    f"{candidate_counts[expected]} candidate(s), expected at least "
                    f"{expected_candidate_count}"
                )
        reasons = list(dict.fromkeys(blocking_reasons))
        return {
            **self.payload(),
            "passed": not reasons,
            "blocking_reasons": reasons,
        }


def score_delta_summary(scores_a: Sequence[float], scores_b: Sequence[float]) -> ScoreDeltaSummary:
    """Return mean and nearest-rank p95 absolute score deltas."""

    deltas = _absolute_deltas(scores_a, scores_b)
    if not deltas:
        return ScoreDeltaSummary(mean_abs_delta=0.0, p95_abs_delta=0.0)
    sorted_deltas = sorted(deltas)
    p95_index = min(len(sorted_deltas) - 1, max(0, math.ceil(len(sorted_deltas) * 0.95) - 1))
    return ScoreDeltaSummary(
        mean_abs_delta=sum(sorted_deltas) / len(sorted_deltas),
        p95_abs_delta=sorted_deltas[p95_index],
    )


def spearman_rank_correlation(scores_a: Sequence[float], scores_b: Sequence[float]) -> float:
    """Return Spearman rank correlation for two aligned score lists.

    Ties receive average ranks. If both rank vectors are constant and equal,
    treat the ordering as unchanged and return ``1.0``.
    """

    _validate_aligned_scores(scores_a, scores_b)
    if not scores_a:
        return 1.0

    ranks_a = _average_ranks(scores_a)
    ranks_b = _average_ranks(scores_b)
    mean_a = sum(ranks_a) / len(ranks_a)
    mean_b = sum(ranks_b) / len(ranks_b)
    diffs_a = [rank - mean_a for rank in ranks_a]
    diffs_b = [rank - mean_b for rank in ranks_b]
    numerator = sum(left * right for left, right in zip(diffs_a, diffs_b, strict=True))
    denom_a = sum(value * value for value in diffs_a)
    denom_b = sum(value * value for value in diffs_b)
    denominator = math.sqrt(denom_a * denom_b)
    if denominator == 0:
        return 1.0 if ranks_a == ranks_b else 0.0
    return numerator / denominator


def admission_flip_summary(
    candidates: Sequence[object],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    *,
    admission_min_score: float = 0.60,
) -> AdmissionFlipSummary:
    """Return admission flips using the same effective thresholds as production."""

    _validate_aligned_scores(scores_a, scores_b)
    if len(candidates) != len(scores_a):
        raise ValueError("candidates and score lists must have the same length")

    per_strategy: dict[str, int] = {}
    flip_count = 0
    for candidate, score_a, score_b in zip(candidates, scores_a, scores_b, strict=True):
        strategy = _candidate_strategy(candidate)
        threshold = _candidate_admission_threshold(
            candidate,
            admission_min_score=admission_min_score,
        )
        flipped = score_a >= threshold > score_b or score_b >= threshold > score_a
        if not flipped:
            continue
        flip_count += 1
        per_strategy[strategy] = per_strategy.get(strategy, 0) + 1

    item_count = len(candidates)
    return AdmissionFlipSummary(
        flip_count=flip_count,
        item_count=item_count,
        flip_rate=(flip_count / item_count) if item_count else 0.0,
        per_strategy=dict(sorted(per_strategy.items())),
    )


def select_replay_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    sample: int,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    """Filter and deterministically order recent replay candidate rows."""

    sample_count = max(0, int(sample))
    if sample_count <= 0:
        return []
    platform_filter = _normalize_platform(platform) if platform else ""
    selected: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        if status not in _REPLAY_STATUSES:
            continue
        if platform_filter and _normalize_platform(row.get("source_platform")) != platform_filter:
            continue
        selected.append(dict(row))
    selected.sort(key=_candidate_row_sort_key, reverse=True)

    # Preserve the observed production mix. Artificially round-robin sampling
    # platform/strategy groups changes their weights and can make the gate pass
    # on a cohort unlike the traffic that the change will actually see.
    return selected[:sample_count]


@contextmanager
def replay_call_attribution(
    *,
    pair_kind: str,
    repeat: int,
    logical_run: str,
    arm: str,
) -> Iterator[None]:
    """Attribute every nested LLM/embedding call to one logical replay run."""

    token = _REPLAY_ATTRIBUTION.set(
        {
            "pair_kind": pair_kind,
            "repeat": int(repeat),
            "logical_run": logical_run,
            "arm": arm,
        }
    )
    try:
        yield
    finally:
        _REPLAY_ATTRIBUTION.reset(token)


@contextmanager
def configured_topic_lifecycle_serialization(config: object) -> Iterator[bool]:
    """Mirror the API/CLI archived-topic serialization switch for replay."""

    from openbiliclaw.soul.profile_views import (
        set_topic_lifecycle_serialization,
        topic_lifecycle_serialization_enabled,
    )

    previous = topic_lifecycle_serialization_enabled()
    configured = (
        str(
            getattr(
                getattr(config, "soul", None),
                "topic_lifecycle_serialization",
                "off",
            )
        )
        .strip()
        .lower()
        == "on"
    )
    set_topic_lifecycle_serialization(configured)
    try:
        yield configured
    finally:
        set_topic_lifecycle_serialization(previous)


def validate_replay_prefilter_compatibility(config: object) -> str:
    """Return production prefilter mode or reject behavior-changing enforce runs."""

    mode = (
        str(
            getattr(getattr(config, "discovery", None), "eval_prefilter_mode", "shadow") or "shadow"
        )
        .strip()
        .lower()
    )
    if mode not in {"off", "shadow", "enforce"}:
        mode = "shadow"
    if mode == "enforce":
        raise RuntimeError(
            "Replay isolates model-visible diet changes with eval_prefilter_mode=off, but "
            "production config is enforce. Switch production back to shadow/off before "
            "collecting landing evidence; otherwise the replay cohort is not equivalent."
        )
    return mode


def _embedding_namespace(service: object) -> str:
    for attribute in (
        "cache_model_namespace",
        "embedding_fingerprint",
        "embedding_model",
    ):
        value = str(getattr(service, attribute, "") or "").strip()
        if value:
            return value
    service_type = type(service)
    return _digest(f"{service_type.__module__}.{service_type.__qualname__}")[:32]


def _validated_embedding_vector(value: object) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ReplayEmbeddingValidationError("embedding returned an empty or non-list vector")
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ReplayEmbeddingValidationError("embedding vector contained a non-numeric value")
        number = float(item)
        if not math.isfinite(number):
            raise ReplayEmbeddingValidationError("embedding vector contained NaN or infinity")
        vector.append(number)
    return vector


def _embedding_error_reason(exc: BaseException) -> str:
    if isinstance(exc, ReplayEmbeddingValidationError):
        return str(exc)
    return f"embedding request raised {type(exc).__name__}"


# ``--arm-b reason-diet``: arm A restores the pre-2689d412 reason instruction
# (unconditional one-sentence reasons) by surgically swapping the exact new
# snippets back to the legacy text inside the current prompt constants; arm B
# runs the production reason contract (skip <0.5, ≤30字). Each (current, legacy)
# pair must match the live constant verbatim — the guard below fails loudly if
# a later prompt edit breaks the swap, instead of silently comparing A to A.
_REASON_DIET_SWAPS: tuple[tuple[str, str, str], ...] = (
    (
        "_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "3. reason 仅供内部诊断,不是面向用户的推荐文案。写法(省 token):"
        'score 严格低于 0.5 的条目,reason 必须写成空串 ""'
        "(这些条目达不到准入门槛、会被直接丢弃,写理由是纯浪费);"
        "score 大于等于 0.5 的条目,reason 写一句精炼中文,"
        "不超过 30 个 Unicode 字符,说明内容与画像匹配或不匹配的依据。\n",
        "3. reason 只写一句中文,解释为什么这个人会喜欢或不喜欢这个内容。\n",
    ),
    (
        "_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '  "reason": "主题契合画像中的长期兴趣,内容角度有增量",\n',
        '  "reason": "这个视频的选题角度新颖,节奏轻快,契合你对该领域的好奇心。",\n',
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "、reason、topic_group(2-4词粗分类)、style_key(13选1)、",
        "、reason(一句中文)、topic_group(2-4词粗分类)、style_key(13选1)、",
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "3a. reason 仅供内部诊断,不是面向用户的推荐文案。写法(省 token):"
        'score 严格低于 0.5 的条目,reason 必须写成空串 ""'
        "(这些条目达不到准入门槛、会被直接丢弃,写理由是纯浪费);"
        "score 大于等于 0.5 的条目,reason 写一句精炼中文,"
        "不超过 30 个 Unicode 字符,说明内容与画像匹配的依据。\n",
        "",
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '"score": 0.45, "reason": ""',
        '"score": 0.45, "reason": "..."',
    ),
)


@contextmanager
def legacy_reason_prompts() -> Iterator[None]:
    """Temporarily restore the pre-reason-diet evaluation prompts (arm A)."""

    from openbiliclaw.llm import prompts as prompts_module

    originals: dict[str, str] = {}
    patched: dict[str, str] = {}
    for constant_name, current_snippet, legacy_snippet in _REASON_DIET_SWAPS:
        base = patched.get(constant_name, getattr(prompts_module, constant_name))
        if constant_name not in originals:
            originals[constant_name] = getattr(prompts_module, constant_name)
        if current_snippet not in base:
            raise RuntimeError(
                "reason-diet arm is stale: expected snippet not found in "
                f"{constant_name}; update _REASON_DIET_SWAPS to match the live prompt."
            )
        patched[constant_name] = base.replace(current_snippet, legacy_snippet, 1)
    for constant_name, text in patched.items():
        setattr(prompts_module, constant_name, text)
    try:
        yield
    finally:
        for constant_name, text in originals.items():
            setattr(prompts_module, constant_name, text)


# ``--arm-b reason-off`` is intentionally replay-only. Arm A keeps the live
# production prompt while arm B removes the reason field from both instructions
# and output schemas. Exact-snippet guards make prompt drift fail loudly instead
# of silently producing an A/A comparison.
_REASON_OFF_SWAPS: tuple[tuple[str, str, str], ...] = (
    (
        "_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "3. reason 仅供内部诊断,不是面向用户的推荐文案。写法(省 token):"
        'score 严格低于 0.5 的条目,reason 必须写成空串 ""'
        "(这些条目达不到准入门槛、会被直接丢弃,写理由是纯浪费);"
        "score 大于等于 0.5 的条目,reason 写一句精炼中文,"
        "不超过 30 个 Unicode 字符,说明内容与画像匹配或不匹配的依据。\n",
        "3. 输出中禁止包含 reason 字段;只保留 score、topic_group、style_key、franchise_key。\n",
    ),
    (
        "_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '4. 不要只说"因为热门"或"因为看过类似的",要结合用户画像。\n',
        "4. 评分必须结合用户画像,不得只因为热门或看过类似内容就提高分数。\n",
    ),
    (
        "_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '  "reason": "主题契合画像中的长期兴趣,内容角度有增量",\n',
        "",
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "、reason、topic_group(2-4词粗分类)、style_key(13选1)、",
        "、topic_group(2-4词粗分类)、style_key(13选1)、",
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        "3a. reason 仅供内部诊断,不是面向用户的推荐文案。写法(省 token):"
        'score 严格低于 0.5 的条目,reason 必须写成空串 ""'
        "(这些条目达不到准入门槛、会被直接丢弃,写理由是纯浪费);"
        "score 大于等于 0.5 的条目,reason 写一句精炼中文,"
        "不超过 30 个 Unicode 字符,说明内容与画像匹配的依据。\n",
        "3a. results 的每个条目都禁止包含 reason 字段。\n",
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '"score": 0.78, "reason": "...", "topic_group": "认知科学"',
        '"score": 0.78, "topic_group": "认知科学"',
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '"score": 0.72, "reason": "...", "topic_group": "游戏摄影"',
        '"score": 0.72, "topic_group": "游戏摄影"',
    ),
    (
        "_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT",
        '"score": 0.45, "reason": "", "topic_group": "美食"',
        '"score": 0.45, "topic_group": "美食"',
    ),
)


@contextmanager
def reason_off_prompts() -> Iterator[None]:
    """Temporarily remove evaluation ``reason`` outputs for replay arm B."""

    from openbiliclaw.llm import prompts as prompts_module

    originals: dict[str, str] = {}
    patched: dict[str, str] = {}
    for constant_name, current_snippet, reason_off_snippet in _REASON_OFF_SWAPS:
        base = patched.get(constant_name, getattr(prompts_module, constant_name))
        if constant_name not in originals:
            originals[constant_name] = getattr(prompts_module, constant_name)
        if current_snippet not in base:
            raise RuntimeError(
                "reason-off arm is stale: expected snippet not found in "
                f"{constant_name}; update _REASON_OFF_SWAPS to match the live prompt."
            )
        patched[constant_name] = base.replace(current_snippet, reason_off_snippet, 1)
    for constant_name, text in patched.items():
        setattr(prompts_module, constant_name, text)
    try:
        yield
    finally:
        for constant_name, text in originals.items():
            setattr(prompts_module, constant_name, text)


def parse_model_override(raw_arm: str) -> ModelOverride | None:
    """Parse a model arm value, returning None for non-model arms."""

    if not raw_arm.startswith("model="):
        return None
    value = raw_arm.removeprefix("model=").strip()
    provider, sep, model = value.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not provider or (sep and not model):
        raise ValueError(
            "--arm-b model override must be model=<instance-id> for v2 routing "
            "or model=<provider:model> for legacy routing"
        )
    return ModelOverride(provider=provider, model=model)


def _absolute_deltas(scores_a: Sequence[float], scores_b: Sequence[float]) -> list[float]:
    _validate_aligned_scores(scores_a, scores_b)
    return [abs(float(left) - float(right)) for left, right in zip(scores_a, scores_b, strict=True)]


def _validate_aligned_scores(scores_a: Sequence[float], scores_b: Sequence[float]) -> None:
    if len(scores_a) != len(scores_b):
        raise ValueError("score lists must have the same length")


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted((float(value), index) for index, value in enumerate(values))
    ranks = [0.0] * len(indexed)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][0] == indexed[start][0]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for _value, original_index in indexed[start:end]:
            ranks[original_index] = average_rank
        start = end
    return ranks


def _candidate_score_threshold(candidate: object) -> float:
    if isinstance(candidate, Mapping):
        raw_threshold = candidate.get("score_threshold")
    else:
        raw_threshold = getattr(candidate, "score_threshold", 0.0)
    try:
        threshold = float(raw_threshold or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return threshold if threshold > 0 else 0.0


def _candidate_admission_threshold(
    candidate: object,
    *,
    admission_min_score: float,
) -> float:
    from openbiliclaw.discovery.admission import effective_admission_threshold

    requested = _candidate_score_threshold(candidate)
    return effective_admission_threshold(
        _candidate_strategy(candidate),
        admission_min_score,
        requested if requested > 0 else None,
    )


def _candidate_strategy(candidate: object) -> str:
    if isinstance(candidate, Mapping):
        raw_strategy = candidate.get("source_strategy")
    else:
        raw_strategy = getattr(candidate, "source_strategy", "")
    strategy = str(raw_strategy or "default").strip().lower()
    return strategy or "default"


def _normalize_platform(value: object) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "bili": "bilibili",
        "b站": "bilibili",
        "xhs": "xiaohongshu",
        "dy": "douyin",
        "yt": "youtube",
        "x": "twitter",
    }
    return aliases.get(raw, raw)


def _candidate_row_sort_key(row: Mapping[str, Any]) -> tuple[str, int]:
    timestamp = str(
        row.get("evaluated_at")
        or row.get("cached_at")
        or row.get("last_seen_at")
        or row.get("created_at")
        or ""
    )
    return timestamp, _to_int(row.get("id"))


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _row_to_replay_candidate(row: Mapping[str, Any]) -> ReplayCandidate:
    content_id = str(row.get("content_id") or row.get("bvid") or "").strip()
    return ReplayCandidate(
        candidate_id=_to_int(row.get("id")),
        title=str(row.get("title") or ""),
        source_strategy=str(row.get("source_strategy") or ""),
        source_platform=_normalize_platform(row.get("source_platform")),
        content_id=content_id,
        content_url=str(row.get("content_url") or ""),
        score_threshold=float(row.get("score_threshold") or 0.0),
    )


def _read_only_sqlite_connection(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(db_path), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _load_read_only_database(db_path: Path) -> Any:
    from openbiliclaw.storage.database import Database

    database = Database(db_path)
    database._conn = _read_only_sqlite_connection(db_path)  # noqa: SLF001
    return database


def _database_path(config: object) -> Path:
    data_path = Path(getattr(config, "data_path", PROJECT_ROOT / "data"))
    return data_path / "openbiliclaw.db"


def _fetch_replay_rows(database: Any, *, sample: int, platform: str | None) -> list[dict[str, Any]]:
    limit = max(sample * 4, sample, 100)
    platform_filter = _normalize_platform(platform) if platform else ""
    params: list[object] = ["evaluated", "cached", "rejected_low_score"]
    platform_clause = ""
    if platform_filter:
        platform_clause = "AND lower(source_platform) = ?"
        params.append(platform_filter)
    params.append(limit)
    cursor = database.conn.execute(
        f"""
        SELECT *
        FROM discovery_candidates
        WHERE status IN (?, ?, ?)
          {platform_clause}
        ORDER BY COALESCE(evaluated_at, cached_at, last_seen_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    selected = select_replay_rows(rows, sample=sample, platform=platform)
    if len(selected) != sample:
        raise RuntimeError(
            f"Replay requires exactly {sample} candidates, but only {len(selected)} eligible "
            "evaluated/cached/rejected_low_score rows were available."
        )
    return selected


def _profile_digest_payload(profile: object) -> dict[str, object]:
    serialized = profile.to_dict() if callable(getattr(profile, "to_dict", None)) else str(profile)
    speculations = getattr(profile, "_active_speculations", None)
    speculation_payload = [
        item.to_dict() if callable(getattr(item, "to_dict", None)) else str(item)
        for item in (speculations if isinstance(speculations, list) else [])
    ]
    return {"profile": serialized, "active_speculations": speculation_payload}


def _load_profile_snapshot(data_root: Path) -> ReplayProfileSnapshot:
    """Load the exact effective profile shape exposed by SoulEngine.get_profile."""

    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.overrides import apply_overrides
    from openbiliclaw.soul.profile import OnionProfile
    from openbiliclaw.soul.speculator import load_speculative_state

    memory = MemoryManager(data_root)
    soul_layer = memory.get_layer("soul")
    soul_layer.load()
    if not soul_layer.data:
        raise RuntimeError(
            f"No current soul profile found in {data_root / 'memory' / 'soul.json'}."
        )
    raw_profile = OnionProfile.from_dict(dict(soul_layer.data))
    overrides = memory.load_profile_overrides()
    effective_profile = apply_overrides(raw_profile, overrides)
    active_speculations = [
        item for item in load_speculative_state(data_root).active if item.status == "active"
    ]
    if active_speculations:
        effective_profile._active_speculations = active_speculations  # type: ignore[attr-defined]
    return ReplayProfileSnapshot(
        raw_profile=raw_profile,
        effective_profile=effective_profile,
        raw_digest=_digest(_profile_digest_payload(raw_profile)),
        effective_digest=_digest(_profile_digest_payload(effective_profile)),
        overrides_present=not overrides.is_empty(),
        active_speculation_count=len(active_speculations),
    )


def _load_current_profile(data_root: Path) -> object:
    """Backward-compatible helper returning the effective replay profile."""

    return _load_profile_snapshot(data_root).effective_profile


def _load_memory_for_llm(data_root: Path) -> object:
    from openbiliclaw.memory.manager import MemoryManager

    memory = MemoryManager(data_root)
    for layer_name in ("soul", "preference"):
        try:
            memory.get_layer(layer_name).load()
        except Exception:
            logger.debug(
                "Failed to load memory layer %s for LLM service", layer_name, exc_info=True
            )
    return memory


def _recent_negative_exemplars(database: Any) -> list[dict[str, object]] | None:
    from openbiliclaw.soul.negative_exemplars import recent_negative_exemplars

    exemplars = recent_negative_exemplars(database)
    if not exemplars:
        return None
    return [dict(item) for item in exemplars]


def _build_llm_service(
    config: object,
    data_root: Path,
    *,
    model_override: ModelOverride | None = None,
) -> object:
    from openbiliclaw.config import llm_concurrency_from_config
    from openbiliclaw.llm.registry import build_llm_registry
    from openbiliclaw.llm.service import LLMService, ModuleOverride, module_overrides_from_config

    registry = build_llm_registry(config)
    module_overrides = dict(module_overrides_from_config(config))
    if model_override is not None:
        if not registry.is_chat_capable(model_override.provider):
            raise RuntimeError(
                f"Provider {model_override.provider!r} is not registered/chat-capable."
            )
        if bool(getattr(getattr(config, "llm", None), "instance_routing", False)):
            if model_override.model:
                raise RuntimeError(
                    "v2 instance routing binds the model in [llm.instances]; "
                    "use --arm-b model=<instance-id> without :model"
                )
            module_overrides["evaluation"] = ModuleOverride(
                chain=(model_override.provider,),
                custom_chain=True,
            )
        else:
            if not model_override.model:
                raise RuntimeError("legacy routing requires --arm-b model=<provider:model>")
            module_overrides["evaluation"] = ModuleOverride(
                provider=model_override.provider,
                model=model_override.model,
            )
    return LLMService(
        registry=registry,
        memory=_load_memory_for_llm(data_root),
        module_overrides=module_overrides,
        concurrency=llm_concurrency_from_config(config),
    )


def _build_embedding_service(config: object) -> object | None:
    from openbiliclaw.llm.registry import build_embedding_service, build_llm_registry

    registry = build_llm_registry(config)
    return build_embedding_service(config, registry)


def _configured_embedding_provider(config: object) -> str:
    llm_config = getattr(config, "llm", None)
    embedding_config = getattr(llm_config, "embedding", None)
    primary = str(getattr(embedding_config, "provider", "") or "").strip().lower()
    fallback = str(getattr(embedding_config, "fallback_provider", "") or "").strip().lower()
    return primary or fallback


@contextmanager
def run_scoped_embedding_audit(
    config: object,
    *,
    allow_no_embedding: bool,
) -> Iterator[ReplayEmbeddingAudit | None]:
    """Build a replay-only embedding cache that remains alive for the full run."""

    original_data_dir = getattr(config, "data_dir", None)
    configured_provider = _configured_embedding_provider(config)
    try:
        with TemporaryDirectory(prefix="openbiliclaw-replay-embedding-") as cache_dir:
            config.data_dir = cache_dir  # type: ignore[attr-defined]
            service = _build_embedding_service(config)
            if service is None:
                if configured_provider:
                    raise RuntimeError(
                        "Configured embedding provider could not be constructed; "
                        "replay cannot treat this as a zero-recall observation."
                    )
                if not allow_no_embedding:
                    raise RuntimeError(
                        "Embedding is disabled in production config. Replay requires a usable "
                        "embedding service by default; pass --allow-no-embedding only to emit "
                        "a degraded, non-landing artifact."
                    )
                yield None
                return

            audit = ReplayEmbeddingAudit(service)
            try:
                yield audit
            finally:
                l2_cache = getattr(service, "_l2_cache", None)
                close_cache = getattr(l2_cache, "close", None)
                if callable(close_cache):
                    close_cache()
    finally:
        config.data_dir = original_data_dir  # type: ignore[attr-defined]


def _evaluation_output_entries(parsed: object) -> list[tuple[str, Mapping[str, object]]]:
    """Return optional mapping keys and score-bearing evaluator results."""

    if isinstance(parsed, list):
        return [("", item) for item in parsed if isinstance(item, Mapping) and "score" in item]
    if not isinstance(parsed, Mapping):
        return []
    if "score" in parsed:
        return [("", parsed)]
    for wrapper_key in ("results", "items", "evaluations", "scores", "data"):
        wrapped = parsed.get(wrapper_key)
        if isinstance(wrapped, list):
            return [("", item) for item in wrapped if isinstance(item, Mapping) and "score" in item]
        if isinstance(wrapped, Mapping):
            if "score" in wrapped:
                return [("", wrapped)]
            mapped_entries = [
                (str(key), item)
                for key, item in wrapped.items()
                if isinstance(item, Mapping) and "score" in item
            ]
            if mapped_entries:
                return mapped_entries
    return [
        (str(key), item)
        for key, item in parsed.items()
        if isinstance(item, Mapping) and "score" in item
    ]


@dataclass(frozen=True)
class _CandidateTransportContext:
    """Ephemeral decoded candidate facts; raw identities never enter artifacts."""

    transport: str
    decode_valid: bool
    payload_start: int
    payload_end: int
    payload: str
    canonical_payload: dict[str, object] | None
    identity_positions: dict[str, int]
    local_ids: tuple[str, ...]
    raw_identity_field_count: int
    raw_url_field_count: int


def _candidate_block_span(user_input: str) -> tuple[int, int, str] | None:
    """Return the builder-owned content-batch body without matching content data."""

    open_frame = "<content_batch>\n\n"
    close_frame = "\n\n</content_batch>"
    start = user_input.find(open_frame)
    if start < 0:
        return None
    payload_start = start + len(open_frame)
    payload_end = user_input.find(close_frame, payload_start)
    if payload_end < 0:
        return None
    if user_input.find(open_frame, payload_end + len(close_frame)) >= 0:
        return None
    return payload_start, payload_end, user_input[payload_start:payload_end]


def _recursive_field_count(value: object, fields: frozenset[str]) -> int:
    if isinstance(value, Mapping):
        return sum(key in fields for key in value) + sum(
            _recursive_field_count(member, fields) for member in value.values()
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return sum(_recursive_field_count(member, fields) for member in value)
    return 0


def _candidate_transport_context(
    user_input: str,
    *,
    expected_transport: str,
) -> _CandidateTransportContext:
    """Strictly decode one candidate block through the shared canonical APIs."""

    span = _candidate_block_span(user_input)
    if span is None:
        return _CandidateTransportContext(
            transport="",
            decode_valid=False,
            payload_start=0,
            payload_end=0,
            payload="",
            canonical_payload=None,
            identity_positions={},
            local_ids=(),
            raw_identity_field_count=0,
            raw_url_field_count=0,
        )
    payload_start, payload_end, payload = span
    actual_transport = expected_transport
    decoded_wire: object = None
    canonical_payload: dict[str, object] | None = None
    identity_positions: dict[str, int] = {}
    local_ids: tuple[str, ...] = ()
    decode_valid = False
    try:
        from openbiliclaw.discovery.eval_payload import (
            build_canonical_evaluation_batch,
            decode_sparse_evaluation_json,
        )
        from openbiliclaw.llm.evaluation_wire import (
            ROW_WIRE_V1_HEADER,
            decode_evaluation_row_wire,
        )

        if payload.startswith(ROW_WIRE_V1_HEADER):
            actual_transport = "row-wire-v1"
            decoded_wire = decode_evaluation_row_wire(payload)
            assert isinstance(decoded_wire, dict)
            batch = decode_sparse_evaluation_json(
                json.dumps(
                    decoded_wire,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            canonical_payload = batch.as_payload()
            local_ids = batch.local_ids
            identity_positions = batch.local_id_to_index
        else:
            decoded_wire = json.loads(payload)
            if isinstance(decoded_wire, list) and all(
                isinstance(item, Mapping) for item in decoded_wire
            ):
                actual_transport = "production-json"
                source_items = [dict(item) for item in decoded_wire]
                batch = build_canonical_evaluation_batch(source_items)
                canonical_payload = batch.as_payload()
                local_ids = batch.local_ids
                raw_identity_positions: dict[str, list[int]] = {}
                for position, item in enumerate(source_items):
                    for field in ("content_id", "bvid", "item_key"):
                        identifier = str(item.get(field) or "").strip()
                        if identifier:
                            raw_identity_positions.setdefault(identifier, []).append(position)
                identity_positions = {
                    identifier: positions[0]
                    for identifier, positions in raw_identity_positions.items()
                    if len(set(positions)) == 1
                }
            elif isinstance(decoded_wire, dict):
                actual_transport = "sparse-json"
                batch = decode_sparse_evaluation_json(payload)
                canonical_payload = batch.as_payload()
                local_ids = batch.local_ids
                identity_positions = batch.local_id_to_index
            else:
                actual_transport = ""
        decode_valid = canonical_payload is not None
    except (AssertionError, TypeError, ValueError, json.JSONDecodeError):
        decode_valid = False
        canonical_payload = None
        identity_positions = {}
        local_ids = ()

    inspected_payload = decoded_wire if decoded_wire is not None else payload
    return _CandidateTransportContext(
        transport=actual_transport,
        decode_valid=decode_valid,
        payload_start=payload_start,
        payload_end=payload_end,
        payload=payload,
        canonical_payload=canonical_payload,
        identity_positions=identity_positions,
        local_ids=local_ids,
        raw_identity_field_count=_recursive_field_count(
            inspected_payload,
            frozenset({"bvid", "content_id", "item_key"}),
        ),
        raw_url_field_count=_recursive_field_count(
            inspected_payload,
            frozenset({"content_url", "cover_url", "url"}),
        ),
    )


def _structured_output_metadata(
    response: object,
    *,
    candidate_positions: Mapping[str, int] | None = None,
) -> dict[str, object]:
    """Build privacy-safe metadata proving whether an output omitted reason."""

    from openbiliclaw.llm.json_utils import parse_llm_json_tolerant

    content = str(getattr(response, "content", "") or "").strip()
    parsed = parse_llm_json_tolerant(content) if content else None
    if parsed is None:
        return {
            "structured_output_parseable": False,
            "structured_item_count": 0,
            "reason_field_count": 0,
            "classification_items": [],
        }
    from openbiliclaw.discovery.style_keys import normalize_style_key

    entries = _evaluation_output_entries(parsed)
    classification_items: list[dict[str, object]] = []
    for position, (mapped_key, item) in enumerate(entries):
        fields: dict[str, object] = {}
        for field in _REPLAY_CLASSIFICATION_FIELDS:
            value = item.get(field)
            normalized = value.strip() if isinstance(value, str) else None
            if field == "style_key" and normalized is not None:
                normalized = normalize_style_key(normalized)
            fields[field] = {
                "digest": (
                    _privacy_digest({"field": field, "value": normalized})
                    if normalized is not None
                    else ""
                ),
                "nonempty": bool(normalized),
            }
        identifier = str(
            item.get("id")
            or item.get("content_id")
            or item.get("bvid")
            or item.get("item_key")
            or mapped_key
        ).strip()
        candidate_position = (
            candidate_positions.get(identifier)
            if candidate_positions is not None and identifier
            else None
        )
        if candidate_positions is not None:
            candidate_key_payload: object = (
                {"candidate_position": candidate_position}
                if candidate_position is not None
                else {"unbound_result_position": position, "identifier_present": bool(identifier)}
            )
        else:
            candidate_key_payload = {"candidate_key": identifier}
        classification_items.append(
            {
                "candidate_key_digest": (
                    _privacy_digest(candidate_key_payload)
                    if identifier or candidate_positions is not None
                    else ""
                ),
                "position": position,
                "fields": fields,
            }
        )
    return {
        "structured_output_parseable": True,
        "structured_item_count": len(entries),
        "reason_field_count": sum("reason" in item for _mapped_key, item in entries),
        "classification_items": classification_items,
    }


def _tagged_json_prompt_metadata(user_input: str) -> tuple[str, list[dict[str, object]]]:
    """Return a JSON-whitespace-normalized prompt and privacy-safe block facts."""

    decoder = json.JSONDecoder()
    spans: list[tuple[int, int, str]] = []
    blocks: list[dict[str, object]] = []
    for name in _EVALUATION_JSON_TAGS:
        open_marker = f"<{name}>"
        close_marker = f"</{name}>"
        search_from = 0
        while True:
            marker_start = user_input.find(open_marker, search_from)
            if marker_start < 0:
                break
            marker_end = marker_start + len(open_marker)
            if (
                marker_start > 0 and user_input[marker_start - 2 : marker_start] != "\n\n"
            ) or user_input[marker_end : marker_end + 2] != "\n\n":
                # A candidate string may itself mention one of the XML-like
                # tags. Only builder-owned standalone blocks are transport
                # boundaries; JSON string contents are data.
                search_from = marker_end
                continue
            json_start = marker_end
            while json_start < len(user_input) and user_input[json_start].isspace():
                json_start += 1
            if name == "content_batch" and user_input.startswith("ROW-WIRE-V1", json_start):
                closing_frame = f"\n\n{close_marker}"
                close_frame_start = user_input.find(closing_frame, json_start)
                if close_frame_start < 0:
                    raise RuntimeError(f"missing closing replay JSON tag </{name}>")
                spans.append(
                    (
                        json_start,
                        close_frame_start,
                        user_input[json_start:close_frame_start],
                    )
                )
                search_from = close_frame_start + len(closing_frame)
                continue
            try:
                value, json_end = decoder.raw_decode(user_input, json_start)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"malformed replay JSON block <{name}>") from exc
            close_start = json_end
            while close_start < len(user_input) and user_input[close_start].isspace():
                close_start += 1
            if not user_input.startswith(close_marker, close_start):
                raise RuntimeError(f"missing closing replay JSON tag </{name}>")

            compact = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            pretty = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            raw_json = user_input[json_start:json_end]
            spans.append((json_start, json_end, compact))
            blocks.append(
                {
                    "name": name,
                    "semantic_digest": _privacy_digest(
                        {"prompt_json_block": name, "canonical": compact}
                    ),
                    "json_chars": len(raw_json),
                    "json_bytes": len(raw_json.encode("utf-8")),
                    "exact_compact": raw_json == compact,
                    "exact_pretty": raw_json == pretty,
                }
            )
            search_from = close_start + len(close_marker)

    normalized_parts: list[str] = []
    cursor = 0
    for start, end, compact in sorted(spans):
        normalized_parts.extend((user_input[cursor:start], compact))
        cursor = end
    normalized_parts.append(user_input[cursor:])
    return "".join(normalized_parts), blocks


def _clock_neutral_evaluation_context(user_input: str) -> str:
    """Replace only the volatile evaluator clock for paired transport audits."""

    open_frame = "<evaluation_context>\n\n"
    close_frame = "\n\n</evaluation_context>"
    prefix, separator, remainder = user_input.partition(open_frame)
    if not separator:
        return user_input
    payload, separator, suffix = remainder.partition(close_frame)
    if not separator:
        return user_input
    try:
        context = json.loads(payload)
    except (TypeError, ValueError):
        return user_input
    if not isinstance(context, dict) or set(context) != {"evaluated_at"}:
        return user_input
    return "".join(
        (
            prefix,
            open_frame,
            '{"evaluated_at":"<replay-clock>"}',
            close_frame,
            suffix,
        )
    )


def _candidate_prompt_metadata(
    context: _CandidateTransportContext,
    *,
    user_input: str,
    image_inputs: Sequence[object],
) -> dict[str, object]:
    """Return privacy-safe proof that one provider-visible candidate block is canonical."""

    canonical_payload = context.canonical_payload
    canonical_items = (
        canonical_payload.get("items") if isinstance(canonical_payload, Mapping) else None
    )
    items = (
        [item for item in canonical_items if isinstance(item, Mapping)]
        if isinstance(canonical_items, list)
        else []
    )
    expected_ids = tuple(str(position) for position in range(len(items)))
    local_transport = context.transport in {"sparse-json", "row-wire-v1"}
    local_coverage = (
        context.decode_valid
        and local_transport
        and context.local_ids == expected_ids
        and len(set(context.local_ids)) == len(context.local_ids)
    )

    image_payloads: list[dict[str, object]] = []
    image_anchors: list[str] = []
    for position, raw_image in enumerate(image_inputs):
        if not isinstance(raw_image, Mapping):
            continue
        content_id = str(raw_image.get("content_id") or "")
        image_anchors.append(f"cover:{content_id}")
        image_payloads.append(
            {
                "position": position,
                "mime_type": str(raw_image.get("mime_type") or ""),
                "data_url": str(raw_image.get("data_url") or ""),
            }
        )
    canonical_anchors = [
        str(item.get("cover_image_ref") or "")
        for item in items
        if str(item.get("cover_image_ref") or "")
    ]
    if local_transport:
        image_anchor_coverage_complete = (
            len(image_anchors) == len(set(image_anchors))
            and len(canonical_anchors) == len(set(canonical_anchors))
            and set(image_anchors) == set(canonical_anchors)
        )
    else:
        # Global-ID production anchors are not part of the local-ID gate. They
        # remain covered by existing production multimodal tests; paired image
        # bytes/MIME/order are compared independently below.
        image_anchor_coverage_complete = True

    user_context = (
        user_input[: context.payload_start]
        + "<canonical-candidate-block>"
        + user_input[context.payload_end :]
        if context.payload_end > context.payload_start
        else user_input
    )
    clock_neutral_user_input = _clock_neutral_evaluation_context(user_input)
    clock_neutral_user_context = _clock_neutral_evaluation_context(user_context)
    return {
        "candidate_transport": context.transport,
        "candidate_decode_valid": context.decode_valid,
        "candidate_item_count": len(items),
        "candidate_canonical_digest": (
            _privacy_digest({"canonical_candidate_payload": canonical_payload})
            if context.decode_valid
            else ""
        ),
        "candidate_payload_chars": len(context.payload),
        "candidate_payload_bytes": len(context.payload.encode("utf-8")),
        "candidate_local_id_coverage_complete": local_coverage,
        "candidate_global_identity_field_count": context.raw_identity_field_count,
        "candidate_url_field_count": context.raw_url_field_count,
        "user_context_digest": _privacy_digest(
            {"candidate_free_user_context": clock_neutral_user_context}
        ),
        "candidate_contract_prompt_digest": _privacy_digest(
            {"clock_neutral_user_input": clock_neutral_user_input}
        ),
        "image_payloads_digest": _privacy_digest({"ordered_image_payloads": image_payloads}),
        "image_anchor_coverage_complete": image_anchor_coverage_complete,
        "image_reference_count": len(canonical_anchors),
    }


def _result_identity_metadata(
    response: object | None,
    *,
    context: _CandidateTransportContext | None,
    expected_transport: str,
) -> dict[str, object]:
    """Prove local result members are resolved only through the shared safe resolver."""

    from openbiliclaw.llm.json_utils import parse_llm_json_tolerant

    content = str(getattr(response, "content", "") or "").strip()
    parsed = parse_llm_json_tolerant(content) if content else None
    entries = _evaluation_output_entries(parsed)
    members = [dict(item) for _mapped_key, item in entries]
    if expected_transport not in {"sparse-json", "row-wire-v1"}:
        positions = context.identity_positions if context is not None else {}
        bound_global_identity_count = sum(
            bool(str(item.get("content_id") or item.get("bvid") or mapped_key).strip() in positions)
            for mapped_key, item in entries
        )
        return {
            "result_identity_contract": (
                "global-id"
                if entries and bound_global_identity_count == len(entries)
                else "unverified-global-id"
            ),
            "result_local_id_binding_safe": True,
            "result_local_id_count": 0,
            "result_missing_local_id_count": 0,
            "result_unknown_local_id_count": 0,
            "result_duplicate_local_id_count": 0,
            "result_global_identity_field_count": _recursive_field_count(
                members,
                frozenset({"bvid", "content_id", "item_key"}),
            ),
        }

    from openbiliclaw.discovery.eval_payload import resolve_local_evaluation_results

    expected_ids = context.local_ids if context is not None else ()
    explicit_ids = [
        item.get("id") for item in members if item.get("id") is not None and item.get("id") != ""
    ]
    valid_ids = [
        identifier
        for identifier in explicit_ids
        if isinstance(identifier, str) and identifier in set(expected_ids)
    ]
    duplicate_count = len(valid_ids) - len(set(valid_ids))
    unknown_count = sum(
        not isinstance(identifier, str) or identifier not in set(expected_ids)
        for identifier in explicit_ids
    )
    missing_count = max(len(expected_ids) - len(set(valid_ids)), 0)
    global_identity_count = _recursive_field_count(
        members,
        frozenset({"bvid", "content_id", "item_key"}),
    ) + sum(
        bool(mapped_key) and mapped_key not in set(expected_ids) for mapped_key, _item in entries
    )
    binding_safe = False
    if context is not None and context.decode_valid:
        try:
            resolved = resolve_local_evaluation_results(members, expected_ids)
            binding_safe = all(
                member is None
                or member.get("id") == expected_id
                or (len(expected_ids) == 1 and len(members) == 1 and member.get("id") in {None, ""})
                for expected_id, member in zip(expected_ids, resolved, strict=True)
            )
        except (TypeError, ValueError):
            binding_safe = False
    return {
        "result_identity_contract": ("local-id" if global_identity_count == 0 else "global-id"),
        "result_local_id_binding_safe": binding_safe,
        "result_local_id_count": len(valid_ids),
        "result_missing_local_id_count": missing_count,
        "result_unknown_local_id_count": unknown_count,
        "result_duplicate_local_id_count": duplicate_count,
        "result_global_identity_field_count": global_identity_count,
    }


def _prompt_transport_metadata(
    kwargs: Mapping[str, object],
    *,
    expected_compact_json: bool,
    expected_candidate_transport: str = "production-json",
    candidate_transport_audit_enabled: bool = False,
) -> dict[str, object]:
    """Describe exact provider-visible text without retaining prompt payloads."""

    system_instruction = str(kwargs.get("system_instruction") or "")
    user_input = str(kwargs.get("user_input") or "")
    normalized_user_input, blocks = _tagged_json_prompt_metadata(user_input)
    compact_block_count = sum(bool(block["exact_compact"]) for block in blocks)
    pretty_block_count = sum(bool(block["exact_pretty"]) for block in blocks)
    profile_block_count = sum(str(block["name"]).startswith("profile_") for block in blocks)
    names = [str(block["name"]) for block in blocks]
    raw_image_inputs = kwargs.get("image_inputs")
    image_inputs = (
        raw_image_inputs
        if isinstance(raw_image_inputs, Sequence) and not isinstance(raw_image_inputs, str | bytes)
        else ()
    )
    image_metadata: list[dict[str, object]] = []
    for position, image in enumerate(image_inputs):
        if not isinstance(image, Mapping):
            continue
        content_id = str(image.get("content_id") or "")
        data_url = str(image.get("data_url") or "")
        mime_type = str(image.get("mime_type") or "")
        image_metadata.append(
            {
                "position": position,
                "content_id_digest": _privacy_digest({"content_id": content_id}),
                "mime_type": mime_type,
                "data_url_bytes": len(data_url.encode("utf-8")),
                "data_url_digest": _privacy_digest({"image_data_url": data_url}),
            }
        )
    image_semantics = json.dumps(
        image_metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    prompt_payload = system_instruction + "\0" + user_input + "\0" + image_semantics
    semantic_payload = system_instruction + "\0" + normalized_user_input + "\0" + image_semantics
    metadata = {
        "prompt_chars": len(system_instruction) + len(user_input),
        "prompt_bytes": len(system_instruction.encode("utf-8")) + len(user_input.encode("utf-8")),
        "system_digest": hashlib.sha256(system_instruction.encode("utf-8")).hexdigest(),
        "prompt_digest": _privacy_digest({"raw_prompt": prompt_payload}),
        "prompt_semantic_digest": _privacy_digest({"semantic_prompt": semantic_payload}),
        "expected_compact_json": expected_compact_json,
        "expected_candidate_transport": expected_candidate_transport,
        "json_block_count": len(blocks),
        "compact_json_block_count": compact_block_count,
        "all_target_json_compact": bool(blocks) and compact_block_count == len(blocks),
        "pretty_json_block_count": pretty_block_count,
        "all_target_json_pretty": bool(blocks) and pretty_block_count == len(blocks),
        "profile_json_block_count": profile_block_count,
        "negative_examples_json_block_count": names.count("negative_examples"),
        "content_batch_json_block_count": names.count("content_batch"),
        "image_input_count": len(image_metadata),
        "image_inputs_digest": _privacy_digest({"image_inputs": image_semantics}),
        "image_data_url_bytes": sum(int(item["data_url_bytes"]) for item in image_metadata),
        "json_blocks": blocks,
    }
    if candidate_transport_audit_enabled:
        context = _candidate_transport_context(
            user_input,
            expected_transport=expected_candidate_transport,
        )
        metadata.update(
            _candidate_prompt_metadata(
                context,
                user_input=user_input,
                image_inputs=image_inputs,
            )
        )
    return metadata


def _normalized_usage(
    provider: str,
    usage: object,
) -> tuple[dict[object, object] | None, str, bool]:
    """Normalize one provider usage payload for paired replay comparisons."""

    if not isinstance(usage, Mapping):
        return None, "unsupported", False
    normalized: dict[object, object] = dict(usage)
    semantics = _CACHE_USAGE_SEMANTICS_BY_PROVIDER.get(provider.strip().lower(), "unsupported")
    if semantics == "unsupported":
        return normalized, semantics, False

    cached = _usage_token_value(normalized, ("cached_input_tokens",))
    normalized["cached_input_tokens"] = cached
    if semantics == "prompt_excludes_cached":
        provider_uncached = _usage_token_value(normalized, ("prompt_tokens", "input_tokens"))
        cache_creation = _usage_token_value(normalized, ("cache_creation_input_tokens",))
        completion = _usage_token_value(normalized, ("completion_tokens", "output_tokens"))
        prompt_tokens = provider_uncached + cached + cache_creation
        normalized["provider_uncached_input_tokens"] = provider_uncached
        normalized["prompt_tokens"] = prompt_tokens
        normalized["total_tokens"] = prompt_tokens + completion
    return normalized, semantics, True


def _normalized_replay_usage(
    response: object,
) -> tuple[dict[object, object] | None, str, bool]:
    """Normalize provider cache accounting for paired replay comparisons."""

    return _normalized_usage(
        str(getattr(response, "provider", "") or ""),
        getattr(response, "usage", None),
    )


def _raw_openai_usage(response: object) -> dict[str, int] | None:
    """Extract usage from one successful OpenAI-protocol wire attempt."""

    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    def value(*names: str) -> int:
        for name in names:
            raw = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                return raw
        return 0

    prompt = value("prompt_tokens", "input_tokens")
    completion = value("completion_tokens", "output_tokens")
    total = value("total_tokens") or (prompt + completion)
    cached = value("prompt_cache_hit_tokens", "cached_input_tokens")
    details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage, Mapping)
        else getattr(usage, "prompt_tokens_details", None)
    )
    if details is None:
        details = (
            usage.get("input_tokens_details")
            if isinstance(usage, Mapping)
            else getattr(usage, "input_tokens_details", None)
        )
    if not cached and details is not None:
        raw_cached = (
            details.get("cached_tokens")
            if isinstance(details, Mapping)
            else getattr(details, "cached_tokens", None)
        )
        if isinstance(raw_cached, int) and not isinstance(raw_cached, bool) and raw_cached >= 0:
            cached = raw_cached
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_input_tokens": cached,
    }


class _ProviderAttemptUsageRecorder:
    """Account successful raw OpenAI-protocol attempts hidden by adapter retries."""

    _METHODS = ("_request_with_retry", "_responses_request_with_retry")

    def __init__(self) -> None:
        self._attempts: dict[str, list[dict[str, object]]] = {}
        self._instrumented_provider_ids: set[int] = set()

    def instrument_registry(self, registry: object) -> None:
        available = getattr(registry, "available_providers", ())
        get_provider = getattr(registry, "get", None)
        provider_type = getattr(registry, "provider_type", None)
        if not callable(get_provider) or not callable(provider_type):
            return
        names = available if isinstance(available, Sequence) else ()
        for name in names:
            adapter = str(provider_type(name) or "").strip().lower()
            if adapter not in {
                "openai",
                "openai_compatible",
                "openrouter",
                "orcarouter",
                "requesty",
                "api_route",
                "cheaperinference",
                "deepseek",
            }:
                continue
            provider = get_provider(name)
            if id(provider) in self._instrumented_provider_ids:
                continue
            self._instrumented_provider_ids.add(id(provider))
            provider_name = str(getattr(provider, "name", "") or adapter).strip().lower()
            for method_name in self._METHODS:
                original = getattr(provider, method_name, None)
                if not callable(original):
                    continue

                async def recorded(
                    *args: object,
                    __original: Any = original,
                    __provider_name: str = provider_name,
                    __method_name: str = method_name,
                    **kwargs: object,
                ) -> object:
                    response = await __original(*args, **kwargs)
                    self._record(
                        call_id=_REPLAY_PROVIDER_CALL_ID.get(),
                        provider=__provider_name,
                        method=__method_name,
                        response=response,
                    )
                    return response

                setattr(provider, method_name, recorded)

    def _record(
        self,
        *,
        call_id: str,
        provider: str,
        method: str,
        response: object,
    ) -> None:
        if not call_id:
            return
        usage, semantics, supported = _normalized_usage(provider, _raw_openai_usage(response))
        self._attempts.setdefault(call_id, []).append(
            {
                "provider": provider,
                "method": method,
                "usage": usage,
                "cache_usage_semantics": semantics,
                "cache_metric_supported": supported,
            }
        )

    def take(self, call_id: str) -> list[dict[str, object]]:
        return self._attempts.pop(call_id, [])


def _aggregate_provider_attempt_usage(
    attempts: Sequence[Mapping[str, object]],
) -> dict[object, object] | None:
    usages = [attempt.get("usage") for attempt in attempts]
    if not usages or any(not isinstance(usage, Mapping) for usage in usages):
        return None
    keys = {
        str(key)
        for usage in usages
        if isinstance(usage, Mapping)
        for key in usage
        if isinstance(key, str)
    }
    return {
        key: sum(
            _usage_token_value(usage, (key,)) for usage in usages if isinstance(usage, Mapping)
        )
        for key in keys
    }


class _DeterministicLLMService:
    """Force temperature=0 so the replay measures prompt changes, not sampling noise.

    The production evaluator samples at the provider default temperature and
    repeated A/A runs show material gateway/model noise. Pinning temperature
    does not eliminate that noise, so the gate still measures an empirical
    repeated A/A envelope around both treatment arms.
    """

    def __init__(
        self,
        inner: object,
        *,
        service: str = "",
        expected_compact_json: bool = False,
        expected_candidate_transport: str = "production-json",
        candidate_transport_audit_enabled: bool = False,
        attempt_usage_recorder: _ProviderAttemptUsageRecorder | None = None,
    ) -> None:
        self._inner = inner
        self._service = service
        self._expected_compact_json = expected_compact_json
        self._expected_candidate_transport = expected_candidate_transport
        self._candidate_transport_audit_enabled = candidate_transport_audit_enabled
        self._attempt_usage_recorder = attempt_usage_recorder
        self._provider_call_sequence = 0
        self.calls: list[dict[str, object]] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def complete_structured_task(self, **kwargs: Any) -> object:
        return await self._complete("complete_structured_task", kwargs)

    async def complete_multimodal_structured_task(self, **kwargs: Any) -> object:
        return await self._complete("complete_multimodal_structured_task", kwargs)

    async def _complete(self, method_name: str, kwargs: dict[str, Any]) -> object:
        kwargs["temperature"] = 0.0
        # Keep the replay production-equivalent. If a gateway burns the 4096
        # budget on hidden reasoning and emits no structured response, the gate
        # must fail rather than masking a production failure with extra headroom.
        kwargs["max_tokens"] = 4096
        method = getattr(self._inner, method_name)
        self._provider_call_sequence += 1
        provider_call_id = f"{self._service}:{self._provider_call_sequence}"
        provider_call_token = _REPLAY_PROVIDER_CALL_ID.set(provider_call_id)
        attribution = dict(_REPLAY_ATTRIBUTION.get())
        candidate_context = (
            _candidate_transport_context(
                str(kwargs.get("user_input") or ""),
                expected_transport=self._expected_candidate_transport,
            )
            if self._candidate_transport_audit_enabled
            else None
        )
        prompt_metadata = _prompt_transport_metadata(
            kwargs,
            expected_compact_json=self._expected_compact_json,
            expected_candidate_transport=self._expected_candidate_transport,
            candidate_transport_audit_enabled=self._candidate_transport_audit_enabled,
        )
        try:
            response = await method(**kwargs)
        except Exception as exc:
            attempts = (
                self._attempt_usage_recorder.take(provider_call_id)
                if self._attempt_usage_recorder is not None
                else []
            )
            retryable_rate_limit = _is_retryable_replay_rate_limit(exc)
            self.calls.append(
                {
                    "service": self._service,
                    **attribution,
                    "method": method_name,
                    "caller": str(kwargs.get("caller") or ""),
                    "provider": "",
                    "instance_id": "",
                    "model": "",
                    "temperature": 0.0,
                    "max_tokens": 4096,
                    **prompt_metadata,
                    "usage": _aggregate_provider_attempt_usage(attempts),
                    "provider_attempt_count": len(attempts),
                    "provider_hidden_retry_count": max(0, len(attempts) - 1),
                    "provider_attempt_usage_complete": bool(attempts)
                    and _aggregate_provider_attempt_usage(attempts) is not None,
                    "provider_attempt_accounting": (
                        "raw_adapter_attempts" if attempts else "logical_response_only"
                    ),
                    "provider_attempts": attempts,
                    "status": "error",
                    "structured_output_parseable": False,
                    "structured_item_count": 0,
                    "reason_field_count": 0,
                    "classification_items": [],
                    **(
                        _result_identity_metadata(
                            None,
                            context=candidate_context,
                            expected_transport=self._expected_candidate_transport,
                        )
                        if self._candidate_transport_audit_enabled
                        else {}
                    ),
                    "error_kind": (
                        "transient_rate_limit" if retryable_rate_limit else type(exc).__name__
                    ),
                }
            )
            raise
        finally:
            _REPLAY_PROVIDER_CALL_ID.reset(provider_call_token)
        usage, cache_usage_semantics, cache_metric_supported = _normalized_replay_usage(response)
        attempts = (
            self._attempt_usage_recorder.take(provider_call_id)
            if self._attempt_usage_recorder is not None
            else []
        )
        aggregate_attempt_usage = _aggregate_provider_attempt_usage(attempts)
        if attempts:
            usage = aggregate_attempt_usage
            cache_semantics = {
                str(attempt.get("cache_usage_semantics") or "") for attempt in attempts
            }
            cache_support = {bool(attempt.get("cache_metric_supported")) for attempt in attempts}
            cache_usage_semantics = (
                next(iter(cache_semantics)) if len(cache_semantics) == 1 else "mixed"
            )
            cache_metric_supported = cache_support == {True}
        self.calls.append(
            {
                "service": self._service,
                **attribution,
                "method": method_name,
                "caller": str(kwargs.get("caller") or ""),
                "provider": str(getattr(response, "provider", "") or ""),
                "instance_id": str(getattr(response, "instance_id", "") or ""),
                "model": str(getattr(response, "model", "") or ""),
                "temperature": 0.0,
                "max_tokens": 4096,
                **prompt_metadata,
                "cache_usage_semantics": cache_usage_semantics,
                "cache_metric_supported": cache_metric_supported,
                "usage": usage,
                "provider_attempt_count": len(attempts) or 1,
                "provider_hidden_retry_count": max(0, len(attempts) - 1),
                "provider_attempt_usage_complete": (
                    aggregate_attempt_usage is not None if attempts else usage is not None
                ),
                "provider_attempt_accounting": (
                    "raw_adapter_attempts" if attempts else "logical_response_only"
                ),
                "provider_attempts": attempts,
                "status": "ok",
                **_structured_output_metadata(
                    response,
                    candidate_positions=(
                        candidate_context.identity_positions
                        if candidate_context is not None
                        else None
                    ),
                ),
                **(
                    _result_identity_metadata(
                        response,
                        context=candidate_context,
                        expected_transport=self._expected_candidate_transport,
                    )
                    if self._candidate_transport_audit_enabled
                    else {}
                ),
            }
        )
        return response


def _usage_token_value(usage: Mapping[object, object], keys: Sequence[str]) -> int:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def _token_usage_summary(calls: Sequence[Mapping[str, object]]) -> dict[str, object]:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    cached_input_tokens = 0
    observed = 0
    cache_metric_observed = 0
    for call in calls:
        usage = call.get("usage")
        if not isinstance(usage, Mapping):
            continue
        observed += 1
        call_input = _usage_token_value(usage, ("prompt_tokens", "input_tokens"))
        call_completion = _usage_token_value(usage, ("completion_tokens", "output_tokens"))
        call_total = _usage_token_value(usage, ("total_tokens",))
        call_cached = _usage_token_value(usage, ("cached_input_tokens",))
        prompt_tokens += call_input
        completion_tokens += call_completion
        total_tokens += call_total or (call_input + call_completion)
        cached_input_tokens += call_cached
        if call.get("cache_metric_supported") is True:
            cache_metric_observed += 1
    evaluated_item_count = sum(
        int(call.get("request_candidate_count") or 0)
        for call in calls
        if call.get("status") == "ok" and call.get("request_kind") == "root"
    )
    evaluated_item_count_basis = "root_request_candidates"
    if evaluated_item_count <= 0:
        evaluated_item_count = sum(
            int(call.get("structured_item_count") or 0)
            for call in calls
            if call.get("status") == "ok"
        )
        evaluated_item_count_basis = "structured_output_items"

    return {
        "call_count": len(calls),
        "provider_attempt_count": sum(
            int(call.get("provider_attempt_count") or 0) for call in calls
        ),
        "provider_hidden_retry_count": sum(
            int(call.get("provider_hidden_retry_count") or 0) for call in calls
        ),
        "successful_call_count": sum(call.get("status") == "ok" for call in calls),
        "error_call_count": sum(call.get("status") != "ok" for call in calls),
        "usage_missing_call_count": len(calls) - observed,
        "evaluated_item_count": evaluated_item_count,
        "evaluated_item_count_basis": evaluated_item_count_basis,
        "prompt_tokens": prompt_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": max(prompt_tokens - cached_input_tokens, 0),
        "cache_metric_observed_call_count": cache_metric_observed,
        "cache_hit_ratio": (cached_input_tokens / prompt_tokens if prompt_tokens else None),
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "prompt_tokens_per_evaluated_item": (
            prompt_tokens / evaluated_item_count if evaluated_item_count else None
        ),
        "cached_input_tokens_per_evaluated_item": (
            cached_input_tokens / evaluated_item_count if evaluated_item_count else None
        ),
        "uncached_input_tokens_per_evaluated_item": (
            max(prompt_tokens - cached_input_tokens, 0) / evaluated_item_count
            if evaluated_item_count
            else None
        ),
        "completion_tokens_per_evaluated_item": (
            completion_tokens / evaluated_item_count if evaluated_item_count else None
        ),
        "total_tokens_per_evaluated_item": (
            total_tokens / evaluated_item_count if evaluated_item_count else None
        ),
    }


def _token_usage_audit(calls: Sequence[Mapping[str, object]]) -> dict[str, object]:
    grouped: dict[tuple[str, int, str, str], list[Mapping[str, object]]] = {}
    for call in calls:
        key = (
            str(call.get("pair_kind") or ""),
            int(call.get("repeat") or 0),
            str(call.get("logical_run") or ""),
            str(call.get("arm") or ""),
        )
        grouped.setdefault(key, []).append(call)
    return {
        "comparison_basis": "treatment A versus B only; A/A controls excluded",
        "logical_runs": [
            {
                "pair_kind": key[0],
                "repeat": key[1],
                "logical_run": key[2],
                "arm": key[3],
                **_token_usage_summary(grouped[key]),
            }
            for key in sorted(grouped)
        ],
        "treatment_comparison": {
            arm: _token_usage_summary(
                [
                    call
                    for call in calls
                    if call.get("pair_kind") == "treatment"
                    and call.get("logical_run") == arm
                    and call.get("arm") == arm
                ]
            )
            for arm in ("A", "B")
        },
    }


def _replay_run_key(call: Mapping[str, object]) -> tuple[str, int, str, str]:
    return (
        str(call.get("pair_kind") or ""),
        int(call.get("repeat") or 0),
        str(call.get("logical_run") or ""),
        str(call.get("arm") or ""),
    )


def _prompt_run_summary(calls: Sequence[Mapping[str, object]]) -> dict[str, object]:
    usage = _token_usage_summary(calls)
    successful_usage_missing = sum(
        call.get("status") == "ok" and not isinstance(call.get("usage"), Mapping) for call in calls
    )
    return {
        **usage,
        "successful_usage_missing_call_count": successful_usage_missing,
        "prompt_chars": sum(int(call.get("prompt_chars") or 0) for call in calls),
        "prompt_bytes": sum(int(call.get("prompt_bytes") or 0) for call in calls),
        "root_call_count": sum(call.get("request_kind") == "root" for call in calls),
        "repair_call_count": sum(call.get("request_kind") == "repair" for call in calls),
        "repair_candidate_count": sum(
            int(call.get("request_candidate_count") or 0)
            for call in calls
            if call.get("request_kind") == "repair"
        ),
        "system_digests": sorted({str(call.get("system_digest") or "") for call in calls}),
        "prompt_semantic_digests": sorted(
            {str(call.get("prompt_semantic_digest") or "") for call in calls}
        ),
    }


def _profile_layer_cache_summary(
    stats: Mapping[str, Mapping[str, object]] | None,
) -> dict[str, object]:
    layers: list[dict[str, object]] = []
    for name, raw in sorted((stats or {}).items()):
        hits = max(0, int(raw.get("hits") or 0))
        misses = max(0, int(raw.get("misses") or 0))
        layers.append(
            {
                "name": name,
                "digest": _privacy_digest(
                    {"profile_layer": name, "source_digest": str(raw.get("digest") or "")}
                ),
                "hits": hits,
                "misses": misses,
            }
        )
    hits = sum(int(layer["hits"]) for layer in layers)
    misses = sum(int(layer["misses"]) for layer in layers)
    return {
        "layers": layers,
        "layer_count": len(layers),
        "hits": hits,
        "misses": misses,
        "hit_ratio": hits / (hits + misses) if hits + misses else None,
    }


def _member_repair_audit(
    grouped: Mapping[tuple[str, int, str, str], Sequence[Mapping[str, object]]],
    *,
    repeats: int,
    experiment_label: str,
) -> dict[str, object]:
    blocking_reasons: list[str] = []
    logical_runs: list[dict[str, object]] = []
    for key in sorted(grouped):
        summary = _prompt_run_summary(grouped[key])
        logical_runs.append(
            {
                "pair_kind": key[0],
                "repeat": key[1],
                "logical_run": key[2],
                "arm": key[3],
                "call_count": summary["call_count"],
                "root_call_count": summary["root_call_count"],
                "repair_call_count": summary["repair_call_count"],
                "repair_candidate_count": summary["repair_candidate_count"],
                "successful_call_count": summary["successful_call_count"],
                "error_call_count": summary["error_call_count"],
            }
        )

    for calls in grouped.values():
        for call in calls:
            kind = str(call.get("request_kind") or "")
            ordinal = call.get("request_ordinal")
            candidate_count = call.get("request_candidate_count")
            if kind not in {"root", "repair"}:
                blocking_reasons.append("evaluation call is missing root/repair attribution")
            if not isinstance(ordinal, int) or ordinal < 0:
                blocking_reasons.append("evaluation call has invalid request ordinal")
            elif (kind == "root" and ordinal != 0) or (kind == "repair" and ordinal == 0):
                blocking_reasons.append("evaluation call has inconsistent repair ordinal")
            if not isinstance(candidate_count, int) or candidate_count <= 0:
                blocking_reasons.append("evaluation call has invalid request candidate count")

    def repair_values(key: tuple[str, int, str, str]) -> tuple[int, int]:
        summary = _prompt_run_summary(grouped.get(key, ()))
        if int(summary["root_call_count"]) <= 0:
            blocking_reasons.append(
                f"{experiment_label} repair audit found no root calls for "
                f"{key[0]} #{key[1]} {key[2]}"
            )
        return int(summary["repair_call_count"]), int(summary["repair_candidate_count"])

    control_call_deltas: list[float] = []
    control_candidate_deltas: list[float] = []
    treatment_call_deltas: list[float] = []
    treatment_candidate_deltas: list[float] = []
    for repeat in range(1, repeats + 1):
        a1_calls, a1_candidates = repair_values(("control", repeat, "A1", "A"))
        a2_calls, a2_candidates = repair_values(("control", repeat, "A2", "A"))
        a_calls, a_candidates = repair_values(("treatment", repeat, "A", "A"))
        b_calls, b_candidates = repair_values(("treatment", repeat, "B", "B"))
        control_call_deltas.append(float(abs(a2_calls - a1_calls)))
        control_candidate_deltas.append(float(abs(a2_candidates - a1_candidates)))
        treatment_call_deltas.append(float(b_calls - a_calls))
        treatment_candidate_deltas.append(float(b_candidates - a_candidates))

    call_ceiling = max(control_call_deltas, default=0.0)
    candidate_ceiling = max(control_candidate_deltas, default=0.0)
    treatment_call_median = median(treatment_call_deltas) if treatment_call_deltas else None
    treatment_candidate_median = (
        median(treatment_candidate_deltas) if treatment_candidate_deltas else None
    )
    if treatment_call_median is None or treatment_call_median > call_ceiling:
        blocking_reasons.append(
            f"{experiment_label} member-repair call amplification exceeded A/A noise"
        )
    if treatment_candidate_median is None or treatment_candidate_median > candidate_ceiling:
        blocking_reasons.append(
            f"{experiment_label} member-repair candidate amplification exceeded A/A noise"
        )
    unique_reasons = list(dict.fromkeys(blocking_reasons))
    return {
        "passed": not unique_reasons,
        "blocking_reasons": unique_reasons,
        "logical_runs": logical_runs,
        "control_repair_call_delta_ceiling": call_ceiling,
        "control_repair_candidate_delta_ceiling": candidate_ceiling,
        "treatment_repair_call_delta_median": treatment_call_median,
        "treatment_repair_candidate_delta_median": treatment_candidate_median,
    }


def _json_minify_repair_audit(
    grouped: Mapping[tuple[str, int, str, str], Sequence[Mapping[str, object]]],
    *,
    repeats: int,
) -> dict[str, object]:
    return _member_repair_audit(
        grouped,
        repeats=repeats,
        experiment_label="json-minify",
    )


def validate_json_minify_transport(
    calls: Sequence[Mapping[str, object]],
    *,
    enabled: bool,
    repeats: int,
    arm_a_profile_cache_stats: Mapping[str, Mapping[str, object]] | None = None,
    arm_b_profile_cache_stats: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Validate whitespace-only treatment evidence without retaining prompts."""

    if not enabled:
        return {"enabled": False, "passed": True, "blocking_reasons": []}

    grouped: dict[tuple[str, int, str, str], list[Mapping[str, object]]] = {}
    for call in calls:
        grouped.setdefault(_replay_run_key(call), []).append(call)
    blocking_reasons: list[str] = []
    repair_audit = _json_minify_repair_audit(grouped, repeats=repeats)
    blocking_reasons.extend(str(reason) for reason in repair_audit["blocking_reasons"])

    system_digests = {str(call.get("system_digest") or "") for call in calls}
    if len(system_digests) != 1 or "" in system_digests:
        blocking_reasons.append("json-minify system prompt digest changed across replay calls")

    for call in calls:
        arm = str(call.get("arm") or "")
        provider = str(call.get("provider") or "").strip().lower()
        if provider in {
            "openai",
            "openai_compatible",
            "openrouter",
            "orcarouter",
            "requesty",
            "api_route",
            "cheaperinference",
            "deepseek",
        }:
            if call.get("provider_attempt_accounting") != "raw_adapter_attempts":
                blocking_reasons.append(
                    "OpenAI-protocol call lacks raw provider-attempt accounting"
                )
            if call.get("provider_attempt_usage_complete") is not True:
                blocking_reasons.append(
                    "OpenAI-protocol call has incomplete provider-attempt usage"
                )
        usage = call.get("usage")
        if isinstance(usage, Mapping) and call.get("cache_metric_supported") is True:
            if "cached_input_tokens" not in usage:
                blocking_reasons.append("supported cache usage omitted cached_input_tokens")
            prompt_tokens = _usage_token_value(usage, ("prompt_tokens", "input_tokens"))
            cached_tokens = _usage_token_value(usage, ("cached_input_tokens",))
            if cached_tokens > prompt_tokens:
                blocking_reasons.append("cached input tokens exceed prompt tokens")
        if arm == "B":
            if call.get("expected_compact_json") is not True:
                blocking_reasons.append("json-minify B call did not use the instance compact flag")
            if call.get("all_target_json_compact") is not True:
                blocking_reasons.append("json-minify B call contains non-compact target JSON")
            if int(call.get("profile_json_block_count") or 0) <= 0:
                blocking_reasons.append("json-minify B call has no profile JSON block")
            if int(call.get("content_batch_json_block_count") or 0) != 1:
                blocking_reasons.append(
                    "json-minify B call has an invalid content-batch block count"
                )
        elif arm == "A":
            if call.get("expected_compact_json") is not False:
                blocking_reasons.append("json-minify A call unexpectedly enabled compact JSON")
            if call.get("all_target_json_pretty") is not True:
                blocking_reasons.append("json-minify A call contains non-pretty target JSON")

    pair_payloads: list[dict[str, object]] = []

    def compare_prompt_runs(
        *,
        pair_kind: str,
        repeat: int,
        left_run: str,
        left_arm: str,
        right_run: str,
        right_arm: str,
        treatment: bool,
    ) -> None:
        left = [
            call
            for call in grouped.get((pair_kind, repeat, left_run, left_arm), ())
            if call.get("request_kind") == "root" and call.get("status") == "ok"
        ]
        right = [
            call
            for call in grouped.get((pair_kind, repeat, right_run, right_arm), ())
            if call.get("request_kind") == "root" and call.get("status") == "ok"
        ]
        left_semantic_counts = Counter(
            str(call.get("prompt_semantic_digest") or "") for call in left
        )
        right_semantic_counts = Counter(
            str(call.get("prompt_semantic_digest") or "") for call in right
        )
        left_semantic = set(left_semantic_counts)
        right_semantic = set(right_semantic_counts)
        semantic_equal = bool(left_semantic_counts) and (
            left_semantic_counts == right_semantic_counts
        )
        if not semantic_equal or "" in left_semantic | right_semantic:
            blocking_reasons.append(
                f"json-minify {pair_kind} #{repeat} root prompt semantics differ"
            )
        raw_changed_only = True
        compact_smaller = True
        runtime_equal = True
        for semantic_digest in left_semantic & right_semantic:
            left_matches = [
                call for call in left if call.get("prompt_semantic_digest") == semantic_digest
            ]
            right_matches = [
                call for call in right if call.get("prompt_semantic_digest") == semantic_digest
            ]
            left_raw = {str(call.get("prompt_digest") or "") for call in left_matches}
            right_raw = {str(call.get("prompt_digest") or "") for call in right_matches}
            if treatment:
                raw_changed_only = (
                    raw_changed_only and bool(left_raw) and left_raw.isdisjoint(right_raw)
                )
                compact_smaller = compact_smaller and (
                    max(int(call.get("prompt_chars") or 0) for call in right_matches)
                    < min(int(call.get("prompt_chars") or 0) for call in left_matches)
                    and max(int(call.get("prompt_bytes") or 0) for call in right_matches)
                    < min(int(call.get("prompt_bytes") or 0) for call in left_matches)
                )
            else:
                raw_changed_only = raw_changed_only and left_raw == right_raw

            def runtime_signatures(
                matching_calls: Sequence[Mapping[str, object]],
            ) -> set[tuple[object, ...]]:
                return {
                    (
                        call.get("method"),
                        call.get("caller"),
                        call.get("temperature"),
                        call.get("max_tokens"),
                        call.get("request_candidate_count"),
                        call.get("image_input_count"),
                        call.get("image_inputs_digest"),
                    )
                    for call in matching_calls
                }

            runtime_equal = runtime_equal and (
                runtime_signatures(left_matches) == runtime_signatures(right_matches)
            )
        if not raw_changed_only:
            blocking_reasons.append(
                f"json-minify {pair_kind} #{repeat} raw prompt bytes violate arm contract"
            )
        if treatment and not compact_smaller:
            blocking_reasons.append(
                f"json-minify treatment #{repeat} compact prompts are not smaller"
            )
        if not runtime_equal:
            blocking_reasons.append(
                f"json-minify {pair_kind} #{repeat} changed provider call settings"
            )
        pair_payloads.append(
            {
                "pair_kind": pair_kind,
                "repeat": repeat,
                "left_run": left_run,
                "right_run": right_run,
                "semantic_equal": semantic_equal,
                "raw_prompt_contract_passed": raw_changed_only,
                "compact_smaller": compact_smaller if treatment else None,
                "runtime_equal": runtime_equal,
                "root_prompt_count": len(left),
                "semantic_digests": sorted(left_semantic & right_semantic),
            }
        )

    for repeat in range(1, repeats + 1):
        compare_prompt_runs(
            pair_kind="control",
            repeat=repeat,
            left_run="A1",
            left_arm="A",
            right_run="A2",
            right_arm="A",
            treatment=False,
        )
        compare_prompt_runs(
            pair_kind="treatment",
            repeat=repeat,
            left_run="A",
            left_arm="A",
            right_run="B",
            right_arm="B",
            treatment=True,
        )

    usage_audit = _token_usage_audit(calls)
    token_pairs: list[dict[str, object]] = []
    prompt_savings: list[float] = []
    total_savings: list[float] = []
    control_cache_deltas: list[float] = []
    treatment_cache_deltas: list[float] = []
    for repeat in range(1, repeats + 1):
        a1 = _token_usage_summary(grouped.get(("control", repeat, "A1", "A"), ()))
        a2 = _token_usage_summary(grouped.get(("control", repeat, "A2", "A"), ()))
        arm_a = _token_usage_summary(grouped.get(("treatment", repeat, "A", "A"), ()))
        arm_b = _token_usage_summary(grouped.get(("treatment", repeat, "B", "B"), ()))
        for arm, summary in (("A", arm_a), ("B", arm_b)):
            successful = int(summary["successful_call_count"])
            if (
                successful <= 0
                or int(summary["error_call_count"]) > 0
                or int(summary["usage_missing_call_count"]) > 0
                or int(summary["prompt_tokens"]) <= 0
            ):
                blocking_reasons.append(
                    f"json-minify treatment #{repeat} arm {arm} lacks complete token usage"
                )
        a_prompt = int(arm_a["prompt_tokens"])
        b_prompt = int(arm_b["prompt_tokens"])
        a_total = int(arm_a["total_tokens"])
        b_total = int(arm_b["total_tokens"])
        prompt_saving = (a_prompt - b_prompt) / a_prompt if a_prompt else None
        total_saving = (a_total - b_total) / a_total if a_total else None
        if prompt_saving is not None:
            prompt_savings.append(prompt_saving)
        if total_saving is not None:
            total_savings.append(total_saving)
        a1_cache = a1["cache_hit_ratio"]
        a2_cache = a2["cache_hit_ratio"]
        a_cache = arm_a["cache_hit_ratio"]
        b_cache = arm_b["cache_hit_ratio"]
        control_cache_complete = all(
            int(summary["successful_call_count"]) > 0
            and int(summary["error_call_count"]) == 0
            and int(summary["usage_missing_call_count"]) == 0
            and int(summary["cache_metric_observed_call_count"])
            == int(summary["successful_call_count"])
            for summary in (a1, a2)
        )
        if control_cache_complete and all(
            isinstance(value, int | float) for value in (a1_cache, a2_cache)
        ):
            control_cache_deltas.append(abs(float(a2_cache) - float(a1_cache)))
        else:
            blocking_reasons.append(f"json-minify control #{repeat} lacks cache ratio evidence")
        treatment_cache_complete = all(
            int(summary["successful_call_count"]) > 0
            and int(summary["error_call_count"]) == 0
            and int(summary["usage_missing_call_count"]) == 0
            and int(summary["cache_metric_observed_call_count"])
            == int(summary["successful_call_count"])
            for summary in (arm_a, arm_b)
        )
        if treatment_cache_complete and all(
            isinstance(value, int | float) for value in (a_cache, b_cache)
        ):
            treatment_cache_deltas.append(float(b_cache) - float(a_cache))
        else:
            blocking_reasons.append(f"json-minify treatment #{repeat} lacks cache ratio evidence")
        token_pairs.append(
            {
                "repeat": repeat,
                "arm_a": arm_a,
                "arm_b": arm_b,
                "prompt_token_savings": prompt_saving,
                "total_token_savings": total_saving,
                "cache_hit_ratio_delta": (
                    float(b_cache) - float(a_cache)
                    if isinstance(a_cache, int | float) and isinstance(b_cache, int | float)
                    else None
                ),
            }
        )

    prompt_savings_median = median(prompt_savings) if len(prompt_savings) == repeats else None
    total_savings_median = median(total_savings) if len(total_savings) == repeats else None
    if (
        prompt_savings_median is None
        or prompt_savings_median < _JSON_MINIFY_PROMPT_TOKEN_SAVINGS_MIN
    ):
        blocking_reasons.append("json-minify prompt-token savings missed the 10% gate")
    if total_savings_median is None or total_savings_median < _JSON_MINIFY_TOTAL_TOKEN_SAVINGS_MIN:
        blocking_reasons.append("json-minify total-token savings missed the 8% gate")
    cache_regression_ceiling = max(control_cache_deltas, default=0.0)
    treatment_cache_delta_median = (
        median(treatment_cache_deltas) if len(treatment_cache_deltas) == repeats else None
    )
    if (
        treatment_cache_delta_median is None
        or treatment_cache_delta_median < -cache_regression_ceiling
    ):
        blocking_reasons.append("json-minify provider cache ratio regressed beyond A/A noise")

    profile_cache = {
        "A": _profile_layer_cache_summary(arm_a_profile_cache_stats),
        "B": _profile_layer_cache_summary(arm_b_profile_cache_stats),
    }
    if int(profile_cache["B"]["layer_count"]) <= 0:
        blocking_reasons.append("json-minify B profile-layer cache evidence is missing")
    if int(profile_cache["B"]["hits"]) <= 0:
        blocking_reasons.append("json-minify B profile-layer cache recorded no reuse")

    prompt_logical_runs = [
        {
            "pair_kind": key[0],
            "repeat": key[1],
            "logical_run": key[2],
            "arm": key[3],
            **_prompt_run_summary(grouped[key]),
        }
        for key in sorted(grouped)
    ]
    treatment_prompt_totals = {
        arm: {
            "prompt_chars": sum(
                int(call.get("prompt_chars") or 0)
                for call in calls
                if call.get("pair_kind") == "treatment" and call.get("arm") == arm
            ),
            "prompt_bytes": sum(
                int(call.get("prompt_bytes") or 0)
                for call in calls
                if call.get("pair_kind") == "treatment" and call.get("arm") == arm
            ),
        }
        for arm in ("A", "B")
    }
    classification_audit = _classification_output_audit(
        calls,
        enabled=True,
        experiment_label="json-minify",
    )
    blocking_reasons.extend(
        str(reason) for reason in classification_audit.get("blocking_reasons", [])
    )
    unique_reasons = list(dict.fromkeys(blocking_reasons))
    return {
        "enabled": True,
        "passed": not unique_reasons,
        "blocking_reasons": unique_reasons,
        "privacy": {
            "raw_prompt_retained": False,
            "recorded_fields": "lengths, counts, usage, and SHA-256 digests only",
        },
        "system_digests": sorted(system_digests),
        "prompt_pairs": pair_payloads,
        "logical_runs": prompt_logical_runs,
        "treatment_prompt_totals": treatment_prompt_totals,
        "token_usage": usage_audit,
        "token_gate": {
            "prompt_savings_min": _JSON_MINIFY_PROMPT_TOKEN_SAVINGS_MIN,
            "total_savings_min": _JSON_MINIFY_TOTAL_TOKEN_SAVINGS_MIN,
            "prompt_savings_median": prompt_savings_median,
            "total_savings_median": total_savings_median,
            "cache_regression_ceiling": cache_regression_ceiling,
            "treatment_cache_delta_median": treatment_cache_delta_median,
            "pairs": token_pairs,
        },
        "profile_layer_cache": profile_cache,
        "repair": repair_audit,
        "classification": classification_audit,
    }


def validate_candidate_transport_experiment(
    calls: Sequence[Mapping[str, object]],
    *,
    experiment: str,
    repeats: int,
) -> dict[str, object]:
    """Validate one isolated candidate-wire experiment and its locked savings gate."""

    raw_config = _CANDIDATE_TRANSPORT_EXPERIMENTS.get(experiment)
    if raw_config is None:
        return {"enabled": False, "passed": True, "blocking_reasons": []}
    arm_transports = {
        "A": str(raw_config["arm_a_transport"]),
        "B": str(raw_config["arm_b_transport"]),
    }
    prompt_savings_min = float(raw_config["prompt_token_savings_min"])
    total_savings_min = float(raw_config["total_token_savings_min"])
    total_savings_strict = bool(raw_config["total_savings_strict"])
    grouped: dict[tuple[str, int, str, str], list[Mapping[str, object]]] = {}
    for call in calls:
        grouped.setdefault(_replay_run_key(call), []).append(call)

    blocking_reasons: list[str] = []
    repair_audit = _member_repair_audit(
        grouped,
        repeats=repeats,
        experiment_label=experiment,
    )
    blocking_reasons.extend(str(reason) for reason in repair_audit["blocking_reasons"])

    system_digests_by_arm: dict[str, list[str]] = {}
    for arm in ("A", "B"):
        digests = sorted(
            {str(call.get("system_digest") or "") for call in calls if call.get("arm") == arm}
        )
        system_digests_by_arm[arm] = digests
        if len(digests) != 1 or "" in digests:
            blocking_reasons.append(f"{experiment} arm {arm} system prompt is not stable")
    if experiment == "row-wire-v1" and system_digests_by_arm["A"] != system_digests_by_arm["B"]:
        blocking_reasons.append("row-wire-v1 changed the local-ID system prompt between arms")

    local_transports = {"sparse-json", "row-wire-v1"}
    for call in calls:
        arm = str(call.get("arm") or "")
        expected_transport = arm_transports.get(arm, "")
        actual_transport = str(call.get("candidate_transport") or "")
        if call.get("expected_candidate_transport") != expected_transport:
            blocking_reasons.append(
                f"{experiment} arm {arm} wrapper expected the wrong candidate transport"
            )
        if actual_transport != expected_transport:
            blocking_reasons.append(
                f"{experiment} arm {arm} rendered {actual_transport or 'unknown'} "
                f"instead of {expected_transport}"
            )
        if call.get("candidate_decode_valid") is not True:
            blocking_reasons.append(f"{experiment} candidate payload did not decode canonically")
        request_count = int(call.get("request_candidate_count") or 0)
        if int(call.get("candidate_item_count") or 0) != request_count:
            blocking_reasons.append(f"{experiment} candidate payload member count drifted")
        if not str(call.get("candidate_canonical_digest") or ""):
            blocking_reasons.append(f"{experiment} candidate canonical digest is missing")
        if not str(call.get("user_context_digest") or ""):
            blocking_reasons.append(f"{experiment} non-candidate prompt digest is missing")
        if not str(call.get("image_payloads_digest") or ""):
            blocking_reasons.append(f"{experiment} ordered image payload digest is missing")
        structured_item_count = int(call.get("structured_item_count") or 0)
        # The candidate-transport gate attributes contract drift to the changed
        # transport only.  Provider noise in an A/A control (or the unchanged
        # treatment A baseline) is preserved in the artifact, but must not be
        # mislabeled as a sparse/row regression.  Arm B still fails closed when
        # any structured result omits the production ``reason`` field.
        is_changed_transport_response = (
            arm == "B" and str(call.get("pair_kind") or "") == "treatment"
        )
        if (
            is_changed_transport_response
            and structured_item_count > 0
            and int(call.get("reason_field_count") or 0) != structured_item_count
        ):
            blocking_reasons.append(f"{experiment} response changed the reason output contract")
        raw_classification_items = call.get("classification_items")
        if structured_item_count > 0 and (
            not isinstance(raw_classification_items, list)
            or len(raw_classification_items) != structured_item_count
        ):
            blocking_reasons.append(f"{experiment} response classification metadata is incomplete")
        usage = call.get("usage")
        if not isinstance(usage, Mapping):
            blocking_reasons.append(f"{experiment} provider call lacks billable usage")
        else:
            call_prompt_tokens = _usage_token_value(
                usage,
                ("prompt_tokens", "input_tokens"),
            )
            call_completion_tokens = _usage_token_value(
                usage,
                ("completion_tokens", "output_tokens"),
            )
            call_total_tokens = _usage_token_value(usage, ("total_tokens",)) or (
                call_prompt_tokens + call_completion_tokens
            )
            if call_prompt_tokens <= 0 or call_total_tokens <= 0:
                blocking_reasons.append(
                    f"{experiment} provider call reported zero or incomplete billable usage"
                )
        if expected_transport in local_transports:
            if call.get("candidate_local_id_coverage_complete") is not True:
                blocking_reasons.append(f"{experiment} local-ID request coverage is incomplete")
            if int(call.get("candidate_global_identity_field_count") or 0) != 0:
                blocking_reasons.append(f"{experiment} leaked a global identity field")
            if int(call.get("candidate_url_field_count") or 0) != 0:
                blocking_reasons.append(f"{experiment} leaked a URL field")
            if call.get("image_anchor_coverage_complete") is not True:
                blocking_reasons.append(f"{experiment} image/local-ID anchors do not match")
            if structured_item_count > 0 and call.get("result_identity_contract") != "local-id":
                blocking_reasons.append(f"{experiment} response did not use local IDs")
            if call.get("result_local_id_binding_safe") is not True:
                blocking_reasons.append(f"{experiment} response local-ID binding was unsafe")
        elif structured_item_count > 0 and call.get("result_identity_contract") != "global-id":
            blocking_reasons.append(
                f"{experiment} production response identity contract was not verified"
            )

        provider = str(call.get("provider") or "").strip().lower()
        if provider in {
            "openai",
            "openai_compatible",
            "openrouter",
            "orcarouter",
            "requesty",
            "api_route",
            "cheaperinference",
            "deepseek",
        }:
            if call.get("provider_attempt_accounting") != "raw_adapter_attempts":
                blocking_reasons.append(
                    f"{experiment} OpenAI-protocol call lacks raw provider-attempt accounting"
                )
            if call.get("provider_attempt_usage_complete") is not True:
                blocking_reasons.append(
                    f"{experiment} OpenAI-protocol call has incomplete provider-attempt usage"
                )

    pair_payloads: list[dict[str, object]] = []

    def successful_roots(key: tuple[str, int, str, str]) -> list[Mapping[str, object]]:
        return [
            call
            for call in grouped.get(key, ())
            if call.get("request_kind") == "root" and call.get("status") == "ok"
        ]

    def semantic_counter(items: Sequence[Mapping[str, object]]) -> Counter[tuple[object, ...]]:
        return Counter(
            (
                call.get("candidate_canonical_digest"),
                call.get("user_context_digest"),
                call.get("image_payloads_digest"),
                call.get("method"),
                call.get("caller"),
                call.get("temperature"),
                call.get("max_tokens"),
                call.get("request_candidate_count"),
                call.get("image_input_count"),
            )
            for call in items
        )

    def compare_pair(
        *,
        pair_kind: str,
        repeat: int,
        left_run: str,
        right_run: str,
        treatment: bool,
    ) -> None:
        left = successful_roots((pair_kind, repeat, left_run, "A"))
        right_arm = "B" if treatment else "A"
        right = successful_roots((pair_kind, repeat, right_run, right_arm))
        semantic_equal = bool(left) and semantic_counter(left) == semantic_counter(right)
        if not semantic_equal:
            blocking_reasons.append(
                f"{experiment} {pair_kind} #{repeat} canonical candidate semantics differ"
            )
        left_raw = Counter(
            str(call.get("candidate_contract_prompt_digest") or call.get("prompt_digest") or "")
            for call in left
        )
        right_raw = Counter(
            str(call.get("candidate_contract_prompt_digest") or call.get("prompt_digest") or "")
            for call in right
        )
        raw_contract_passed = bool(left_raw)
        if treatment:
            raw_contract_passed = raw_contract_passed and set(left_raw).isdisjoint(right_raw)
        else:
            raw_contract_passed = raw_contract_passed and left_raw == right_raw
        if not raw_contract_passed:
            blocking_reasons.append(
                f"{experiment} {pair_kind} #{repeat} raw prompt contract failed"
            )

        payload_smaller = True
        if treatment:
            left_by_canonical: dict[str, list[Mapping[str, object]]] = {}
            right_by_canonical: dict[str, list[Mapping[str, object]]] = {}
            for call in left:
                left_by_canonical.setdefault(
                    str(call.get("candidate_canonical_digest") or ""), []
                ).append(call)
            for call in right:
                right_by_canonical.setdefault(
                    str(call.get("candidate_canonical_digest") or ""), []
                ).append(call)
            for digest in set(left_by_canonical) & set(right_by_canonical):
                left_matches = left_by_canonical[digest]
                right_matches = right_by_canonical[digest]
                payload_smaller = payload_smaller and (
                    max(int(call.get("candidate_payload_bytes") or 0) for call in right_matches)
                    < min(int(call.get("candidate_payload_bytes") or 0) for call in left_matches)
                    and max(int(call.get("prompt_bytes") or 0) for call in right_matches)
                    < min(int(call.get("prompt_bytes") or 0) for call in left_matches)
                )
            payload_smaller = payload_smaller and bool(
                set(left_by_canonical) & set(right_by_canonical)
            )
            if not payload_smaller:
                blocking_reasons.append(
                    f"{experiment} treatment #{repeat} candidate transport is not smaller"
                )
        pair_payloads.append(
            {
                "pair_kind": pair_kind,
                "repeat": repeat,
                "semantic_equal": semantic_equal,
                "raw_prompt_contract_passed": raw_contract_passed,
                "candidate_transport_smaller": payload_smaller if treatment else None,
                "root_prompt_count": len(left),
            }
        )

    for repeat in range(1, repeats + 1):
        compare_pair(
            pair_kind="control",
            repeat=repeat,
            left_run="A1",
            right_run="A2",
            treatment=False,
        )
        compare_pair(
            pair_kind="treatment",
            repeat=repeat,
            left_run="A",
            right_run="B",
            treatment=True,
        )

    token_pairs: list[dict[str, object]] = []
    prompt_savings: list[float] = []
    total_savings: list[float] = []
    expected_keys = [
        key
        for repeat in range(1, repeats + 1)
        for key in (
            ("control", repeat, "A1", "A"),
            ("control", repeat, "A2", "A"),
            ("treatment", repeat, "A", "A"),
            ("treatment", repeat, "B", "B"),
        )
    ]
    for key in expected_keys:
        summary = _token_usage_summary(grouped.get(key, ()))
        if (
            int(summary["successful_call_count"]) <= 0
            or int(summary["usage_missing_call_count"]) > 0
            or int(summary["prompt_tokens"]) <= 0
        ):
            blocking_reasons.append(
                f"{experiment} {key[0]} #{key[1]} {key[2]} lacks complete billable usage"
            )
    for repeat in range(1, repeats + 1):
        arm_a = _token_usage_summary(grouped.get(("treatment", repeat, "A", "A"), ()))
        arm_b = _token_usage_summary(grouped.get(("treatment", repeat, "B", "B"), ()))
        a_prompt = int(arm_a["prompt_tokens"])
        b_prompt = int(arm_b["prompt_tokens"])
        a_total = int(arm_a["total_tokens"])
        b_total = int(arm_b["total_tokens"])
        prompt_saving = (a_prompt - b_prompt) / a_prompt if a_prompt else None
        total_saving = (a_total - b_total) / a_total if a_total else None
        if prompt_saving is not None:
            prompt_savings.append(prompt_saving)
        if total_saving is not None:
            total_savings.append(total_saving)
        token_pairs.append(
            {
                "repeat": repeat,
                "arm_a": arm_a,
                "arm_b": arm_b,
                "prompt_token_savings": prompt_saving,
                "total_token_savings": total_saving,
            }
        )
    prompt_savings_median = median(prompt_savings) if len(prompt_savings) == repeats else None
    total_savings_median = median(total_savings) if len(total_savings) == repeats else None
    if prompt_savings_median is None or prompt_savings_median < prompt_savings_min:
        blocking_reasons.append(
            f"{experiment} prompt-token savings missed the {prompt_savings_min:.0%} gate"
        )
    total_gate_passed = total_savings_median is not None and (
        total_savings_median > total_savings_min
        if total_savings_strict
        else total_savings_median >= total_savings_min
    )
    if not total_gate_passed:
        comparator = ">" if total_savings_strict else ">="
        blocking_reasons.append(
            f"{experiment} total-token savings missed the {comparator} {total_savings_min:.0%} gate"
        )

    classification_audit = _classification_output_audit(
        calls,
        enabled=True,
        experiment_label=experiment,
    )
    blocking_reasons.extend(
        str(reason) for reason in classification_audit.get("blocking_reasons", [])
    )
    logical_runs = [
        {
            "pair_kind": key[0],
            "repeat": key[1],
            "logical_run": key[2],
            "arm": key[3],
            **_prompt_run_summary(grouped[key]),
        }
        for key in sorted(grouped)
    ]
    unique_reasons = list(dict.fromkeys(blocking_reasons))
    return {
        "enabled": True,
        "experiment": experiment,
        "arm_transports": arm_transports,
        "passed": not unique_reasons,
        "blocking_reasons": unique_reasons,
        "privacy": {
            "raw_prompt_retained": False,
            "raw_candidate_payload_retained": False,
            "digests": "run-salted for all prompt, candidate, image, and identity data",
        },
        "system_digests_by_arm": system_digests_by_arm,
        "system_comparison": (
            "identical local-ID contract"
            if experiment == "row-wire-v1"
            else "arm-specific identity contract"
        ),
        "prompt_pairs": pair_payloads,
        "logical_runs": logical_runs,
        "token_usage": _token_usage_audit(calls),
        "token_gate": {
            "prompt_savings_min": prompt_savings_min,
            "total_savings_min": total_savings_min,
            "total_savings_strict": total_savings_strict,
            "prompt_savings_median": prompt_savings_median,
            "total_savings_median": total_savings_median,
            "pairs": token_pairs,
        },
        "cache_diagnostics_only": True,
        "repair": repair_audit,
        "classification": classification_audit,
    }


def _classification_items_by_run(
    calls: Sequence[Mapping[str, object]],
) -> dict[tuple[str, int, str, str], dict[str, Mapping[str, object]]]:
    grouped: dict[tuple[str, int, str, str], dict[str, Mapping[str, object]]] = {}
    call_positions: Counter[tuple[str, int, str, str]] = Counter()
    for call in calls:
        if call.get("status") != "ok":
            continue
        run_key = (
            str(call.get("pair_kind") or ""),
            int(call.get("repeat") or 0),
            str(call.get("logical_run") or ""),
            str(call.get("arm") or ""),
        )
        raw_items = call.get("classification_items")
        if not isinstance(raw_items, list):
            continue
        call_position = call_positions[run_key]
        call_positions[run_key] += 1
        run_items = grouped.setdefault(run_key, {})
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            candidate_digest = str(raw_item.get("candidate_key_digest") or "") or (
                f"position:{call_position}:{int(raw_item.get('position') or 0)}"
            )
            fields = raw_item.get("fields")
            if isinstance(fields, Mapping):
                run_items[candidate_digest] = fields
    return grouped


def _classification_field_metadata(
    fields: Mapping[str, object] | None,
    field: str,
) -> tuple[str, bool]:
    raw = fields.get(field) if fields is not None else None
    if not isinstance(raw, Mapping):
        return "", False
    return str(raw.get("digest") or ""), bool(raw.get("nonempty"))


def _classification_run_summary(
    key: tuple[str, int, str, str],
    items: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    item_count = len(items)
    return {
        "pair_kind": key[0],
        "repeat": key[1],
        "logical_run": key[2],
        "arm": key[3],
        "item_count": item_count,
        "fields": {
            field: {
                "presence_rate": (
                    sum(
                        bool(_classification_field_metadata(item, field)[0])
                        for item in items.values()
                    )
                    / item_count
                    if item_count
                    else 0.0
                ),
                "fill_rate": (
                    sum(_classification_field_metadata(item, field)[1] for item in items.values())
                    / item_count
                    if item_count
                    else 0.0
                ),
                "values_digest": _digest(
                    sorted(
                        _classification_field_metadata(item, field)[0] for item in items.values()
                    )
                ),
            }
            for field in _REPLAY_CLASSIFICATION_FIELDS
        },
    }


def _classification_pair_summary(
    *,
    kind: str,
    repeat: int,
    left: Mapping[str, Mapping[str, object]],
    right: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    candidate_keys = sorted(set(left) | set(right))
    item_count = len(candidate_keys)
    fields: dict[str, object] = {}
    for field in _REPLAY_CLASSIFICATION_FIELDS:
        agreement = left_present = right_present = left_filled = right_filled = 0
        for candidate_key in candidate_keys:
            left_digest, left_nonempty = _classification_field_metadata(
                left.get(candidate_key), field
            )
            right_digest, right_nonempty = _classification_field_metadata(
                right.get(candidate_key), field
            )
            left_present += bool(left_digest)
            right_present += bool(right_digest)
            left_filled += left_nonempty
            right_filled += right_nonempty
            agreement += bool(left_digest and left_digest == right_digest)
        fields[field] = {
            "agreement_rate": agreement / item_count if item_count else 0.0,
            "left_presence_rate": left_present / item_count if item_count else 0.0,
            "right_presence_rate": right_present / item_count if item_count else 0.0,
            "left_fill_rate": left_filled / item_count if item_count else 0.0,
            "right_fill_rate": right_filled / item_count if item_count else 0.0,
        }
    return {
        "kind": kind,
        "repeat": repeat,
        "item_count": item_count,
        "fields": fields,
    }


def _classification_output_audit(
    calls: Sequence[Mapping[str, object]],
    *,
    enabled: bool,
    experiment_label: str = "reason-off",
) -> dict[str, object]:
    runs = _classification_items_by_run(calls)
    repeats = sorted(
        {int(call.get("repeat") or 0) for call in calls if int(call.get("repeat") or 0) > 0}
    )
    control_pairs = [
        _classification_pair_summary(
            kind="control",
            repeat=repeat,
            left=runs.get(("control", repeat, "A1", "A"), {}),
            right=runs.get(("control", repeat, "A2", "A"), {}),
        )
        for repeat in repeats
    ]
    treatment_pairs = [
        _classification_pair_summary(
            kind="treatment",
            repeat=repeat,
            left=runs.get(("treatment", repeat, "A", "A"), {}),
            right=runs.get(("treatment", repeat, "B", "B"), {}),
        )
        for repeat in repeats
    ]
    blocking_reasons: list[str] = []
    gate: dict[str, object] = {}
    if enabled:
        if not repeats:
            blocking_reasons.append(
                f"{experiment_label} classification audit found no attributed repeats"
            )
        expected_runs = [
            key
            for repeat in repeats
            for key in (
                ("control", repeat, "A1", "A"),
                ("control", repeat, "A2", "A"),
                ("treatment", repeat, "A", "A"),
                ("treatment", repeat, "B", "B"),
            )
        ]
        missing_run_count = sum(not runs.get(key) for key in expected_runs)
        if missing_run_count:
            blocking_reasons.append(
                f"{experiment_label} classification audit has {missing_run_count} empty logical runs"
            )
        if control_pairs and treatment_pairs:
            for field in _REPLAY_CLASSIFICATION_FIELDS:
                control_agreement_floor = _nearest_rank_percentile(
                    [float(pair["fields"][field]["agreement_rate"]) for pair in control_pairs],
                    0.05,
                )
                treatment_agreement_median = median(
                    float(pair["fields"][field]["agreement_rate"]) for pair in treatment_pairs
                )
                treatment_a_fill_median = median(
                    float(pair["fields"][field]["left_fill_rate"]) for pair in treatment_pairs
                )
                treatment_b_fill_median = median(
                    float(pair["fields"][field]["right_fill_rate"]) for pair in treatment_pairs
                )
                treatment_a_presence_median = median(
                    float(pair["fields"][field]["left_presence_rate"]) for pair in treatment_pairs
                )
                treatment_b_presence_median = median(
                    float(pair["fields"][field]["right_presence_rate"]) for pair in treatment_pairs
                )
                agreement_passed = treatment_agreement_median >= control_agreement_floor
                presence_passed = treatment_b_presence_median >= treatment_a_presence_median - 0.03
                fill_passed = (
                    field == "franchise_key"
                    or treatment_b_fill_median >= treatment_a_fill_median - 0.03
                )
                gate[field] = {
                    "control_agreement_floor": control_agreement_floor,
                    "treatment_agreement_median": treatment_agreement_median,
                    "treatment_a_fill_median": treatment_a_fill_median,
                    "treatment_b_fill_median": treatment_b_fill_median,
                    "passed": agreement_passed and presence_passed and fill_passed,
                }
                if not agreement_passed:
                    blocking_reasons.append(
                        f"{experiment_label} {field} agreement fell below the A/A noise floor"
                    )
                if not fill_passed:
                    blocking_reasons.append(f"{experiment_label} {field} fill rate regressed")
                if not presence_passed:
                    blocking_reasons.append(f"{experiment_label} {field} presence rate regressed")
    return {
        "passed": not blocking_reasons,
        "runs": [_classification_run_summary(key, items) for key, items in sorted(runs.items())],
        "control_pairs": control_pairs,
        "treatment_pairs": treatment_pairs,
        "gate": gate,
        "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
        "cap_drop_audit": "not measured: replay stops before downstream cross-batch caps",
    }


def validate_reason_off_outputs(
    calls: Sequence[Mapping[str, object]],
    *,
    enabled: bool,
) -> dict[str, object]:
    """Fail closed unless every successful reason-off B output omits reason."""

    arm_calls = {arm: [call for call in calls if call.get("arm") == arm] for arm in ("A", "B")}
    classification_audit = _classification_output_audit(calls, enabled=enabled)
    blocking_reasons: list[str] = []
    if enabled:
        successful_b = [call for call in arm_calls["B"] if call.get("status") == "ok"]
        if not successful_b:
            blocking_reasons.append("reason-off arm B produced no successful LLM responses")
        if any(
            not call.get("structured_output_parseable")
            or int(call.get("structured_item_count") or 0) == 0
            for call in successful_b
        ):
            blocking_reasons.append(
                "reason-off arm B returned successful responses without verifiable scored JSON"
            )
        if any(int(call.get("reason_field_count") or 0) for call in successful_b):
            blocking_reasons.append("reason-off arm B returned one or more reason fields")
        blocking_reasons.extend(
            str(reason) for reason in classification_audit.get("blocking_reasons", [])
        )
    return {
        "enabled": enabled,
        "passed": not blocking_reasons,
        "reason_field_count": {
            arm: sum(int(call.get("reason_field_count") or 0) for call in arm_calls[arm])
            for arm in ("A", "B")
        },
        "token_usage": _token_usage_audit(calls),
        "classification": classification_audit,
        "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
    }


def _expected_evaluation_instance(service: object) -> str:
    inner = getattr(service, "_inner", service)
    resolve_chain = getattr(inner, "_resolve_module_chain", None)
    if callable(resolve_chain):
        chain = resolve_chain("discovery.evaluate_batch")
        if isinstance(chain, Sequence) and not isinstance(chain, str | bytes) and chain:
            return str(chain[0]).strip().lower()
    resolve_override = getattr(inner, "_resolve_module_override", None)
    if callable(resolve_override):
        override = resolve_override("discovery.evaluate_batch")
        if isinstance(override, tuple) and override:
            return str(override[0]).strip().lower()
    registry = getattr(inner, "registry", None)
    return str(getattr(registry, "default_provider", "") or "").strip().lower()


def validate_replay_routes(
    calls: Sequence[Mapping[str, object]],
    *,
    repeats: int,
    model_override: ModelOverride | None,
    expected_control_instance: str = "",
    expected_treatment_instance: str = "",
) -> dict[str, object]:
    """Require one actual route per logical run and arm-equivalent routing."""

    expected: dict[tuple[str, int, str], str] = {}
    for repeat in range(1, repeats + 1):
        expected[("control", repeat, "A1")] = "A"
        expected[("control", repeat, "A2")] = "A"
        expected[("treatment", repeat, "A")] = "A"
        expected[("treatment", repeat, "B")] = "B"

    grouped: dict[tuple[str, int, str], list[Mapping[str, object]]] = {}
    blocking_reasons: list[str] = []
    for call in calls:
        key = (
            str(call.get("pair_kind") or ""),
            _to_int(call.get("repeat")),
            str(call.get("logical_run") or ""),
        )
        if key not in expected:
            blocking_reasons.append(f"LLM call has missing/invalid replay attribution: {key!r}")
            continue
        if str(call.get("arm") or "") != expected[key]:
            blocking_reasons.append(f"LLM call arm attribution mismatch for {key!r}")
        grouped.setdefault(key, []).append(call)

    run_payloads: list[dict[str, object]] = []
    route_by_run: dict[tuple[str, int, str], tuple[str, str, str]] = {}
    for key, expected_arm in expected.items():
        run_calls = grouped.get(key, [])
        if not run_calls:
            blocking_reasons.append(f"logical run {key!r} emitted no LLM call")
            continue
        successful_calls = [call for call in run_calls if call.get("status") == "ok"]
        failed_calls = [call for call in run_calls if call.get("status") != "ok"]
        recovered_rate_limit_calls = [
            call for call in failed_calls if call.get("error_kind") == "transient_rate_limit"
        ]
        fatal_failed_calls = [
            call for call in failed_calls if call.get("error_kind") != "transient_rate_limit"
        ]
        routes = {
            (
                str(call.get("provider") or "").strip(),
                str(call.get("instance_id") or "").strip(),
                str(call.get("model") or "").strip(),
            )
            for call in successful_calls
        }
        if fatal_failed_calls:
            blocking_reasons.append(f"logical run {key!r} contains a fatal failed LLM call")
        if failed_calls and len(recovered_rate_limit_calls) != len(failed_calls):
            blocking_reasons.append(f"logical run {key!r} contains an unaudited failed LLM call")
        if not successful_calls:
            blocking_reasons.append(f"logical run {key!r} emitted no successful LLM call")
            continue
        if any(not all(route) for route in routes):
            blocking_reasons.append(f"logical run {key!r} contains an empty actual route")
        if len(routes) != 1:
            blocking_reasons.append(f"logical run {key!r} mixed {len(routes)} actual routes")
        route = next(iter(routes))
        route_by_run[key] = route
        run_payloads.append(
            {
                "pair_kind": key[0],
                "repeat": key[1],
                "logical_run": key[2],
                "arm": expected_arm,
                "call_count": len(run_calls),
                "successful_call_count": len(successful_calls),
                "recovered_rate_limit_call_count": len(recovered_rate_limit_calls),
                "route": {
                    "provider": route[0],
                    "instance_id": route[1],
                    "model": route[2],
                },
            }
        )

    baseline_routes = {
        route
        for key, route in route_by_run.items()
        if key[0] == "control" or (key[0] == "treatment" and key[2] == "A")
    }
    if len(baseline_routes) != 1:
        blocking_reasons.append(
            "control A/A and treatment A did not use one identical actual route"
        )
    elif expected_control_instance:
        baseline_route = next(iter(baseline_routes))
        if baseline_route[1] != expected_control_instance:
            blocking_reasons.append(
                "control route unexpectedly failed over from configured instance "
                f"{expected_control_instance!r} to {baseline_route[1]!r}"
            )

    treatment_b_routes = {
        route for key, route in route_by_run.items() if key[0] == "treatment" and key[2] == "B"
    }
    if len(treatment_b_routes) != 1:
        blocking_reasons.append("treatment B did not use one stable actual route")
    else:
        treatment_b_route = next(iter(treatment_b_routes))
        if expected_treatment_instance and treatment_b_route[1] != expected_treatment_instance:
            blocking_reasons.append(
                "treatment route unexpectedly failed over from configured instance "
                f"{expected_treatment_instance!r} to {treatment_b_route[1]!r}"
            )
    if len(treatment_b_routes) == 1:
        treatment_b_route = next(iter(treatment_b_routes))
        if model_override is None:
            if baseline_routes != treatment_b_routes:
                blocking_reasons.append("non-model experiment drifted route between arms A and B")
        else:
            if model_override.model:
                if (
                    treatment_b_route[0] != model_override.provider
                    or treatment_b_route[2] != model_override.model
                ):
                    blocking_reasons.append(
                        "legacy model treatment did not use the requested provider/model"
                    )
            elif treatment_b_route[1] != model_override.provider:
                blocking_reasons.append(
                    "instance-routed model treatment did not use the requested instance"
                )

    unique_reasons = list(dict.fromkeys(blocking_reasons))
    return {
        "passed": not unique_reasons,
        "blocking_reasons": unique_reasons,
        "recovered_rate_limit_call_count": sum(
            int(run.get("recovered_rate_limit_call_count") or 0) for run in run_payloads
        ),
        "logical_runs": run_payloads,
    }


def _build_engine(
    llm_service: object,
    config: object,
    *,
    compact_profile: bool,
    negative_examples: list[dict[str, object]] | None,
    legacy_profile: bool,
    embedding_service: object | None,
    recall_audit: ReplayRecallAudit | None = None,
    compact_evaluation_json: bool = False,
    evaluation_candidate_transport: str = "production",
) -> object:
    from openbiliclaw.discovery.engine import (
        ContentDiscoveryEngine,
        DiscoveryConcurrencyController,
        _BatchRelatedInterestRecall,
        _RelatedInterestRecall,
    )
    from openbiliclaw.discovery.strategies._utils import build_profile_summary

    class ReplayDiscoveryEngine(ContentDiscoveryEngine):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._replay_negative_examples = negative_examples
            self._replay_compact_profile = compact_profile
            self._replay_legacy_profile = legacy_profile
            self._replay_recall_audit = recall_audit
            super().__init__(*args, **kwargs)

        def _get_eval_cache_entry(self, cache_key: str) -> None:
            return None

        def _set_eval_cache_entry(self, cache_key: str, entry: object) -> None:
            return None

        async def _evaluate_batch(self, *args: Any, **kwargs: Any) -> list[float]:
            request_token = _REPLAY_EVAL_REQUEST_COUNTER.set({"ordinal": 0})
            try:
                return await super()._evaluate_batch(*args, **kwargs)
            finally:
                _REPLAY_EVAL_REQUEST_COUNTER.reset(request_token)

        async def _evaluate_batch_once(
            self,
            batch: list[Any],
            *args: Any,
            **kwargs: Any,
        ) -> list[float | None]:
            request_counter = _REPLAY_EVAL_REQUEST_COUNTER.get()
            ordinal = request_counter["ordinal"] if request_counter is not None else 0
            if request_counter is not None:
                request_counter["ordinal"] = ordinal + 1
            attribution = {
                **dict(_REPLAY_ATTRIBUTION.get()),
                "request_kind": "root" if ordinal == 0 else "repair",
                "request_ordinal": ordinal,
                "request_candidate_count": len(batch),
            }
            attribution_token = _REPLAY_ATTRIBUTION.set(attribution)
            try:
                return await super()._evaluate_batch_once(batch, *args, **kwargs)
            finally:
                _REPLAY_ATTRIBUTION.reset(attribution_token)

        def _get_negative_exemplars(self) -> list[dict[str, object]] | None:
            examples = getattr(self, "_replay_negative_examples", None)
            if not examples:
                return None
            return [dict(item) for item in examples]

        def _recent_viewed_content_keys(self) -> set[str]:
            return set()

        def _evaluation_profile_summary(self, profile: object) -> dict[str, object]:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return build_profile_summary(profile)
            # Production owns the compact view. Applying the transform again
            # would let replay drift if it ever stops being idempotent.
            return ContentDiscoveryEngine._evaluation_profile_summary(profile)

        async def _related_interests_for_content(
            self,
            content: object,
            profile: object,
            *,
            top_k: int = 3,
        ) -> list[str]:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return []
            return await super()._related_interests_for_content(content, profile, top_k=top_k)

        async def _related_interests_for_content_result(
            self,
            content: object,
            profile: object,
            *,
            top_k: int = 3,
        ) -> _RelatedInterestRecall:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return _RelatedInterestRecall([], True)
            result = await super()._related_interests_for_content_result(
                content,
                profile,
                top_k=top_k,
            )
            audit = getattr(self, "_replay_recall_audit", None)
            if isinstance(audit, ReplayRecallAudit):
                audit.record_single(result.related, complete=result.complete)
            return result

        async def _related_interests_for_batch(
            self,
            contents: Sequence[object],
            profile: object,
            *,
            top_k: int = 3,
        ) -> dict[int, list[str]]:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return {}
            return await super()._related_interests_for_batch(contents, profile, top_k=top_k)

        async def _related_interests_for_batch_result(
            self,
            contents: Sequence[object],
            profile: object,
            *,
            top_k: int = 3,
        ) -> _BatchRelatedInterestRecall:
            if bool(getattr(self, "_replay_legacy_profile", False)):
                return _BatchRelatedInterestRecall({}, frozenset(range(len(contents))))
            result = await super()._related_interests_for_batch_result(
                contents,
                profile,
                top_k=top_k,
            )
            audit = getattr(self, "_replay_recall_audit", None)
            if isinstance(audit, ReplayRecallAudit):
                audit.record_batch(
                    result.related_by_index,
                    candidate_count=len(contents),
                    complete_candidate_count=len(result.complete_indices),
                )
            return result

    discovery_cfg = getattr(config, "discovery", None)
    return ReplayDiscoveryEngine(
        llm_service=llm_service,
        database=None,
        concurrency=DiscoveryConcurrencyController(llm_evaluation_concurrency=2),
        embedding_service=embedding_service,
        multimodal_evaluation_enabled=bool(
            getattr(discovery_cfg, "multimodal_evaluation_enabled", False)
        ),
        multimodal_batch_size=int(getattr(discovery_cfg, "multimodal_batch_size", 8)),
        multimodal_image_max_px=int(getattr(discovery_cfg, "multimodal_image_max_px", 384)),
        multimodal_image_quality=int(getattr(discovery_cfg, "multimodal_image_quality", 72)),
        multimodal_image_timeout_seconds=int(
            getattr(discovery_cfg, "multimodal_image_timeout_seconds", 6)
        ),
        compact_evaluation_json=compact_evaluation_json,
        evaluation_candidate_transport=evaluation_candidate_transport,
        eval_prefilter_mode="off",
    )


def _rows_to_contents(rows: Sequence[Mapping[str, Any]]) -> list[Any]:
    from openbiliclaw.discovery.candidate_pool import row_to_discovered_content

    return [row_to_discovered_content(dict(row)) for row in rows]


async def _score_contents(
    engine: object,
    contents: Sequence[Any],
    profile: object,
    *,
    source_context: str,
) -> list[float]:
    if not contents:
        return []
    # Match the current API coordinator's production claim size (30), not the
    # engine's 90-item hard cap: each chunk carries
    # its own recall-embedding warm-up, so smaller chunks keep the per-chunk
    # timeout budget meaningful.
    hard_cap = max(1, int(getattr(engine, "_EVALUATE_BATCH_HARD_CAP", 90) or 90))
    hard_cap = min(hard_cap, _DEFAULT_BATCH_SIZE)
    scores: list[float] = []
    evaluate = getattr(engine, "evaluate_content_batch", None)
    if not callable(evaluate):
        raise RuntimeError("Replay engine does not expose evaluate_content_batch")
    for start in range(0, len(contents), hard_cap):
        chunk = list(contents[start : start + hard_cap])
        initial_evaluation_state = [
            {
                field: getattr(content, field)
                for field in _REPLAY_EVALUATION_OUTPUT_FIELDS
                if hasattr(content, field)
            }
            for content in chunk
        ]
        # Hard deadline per chunk: a stalled provider/gateway must fail the
        # gate run loudly instead of hanging it forever (observed in prod:
        # one stuck upstream session blocked an un-timeboxed call for 2h+).
        for attempt in range(len(RATE_LIMIT_RETRY_DELAYS_SECONDS) + 1):
            try:
                chunk_scores = await asyncio.wait_for(
                    evaluate(
                        chunk,
                        profile,
                        source_context=source_context,
                        batch_size=_DEFAULT_BATCH_SIZE,
                    ),
                    timeout=CHUNK_TIMEOUT_SECONDS,
                )
                break
            except TimeoutError as exc:
                raise RuntimeError(
                    f"Evaluation chunk timed out after {CHUNK_TIMEOUT_SECONDS}s "
                    f"({source_context}, items {start}..{start + len(chunk) - 1}); "
                    "check the LLM provider/gateway and rerun."
                ) from exc
            except Exception as exc:
                if not _is_retryable_replay_rate_limit(exc) or attempt >= len(
                    RATE_LIMIT_RETRY_DELAYS_SECONDS
                ):
                    raise
                for content, state in zip(chunk, initial_evaluation_state, strict=True):
                    for field, value in state.items():
                        setattr(content, field, value)
                delay = RATE_LIMIT_RETRY_DELAYS_SECONDS[attempt]
                logger.warning(
                    "Replay chunk hit a transient provider rate limit; retrying items "
                    "%d..%d after %.0fs (%d/%d)",
                    start,
                    start + len(chunk) - 1,
                    delay,
                    attempt + 1,
                    len(RATE_LIMIT_RETRY_DELAYS_SECONDS),
                )
                await asyncio.sleep(delay)
        if len(chunk_scores) != len(chunk):
            raise RuntimeError(
                "Evaluation returned an incomplete score vector "
                f"({source_context}, expected {len(chunk)}, got {len(chunk_scores)}); "
                "the replay gate is invalid."
            )
        missing_ids = [
            str(getattr(content, "content_id", "") or getattr(content, "title", "") or index)
            for index, content in enumerate(chunk, start=start)
            if str(getattr(content, "relevance_reason", "") or "") == "evaluation_response_missing"
        ]
        if missing_ids:
            preview = ", ".join(missing_ids[:5])
            suffix = "..." if len(missing_ids) > 5 else ""
            raise RuntimeError(
                "Evaluation response was missing after retries for "
                f"{len(missing_ids)} item(s) ({preview}{suffix}); "
                "gateway/parse failures cannot be counted as zero-score observations."
            )
        scores.extend(float(score) for score in chunk_scores[: len(chunk)])
    return scores


def _top_delta_items(
    candidates: Sequence[ReplayCandidate],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    *,
    limit: int = 10,
) -> list[tuple[float, float, ReplayCandidate]]:
    rows = [
        (abs(float(score_b) - float(score_a)), float(score_b) - float(score_a), candidate)
        for candidate, score_a, score_b in zip(candidates, scores_a, scores_b, strict=True)
    ]
    rows.sort(key=lambda item: (item[0], abs(item[1]), item[2].candidate_id), reverse=True)
    return rows[:limit]


def _admit_count(
    candidates: Sequence[ReplayCandidate],
    scores: Sequence[float],
    *,
    admission_min_score: float,
) -> int:
    return sum(
        score
        >= _candidate_admission_threshold(
            candidate,
            admission_min_score=admission_min_score,
        )
        for candidate, score in zip(candidates, scores, strict=True)
    )


def _pair_metrics(
    candidates: Sequence[ReplayCandidate],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    *,
    admission_min_score: float,
) -> ReplayMetrics:
    delta = score_delta_summary(scores_a, scores_b)
    flips = admission_flip_summary(
        candidates,
        scores_a,
        scores_b,
        admission_min_score=admission_min_score,
    )
    admitted_a = _admit_count(
        candidates,
        scores_a,
        admission_min_score=admission_min_score,
    )
    admitted_b = _admit_count(
        candidates,
        scores_b,
        admission_min_score=admission_min_score,
    )
    item_count = len(candidates)
    return ReplayMetrics(
        mean_abs_delta=delta.mean_abs_delta,
        p95_abs_delta=delta.p95_abs_delta,
        spearman=spearman_rank_correlation(scores_a, scores_b),
        flip_rate=flips.flip_rate,
        flip_count=flips.flip_count,
        admitted_a=admitted_a,
        admitted_b=admitted_b,
        admission_rate_delta=((admitted_b - admitted_a) / item_count if item_count else 0.0),
    )


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(len(ordered) * max(0.0, min(1.0, percentile))))
    return ordered[min(len(ordered) - 1, rank - 1)]


def relative_gate(
    control_pairs: Sequence[ReplayPair],
    treatment_pairs: Sequence[ReplayPair],
) -> tuple[bool, dict[str, float]]:
    """Compare treatment medians with an empirical repeated A/A envelope."""

    if len(control_pairs) < 3 or len(treatment_pairs) < 3:
        raise ValueError("relative gate requires at least three control and treatment pairs")
    control_flip_ceiling = max(
        FLIP_RATE_MAX,
        _nearest_rank_percentile(
            [pair.metrics.flip_rate for pair in control_pairs],
            0.95,
        ),
    )
    control_spearman_floor = min(
        SPEARMAN_MIN,
        _nearest_rank_percentile(
            [pair.metrics.spearman for pair in control_pairs],
            0.05,
        ),
    )
    control_admission_delta = median(pair.metrics.admission_rate_delta for pair in control_pairs)
    treatment_flip = median(pair.metrics.flip_rate for pair in treatment_pairs)
    treatment_spearman = median(pair.metrics.spearman for pair in treatment_pairs)
    treatment_admission_delta = median(
        pair.metrics.admission_rate_delta for pair in treatment_pairs
    )
    admission_floor = control_admission_delta - _RELATIVE_ADMISSION_SHRINK_MAX
    gate_passed = (
        treatment_flip <= control_flip_ceiling
        and treatment_spearman >= control_spearman_floor
        and treatment_admission_delta >= admission_floor
    )
    return gate_passed, {
        "control_flip_ceiling": control_flip_ceiling,
        "control_spearman_floor": control_spearman_floor,
        "control_admission_delta": control_admission_delta,
        "treatment_flip_median": treatment_flip,
        "treatment_spearman_median": treatment_spearman,
        "treatment_admission_delta_median": treatment_admission_delta,
        "admission_delta_floor": admission_floor,
    }


def replay_blocking_reasons(
    *,
    quality_passed: bool,
    route_audit: Mapping[str, object],
    embedding_audit: Mapping[str, object],
    recall_audit: Mapping[str, object],
    reason_output_audit: Mapping[str, object],
    prompt_transport_audit: Mapping[str, object],
    profile_snapshot_stable: bool,
    candidate_snapshot_stable: bool,
) -> list[str]:
    """Return every independent reason that invalidates landing evidence."""

    blocking_reasons: list[str] = []
    if not quality_passed:
        blocking_reasons.append("relative quality gate failed")

    for label, audit in (
        ("route", route_audit),
        ("embedding", embedding_audit),
        ("recall", recall_audit),
        ("reason-output", reason_output_audit),
        ("prompt-transport", prompt_transport_audit),
    ):
        if not bool(audit.get("passed")):
            blocking_reasons.append(f"{label} audit failed")
        blocking_reasons.extend(str(reason) for reason in audit.get("blocking_reasons", []))

    if not profile_snapshot_stable:
        blocking_reasons.append("effective profile snapshot drifted during replay")
    if not candidate_snapshot_stable:
        blocking_reasons.append("candidate snapshot drifted during replay")
    return list(dict.fromkeys(blocking_reasons))


def _print_report(
    *,
    arm_b: str,
    candidates: Sequence[ReplayCandidate],
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    platform: str | None,
    recall_note: str = "",
    admission_min_score: float = 0.60,
) -> bool:
    delta = score_delta_summary(scores_a, scores_b)
    spearman = spearman_rank_correlation(scores_a, scores_b)
    flips = admission_flip_summary(
        candidates,
        scores_a,
        scores_b,
        admission_min_score=admission_min_score,
    )
    gate_passed = flips.flip_rate <= FLIP_RATE_MAX and spearman >= SPEARMAN_MIN

    print("\nProfile Diet A/B Replay")
    print(f"  sample: {len(candidates)}")
    print(f"  platform: {platform or 'all'}")
    arm_a_label = "legacy full-profile/no-recall" if arm_b == "compact" else "production"
    print(f"  arm A: {arm_a_label}")
    print(f"  arm B: {arm_b}")
    if recall_note:
        print(f"  note: {recall_note}")
    print()
    print("Metrics")
    print(f"  mean |delta|: {delta.mean_abs_delta:.4f}")
    print(f"  p95  |delta|: {delta.p95_abs_delta:.4f}")
    print(f"  Spearman:     {spearman:.4f}  (gate >= {SPEARMAN_MIN:.2f})")
    print(
        f"  flip rate:    {flips.flip_rate:.2%} "
        f"({flips.flip_count}/{flips.item_count}, gate <= {FLIP_RATE_MAX:.0%})"
    )
    print()
    print("Drift (noise-robust: symmetric sampling noise cancels, one-sided bias does not)")
    signed = [float(b) - float(a) for a, b in zip(scores_a, scores_b, strict=True)]
    mean_signed = sum(signed) / len(signed) if signed else 0.0
    admit_a = _admit_count(
        candidates,
        scores_a,
        admission_min_score=admission_min_score,
    )
    admit_b = _admit_count(
        candidates,
        scores_b,
        admission_min_score=admission_min_score,
    )
    print(f"  mean signed delta (B-A): {mean_signed:+.4f}")
    print(
        f"  admitted: arm A {admit_a}/{len(candidates)}, arm B {admit_b}/{len(candidates)} "
        f"(rate delta {(admit_b - admit_a) / len(candidates):+.1%})"
        if candidates
        else "  admitted: n/a"
    )
    per_platform: dict[str, list[float]] = {}
    for candidate, signed_delta in zip(candidates, signed, strict=True):
        per_platform.setdefault(candidate.source_platform or "unknown", []).append(signed_delta)
    print("  per-platform mean signed delta:")
    for platform_key in sorted(per_platform):
        values = per_platform[platform_key]
        print(f"    {platform_key}: {sum(values) / len(values):+.4f} (n={len(values)})")
    print()
    print("Per-strategy flips")
    if flips.per_strategy:
        for strategy, count in flips.per_strategy.items():
            print(f"  {strategy}: {count}")
    else:
        print("  none")

    print()
    print("Top 10 |delta| items")
    for abs_delta, signed_delta, candidate in _top_delta_items(candidates, scores_a, scores_b):
        title = candidate.title.replace("\n", " ").strip()
        if len(title) > 100:
            title = title[:97] + "..."
        print(
            f"  {candidate.candidate_id:>6} "
            f"{candidate.source_strategy or 'default':<14} "
            f"|delta|={abs_delta:.4f} delta={signed_delta:+.4f} "
            f"{title}"
        )

    print()
    print("Gate:", "PASS" if gate_passed else "FAIL")
    return gate_passed


def _print_repeated_report(
    *,
    arm_b: str,
    candidates: Sequence[ReplayCandidate],
    control_pairs: Sequence[ReplayPair],
    treatment_pairs: Sequence[ReplayPair],
    platform: str | None,
    recall_note: str,
) -> tuple[bool, dict[str, float]]:
    gate_passed, gate = relative_gate(control_pairs, treatment_pairs)
    print("\nProfile Diet Repeated Replay")
    print(f"  sample: {len(candidates)}")
    print(f"  repeats: {len(control_pairs)}")
    print(f"  platform: {platform or 'all'}")
    print(f"  arm B: {arm_b}")
    print(f"  source_context: {_REPLAY_SOURCE_CONTEXT}")
    if recall_note:
        print(f"  note: {recall_note}")
    print()
    print("Pairs")
    for pair in [*control_pairs, *treatment_pairs]:
        metrics = pair.metrics
        print(
            f"  {pair.kind:<9} #{pair.repeat}: first={pair.first_arm:<5} "
            f"flip={metrics.flip_rate:.2%} rho={metrics.spearman:.4f} "
            f"admission_delta={metrics.admission_rate_delta:+.2%}"
        )
    print()
    print("Relative gate")
    print(
        "  treatment flip median: "
        f"{gate['treatment_flip_median']:.2%} "
        f"(control envelope <= {gate['control_flip_ceiling']:.2%})"
    )
    print(
        "  treatment Spearman median: "
        f"{gate['treatment_spearman_median']:.4f} "
        f"(control envelope >= {gate['control_spearman_floor']:.4f})"
    )
    print(
        "  treatment admission delta median: "
        f"{gate['treatment_admission_delta_median']:+.2%} "
        f"(floor {gate['admission_delta_floor']:+.2%})"
    )
    print()
    print("Gate:", "PASS" if gate_passed else "FAIL")
    return gate_passed, gate


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _privacy_digest(value: object) -> str:
    """Return a run-private digest that cannot be reversed by dictionary lookup."""

    payload = _canonical_json(value).encode("utf-8")
    return hashlib.sha256(_REPLAY_PRIVACY_DIGEST_SALT + payload).hexdigest()


def _pair_payload(
    pair: ReplayPair,
    *,
    candidates: Sequence[ReplayCandidate],
    admission_min_score: float,
) -> dict[str, object]:
    thresholds = [
        _candidate_admission_threshold(candidate, admission_min_score=admission_min_score)
        for candidate in candidates
    ]
    admitted_a = [
        score >= threshold for score, threshold in zip(pair.scores_a, thresholds, strict=True)
    ]
    admitted_b = [
        score >= threshold for score, threshold in zip(pair.scores_b, thresholds, strict=True)
    ]
    return {
        "repeat": pair.repeat,
        "kind": pair.kind,
        "first_arm": pair.first_arm,
        "scores_a": list(pair.scores_a),
        "scores_b": list(pair.scores_b),
        "scores_a_digest": _digest(list(pair.scores_a)),
        "scores_b_digest": _digest(list(pair.scores_b)),
        "admission_thresholds": thresholds,
        "admitted_a": admitted_a,
        "admitted_b": admitted_b,
        "admitted_a_digest": _digest(admitted_a),
        "admitted_b_digest": _digest(admitted_b),
        "metrics": {
            "mean_abs_delta": pair.metrics.mean_abs_delta,
            "p95_abs_delta": pair.metrics.p95_abs_delta,
            "spearman": pair.metrics.spearman,
            "flip_rate": pair.metrics.flip_rate,
            "flip_count": pair.metrics.flip_count,
            "admitted_a": pair.metrics.admitted_a,
            "admitted_b": pair.metrics.admitted_b,
            "admission_rate_delta": pair.metrics.admission_rate_delta,
        },
    }


def _git_metadata() -> dict[str, object]:
    def command(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    return {
        "commit": command("rev-parse", "HEAD"),
        "dirty": bool(command("status", "--porcelain")),
    }


def _write_artifact(
    output_path: Path,
    *,
    args: argparse.Namespace,
    db_path: Path,
    config_path: Path,
    rows: Sequence[Mapping[str, Any]],
    profile_snapshot: ReplayProfileSnapshot,
    negative_examples: Sequence[Mapping[str, object]] | None,
    candidates: Sequence[ReplayCandidate],
    control_pairs: Sequence[ReplayPair],
    treatment_pairs: Sequence[ReplayPair],
    gate_passed: bool,
    gate: Mapping[str, object],
    admission_min_score: float,
    calls: Sequence[Mapping[str, object]],
    route_audit: Mapping[str, object],
    embedding_audit: Mapping[str, object],
    recall_audit: Mapping[str, object],
    reason_output_audit: Mapping[str, object],
    prompt_transport_audit: Mapping[str, object],
    production_prefilter_mode: str,
    topic_lifecycle_serialization: bool,
) -> None:
    candidate_payload = [
        {
            "candidate_ordinal": ordinal,
            "source_strategy": candidate.source_strategy,
            "source_platform": candidate.source_platform,
            "content_key_digest": _privacy_digest(
                {
                    "candidate_id": candidate.candidate_id,
                    "source_platform": candidate.source_platform,
                    "content_id": candidate.content_id,
                }
            ),
            "score_threshold": candidate.score_threshold,
            "status": str(row.get("status") or ""),
        }
        for ordinal, (candidate, row) in enumerate(zip(candidates, rows, strict=True))
    ]
    mix = Counter(
        (
            str(row.get("status") or ""),
            candidate.source_platform or "unknown",
            candidate.source_strategy or "default",
        )
        for candidate, row in zip(candidates, rows, strict=True)
    )
    artifact = {
        "schema_version": 4,
        "created_at": datetime.now(UTC).isoformat(),
        "git": _git_metadata(),
        "arm_b": str(args.arm_b),
        "sample": len(candidates),
        "repeats": int(args.repeats),
        "platform": args.platform,
        "source_context": _REPLAY_SOURCE_CONTEXT,
        "production_context": {
            "eval_prefilter_mode": production_prefilter_mode,
            "topic_lifecycle_serialization": ("on" if topic_lifecycle_serialization else "off"),
        },
        "replay_context": {"eval_prefilter_mode": "off"},
        "config_path_digest": _privacy_digest({"config_path": str(config_path.resolve())}),
        "db_path_digest": _privacy_digest({"db_path": str(db_path.resolve())}),
        "admission_min_score": admission_min_score,
        "snapshot": {
            "candidate_digest": _privacy_digest(
                {"candidate_snapshot": [dict(row) for row in rows]}
            ),
            "candidate_metadata_digest": _privacy_digest({"candidate_metadata": candidate_payload}),
            "raw_profile_digest": _privacy_digest(
                {"raw_profile_source_digest": profile_snapshot.raw_digest}
            ),
            "effective_profile_digest": _privacy_digest(
                {"effective_profile_source_digest": profile_snapshot.effective_digest}
            ),
            "overrides_present": profile_snapshot.overrides_present,
            "active_speculation_count": profile_snapshot.active_speculation_count,
            "negative_examples_digest": _privacy_digest(
                {"negative_examples": negative_examples or []}
            ),
        },
        "cohort_mix": [
            {
                "status": key[0],
                "platform": key[1],
                "strategy": key[2],
                "count": count,
            }
            for key, count in sorted(mix.items())
        ],
        "candidates": candidate_payload,
        "control_pairs": [
            _pair_payload(
                pair,
                candidates=candidates,
                admission_min_score=admission_min_score,
            )
            for pair in control_pairs
        ],
        "treatment_pairs": [
            _pair_payload(
                pair,
                candidates=candidates,
                admission_min_score=admission_min_score,
            )
            for pair in treatment_pairs
        ],
        "gate_constants": {
            "flip_rate_max": FLIP_RATE_MAX,
            "spearman_min": SPEARMAN_MIN,
            "relative_admission_shrink_max": _RELATIVE_ADMISSION_SHRINK_MAX,
            "llm_max_tokens": 4096,
            "replay_temperature": 0.0,
            "production_default_temperature": 0.7,
            "batch_size": _DEFAULT_BATCH_SIZE,
            "chunk_timeout_seconds": CHUNK_TIMEOUT_SECONDS,
            "rate_limit_retry_delays_seconds": list(RATE_LIMIT_RETRY_DELAYS_SECONDS),
        },
        "embedding": dict(embedding_audit),
        "recall": dict(recall_audit),
        "routes": dict(route_audit),
        "reason_output": dict(reason_output_audit),
        "prompt_transport": dict(prompt_transport_audit),
        "candidate_transport": (
            dict(prompt_transport_audit)
            if str(args.arm_b) in _CANDIDATE_TRANSPORT_EXPERIMENTS
            else {"enabled": False, "passed": True, "blocking_reasons": []}
        ),
        "gate": {"passed": gate_passed, **dict(gate)},
        "llm_calls": [dict(call) for call in calls],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


async def run(args: argparse.Namespace) -> int:
    from openbiliclaw.config import _default_config_path, load_config
    from openbiliclaw.discovery.engine import _evaluation_recall_interests

    config = load_config(args.config) if args.config else load_config()
    production_prefilter_mode = validate_replay_prefilter_compatibility(config)
    config_path = Path(args.config) if args.config else _default_config_path()
    db_path = Path(args.db) if args.db else _database_path(config)
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")

    model_override = parse_model_override(str(args.arm_b))
    compact_profile = str(args.arm_b) == "compact"
    legacy_reason = str(args.arm_b) == "reason-diet"
    reason_off = str(args.arm_b) == "reason-off"
    json_minify = str(args.arm_b) == "json-minify"
    candidate_transport_config = _CANDIDATE_TRANSPORT_EXPERIMENTS.get(str(args.arm_b))
    arm_a_candidate_transport = (
        str(candidate_transport_config["arm_a_transport"])
        if candidate_transport_config is not None
        else "production-json"
    )
    arm_b_candidate_transport = (
        str(candidate_transport_config["arm_b_transport"])
        if candidate_transport_config is not None
        else "production-json"
    )
    if (
        not compact_profile
        and model_override is None
        and not legacy_reason
        and not reason_off
        and not json_minify
        and candidate_transport_config is None
    ):
        raise ValueError(
            "--arm-b must be compact, reason-diet, reason-off, json-minify, sparse-json, "
            "row-wire-v1, model=<instance-id> (v2), or model=<provider:model> (legacy)"
        )

    database = _load_read_only_database(db_path)
    cleanup = ExitStack()
    topic_lifecycle_serialization = cleanup.enter_context(
        configured_topic_lifecycle_serialization(config)
    )
    try:
        rows = _fetch_replay_rows(database, sample=int(args.sample), platform=args.platform)
        frozen_rows_digest = _digest([dict(row) for row in rows])

        data_root = db_path.parent
        # Memory/profile inputs come from the deployment data directory.
        config.data_dir = str(data_root)  # type: ignore[attr-defined]
        profile_snapshot = _load_profile_snapshot(data_root)
        profile = profile_snapshot.effective_profile
        frozen_profile_digest = profile_snapshot.effective_digest
        negative_examples = _recent_negative_exemplars(database)
        candidates = [_row_to_replay_candidate(row) for row in rows]
        discovery_cfg = getattr(config, "discovery", None)
        admission_min_score = float(getattr(discovery_cfg, "admission_min_score", 0.60) or 0.60)
        eligible_tail_count = len(_evaluation_recall_interests(profile))

        arm_a_inner = _build_llm_service(config, data_root)
        arm_b_inner = _build_llm_service(config, data_root, model_override=model_override)
        arm_a_attempts = _ProviderAttemptUsageRecorder()
        arm_b_attempts = _ProviderAttemptUsageRecorder()
        arm_a_attempts.instrument_registry(getattr(arm_a_inner, "registry", None))
        arm_b_attempts.instrument_registry(getattr(arm_b_inner, "registry", None))
        arm_a_service = _DeterministicLLMService(
            arm_a_inner,
            service="arm_a",
            expected_compact_json=False,
            expected_candidate_transport=arm_a_candidate_transport,
            candidate_transport_audit_enabled=candidate_transport_config is not None,
            attempt_usage_recorder=arm_a_attempts,
        )
        arm_b_service = _DeterministicLLMService(
            arm_b_inner,
            service="arm_b",
            expected_compact_json=json_minify,
            expected_candidate_transport=arm_b_candidate_transport,
            candidate_transport_audit_enabled=candidate_transport_config is not None,
            attempt_usage_recorder=arm_b_attempts,
        )
        recall_audit = ReplayRecallAudit()

        # The run-scoped L2 database lives until every A/A and A/B call has
        # completed, then closes before its temporary directory is removed.
        with run_scoped_embedding_audit(
            config,
            allow_no_embedding=bool(getattr(args, "allow_no_embedding", False)),
        ) as embedding_audit_service:
            embedding_service: object | None = embedding_audit_service
            recall_note = (
                "related_interests recall disabled by explicit degraded replay flag"
                if embedding_service is None
                else ""
            )
            arm_a_engine = _build_engine(
                arm_a_service,
                config,
                compact_profile=False,
                negative_examples=negative_examples,
                legacy_profile=compact_profile,
                embedding_service=None if compact_profile else embedding_service,
                recall_audit=recall_audit,
                evaluation_candidate_transport=_ENGINE_CANDIDATE_TRANSPORTS[
                    arm_a_candidate_transport
                ],
            )
            arm_b_engine = _build_engine(
                arm_b_service,
                config,
                compact_profile=compact_profile,
                negative_examples=negative_examples,
                legacy_profile=False,
                embedding_service=embedding_service,
                recall_audit=recall_audit,
                compact_evaluation_json=json_minify,
                evaluation_candidate_transport=_ENGINE_CANDIDATE_TRANSPORTS[
                    arm_b_candidate_transport
                ],
            )

            async def score_arm_a(
                *,
                pair_kind: str,
                repeat: int,
                logical_run: str,
            ) -> tuple[float, ...]:
                with replay_call_attribution(
                    pair_kind=pair_kind,
                    repeat=repeat,
                    logical_run=logical_run,
                    arm="A",
                ):
                    if legacy_reason:
                        with legacy_reason_prompts():
                            scores = await _score_contents(
                                arm_a_engine,
                                _rows_to_contents(rows),
                                profile,
                                source_context=_REPLAY_SOURCE_CONTEXT,
                            )
                    else:
                        scores = await _score_contents(
                            arm_a_engine,
                            _rows_to_contents(rows),
                            profile,
                            source_context=_REPLAY_SOURCE_CONTEXT,
                        )
                return tuple(scores)

            async def score_arm_b(
                *,
                pair_kind: str,
                repeat: int,
            ) -> tuple[float, ...]:
                with replay_call_attribution(
                    pair_kind=pair_kind,
                    repeat=repeat,
                    logical_run="B",
                    arm="B",
                ):
                    if reason_off:
                        with reason_off_prompts():
                            scores = await _score_contents(
                                arm_b_engine,
                                _rows_to_contents(rows),
                                profile,
                                source_context=_REPLAY_SOURCE_CONTEXT,
                            )
                    else:
                        scores = await _score_contents(
                            arm_b_engine,
                            _rows_to_contents(rows),
                            profile,
                            source_context=_REPLAY_SOURCE_CONTEXT,
                        )
                return tuple(scores)

            async def control_pair(repeat_index: int) -> ReplayPair:
                repeat = repeat_index + 1
                scores_a = await score_arm_a(
                    pair_kind="control",
                    repeat=repeat,
                    logical_run="A1",
                )
                scores_b = await score_arm_a(
                    pair_kind="control",
                    repeat=repeat,
                    logical_run="A2",
                )
                return ReplayPair(
                    repeat=repeat,
                    kind="control",
                    first_arm="A",
                    scores_a=scores_a,
                    scores_b=scores_b,
                    metrics=_pair_metrics(
                        candidates,
                        scores_a,
                        scores_b,
                        admission_min_score=admission_min_score,
                    ),
                )

            async def treatment_pair(repeat_index: int) -> ReplayPair:
                repeat = repeat_index + 1
                if repeat_index % 2 == 0:
                    scores_a = await score_arm_a(
                        pair_kind="treatment",
                        repeat=repeat,
                        logical_run="A",
                    )
                    scores_b = await score_arm_b(pair_kind="treatment", repeat=repeat)
                    first_arm = "A"
                else:
                    scores_b = await score_arm_b(pair_kind="treatment", repeat=repeat)
                    scores_a = await score_arm_a(
                        pair_kind="treatment",
                        repeat=repeat,
                        logical_run="A",
                    )
                    first_arm = "B"
                return ReplayPair(
                    repeat=repeat,
                    kind="treatment",
                    first_arm=first_arm,
                    scores_a=scores_a,
                    scores_b=scores_b,
                    metrics=_pair_metrics(
                        candidates,
                        scores_a,
                        scores_b,
                        admission_min_score=admission_min_score,
                    ),
                )

            control_pairs: list[ReplayPair] = []
            treatment_pairs: list[ReplayPair] = []
            for repeat_index in range(int(args.repeats)):
                # Alternate control/treatment order across repeats so gateway
                # drift is not systematically assigned to one pair type.
                if repeat_index % 2 == 0:
                    control_pairs.append(await control_pair(repeat_index))
                    treatment_pairs.append(await treatment_pair(repeat_index))
                else:
                    treatment_pairs.append(await treatment_pair(repeat_index))
                    control_pairs.append(await control_pair(repeat_index))

            quality_passed, quality_gate = _print_repeated_report(
                arm_b=str(args.arm_b),
                candidates=candidates,
                control_pairs=control_pairs,
                treatment_pairs=treatment_pairs,
                platform=args.platform,
                recall_note=recall_note,
            )
            calls = [*arm_a_service.calls, *arm_b_service.calls]
            reason_output_audit = validate_reason_off_outputs(calls, enabled=reason_off)
            if candidate_transport_config is not None:
                prompt_transport_audit = validate_candidate_transport_experiment(
                    calls,
                    experiment=str(args.arm_b),
                    repeats=int(args.repeats),
                )
            else:
                prompt_transport_audit = validate_json_minify_transport(
                    calls,
                    enabled=json_minify,
                    repeats=int(args.repeats),
                    arm_a_profile_cache_stats=arm_a_engine.evaluation_profile_prompt_cache_stats(),
                    arm_b_profile_cache_stats=arm_b_engine.evaluation_profile_prompt_cache_stats(),
                )
            route_audit = validate_replay_routes(
                calls,
                repeats=int(args.repeats),
                model_override=model_override,
                expected_control_instance=_expected_evaluation_instance(arm_a_service),
                expected_treatment_instance=_expected_evaluation_instance(arm_b_service),
            )
            expected_recall_runs: set[tuple[str, int, str]] = set()
            for repeat in range(1, int(args.repeats) + 1):
                expected_recall_runs.add(("treatment", repeat, "B"))
                if not compact_profile:
                    expected_recall_runs.update(
                        {
                            ("control", repeat, "A1"),
                            ("control", repeat, "A2"),
                            ("treatment", repeat, "A"),
                        }
                    )
            recall_validation = recall_audit.validate(
                expected_runs=expected_recall_runs,
                minimum_batches_per_run=math.ceil(len(rows) / _DEFAULT_BATCH_SIZE),
                expected_candidate_count=len(rows),
            )
            if embedding_audit_service is None:
                embedding_audit: dict[str, object] = {
                    "passed": False,
                    "degraded": True,
                    "namespace": "",
                    "call_count": 0,
                    "successful_call_count": 0,
                    "dimension": 0,
                    "eligible_tail_count": eligible_tail_count,
                    "blocking_reasons": [
                        "embedding disabled; artifact is degraded and not landing evidence"
                    ],
                    "calls": [],
                }
            else:
                embedding_audit = embedding_audit_service.summary(
                    eligible_tail_count=eligible_tail_count,
                    recall_audit=recall_audit,
                    expected_runs=expected_recall_runs,
                )

            blocking_reasons = replay_blocking_reasons(
                quality_passed=quality_passed,
                route_audit=route_audit,
                embedding_audit=embedding_audit,
                recall_audit=recall_validation,
                reason_output_audit=reason_output_audit,
                prompt_transport_audit=prompt_transport_audit,
                profile_snapshot_stable=(
                    _digest(_profile_digest_payload(profile)) == frozen_profile_digest
                ),
                candidate_snapshot_stable=(
                    _digest([dict(row) for row in rows]) == frozen_rows_digest
                ),
            )
            gate_passed = not blocking_reasons
            gate: dict[str, object] = {
                **quality_gate,
                "quality_passed": quality_passed,
                "route_passed": bool(route_audit.get("passed")),
                "embedding_passed": bool(embedding_audit.get("passed")),
                "recall_passed": bool(recall_validation.get("passed")),
                "reason_output_passed": bool(reason_output_audit.get("passed")),
                "prompt_transport_passed": bool(prompt_transport_audit.get("passed")),
                "blocking_reasons": blocking_reasons,
            }
            if blocking_reasons:
                print("\nBlocking reasons")
                for reason in blocking_reasons:
                    print(f"  - {reason}")
                print("\nFinal gate: FAIL")
            else:
                print("\nFinal gate: PASS")

            output_path = Path(args.output)
            _write_artifact(
                output_path,
                args=args,
                db_path=db_path,
                config_path=config_path,
                rows=rows,
                profile_snapshot=profile_snapshot,
                negative_examples=negative_examples,
                candidates=candidates,
                control_pairs=control_pairs,
                treatment_pairs=treatment_pairs,
                gate_passed=gate_passed,
                gate=gate,
                admission_min_score=admission_min_score,
                calls=calls,
                route_audit=route_audit,
                embedding_audit=embedding_audit,
                recall_audit=recall_validation,
                reason_output_audit=reason_output_audit,
                prompt_transport_audit=prompt_transport_audit,
                production_prefilter_mode=production_prefilter_mode,
                topic_lifecycle_serialization=topic_lifecycle_serialization,
            )
            print(f"Artifact: {output_path}")
            return 0 if gate_passed else 1
    finally:
        try:
            close_database = getattr(database, "close", None)
            if callable(close_database):
                close_database()
        finally:
            cleanup.close()


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _minimum_three(raw: str) -> int:
    value = _positive_int(raw)
    if value < 3:
        raise argparse.ArgumentTypeError("must be at least 3 for the relative gate")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discovery profile-diet A/B replay gate")
    parser.add_argument("--sample", type=_positive_int, default=100, help="Candidate sample size")
    parser.add_argument(
        "--repeats",
        type=_minimum_three,
        default=3,
        help="Repeated A/A and A/B pairs; minimum 3 (default: 3)",
    )
    parser.add_argument(
        "--platform", type=str, default=None, help="Optional source platform filter"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Explicit path to openbiliclaw.db (default: resolve from config data_dir)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Explicit path to config.toml (default: standard config resolution)",
    )
    parser.add_argument(
        "--arm-b",
        required=True,
        help=(
            "Arm B transform: compact, reason-diet, reason-off, json-minify, sparse-json, "
            "row-wire-v1, model=<instance-id> (v2), or model=<provider:model> (legacy)"
        ),
    )
    parser.add_argument(
        "--allow-no-embedding",
        action="store_true",
        help=(
            "Allow an explicitly embedding-disabled config to run only as degraded, "
            "non-landing evidence (the final gate still fails)."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Write raw paired scores, snapshot digests, routes, and gate metrics to JSON",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    try:
        exit_code = asyncio.run(run(parse_args()))
    except Exception as exc:
        logger.error("profile diet replay failed: %s", exc)
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
