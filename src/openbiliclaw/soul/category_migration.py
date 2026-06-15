"""One-shot migration of free-form interest categories onto ``CATEGORY_VOCAB``."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openbiliclaw.llm.json_utils import DEFAULT_STRUCTURED_MAX_TOKENS, parse_llm_json_tolerant
from openbiliclaw.llm.prompts import build_category_mapping_prompt
from openbiliclaw.soul.consolidator import (
    _CHANGELOG_FILENAME,
    _RUNS_DIRNAME,
    SupportsStructuredTask,
    rebuild_profile_tree,
)
from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB, FALLBACK_CATEGORY

logger = logging.getLogger(__name__)


@dataclass
class CategoryMigrationReport:
    """Outcome of a category migration preview or apply run."""

    ran: bool = False
    dry_run: bool = False
    run_id: str = ""
    histogram: dict[str, int] = field(default_factory=dict)
    mapping: dict[str, str] = field(default_factory=dict)
    target_counts: dict[str, int] = field(default_factory=dict)
    other_ratio: float = 0.0
    applied: bool = False
    errors: list[str] = field(default_factory=list)


class CategoryMigrator:
    """Map all stored interest categories to the closed first-level taxonomy."""

    def __init__(
        self,
        *,
        memory: Any,
        llm_service: SupportsStructuredTask | None,
        data_dir: Path | str | None = None,
    ) -> None:
        self._memory = memory
        self._llm_service = llm_service
        resolved_dir = data_dir or getattr(memory, "_data_dir", None)
        self._data_dir = Path(resolved_dir) if resolved_dir else None

    async def run(self, *, dry_run: bool, now: datetime | None = None) -> CategoryMigrationReport:
        """Run a dry-run preview or apply the validated category mapping."""
        current = now or datetime.now()
        report = CategoryMigrationReport(
            ran=True,
            dry_run=dry_run,
            run_id=current.strftime("%Y%m%d-%H%M%S"),
        )

        preference_layer = self._memory.get_layer("preference")
        interests = _interests_from_layer(preference_layer.data)
        dislikes = _str_list(preference_layer.data.get("disliked_topics"))
        histogram = Counter(_raw_category(item) for item in interests)
        report.histogram = dict(histogram)
        nonempty_categories = sorted(category for category in histogram if category)
        unknown_categories = [
            category for category in nonempty_categories if category not in CATEGORY_VOCAB
        ]

        mapping = {
            category: category for category in nonempty_categories if category in CATEGORY_VOCAB
        }
        if unknown_categories:
            if self._llm_service is None:
                report.errors.append("llm: service unavailable")
                return report
            try:
                mapping.update(await self._load_mapping_from_llm(nonempty_categories, histogram))
            except Exception as exc:
                logger.warning("category migration LLM call failed: %s", exc)
                report.errors.append(f"llm: {exc}")
                return report
        for category in nonempty_categories:
            if category in CATEGORY_VOCAB:
                mapping[category] = category

        errors = _validate_mapping(mapping, nonempty_categories)
        if errors:
            report.errors.extend(errors)
            return report

        full_mapping = dict(mapping)
        report.mapping = full_mapping
        report.target_counts = _target_counts(interests, full_mapping)
        total = len(interests)
        report.other_ratio = (
            report.target_counts.get(FALLBACK_CATEGORY, 0) / total if total else 0.0
        )
        if report.other_ratio > 0.10:
            logger.warning(
                "category migration fallback ratio %.1f%% exceeds 10%%",
                report.other_ratio * 100,
            )

        if dry_run:
            return report

        before_snapshot = {
            "interests": [dict(item) for item in interests],
            "disliked_topics": list(dislikes),
        }
        for item in interests:
            item["category"] = full_mapping.get(_raw_category(item), FALLBACK_CATEGORY)
        preference_layer.data["interests"] = interests
        preference_layer.data["disliked_topics"] = dislikes
        preference_layer.save()
        rebuild_profile_tree(self._memory, preference_layer.data)
        self._warn_stale_domain_overrides(set(full_mapping))
        self._write_run_record(report, before_snapshot, full_mapping)
        self._append_changelog(report, current)
        report.applied = True
        return report

    async def _load_mapping_from_llm(
        self, categories: list[str], histogram: Counter[str]
    ) -> dict[str, str]:
        if not categories:
            return {}
        prompt_categories = [
            {"category": category, "tag_count": histogram.get(category, 0)}
            for category in categories
        ]
        messages = build_category_mapping_prompt(categories=prompt_categories)
        if self._llm_service is None:
            return {}
        response = await self._llm_service.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            temperature=0.2,
            max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
            caller="soul.category_migration",
        )
        parsed = parse_llm_json_tolerant(response.content)
        if not isinstance(parsed, dict):
            raise ValueError("category mapping response is not a JSON object")
        raw_mapping = parsed.get("mapping")
        if not isinstance(raw_mapping, dict):
            raise ValueError("category mapping response missing mapping object")
        return {str(old): str(new) for old, new in raw_mapping.items()}

    def _write_run_record(
        self,
        report: CategoryMigrationReport,
        before_snapshot: dict[str, object],
        mapping: dict[str, str],
    ) -> None:
        if self._data_dir is None:
            return
        runs_dir = self._data_dir / _RUNS_DIRNAME
        try:
            runs_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "run_id": report.run_id,
                "kind": "category_migration",
                "before": before_snapshot,
                "mapping": mapping,
                "target_counts": report.target_counts,
                "other_ratio": report.other_ratio,
                "rule_merges": [],
                "merges": [],
                "rename_map": {},
                "rejected_clusters": [],
                "overrides_before": None,
            }
            (runs_dir / f"{report.run_id}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.debug("Failed to write category migration run record", exc_info=True)

    def _append_changelog(self, report: CategoryMigrationReport, now: datetime) -> None:
        if self._data_dir is None:
            return
        lines = [
            f"\n## 分类迁移 {report.run_id}（{now.strftime('%Y-%m-%d %H:%M')}）\n",
            f"- 「其他」占比: {report.other_ratio:.1%}\n",
        ]
        for old, new in sorted(
            report.mapping.items(), key=lambda item: -report.histogram.get(item[0], 0)
        ):
            lines.append(f"- {old}({report.histogram.get(old, 0)} 个标签) → {new}\n")
        try:
            with (self._data_dir / _CHANGELOG_FILENAME).open("a", encoding="utf-8") as fh:
                fh.writelines(lines)
        except Exception:
            logger.debug("Failed to append category migration changelog", exc_info=True)

    def _warn_stale_domain_overrides(self, migrated_categories: set[str]) -> None:
        loader = getattr(self._memory, "load_profile_overrides", None)
        if not callable(loader):
            return
        try:
            overrides = loader()
            raw = overrides.to_dict()
        except Exception:
            logger.debug("Failed to inspect overrides after category migration", exc_info=True)
            return
        raw_text = json.dumps(raw, ensure_ascii=False)
        stale = sorted(category for category in migrated_categories if category in raw_text)
        if stale:
            logger.warning("category migration may leave stale domain overrides: %s", stale)


def _interests_from_layer(preference_data: dict[str, object]) -> list[dict[str, Any]]:
    raw = preference_data.get("interests", [])
    if not isinstance(raw, list):
        return []
    return [
        dict(item) for item in raw if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _raw_category(item: dict[str, Any]) -> str:
    return str(item.get("category", "")).strip()


def _validate_mapping(mapping: dict[str, str], expected_categories: list[str]) -> list[str]:
    expected = set(expected_categories)
    actual = set(mapping)
    errors: list[str] = []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(f"mapping missing categories: {missing}")
    if extra:
        errors.append(f"mapping has unknown categories: {extra}")
    invalid_targets = {old: new for old, new in mapping.items() if new not in CATEGORY_VOCAB}
    if invalid_targets:
        errors.append(f"mapping targets outside vocab: {invalid_targets}")
    return errors


def _target_counts(interests: list[dict[str, Any]], mapping: dict[str, str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in interests:
        counts[mapping.get(_raw_category(item), FALLBACK_CATEGORY)] += 1
    return dict(counts)
