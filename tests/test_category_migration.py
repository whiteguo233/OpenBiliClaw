from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from openbiliclaw.soul.consolidator import ProfileConsolidator
from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

if TYPE_CHECKING:
    from pathlib import Path


class _FakeLayer:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.save_count = 0

    def save(self) -> None:
        self.save_count += 1


class _FakeMemory:
    def __init__(
        self,
        preference: dict[str, Any],
        soul: dict[str, Any] | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._layers = {
            "preference": _FakeLayer(preference),
            "soul": _FakeLayer(soul or {}),
        }
        self._data_dir = data_dir
        self.synced_profiles: list[Any] = []

    def get_layer(self, name: str) -> _FakeLayer:
        return self._layers[name]

    def sync_profile_files(self, profile: Any) -> None:
        self.synced_profiles.append(profile)


class _StubLLM:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload or {}
        self.calls = 0
        self.last_user_input = ""

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.last_user_input = str(kwargs.get("user_input", ""))
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


class _FailingLLM:
    async def complete_structured_task(self, **_: Any) -> Any:
        raise RuntimeError("llm down")


def _interest(name: str, category: str, weight: float = 0.8) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "weight": weight,
        "first_seen": "2026-01-01T00:00:00",
        "last_seen": "2026-06-01T00:00:00",
        "source": "watch history",
    }


def _memory(tmp_path: Path, *, soul: dict[str, Any] | None = None) -> _FakeMemory:
    return _FakeMemory(
        {
            "interests": [
                _interest("综艺", "泛娱乐", 0.9),
                _interest("搞笑", "泛娱乐", 0.8),
                _interest("猫咪", "宠物", 0.7),
                _interest("AI", "科技", 0.95),
            ],
            "disliked_topics": ["标题党"],
        },
        soul=soul,
        data_dir=tmp_path,
    )


async def test_dry_run_prints_full_mapping_and_writes_nothing(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _memory(tmp_path)
    llm = _StubLLM({"mapping": {"泛娱乐": "娱乐", "宠物": "萌宠", "科技": "科技"}})
    migrator = CategoryMigrator(memory=memory, llm_service=llm, data_dir=tmp_path)

    report = await migrator.run(dry_run=True)

    assert set(report.mapping) == {"泛娱乐", "宠物", "科技"}
    assert set(report.mapping.values()) <= set(CATEGORY_VOCAB)
    assert memory.get_layer("preference").save_count == 0
    assert not (tmp_path / "consolidation_runs").exists()


async def test_validation_gap_aborts_with_zero_writes(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _memory(tmp_path)
    missing = _StubLLM({"mapping": {"泛娱乐": "娱乐", "科技": "科技"}})
    report = await CategoryMigrator(memory=memory, llm_service=missing, data_dir=tmp_path).run(
        dry_run=False
    )

    assert report.errors
    assert report.mapping == {}
    assert memory.get_layer("preference").save_count == 0
    assert not (tmp_path / "consolidation_runs").exists()

    bad_target = _StubLLM({"mapping": {"泛娱乐": "娱乐", "宠物": "数码", "科技": "科技"}})
    report = await CategoryMigrator(memory=memory, llm_service=bad_target, data_dir=tmp_path).run(
        dry_run=False
    )

    assert report.errors
    assert report.mapping == {}
    assert memory.get_layer("preference").save_count == 0


async def test_apply_rewrites_categories_and_records_run(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _memory(tmp_path)
    llm = _StubLLM({"mapping": {"泛娱乐": "娱乐", "宠物": "萌宠", "科技": "科技"}})

    report = await CategoryMigrator(memory=memory, llm_service=llm, data_dir=tmp_path).run(
        dry_run=False
    )

    categories = {item["category"] for item in memory.get_layer("preference").data["interests"]}
    assert categories <= set(CATEGORY_VOCAB)
    assert report.applied
    record_path = next((tmp_path / "consolidation_runs").glob("*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["kind"] == "category_migration"
    assert record["mapping"]["泛娱乐"] == "娱乐"
    assert len(record["before"]["interests"]) == 4
    assert "分类迁移" in (tmp_path / "soul_changelog.md").read_text(encoding="utf-8")


async def test_revert_restores_interests_byte_identical(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _memory(tmp_path)
    before = [dict(item) for item in memory.get_layer("preference").data["interests"]]
    llm = _StubLLM({"mapping": {"泛娱乐": "娱乐", "宠物": "萌宠", "科技": "科技"}})
    report = await CategoryMigrator(memory=memory, llm_service=llm, data_dir=tmp_path).run(
        dry_run=False
    )

    consolidator = ProfileConsolidator(memory=memory, llm_service=None, data_dir=tmp_path)
    assert consolidator.revert(report.run_id)
    assert memory.get_layer("preference").data["interests"] == before


async def test_in_vocab_categories_forced_identity(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _memory(tmp_path)
    llm = _StubLLM({"mapping": {"泛娱乐": "娱乐", "宠物": "萌宠", "科技": "生活"}})

    await CategoryMigrator(memory=memory, llm_service=llm, data_dir=tmp_path).run(dry_run=False)

    ai = next(
        item for item in memory.get_layer("preference").data["interests"] if item["name"] == "AI"
    )
    assert ai["category"] == "科技"


async def test_in_vocab_categories_do_not_require_llm(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _FakeMemory(
        {
            "interests": [
                _interest("AI", "科技"),
                _interest("篮球", "体育"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )

    report = await CategoryMigrator(memory=memory, llm_service=None, data_dir=tmp_path).run(
        dry_run=True
    )

    assert report.errors == []
    assert report.mapping == {"体育": "体育", "科技": "科技"}
    assert memory.get_layer("preference").save_count == 0


async def test_llm_unavailable_degrades_to_preview(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _memory(tmp_path)

    report = await CategoryMigrator(memory=memory, llm_service=None, data_dir=tmp_path).run(
        dry_run=True
    )

    assert report.histogram == {"泛娱乐": 2, "宠物": 1, "科技": 1}
    assert report.errors == ["llm: service unavailable"]
    assert memory.get_layer("preference").save_count == 0


async def test_llm_call_failure_degrades_to_preview(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _memory(tmp_path)

    migrator = CategoryMigrator(memory=memory, llm_service=_FailingLLM(), data_dir=tmp_path)
    report = await migrator.run(dry_run=False)

    assert report.histogram == {"泛娱乐": 2, "宠物": 1, "科技": 1}
    assert report.mapping == {}
    assert report.errors == ["llm: llm down"]
    assert memory.get_layer("preference").save_count == 0
    assert not (tmp_path / "consolidation_runs").exists()


async def test_empty_category_assigned_fallback(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    memory = _FakeMemory(
        {"interests": [_interest("空域", ""), _interest("AI", "科技")], "disliked_topics": []},
        data_dir=tmp_path,
    )
    llm = _StubLLM({"mapping": {"科技": "科技"}})

    await CategoryMigrator(memory=memory, llm_service=llm, data_dir=tmp_path).run(dry_run=False)

    categories = [item["category"] for item in memory.get_layer("preference").data["interests"]]
    assert categories == ["其他", "科技"]


async def test_apply_rebuilds_onion_tree_first_level_within_vocab(tmp_path: Path) -> None:
    from openbiliclaw.soul.category_migration import CategoryMigrator
    from openbiliclaw.soul.profile import OnionProfile

    soul = OnionProfile().to_dict()
    memory = _memory(tmp_path, soul=soul)
    llm = _StubLLM({"mapping": {"泛娱乐": "娱乐", "宠物": "萌宠", "科技": "科技"}})

    await CategoryMigrator(memory=memory, llm_service=llm, data_dir=tmp_path).run(dry_run=False)

    likes = memory.get_layer("soul").data["interest"]["likes"]
    assert {item["domain"] for item in likes} <= set(CATEGORY_VOCAB)
    assert memory.get_layer("soul").save_count == 1
    assert memory.synced_profiles
