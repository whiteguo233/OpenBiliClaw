from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.memory.manager import MemoryManager

if TYPE_CHECKING:
    from pathlib import Path


def test_initialize_sets_up_database(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)

    memory.initialize()

    events = memory.query_events()
    assert events == []


def test_profile_overrides_roundtrip(tmp_path: Path) -> None:
    from openbiliclaw.soul.overrides import ListEdit, ProfileOverrides

    memory = MemoryManager(tmp_path)
    memory.initialize()

    ov = ProfileOverrides(list_edits={"core.core_traits": ListEdit(add=["务实"])})
    memory.save_profile_overrides(ov)

    loaded = memory.load_profile_overrides()
    assert loaded.list_edits["core.core_traits"].add == ["务实"]


def test_load_profile_overrides_missing_returns_empty(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    assert memory.load_profile_overrides().is_empty()


def test_sync_profile_files_renders_effective_profile(tmp_path: Path) -> None:
    from openbiliclaw.soul.overrides import ProfileOverrides, apply_edit
    from openbiliclaw.soul.profile import OnionProfile

    memory = MemoryManager(tmp_path)
    memory.initialize()

    ov, _ = apply_edit(
        ProfileOverrides(), target="personality_portrait", op="set", value="我自己写的画像"
    )
    memory.save_profile_overrides(ov)

    # Sync the raw AI profile (as a rebuild would) — different portrait text.
    memory.sync_profile_files(OnionProfile(personality_portrait="AI 生成的画像"))

    md = (tmp_path / "memory" / "soul_profile.md").read_text(encoding="utf-8")
    assert "我自己写的画像" in md
    assert "AI 生成的画像" not in md


def test_sync_profile_files_applies_overlay_on_dict_input(tmp_path: Path) -> None:
    from openbiliclaw.soul.overrides import ProfileOverrides, apply_edit
    from openbiliclaw.soul.profile import OnionProfile

    memory = MemoryManager(tmp_path)
    memory.initialize()

    ov, _ = apply_edit(
        ProfileOverrides(), target="personality_portrait", op="set", value="覆盖文案"
    )
    memory.save_profile_overrides(ov)

    memory.sync_profile_files(OnionProfile(personality_portrait="原始").to_dict())

    md = (tmp_path / "memory" / "soul_profile.md").read_text(encoding="utf-8")
    assert "覆盖文案" in md


def test_propagate_event_documents_event_only_contract() -> None:
    doc = inspect.getdoc(MemoryManager.propagate_event) or ""
    source = inspect.getsource(MemoryManager.propagate_event)

    assert "only persists" in doc
    assert "ProfileUpdatePipeline" in doc
    assert "upward" not in doc
    assert "TODO" not in source


@pytest.mark.asyncio
async def test_propagate_event_persists_to_sqlite(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    await memory.propagate_event(
        {
            "event_type": "view",
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "测试视频",
            "metadata": {"bvid": "BV1xx411c7mD"},
        }
    )

    events = memory.query_events(event_types=["view"])
    assert len(events) == 1
    assert events[0]["title"] == "测试视频"
    assert "BV1xx411c7mD" in events[0]["metadata"]


@pytest.mark.asyncio
async def test_propagate_events_batches_init_imports(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    inserted = await memory.propagate_events(
        [
            {
                "event_type": "view",
                "url": "https://example.test/1",
                "title": "批量事件 1",
                "context": "看完第一条",
                "metadata": {},
            },
            {
                "event_type": "favorite",
                "url": "https://example.test/2",
                "title": "批量事件 2",
                "context": "收藏第二条",
                "metadata": {},
            },
        ]
    )

    assert inserted == 2
    rows = memory.query_events(limit=10)
    assert {row["title"] for row in rows} == {"批量事件 1", "批量事件 2"}
    assert all("signal_strength" in row["metadata"] for row in rows)


@pytest.mark.asyncio
async def test_propagate_event_adds_default_signal_strength_without_overriding(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    await memory.propagate_event(
        {
            "event_type": "view",
            "title": "直接写入的浏览事件",
            "metadata": {"source_platform": "web"},
        }
    )
    await memory.propagate_event(
        {
            "event_type": "follow",
            "title": "平台自定义订阅强度",
            "metadata": {"source_platform": "youtube", "signal_strength": 1.0},
        }
    )

    events = memory.query_events(limit=10)
    by_title = {event["title"]: event for event in events}

    direct_metadata = json.loads(str(by_title["直接写入的浏览事件"]["metadata"]))
    platform_metadata = json.loads(str(by_title["平台自定义订阅强度"]["metadata"]))
    assert direct_metadata["signal_strength"] == 0.35
    assert platform_metadata["signal_strength"] == 1.0


@pytest.mark.asyncio
async def test_propagate_event_accepts_extension_behavior_types(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    for event_type in ["snapshot", "scroll", "hover", "pause", "seek", "coin", "reshuffle"]:
        await memory.propagate_event(
            {
                "event_type": event_type,
                "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "title": f"{event_type} 事件",
                "metadata": {"bvid": "BV1xx411c7mD"},
            }
        )

    events = memory.query_events(limit=20)
    persisted_types = {event["event_type"] for event in events}

    for event_type in ["snapshot", "scroll", "hover", "pause", "seek", "coin", "reshuffle"]:
        assert event_type in persisted_types


def test_query_events_and_stats_delegate_to_database(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    older = datetime.now() - timedelta(days=3)
    memory._database.conn.execute(
        """
        INSERT INTO events (event_type, url, title, context, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "search",
            "https://search.bilibili.com/all?keyword=music",
            "music",
            "{}",
            '{"keyword": "music"}',
            older.isoformat(sep=" "),
        ),
    )
    memory._database.conn.execute(
        """
        INSERT INTO events (event_type, url, title, context, metadata)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "feedback",
            "https://www.bilibili.com/video/BV1feedback",
            "feedback",
            "{}",
            '{"value": "like"}',
        ),
    )
    memory._database.conn.commit()

    queried = memory.query_events(keyword="like")
    stats = memory.get_event_stats()

    assert len(queried) == 1
    assert queried[0]["event_type"] == "feedback"
    assert stats == {"feedback": 1, "search": 1}


@pytest.mark.asyncio
async def test_propagate_event_persists_classification(tmp_path: Path) -> None:
    """MemoryManager.propagate_event flows through Database.insert_event,
    which is the single owner of classify_event_satisfaction. End-to-end
    we should see the classification land on the row."""
    memory = MemoryManager(tmp_path)
    memory.initialize()

    await memory.propagate_event(
        {
            "event_type": "click",
            "url": "https://www.bilibili.com/video/BVquick",
            "title": "标题党 2 秒",
            "metadata": {"watch_seconds": 2, "video_duration_seconds": 600},
        }
    )

    row = memory._database.conn.execute(
        "SELECT inferred_satisfaction, satisfaction_reason FROM events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["inferred_satisfaction"] == "negative"
    assert row["satisfaction_reason"] == "quick_exit"


@pytest.mark.asyncio
async def test_query_events_satisfaction_modes_passthrough(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    await memory.propagate_event(
        {"event_type": "like", "url": "https://a", "title": "深度教程", "metadata": {}}
    )
    await memory.propagate_event(
        {
            "event_type": "click",
            "url": "https://b",
            "title": "标题党",
            "metadata": {"watch_seconds": 2, "video_duration_seconds": 600},
        }
    )

    positives = memory.query_events(satisfaction_modes=frozenset({"positive"}), limit=10)
    assert {row["title"] for row in positives} == {"深度教程"}

    negatives = memory.query_events(satisfaction_modes=frozenset({"negative"}), limit=10)
    assert {row["title"] for row in negatives} == {"标题党"}


def test_get_core_memory_returns_trimmed_summary(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update(
        {
            "personality_portrait": "portrait",
            "core_traits": ["理性", "谨慎"],
            "values": ["成长", "真实"],
            "life_stage": "探索阶段",
            "deep_needs": ["被理解"],
        }
    )
    memory.get_layer("preference").data.update(
        {
            "interests": [
                {"name": "科技", "category": "知识", "weight": 0.9},
                {"name": "历史", "category": "知识", "weight": 0.8},
            ],
            "favorite_up_users": ["何同学", "影视飓风"],
            "disliked_topics": ["标题党"],
        }
    )
    memory.get_layer("awareness").data.update(
        {
            "notes": [
                {"date": "2026-03-08", "observation": "最近更专注。"},
                {"date": "2026-03-07", "observation": "晚上更容易进入深度浏览。"},
            ]
        }
    )
    memory.get_layer("insight").data.update(
        {
            "hypotheses": [
                {"hypothesis": "可能在寻找掌控感。", "confidence": 0.7},
                {"hypothesis": "内容选择偏向结构清晰的表达。", "confidence": 0.62},
            ]
        }
    )

    core = memory.get_core_memory()

    assert core["soul_summary"]["personality_portrait"] == "portrait"
    assert core["preference_summary"]["top_interests"][0]["name"] == "科技"
    assert core["recent_awareness"][0]["observation"] == "最近更专注。"
    assert core["active_insights"][0]["hypothesis"] == "可能在寻找掌控感。"


def test_render_core_memory_prompt_uses_stable_section_order(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update({"personality_portrait": "portrait"})
    memory.get_layer("preference").data.update(
        {"interests": [{"name": "科技", "category": "知识", "weight": 0.9}]}
    )
    memory.get_layer("awareness").data.update(
        {"notes": [{"date": "2026-03-08", "observation": "最近更专注。"}]}
    )
    memory.get_layer("insight").data.update(
        {"hypotheses": [{"hypothesis": "可能在寻找掌控感。", "confidence": 0.7}]}
    )

    prompt = memory.render_core_memory_prompt()

    assert prompt.index("## 用户画像") < prompt.index("## 偏好摘要")
    assert prompt.index("## 偏好摘要") < prompt.index("## 近期观察")
    assert prompt.index("## 近期观察") < prompt.index("## 当前洞察")


def _build_onion_core_memory_fixture(memory_dir: Path) -> MemoryManager:
    """Populate a manager with the same onion profile as the (c) golden snapshot.

    Kept byte-identical to ``scripts``/``tests/golden/chat_core_memory`` so the
    section-parity assertions compare like-for-like against the pre-change
    render captured before the stable/volatile split landed.
    """
    memory = MemoryManager(memory_dir)
    memory.initialize()
    memory.get_layer("soul").data.update(
        {
            "version": 2,
            "personality_portrait": "一个理性又敏感的深度内容爱好者，偏好结构化叙事。",
            "core": {
                "core_traits": ["理性", "敏感", "好奇"],
                "deep_needs": ["被理解", "掌控感"],
                "mbti": {
                    "type": "INTJ",
                    "dimensions": {},
                    "confidence": 0.7,
                    "inferred_from": [],
                },
            },
            "values_layer": {"values": ["成长", "真实"], "motivational_drivers": ["求知"]},
            "role": {"life_stage": "探索阶段", "current_phase": ""},
            "interest": {
                "likes": [
                    {
                        "domain": "科技",
                        "weight": 0.9,
                        "specifics": [{"name": "AI", "weight": 0.9}],
                        "first_seen": "",
                        "last_seen": "",
                        "source": "ai",
                    },
                    {
                        "domain": "历史",
                        "weight": 0.8,
                        "specifics": [],
                        "first_seen": "",
                        "last_seen": "",
                        "source": "ai",
                    },
                ],
                "dislikes": [
                    {
                        "domain": "标题党",
                        "weight": 0.9,
                        "specifics": [],
                        "first_seen": "",
                        "last_seen": "",
                        "source": "ai",
                    }
                ],
                "favorite_up_users": ["何同学", "影视飓风"],
            },
            "surface": {
                "cognitive_style": [],
                "style": {},
                "context": {},
                "exploration_openness": 0.6,
            },
            "source_platform_mix": {},
        }
    )
    memory.get_layer("preference").data.update({"style": {}, "exploration_openness": 0.6})
    memory.get_layer("awareness").data.update(
        {
            "notes": [
                {"date": "2026-03-08", "observation": "最近更专注。"},
                {"date": "2026-03-07", "observation": "晚上更容易进入深度浏览。"},
            ]
        }
    )
    memory.get_layer("insight").data.update(
        {
            "hypotheses": [
                {"hypothesis": "可能在寻找掌控感。", "confidence": 0.7},
                {"hypothesis": "内容选择偏向结构清晰的表达。", "confidence": 0.62},
            ]
        }
    )
    return memory


def _split_core_memory_sections(rendered: str) -> list[str]:
    """Split a rendered core-memory block into its ``## …`` sections."""
    sections: list[str] = []
    for chunk in rendered.split("\n\n## "):
        chunk = chunk.strip()
        if not chunk:
            continue
        sections.append(chunk if chunk.startswith("## ") else "## " + chunk)
    return sections


def test_render_core_memory_prompt_reflects_user_portrait_override(tmp_path: Path) -> None:
    # (a) Chat must honour a user portrait edit. Pre-split, get_core_memory read
    # the raw soul layer and ignored profile_overrides.json, so this failed.
    from openbiliclaw.soul.overrides import ProfileOverrides, apply_edit
    from openbiliclaw.soul.profile import OnionProfile

    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update(
        OnionProfile(personality_portrait="AI 生成的原始画像").to_dict()
    )
    ov, _ = apply_edit(
        ProfileOverrides(),
        target="personality_portrait",
        op="set",
        value="我手动改写的画像",
    )
    memory.save_profile_overrides(ov)

    prompt = memory.render_core_memory_prompt()

    assert "我手动改写的画像" in prompt
    assert "AI 生成的原始画像" not in prompt


def test_render_core_memory_blocks_keep_stable_prefix_across_awareness_churn(
    tmp_path: Path,
) -> None:
    # (b) The system-bound stable block must survive awareness churn (prompt-cache
    # prefix stability); only the user-bound volatile block may change.
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update({"personality_portrait": "稳定画像"})
    memory.get_layer("preference").data.update(
        {"interests": [{"name": "科技", "category": "知识", "weight": 0.9}]}
    )
    memory.get_layer("awareness").data.update(
        {"notes": [{"date": "2026-03-08", "observation": "第一版观察"}]}
    )

    stable_before, volatile_before = memory.render_core_memory_blocks()

    memory.get_layer("awareness").data.update(
        {"notes": [{"date": "2026-03-09", "observation": "完全不同的新观察"}]}
    )
    stable_after, volatile_after = memory.render_core_memory_blocks()

    assert stable_before == stable_after
    assert volatile_before != volatile_after
    assert "第一版观察" not in stable_before
    assert "第一版观察" in volatile_before


def test_chat_core_memory_split_preserves_prechange_sections(tmp_path: Path) -> None:
    # (c) Section-parity golden: the split loses no content that the pre-change
    # single-block render produced, and moves awareness/insights to the volatile
    # (user-bound) block while identity/preference stay in the stable block.
    from pathlib import Path as _Path

    memory = _build_onion_core_memory_fixture(tmp_path)

    stable, volatile = memory.render_core_memory_blocks()
    combined = memory.render_core_memory_prompt()

    golden = (
        _Path(__file__).parent / "golden" / "chat_core_memory" / "pre_change_render.txt"
    ).read_text(encoding="utf-8")
    old_sections = _split_core_memory_sections(golden)
    assert len(old_sections) == 4
    for section in old_sections:
        assert section in combined, f"lost pre-change section: {section!r}"

    assert "## 近期观察" in volatile and "## 近期观察" not in stable
    assert "## 当前洞察" in volatile and "## 当前洞察" not in stable
    assert "## 用户画像" in stable and "## 用户画像" not in volatile
    assert "## 偏好摘要" in stable


def test_feedback_state_defaults_when_missing(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    state = memory.load_feedback_state()

    assert state == {
        "last_processed_feedback_event_id": 0,
        "last_feedback_reanalyzed_at": "",
        # Unified interest line one-shot migration marker; empty = never ran.
        # Shares this file with the cursor so one write persists both.
        "unified_interest_line_migrated_at": "",
        "feedback_owner_version": 0,
        "feedback_owner_cutover_at": "",
    }


def test_feedback_state_save_preserves_feedback_owner_markers(tmp_path: Path) -> None:
    """A cursor-only save (the legacy batch) must not clear owner markers.

    Without the read-back, a single legacy-batch write after a rollback would
    erase the marker and let the one-shot migration replay — double-ingesting
    every feedback row past the cursor.
    """
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.save_feedback_state(
        {
            "last_processed_feedback_event_id": 5,
            "last_feedback_reanalyzed_at": "",
            "unified_interest_line_migrated_at": "2026-07-28T00:00:00",
            "feedback_owner_version": 2,
            "feedback_owner_cutover_at": "2026-08-01T00:00:00",
        }
    )

    memory.save_feedback_state(
        {
            "last_processed_feedback_event_id": 9,
            "last_feedback_reanalyzed_at": "2026-07-28T01:00:00",
        }
    )

    state = memory.load_feedback_state()
    assert state["last_processed_feedback_event_id"] == 9
    assert state["unified_interest_line_migrated_at"] == "2026-07-28T00:00:00"
    assert state["feedback_owner_version"] == 2
    assert state["feedback_owner_cutover_at"] == "2026-08-01T00:00:00"


def test_feedback_state_round_trips_to_json(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    memory.save_feedback_state(
        {
            "last_processed_feedback_event_id": 12,
            "last_feedback_reanalyzed_at": "2026-03-09T12:00:00",
            "feedback_owner_version": 2,
            "feedback_owner_cutover_at": "2026-08-01T01:02:03",
        }
    )

    state = memory.load_feedback_state()

    assert state["last_processed_feedback_event_id"] == 12
    assert state["last_feedback_reanalyzed_at"] == "2026-03-09T12:00:00"
    assert state["feedback_owner_version"] == 2
    assert state["feedback_owner_cutover_at"] == "2026-08-01T01:02:03"


def test_discovery_runtime_state_defaults_when_missing(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    state = memory.load_discovery_runtime_state()

    assert state == {
        "last_event_refresh_at": "",
        "last_trending_refresh_at": "",
        "last_explore_refresh_at": "",
        "last_processed_event_id": 0,
        "last_notification_at": "",
        "last_discovered_count": 0,
        "last_replenished_count": 0,
        "recent_pool_topics": [],
        "probed_domains": {},
        "probed_axes": {},
        "probed_distance_bands": {},
        "probe_feedback_history": [],
        "short_term_exploration_buffer": {"entries": []},
        "probed_avoidance_domains": {},
        "probed_avoidance_axes": {},
        "avoidance_probe_feedback_history": [],
        "last_probe_kind": "",
    }


def test_discovery_runtime_state_round_trips_to_json(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    memory.save_discovery_runtime_state(
        {
            "last_event_refresh_at": "2026-03-10T10:00:00",
            "last_trending_refresh_at": "2026-03-10T09:00:00",
            "last_explore_refresh_at": "2026-03-10T00:00:00",
            "last_processed_event_id": 42,
            "last_notification_at": "2026-03-10T10:30:00",
            "last_discovered_count": 18,
            "last_replenished_count": 12,
            "recent_pool_topics": ["国际时事", "宏观经济", "纪录片"],
            "probed_domains": {"建筑美学": "2026-03-10T10:30:00"},
            "probed_axes": {"aesthetic|light": "2026-03-10T10:30:00"},
            "probed_distance_bands": {"bridge": "2026-05-24T12:00:00"},
            "short_term_exploration_buffer": {
                "entries": [
                    {
                        "domain": "城市基础设施观察",
                        "score": 1.5,
                    }
                ]
            },
            "probed_avoidance_domains": {"浅层热点复读": "2026-05-24T10:00:00"},
            "probed_avoidance_axes": {"knowledge|light": "2026-05-24T10:00:00"},
            "last_probe_kind": "avoidance",
            "avoidance_probe_feedback_history": [
                {
                    "domain": "浅层热点复读",
                    "response": "confirm",
                    "axis": "knowledge|light",
                    "created_at": "2026-05-24T10:01:00",
                }
            ],
            "probe_feedback_history": [
                {
                    "domain": "城市漫游路线",
                    "response": "reject",
                    "axis": "wander_observe|light",
                    "created_at": "2026-05-15T10:00:00",
                }
            ],
        }
    )

    state = memory.load_discovery_runtime_state()

    assert state["last_event_refresh_at"] == "2026-03-10T10:00:00"
    assert state["last_trending_refresh_at"] == "2026-03-10T09:00:00"
    assert state["last_explore_refresh_at"] == "2026-03-10T00:00:00"
    assert state["last_processed_event_id"] == 42
    assert state["last_notification_at"] == "2026-03-10T10:30:00"
    assert state["last_discovered_count"] == 18
    assert state["last_replenished_count"] == 12
    assert state["recent_pool_topics"] == ["国际时事", "宏观经济", "纪录片"]
    assert state["probed_domains"] == {"建筑美学": "2026-03-10T10:30:00"}
    assert state["probed_axes"] == {"aesthetic|light": "2026-03-10T10:30:00"}
    assert state["probed_distance_bands"] == {"bridge": "2026-05-24T12:00:00"}
    assert state["short_term_exploration_buffer"] == {
        "entries": [
            {
                "domain": "城市基础设施观察",
                "score": 1.5,
            }
        ]
    }
    assert state["probed_avoidance_domains"] == {"浅层热点复读": "2026-05-24T10:00:00"}
    assert state["probed_avoidance_axes"] == {"knowledge|light": "2026-05-24T10:00:00"}
    assert state["last_probe_kind"] == "avoidance"
    assert state["avoidance_probe_feedback_history"] == [
        {
            "domain": "浅层热点复读",
            "response": "confirm",
            "axis": "knowledge|light",
            "created_at": "2026-05-24T10:01:00",
        }
    ]
    assert state["probe_feedback_history"] == [
        {
            "domain": "城市漫游路线",
            "response": "reject",
            "axis": "wander_observe|light",
            "created_at": "2026-05-15T10:00:00",
        }
    ]


def test_discovery_runtime_state_round_trips_probed_distance_bands(tmp_path: Path) -> None:
    memory = MemoryManager(data_dir=tmp_path)
    memory.save_discovery_runtime_state(
        {
            "probed_distance_bands": {"bridge": "2026-05-24T12:00:00"},
        }
    )

    state = memory.load_discovery_runtime_state()

    assert state["probed_distance_bands"] == {"bridge": "2026-05-24T12:00:00"}


def test_discovery_runtime_state_round_trips_short_term_exploration_buffer(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(data_dir=tmp_path)
    memory.save_discovery_runtime_state(
        {
            "short_term_exploration_buffer": {
                "entries": [{"domain": "城市基础设施观察", "score": 1.5}]
            },
        }
    )

    state = memory.load_discovery_runtime_state()

    assert state["short_term_exploration_buffer"] == {
        "entries": [{"domain": "城市基础设施观察", "score": 1.5}]
    }


def test_discovery_runtime_state_round_trips_probe_feedback_history(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    memory.save_discovery_runtime_state(
        {
            "probe_feedback_history": [
                {
                    "domain": "城市漫游路线",
                    "response": "reject",
                    "axis": "wander_observe|light",
                    "specifics": ["老街路线"],
                    "created_at": "2026-05-15T10:00:00",
                }
            ]
        }
    )

    state = memory.load_discovery_runtime_state()

    assert state["probe_feedback_history"] == [
        {
            "domain": "城市漫游路线",
            "response": "reject",
            "axis": "wander_observe|light",
            "specifics": ["老街路线"],
            "created_at": "2026-05-15T10:00:00",
        }
    ]


def test_discovery_runtime_state_preserves_full_avoidance_feedback_history(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    memory.save_discovery_runtime_state(
        {
            "avoidance_probe_feedback_history": [
                {"domain": f"避雷{i}", "response": "reject"} for i in range(105)
            ]
        }
    )

    state = memory.load_discovery_runtime_state()

    assert len(state["avoidance_probe_feedback_history"]) == 105
    assert state["avoidance_probe_feedback_history"][0]["domain"] == "避雷0"
    assert state["avoidance_probe_feedback_history"][-1]["domain"] == "避雷104"


def test_update_discovery_runtime_state_preserves_feedback_history(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    stale = memory.load_discovery_runtime_state()
    latest = memory.update_discovery_runtime_state(
        lambda state: state["probe_feedback_history"].append(
            {
                "domain": "建筑美学",
                "response": "confirm",
                "created_at": "2026-06-09T10:00:00",
            }
        )
    )
    assert latest["probe_feedback_history"]

    stale["probed_domains"] = {"建筑美学": "2026-06-09T10:00:01"}
    memory.save_discovery_runtime_state(stale)

    state = memory.load_discovery_runtime_state()
    assert state["probe_feedback_history"][0]["domain"] == "建筑美学"
    assert state["probed_domains"] == {"建筑美学": "2026-06-09T10:00:01"}


def test_stale_discovery_runtime_save_preserves_probe_runtime_maps(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    stale = memory.load_discovery_runtime_state()
    memory.update_discovery_runtime_state(
        lambda state: state.setdefault("probed_domains", {}).update(
            {"建筑美学": "2026-06-09T10:00:00"}
        )
    )

    stale["last_notification_at"] = "2026-06-09T10:00:01"
    memory.save_discovery_runtime_state(stale)

    state = memory.load_discovery_runtime_state()
    assert state["last_notification_at"] == "2026-06-09T10:00:01"
    assert state["probed_domains"] == {"建筑美学": "2026-06-09T10:00:00"}


def test_stale_discovery_runtime_save_does_not_revert_last_probe_kind(
    tmp_path: Path,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    stale = memory.load_discovery_runtime_state()
    stale["last_probe_kind"] = "interest"
    memory.update_discovery_runtime_state(
        lambda state: state.update({"last_probe_kind": "avoidance"})
    )

    stale["last_notification_at"] = "2026-06-09T10:00:01"
    memory.save_discovery_runtime_state(stale)

    state = memory.load_discovery_runtime_state()
    assert state["last_probe_kind"] == "avoidance"


def test_account_sync_state_defaults_when_missing(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    state = memory.load_account_sync_state()

    assert state == {
        "last_history_view_at": 0,
        "last_history_bvid": "",
        "history_bvids_at_last_view_at": [],
        "last_favorites_sync_at": "",
        "favorite_signature": "",
        "favorite_bvids": [],
        "last_following_sync_at": "",
        "following_signature": "",
        "following_mids": [],
        "last_account_sync_at": "",
        "last_sync_error": "",
        "last_sync_error_kind": "",
        "last_sync_issues": [],
    }


def test_account_sync_state_round_trips_to_json(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    memory.save_account_sync_state(
        {
            "last_history_view_at": 1710000000,
            "last_history_bvid": "BV1SYNC",
            "history_bvids_at_last_view_at": ["BV1SYNC", "BV2SYNC"],
            "last_favorites_sync_at": "2026-03-14T12:00:00",
            "favorite_signature": "fav:abc",
            "favorite_bvids": ["BVF1", "BVF2"],
            "last_following_sync_at": "2026-03-14T12:10:00",
            "following_signature": "follow:def",
            "following_mids": ["1", "2"],
            "last_account_sync_at": "2026-03-14T12:10:00",
            "last_sync_error": "",
        }
    )

    state = memory.load_account_sync_state()

    assert state["last_history_view_at"] == 1710000000
    assert state["last_history_bvid"] == "BV1SYNC"
    assert state["history_bvids_at_last_view_at"] == ["BV1SYNC", "BV2SYNC"]
    assert state["last_favorites_sync_at"] == "2026-03-14T12:00:00"
    assert state["favorite_signature"] == "fav:abc"
    assert state["favorite_bvids"] == ["BVF1", "BVF2"]
    assert state["last_following_sync_at"] == "2026-03-14T12:10:00"
    assert state["following_signature"] == "follow:def"
    assert state["following_mids"] == ["1", "2"]
    assert state["last_account_sync_at"] == "2026-03-14T12:10:00"
    assert state["last_sync_error"] == ""


def test_source_bootstrap_state_defaults_when_missing(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    state = memory.load_source_bootstrap_state()

    assert state == {
        "xhs_seen_note_keys": [],
        "dy_seen_video_keys": [],
        "yt_seen_item_keys": [],
        "zhihu_seen_item_keys": [],
        "reddit_seen_item_keys": [],
        "linuxdo_seen_item_keys": [],
        "linuxdo_account_key": "",
        "v2ex_seen_item_keys": [],
        "weibo_seen_item_keys": [],
        "weibo_account_key": "",
        "instagram_seen_item_keys": [],
        "instagram_account_key": "",
        "last_source_bootstrap_sync_at": "",
        "source_incremental": {
            "cursor": "",
            "last_attempt_at": {},
            "last_success_at": {},
            "active_task": None,
        },
    }


def test_source_bootstrap_state_round_trips_to_json(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    memory.save_source_bootstrap_state(
        {
            "xhs_seen_note_keys": ["saved:xhs-1"],
            "dy_seen_video_keys": ["dy_collect:dy-1"],
            "yt_seen_item_keys": ["yt_history:yt-1"],
            "zhihu_seen_item_keys": ["zhihu_favorite:zh-1"],
            "reddit_seen_item_keys": ["t3:reddit-1"],
            "linuxdo_seen_item_keys": ["linuxdo:sha256:test:linuxdo_bookmarks:topic:42"],
            "linuxdo_account_key": "sha256:test-account",
            "v2ex_seen_item_keys": ["alice:favorite_topics:123"],
            "last_source_bootstrap_sync_at": "2026-05-20T12:00:00",
            "source_incremental": {
                "cursor": "reddit",
                "last_attempt_at": {"reddit": "2026-05-20T12:01:00+00:00"},
                "last_success_at": {"linuxdo": "2026-05-20T12:02:00+00:00"},
                "active_task": {"source": "reddit", "task_id": "task-1"},
            },
        }
    )

    state = memory.load_source_bootstrap_state()

    assert state["xhs_seen_note_keys"] == ["saved:xhs-1"]
    assert state["dy_seen_video_keys"] == ["dy_collect:dy-1"]
    assert state["yt_seen_item_keys"] == ["yt_history:yt-1"]
    assert state["zhihu_seen_item_keys"] == ["zhihu_favorite:zh-1"]
    assert state["reddit_seen_item_keys"] == ["t3:reddit-1"]
    assert state["linuxdo_seen_item_keys"] == ["linuxdo:sha256:test:linuxdo_bookmarks:topic:42"]
    assert state["linuxdo_account_key"] == "sha256:test-account"
    assert state["v2ex_seen_item_keys"] == ["alice:favorite_topics:123"]
    assert state["last_source_bootstrap_sync_at"] == "2026-05-20T12:00:00"
    assert state["source_incremental"] == {
        "cursor": "reddit",
        "last_attempt_at": {"reddit": "2026-05-20T12:01:00+00:00"},
        "last_success_at": {"linuxdo": "2026-05-20T12:02:00+00:00"},
        "active_task": {"source": "reddit", "task_id": "task-1"},
    }


def test_insight_candidates_default_to_empty_list(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    candidates = memory.load_insight_candidates()

    assert candidates == []


def test_save_insight_candidates_round_trips_to_json(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    memory.save_insight_candidates(
        [
            {
                "id": "cand-1",
                "kind": "goal",
                "content": "想更系统地理解国际局势",
                "confidence": 0.88,
                "evidence": "用户反复提到想看更深的国际时事分析。",
                "occurrences": 2,
                "confirmed": False,
                "created_at": "2026-03-10T10:00:00",
                "updated_at": "2026-03-10T10:05:00",
            }
        ]
    )

    loaded = memory.load_insight_candidates()

    assert len(loaded) == 1
    assert loaded[0]["kind"] == "goal"
    assert loaded[0]["occurrences"] == 2


def test_cognition_updates_default_to_empty_list(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    updates = memory.load_cognition_updates()

    assert updates == []


def test_save_cognition_updates_round_trips_to_json(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    memory.save_cognition_updates(
        [
            {
                "id": "cog-1",
                "kind": "interest_added",
                "summary": "阿B 现在更确定你会吃讲透来龙去脉这一口。",
                "confidence": 0.86,
                "source": "feedback",
                "notified": False,
                "created_at": "2026-03-10T12:00:00",
            },
            {
                "id": "cog-2",
                "kind": "profile_shift",
                "summary": "我对你又对上了一点：你不是只看热闹的人。",
                "confidence": 0.9,
                "source": "profile_refresh",
                "notified": True,
                "created_at": "2026-03-10T13:00:00",
            },
        ]
    )

    updates = memory.load_cognition_updates()

    assert len(updates) == 2
    assert updates[0]["kind"] == "interest_added"
    assert updates[0]["notified"] is False
    assert updates[1]["kind"] == "profile_shift"
    assert updates[1]["notified"] is True


def test_memory_layer_load_uses_utf8_even_when_default_locale_is_gbk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for the Windows GBK bug.

    On Chinese Windows with the default locale, ``open(path)`` (no
    explicit encoding) decodes as GBK. The data files this layer reads
    contain Chinese profile text + emoji that GBK can't represent, so
    /api/activity-feed and /api/delight/pending-batch returned 500.

    To reproduce on a host whose actual default IS UTF-8 (CI), we
    monkeypatch the builtin ``open`` so any call without an explicit
    ``encoding=`` falls back to GBK. If MemoryLayer.load() is missing
    its encoding kwarg, this test will raise UnicodeDecodeError.
    """
    import builtins
    import json as json_module

    from openbiliclaw.memory.manager import MemoryLayer

    layer_path = tmp_path / "core.json"
    payload = {
        "summary": "你最近吃讲透因果链这一口 🤔",
        "tags": ["因果", "深度", "结构感", "💡"],
    }
    # Pre-populate as UTF-8 — what the real backend writes.
    layer_path.write_text(
        json_module.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    real_open = builtins.open

    def gbk_default_open(*args, **kwargs):  # type: ignore[no-untyped-def]
        # If caller didn't specify encoding AND it's a text-mode open,
        # force GBK to simulate Chinese Windows. Binary mode is left
        # untouched (tomllib uses 'rb' and we should never break that).
        if "encoding" not in kwargs:
            mode = (args[1] if len(args) > 1 else kwargs.get("mode", "r")) or "r"
            if "b" not in mode:
                kwargs["encoding"] = "gbk"
        return real_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", gbk_default_open)

    layer = MemoryLayer(name="core", storage_path=layer_path)
    layer.load()  # Would raise UnicodeDecodeError without the fix.
    assert layer.data == payload

    layer.data["summary"] = "更新后的画像 ✨"
    layer.save()  # Would raise UnicodeEncodeError without the fix.

    # Sanity round-trip via real open() to confirm the file is still
    # valid UTF-8 (i.e. save() also pinned the encoding correctly).
    monkeypatch.setattr(builtins, "open", real_open)
    reloaded = json_module.loads(layer_path.read_text(encoding="utf-8"))
    assert reloaded["summary"] == "更新后的画像 ✨"


def test_load_profile_overrides_corrupt_file_returns_empty(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    path = tmp_path / "memory" / "profile_overrides.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", encoding="utf-8")

    # Corrupt overrides must not raise (which would degrade the profile API).
    assert memory.load_profile_overrides().is_empty()


def test_account_sync_state_default_includes_error_kind(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    # Missing state file must still expose the full schema — a caller reading
    # last_sync_error_kind should never hit a KeyError.
    assert memory.load_account_sync_state()["last_sync_error_kind"] == ""


def test_account_sync_state_roundtrips_error_kind(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    state = memory.load_account_sync_state()
    state["last_sync_error"] = "Bilibili session expired on /x/web-interface/nav (-101)."
    state["last_sync_error_kind"] = "auth_expired"
    state["last_sync_issues"] = [
        {"stage": "bilibili_history", "kind": "auth_expired"},
        {"stage": "bilibili_favorites", "kind": "auth_expired"},
    ]
    memory.save_account_sync_state(state)

    # Regression: the save/load whitelists used to drop this field, so the
    # desktop's auth_expired branch was unreachable and users saw raw English.
    reloaded = memory.load_account_sync_state()
    assert reloaded["last_sync_error_kind"] == "auth_expired"
    assert reloaded["last_sync_issues"] == state["last_sync_issues"]


def test_account_sync_state_rejects_malformed_issue_rows(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    state = memory.load_account_sync_state()
    state["last_sync_issues"] = [
        {"stage": "bilibili_history", "kind": "timeout"},
        {"stage": ["not", "a", "string"], "kind": "api_error"},
        {"stage": "bilibili_favorites", "kind": {"nested": "value"}},
        "not-an-object",
        {"stage": "bilibili_history", "kind": "timeout"},
    ]
    memory.save_account_sync_state(state)

    assert memory.load_account_sync_state()["last_sync_issues"] == [
        {"stage": "bilibili_history", "kind": "timeout"}
    ]


def test_account_sync_state_reads_legacy_file_without_error_kind(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    path = tmp_path / "memory" / "account_sync_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_account_sync_at": "2026-07-18T06:36:39+00:00"}),
        encoding="utf-8",
    )

    # State files written before the field existed must stay readable.
    loaded = memory.load_account_sync_state()
    assert loaded["last_sync_error_kind"] == ""
    assert loaded["last_sync_issues"] == []
    assert loaded["last_account_sync_at"] == "2026-07-18T06:36:39+00:00"
