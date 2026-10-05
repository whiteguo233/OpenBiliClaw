from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from openbiliclaw.config import Config, load_config, save_config

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
from openbiliclaw.discovery.engine import DiscoveredContent, DiscoveryStrategy
from openbiliclaw.recommendation.publication_preference import (
    PRESET_LAST_7_DAYS,
    PublicationDatePreference,
    evaluate_source_publication_preference,
)
from openbiliclaw.sources.platforms import CANONICAL_SOURCE_FAMILIES
from openbiliclaw.storage.database import Database


class _FakeStrategy(DiscoveryStrategy):
    name = "fake"
    source_platform = "youtube"
    date_preference: PublicationDatePreference | None = None

    async def discover(self, profile: object, limit: int = 20) -> list[DiscoveredContent]:
        return []


def test_all_source_configs_expose_date_preference_defaults() -> None:
    config = Config()

    for slug in CANONICAL_SOURCE_FAMILIES:
        source_cfg = getattr(config.sources, slug)
        assert source_cfg.recommendation_date_preset == "all"
        assert source_cfg.recommendation_date_start == ""
        assert source_cfg.recommendation_date_end == ""
        assert source_cfg.recommendation_date_weight == 0.5


def test_non_bilibili_source_date_preference_round_trips(tmp_path: Path) -> None:
    config = Config()
    config.sources.youtube.recommendation_date_preset = "custom"
    config.sources.youtube.recommendation_date_start = "2024-01-01"
    config.sources.youtube.recommendation_date_end = "2024-12-31"
    config.sources.youtube.recommendation_date_weight = 0.5

    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)

    assert loaded.sources.youtube.recommendation_date_preset == "custom"
    assert loaded.sources.youtube.recommendation_date_start == "2024-01-01"
    assert loaded.sources.youtube.recommendation_date_end == "2024-12-31"
    assert loaded.sources.youtube.recommendation_date_weight == 0.5


def test_instagram_date_preference_reaches_shared_admission(tmp_path: Path) -> None:
    from openbiliclaw.config import source_date_preferences

    config = Config()
    config.sources.instagram.recommendation_date_preset = "last_7_days"
    config.sources.instagram.recommendation_date_weight = 1.0
    path = tmp_path / "instagram.toml"
    save_config(config, path)
    loaded = load_config(path)
    preference = source_date_preferences(loaded)["instagram"]
    assert preference.preset == "last_7_days"
    assert preference.weight == 1.0
    assert not evaluate_source_publication_preference(
        published_at="2000-01-01T00:00:00Z",
        preference=preference,
        now=datetime(2026, 10, 5, tzinfo=UTC),
    ).eligible


def test_filter_candidates_for_eval_removes_out_of_window_before_eval() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    recent = DiscoveredContent(
        bvid="recent",
        source_platform="youtube",
        published_at=(now - timedelta(days=1)).isoformat(),
    )
    old = DiscoveredContent(
        bvid="old",
        source_platform="youtube",
        published_at="2000-01-01T00:00:00Z",
    )
    strategy = _FakeStrategy()
    strategy.date_preference = PublicationDatePreference(
        preset=PRESET_LAST_7_DAYS,
        weight=1.0,
    )

    filtered = strategy.filter_candidates_for_eval([recent, old], now=now)

    assert [item.bvid for item in filtered] == ["recent"]


def test_filter_candidates_for_eval_keeps_soft_mode_and_missing_dates() -> None:
    """Soft mode mirrors the raw enqueue gate: no pre-eval hard filtering."""

    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    recent = DiscoveredContent(
        bvid="recent",
        source_platform="youtube",
        published_at=(now - timedelta(days=1)).isoformat(),
    )
    old = DiscoveredContent(
        bvid="old",
        source_platform="youtube",
        published_at="2000-01-01T00:00:00Z",
    )
    missing = DiscoveredContent(
        bvid="missing",
        source_platform="youtube",
        published_at="",
        published_label="5 years ago",
    )
    strategy = _FakeStrategy()
    strategy.date_preference = PublicationDatePreference(
        preset=PRESET_LAST_7_DAYS,
        weight=0.5,
    )

    filtered = strategy.filter_candidates_for_eval([recent, old, missing], now=now)

    assert [item.bvid for item in filtered] == ["recent", "old", "missing"]


def test_filter_candidates_for_eval_strict_mode_drops_missing_dates() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    missing = DiscoveredContent(
        bvid="missing",
        source_platform="youtube",
        published_at="",
        published_label="5 years ago",
    )
    strategy = _FakeStrategy()
    strategy.date_preference = PublicationDatePreference(
        preset=PRESET_LAST_7_DAYS,
        weight=1.0,
    )

    filtered = strategy.filter_candidates_for_eval([missing], now=now)

    assert filtered == []


def test_filter_candidates_for_eval_all_preset_keeps_candidates() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    recent = DiscoveredContent(
        bvid="recent",
        source_platform="youtube",
        published_at=(now - timedelta(days=1)).isoformat(),
    )
    old = DiscoveredContent(
        bvid="old",
        source_platform="youtube",
        published_at="2000-01-01T00:00:00Z",
    )
    strategy = _FakeStrategy()
    strategy.date_preference = PublicationDatePreference()

    filtered = strategy.filter_candidates_for_eval([recent, old], now=now)

    assert [item.bvid for item in filtered] == ["recent", "old"]


def test_evaluate_source_publication_preference_does_not_platform_gate() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    decision = evaluate_source_publication_preference(
        published_at="2000-01-01T00:00:00Z",
        preference=PublicationDatePreference(preset=PRESET_LAST_7_DAYS, weight=1.0),
        now=now,
    )

    assert decision.in_range is False
    assert decision.eligible is False


def test_database_enqueue_filters_raw_candidates_by_source_date_preference(tmp_path: Path) -> None:
    """All source adapters share database.enqueue_discovery_candidates."""

    db = Database(tmp_path / "enqueue-date-filter.db")
    db.initialize()
    db.set_source_publication_date_preferences(
        {
            "youtube": PublicationDatePreference(
                preset=PRESET_LAST_7_DAYS,
                weight=1.0,
            )
        }
    )

    now = datetime.now(UTC)
    recent = DiscoveredContent(
        bvid="recent-yt",
        content_id="recent-yt",
        source_platform="youtube",
        published_at=(now - timedelta(days=1)).isoformat(),
    )
    old = DiscoveredContent(
        bvid="old-yt",
        content_id="old-yt",
        source_platform="youtube",
        published_at="2000-01-01T00:00:00Z",
    )

    inserted = db.enqueue_discovery_candidates(
        [
            discovered_content_to_candidate_write(recent, source_context="yt_search"),
            discovered_content_to_candidate_write(old, source_context="yt_search"),
        ]
    )

    assert inserted == 1


def test_database_enqueue_keeps_soft_mode_out_of_window_candidates(tmp_path: Path) -> None:
    """Soft mode must not silently starve sources whose dates are unknown/old."""

    db = Database(tmp_path / "enqueue-soft-date-filter.db")
    db.initialize()
    db.set_source_publication_date_preferences(
        {
            "youtube": PublicationDatePreference(
                preset=PRESET_LAST_7_DAYS,
                weight=0.5,
            )
        }
    )

    now = datetime.now(UTC)
    recent = DiscoveredContent(
        bvid="soft-recent-yt",
        content_id="soft-recent-yt",
        source_platform="youtube",
        published_at=(now - timedelta(days=1)).isoformat(),
    )
    old = DiscoveredContent(
        bvid="soft-old-yt",
        content_id="soft-old-yt",
        source_platform="youtube",
        published_at="2000-01-01T00:00:00Z",
    )
    # YouTube's primary scrapetube/InnerTube path only exposes a relative label.
    unknown = DiscoveredContent(
        bvid="soft-unknown-yt",
        content_id="soft-unknown-yt",
        source_platform="youtube",
        published_at="",
        published_label="5 years ago",
    )

    inserted = db.enqueue_discovery_candidates(
        [
            discovered_content_to_candidate_write(recent, source_context="yt_search"),
            discovered_content_to_candidate_write(old, source_context="yt_search"),
            discovered_content_to_candidate_write(unknown, source_context="yt_search"),
        ]
    )

    assert inserted == 3
    stats = db.publication_date_filter_stats()["youtube"]
    assert stats["input"] == 3
    assert stats["filtered_by_publication_date"] == 0
    assert stats["inserted"] == 3


def test_database_enqueue_strict_mode_excludes_missing_published_at(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An explicit strict preference still drops candidates with no date."""

    caplog.set_level("WARNING")

    db = Database(tmp_path / "enqueue-strict-missing-date.db")
    db.initialize()
    db.set_source_publication_date_preferences(
        {
            "youtube": PublicationDatePreference(
                preset=PRESET_LAST_7_DAYS,
                weight=1.0,
            )
        }
    )

    unknown = DiscoveredContent(
        bvid="strict-unknown-yt",
        content_id="strict-unknown-yt",
        source_platform="youtube",
        published_at="",
        published_label="5 years ago",
    )

    inserted = db.enqueue_discovery_candidates(
        [discovered_content_to_candidate_write(unknown, source_context="yt_search")]
    )

    assert inserted == 0
    stats = db.publication_date_filter_stats()["youtube"]
    assert stats["input"] == 1
    assert stats["filtered_by_publication_date"] == 1
    assert stats["inserted"] == 0
    assert any(
        "filtered all 1 candidate(s) from source=youtube" in message for message in caplog.messages
    )
