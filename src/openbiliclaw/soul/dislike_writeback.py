"""Shared disliked-topic writeback and candidate-pool purge helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openbiliclaw.memory.manager import MemoryManager

logger = logging.getLogger(__name__)


def _stable_strings(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def merge_new_dislikes_into_preference(
    *,
    memory: MemoryManager,
    topics: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Idempotently add disliked topics to the flat preference layer."""
    preference_layer = memory.get_layer("preference")
    existing = _stable_strings(
        [
            str(item)
            for item in preference_layer.data.get("disliked_topics", [])
            if str(item).strip()
        ]
    )
    existing_set = set(existing)
    added = [topic for topic in _stable_strings(topics) if topic not in existing_set]
    if not added:
        return [], existing
    all_dislikes = [*existing, *added]
    preference_layer.data["disliked_topics"] = all_dislikes
    preference_layer.save()
    return added, all_dislikes


async def purge_pool_for_new_dislikes(
    *,
    database: Any | None,
    embedding_service: Any | None,
    llm_service: Any | None,
    newly_added: Sequence[str],
    all_dislikes: Sequence[str],
) -> list[str]:
    """Run the existing fast and semantic pool-purge path for new dislikes."""
    topics = _stable_strings(newly_added)
    all_topics = _stable_strings(all_dislikes)
    if not topics:
        return []

    changes: list[str] = []
    try:
        purge_fn = getattr(database, "purge_pool_by_disliked_topics", None)
        if callable(purge_fn):
            purged = purge_fn(topics)
            if purged:
                changes.append(f"从候选池清除 {purged} 条相关内容")
    except Exception:
        logger.exception("Failed to purge pool candidates by new dislikes")

    if embedding_service is not None and database is not None:
        if llm_service is not None:
            try:
                from openbiliclaw.soul.pool_purge import recall_and_llm_purge_pool

                smart_purged = await recall_and_llm_purge_pool(
                    database=database,
                    topics=topics,
                    all_disliked_topics=all_topics,
                    embedding_service=embedding_service,
                    llm_service=llm_service,
                )
                if smart_purged:
                    changes.append(f"从候选池召回+LLM 清除 {smart_purged} 条相关内容")
            except Exception:
                logger.exception("Failed to recall+LLM purge pool candidates")
        else:
            try:
                from openbiliclaw.soul.pool_purge import (
                    semantic_purge_pool_by_disliked_topics,
                )

                semantic_purged = await semantic_purge_pool_by_disliked_topics(
                    database=database,
                    topics=topics,
                    embedding_service=embedding_service,
                )
                if semantic_purged:
                    changes.append(f"从候选池语义清除 {semantic_purged} 条相关内容")
            except Exception:
                logger.exception("Failed to semantic-purge pool candidates")

    return changes


async def apply_new_dislikes(
    *,
    memory: MemoryManager,
    database: Any | None,
    embedding_service: Any | None,
    llm_service: Any | None,
    topics: Sequence[str],
) -> list[str]:
    """Add disliked topics, sync profile files, and purge matching pool content."""
    added, all_dislikes = merge_new_dislikes_into_preference(memory=memory, topics=topics)
    if not added:
        return []

    changes = [f"新增讨厌: {topic}" for topic in added]

    from openbiliclaw.soul.profile import OnionProfile

    preference_layer = memory.get_layer("preference")
    soul_layer = memory.get_layer("soul")
    profile = OnionProfile.from_dict(soul_layer.data) if soul_layer.data else OnionProfile()
    profile.populate_from_flat_preference(preference_layer.data)
    soul_layer.data.clear()
    soul_layer.data.update(profile.to_dict())
    # Persist the canonical layer first; then sync derived profile files.
    soul_layer.save()
    memory.sync_profile_files(profile)

    changes.extend(
        await purge_pool_for_new_dislikes(
            database=database,
            embedding_service=embedding_service,
            llm_service=llm_service,
            newly_added=added,
            all_dislikes=all_dislikes,
        )
    )
    return changes


def topics_for_confirmed_avoidance(avoidance: Any) -> list[str]:
    """Return conservative writeback topics for a confirmed avoidance."""
    specifics = [
        str(getattr(item, "name", item)).strip()
        for item in getattr(avoidance, "specifics", []) or []
        if str(getattr(item, "name", item)).strip()
    ]
    if specifics:
        return _stable_strings(specifics)
    domain = str(getattr(avoidance, "domain", "")).strip()
    return [domain] if domain else []
