"""One-off repair of profile facts that were learned from broken provenance.

Three contamination classes accumulated before the provenance contract existed:

1. **MBTI cited as evidence.** Hypotheses whose ``evidence`` reads "ENTP 类型
   具有联想综合的认知风格" — the label was itself inferred from an import
   artefact, so citing it turns a guess into load-bearing evidence and the loop
   never opens.
2. **Timelines computed from the import second.** Awareness notes that claim
   「同一天」/「快速切换」 may describe the batch, not the user. The note is
   checked against the real interaction times of the events it cites; only a
   *provably* wrong span is quarantined, an unverifiable one is kept.
3. **Product operations saved as taste.** Dialogue-derived interests such as
   「系统配置与测试」 and dislike topics such as 「测试后配置未干净还原」 are
   records of running this product, not preferences.

The planner is pure: it takes persisted payloads plus ``event_id → real epoch``
and returns repaired copies. Writing them is the CLI's job
(``openbiliclaw profile-repair-provenance``, dry-run by default).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.soul.meta_usage import (
    DIALOGUE_SOURCES,
    is_operational_topic,
    looks_like_product_ops_topic,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# Claims that only make sense if the timeline is real. A note whose cited events
# are provably spread over more than this is quarantined.
TEMPORAL_CLAIM_MARKERS: tuple[str, ...] = (
    "同一天",
    "当天",
    "同日",
    "半小时内",
    "一小时内",
    "分钟内",
    "短时间内",
    "连续切换",
    "快速切换",
)

# A "same day" claim may legitimately cross midnight; 36h is generous enough
# that a false positive needs an event set that clearly spans days.
SAME_DAY_SPAN_SECONDS = 36 * 3600

# ``\b`` does not work next to CJK: Python treats 类 as a word character, so
# "ENTP类型" has no boundary after P. Anchor on ASCII letters instead.
_MBTI_LABEL_RE = re.compile(r"(?<![A-Za-z])[EI][NS][TF][JP](?![A-Za-z])")

_ARCHIVE_REASON_META_USAGE = "meta-usage: operating this product is not a taste"
_ARCHIVE_REASON_MBTI = "evidence was the MBTI label itself"


@dataclass(frozen=True)
class RepairPlan:
    """Repaired payloads plus a human-readable account of what changed."""

    awareness: dict[str, Any]
    insights: dict[str, Any]
    preference: dict[str, Any]
    summary: dict[str, int] = field(default_factory=dict)
    details: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return any(self.summary.values())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _has_temporal_claim(note: Mapping[str, Any]) -> bool:
    blob = f"{note.get('observation', '')} {note.get('trend', '')}"
    return any(marker in blob for marker in TEMPORAL_CLAIM_MARKERS)


def _note_event_times(
    note: Mapping[str, Any],
    event_times: Mapping[int, float],
) -> list[float]:
    raw_ids = note.get("source_event_ids")
    if not isinstance(raw_ids, list):
        return []
    times: list[float] = []
    for raw in raw_ids:
        try:
            event_id = int(raw)
        except (TypeError, ValueError):
            continue
        stamp = event_times.get(event_id)
        if isinstance(stamp, int | float) and stamp > 0:
            times.append(float(stamp))
    return times


def _is_mbti_evidence(text: str, mbti_type: str) -> bool:
    """Whether an evidence line is the MBTI label itself.

    No "型"/"MBTI" suffix is required: the real corpus also contains
    "ENTP认知风格擅长联想综合…". The four-letter token is unambiguous — the
    pattern only matches the sixteen type codes, and lowercase text is left
    alone.
    """
    upper = text.upper()
    if mbti_type and mbti_type.strip().upper() in upper:
        return True
    return bool(_MBTI_LABEL_RE.search(text))


def _repair_insights(
    insights: Mapping[str, Any],
    *,
    mbti_type: str,
    now_iso: str,
    details: list[str],
    summary: dict[str, int],
) -> dict[str, Any]:
    raw = insights.get("hypotheses")
    hypotheses = raw if isinstance(raw, list) else []
    repaired: list[Any] = []
    for item in hypotheses:
        if not isinstance(item, dict):
            repaired.append(item)
            continue
        evidence = [entry for entry in item.get("evidence") or [] if isinstance(entry, str)]
        kept = [entry for entry in evidence if not _is_mbti_evidence(entry, mbti_type)]
        if len(kept) == len(evidence):
            repaired.append(item)
            continue
        updated = dict(item)
        updated["evidence"] = kept
        summary["insight_evidence_removed"] += len(evidence) - len(kept)
        if not kept:
            updated["confidence"] = min(float(item.get("confidence") or 0.0), 0.2)
            updated["validated"] = False
            updated["invalidated_reason"] = _ARCHIVE_REASON_MBTI
            summary["insights_invalidated"] += 1
            details.append(f"作废假设（唯一证据是 MBTI 标签）：{item.get('hypothesis', '')[:40]}")
        else:
            summary["insights_repaired"] += 1
            details.append(f"删掉 MBTI 证据：{item.get('hypothesis', '')[:40]}")
        updated["provenance_repaired_at"] = now_iso
        repaired.append(updated)

    result = dict(insights)
    result["hypotheses"] = repaired
    return result


def _repair_awareness(
    awareness: Mapping[str, Any],
    *,
    event_times: Mapping[int, float],
    now_iso: str,
    details: list[str],
    summary: dict[str, int],
) -> dict[str, Any]:
    raw = awareness.get("notes")
    notes = raw if isinstance(raw, list) else []
    kept: list[Any] = []
    quarantined: list[Any] = []
    for note in notes:
        if not isinstance(note, dict) or not _has_temporal_claim(note):
            kept.append(note)
            continue
        times = _note_event_times(note, event_times)
        if len(times) < 2 or note.get("source_event_ids_approximate") is True:
            # Cannot prove it wrong — keep the note rather than rewrite history.
            # Approximate ids may describe a superset of what the model saw, so
            # their span says nothing about the claim's truth.
            summary["awareness_unverified"] += 1
            kept.append(note)
            continue
        span = max(times) - min(times)
        if span <= SAME_DAY_SPAN_SECONDS:
            kept.append(note)
            continue
        entry = dict(note)
        entry["quarantined_at"] = now_iso
        entry["quarantined_reason"] = (
            "temporal claim contradicted by real interaction times "
            f"(span {span / 3600:.0f}h across {len(times)} events)"
        )
        quarantined.append(entry)
        summary["awareness_quarantined"] += 1
        details.append(
            f"隔离觉察笔记（时序与真实时间矛盾）：{str(note.get('observation', ''))[:40]}"
        )

    result = dict(awareness)
    result["notes"] = kept
    if quarantined:
        previous = result.get("quarantined_notes")
        result["quarantined_notes"] = [
            *(previous if isinstance(previous, list) else []),
            *quarantined,
        ]
    return result


def _repair_preference(
    preference: Mapping[str, Any],
    *,
    now_iso: str,
    details: list[str],
    summary: dict[str, int],
) -> dict[str, Any]:
    result = dict(preference)

    raw_interests = preference.get("interests")
    interests = raw_interests if isinstance(raw_interests, list) else []
    kept_interests: list[Any] = []
    archived = [
        item for item in preference.get("archived_interests") or [] if isinstance(item, dict)
    ]
    for tag in interests:
        if not isinstance(tag, dict):
            kept_interests.append(tag)
            continue
        name = str(tag.get("name") or "")
        source = str(tag.get("source") or "")
        if source in DIALOGUE_SOURCES and looks_like_product_ops_topic(name):
            entry = dict(tag)
            entry["archived_reason"] = _ARCHIVE_REASON_META_USAGE
            entry["archived_at"] = now_iso
            archived.append(entry)
            summary["interests_archived"] += 1
            details.append(f"归档兴趣（产品运维话题）：{name}")
            continue
        if source in DIALOGUE_SOURCES and is_operational_topic(name):
            summary["interests_review"] += 1
            details.append(f"待人工确认（单个运维词，未自动归档）：{name}")
        kept_interests.append(tag)
    result["interests"] = kept_interests
    result["archived_interests"] = archived

    raw_dislikes = preference.get("disliked_topics")
    dislikes = raw_dislikes if isinstance(raw_dislikes, list) else []
    kept_dislikes: list[Any] = []
    archived_dislikes = [
        str(item) for item in preference.get("archived_disliked_topics") or [] if str(item).strip()
    ]
    for topic in dislikes:
        text = str(topic)
        if looks_like_product_ops_topic(text):
            archived_dislikes.append(text)
            summary["dislikes_archived"] += 1
            details.append(f"归档避雷主题（产品运维话题）：{text}")
            continue
        kept_dislikes.append(topic)
    result["disliked_topics"] = kept_dislikes
    result["archived_disliked_topics"] = archived_dislikes
    return result


def plan_provenance_repair(
    *,
    awareness: Mapping[str, Any],
    insights: Mapping[str, Any],
    preference: Mapping[str, Any],
    event_times: Mapping[int, float] | None = None,
    mbti_type: str = "",
    now_iso: str = "",
) -> RepairPlan:
    """Return repaired copies of the three profile layers plus a summary."""
    stamp = now_iso or _now_iso()
    times = event_times or {}
    summary = {
        "insight_evidence_removed": 0,
        "insights_repaired": 0,
        "insights_invalidated": 0,
        "awareness_quarantined": 0,
        "awareness_unverified": 0,
        "interests_archived": 0,
        "interests_review": 0,
        "dislikes_archived": 0,
    }
    details: list[str] = []
    return RepairPlan(
        awareness=_repair_awareness(
            awareness,
            event_times=times,
            now_iso=stamp,
            details=details,
            summary=summary,
        ),
        insights=_repair_insights(
            insights,
            mbti_type=mbti_type,
            now_iso=stamp,
            details=details,
            summary=summary,
        ),
        preference=_repair_preference(
            preference,
            now_iso=stamp,
            details=details,
            summary=summary,
        ),
        summary=summary,
        details=tuple(details),
    )


class LayerLike(Protocol):
    """Minimal memory-layer surface needed to publish a repaired payload."""

    @property
    def data(self) -> dict[str, Any]: ...

    def save(self) -> None: ...


def replace_layer_payload(layer: LayerLike, payload: Mapping[str, Any]) -> None:
    """Publish a repaired payload into a memory layer and persist it.

    ``MemoryLayer.data`` is a read-only property, so the payload is applied
    in place (clear + update) rather than assigned; ``save()`` then writes the
    layer file. Assignment raises ``AttributeError`` at the very end of the
    command, after the backup has already been taken.
    """
    data = layer.data
    data.clear()
    data.update(payload)
    layer.save()
