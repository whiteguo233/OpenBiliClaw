"""Tests for LLM-judged like/dislike topic consolidation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.soul.consolidator import ProfileConsolidator


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


class _StubEmbedding:
    """Names listed in the same group get identical vectors (cos=1)."""

    def __init__(self, groups: list[list[str]]) -> None:
        self._vectors: dict[str, list[float]] = {}
        dims = len(groups) + 64
        for gi, group in enumerate(groups):
            vec = [0.0] * dims
            vec[gi] = 1.0
            for name in group:
                self._vectors[name] = vec
        self._dims = dims
        self._next_axis = len(groups)

    async def embed(self, text: str) -> list[float]:
        if text not in self._vectors:
            vec = [0.0] * self._dims
            vec[self._next_axis] = 1.0
            self._next_axis += 1
            self._vectors[text] = vec
        return self._vectors[text]


class _StubLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0
        self.last_user_input = ""

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.last_user_input = str(kwargs.get("user_input", ""))
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


def _extract_prompt_json_block(user_input: str, tag: str) -> Any:
    start = f"<{tag}>"
    end = f"</{tag}>"
    raw = user_input.split(start, 1)[1].split(end, 1)[0].strip()
    return json.loads(raw)


class _PayloadAwareMergingLLM:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0
        self.user_inputs: list[str] = []

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        self.calls += 1
        user_input = str(kwargs.get("user_input", ""))
        self.user_inputs.append(user_input)
        if self.fail_first and self.calls == 1:
            raise RuntimeError("batch failed")

        likes = _extract_prompt_json_block(user_input, "likes_clusters")
        like_ops: list[dict[str, Any]] = []
        for cluster in likes:
            members = [
                str(member.get("name", ""))
                for member in cluster.get("members", [])
                if isinstance(member, dict)
            ]
            if len(members) >= 2:
                like_ops.append(
                    {
                        "cluster_id": str(cluster.get("cluster_id", "")),
                        "op": "merge",
                        "members": members,
                        "canonical": members[0],
                    }
                )
        return SimpleNamespace(
            content=json.dumps({"likes": like_ops, "dislikes": []}, ensure_ascii=False)
        )


def _interest(name: str, weight: float, category: str = "科技", **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "weight": weight,
        "first_seen": extra.get("first_seen", "2026-01-01T00:00:00"),
        "last_seen": extra.get("last_seen", "2026-06-01T00:00:00"),
        "source": "watch history",
    }


def _paired_interests(pair_count: int) -> tuple[list[dict[str, Any]], list[list[str]]]:
    interests: list[dict[str, Any]] = []
    groups: list[list[str]] = []
    for pair_index in range(pair_count):
        a = f"主题{pair_index}A"
        b = f"主题{pair_index}B"
        groups.append([a, b])
        interests.append(_interest(a, 1.0 - pair_index * 0.001))
        interests.append(_interest(b, 0.99 - pair_index * 0.001))
    return interests, groups


async def test_same_name_cross_category_no_longer_rule_merged(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("人工智能", 0.98, "技术", first_seen="2026-02-01T00:00:00"),
                _interest("人工智能", 0.89, "科技", first_seen="2026-01-01T00:00:00"),
                _interest("篮球", 0.95, "体育"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=None, data_dir=tmp_path)

    report = await consolidator.run(dry_run=False)

    interests = memory.get_layer("preference").data["interests"]
    names_cats = {(item["name"], item["category"]) for item in interests}
    assert names_cats == {("人工智能", "技术"), ("人工智能", "科技"), ("篮球", "体育")}
    assert report.rule_merges == []
    assert report.clusters_sent == 1
    assert any("llm" in err for err in report.errors)
    assert memory.get_layer("preference").save_count == 0


async def test_same_name_same_category_merged_at_stage_zero(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("猫咪", 0.8, "萌宠", first_seen="2026-02-01T00:00:00"),
                _interest("猫咪", 0.6, "萌宠", first_seen="2026-01-01T00:00:00"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=None, data_dir=tmp_path)

    report = await consolidator.run(dry_run=False)

    interests = memory.get_layer("preference").data["interests"]
    assert len(interests) == 1
    assert interests[0]["name"] == "猫咪"
    assert interests[0]["category"] == "萌宠"
    assert interests[0]["weight"] == 0.8
    assert interests[0]["first_seen"] == "2026-01-01T00:00:00"
    assert len(report.rule_merges) == 1
    assert memory.get_layer("preference").save_count == 1


async def test_homonym_not_rule_merged_and_forced_into_cluster(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("苹果", 0.9, "科技"),
                _interest("苹果", 0.5, "美食"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=None, data_dir=tmp_path)

    report = await consolidator.run(dry_run=False)

    stored_interests = memory.get_layer("preference").data["interests"]
    names_cats = {(item["name"], item["category"]) for item in stored_interests}
    assert names_cats == {("苹果", "科技"), ("苹果", "美食")}
    assert report.rule_merges == []
    assert report.clusters_sent == 1
    assert any("llm" in err for err in report.errors)


async def test_homonym_keep_both_pins_no_merge_with_qualified_keys(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("苹果", 0.9, "科技"),
                _interest("苹果", 0.5, "美食"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {"cluster_id": "H1", "op": "keep", "name": "苹果"},
                {"cluster_id": "H1", "op": "keep", "name": "苹果"},
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=llm, data_dir=tmp_path)

    first = await consolidator.run(dry_run=False)
    second = await consolidator.run(dry_run=False)

    assert first.clusters_sent == 1
    assert second.clusters_sent == 0
    assert llm.calls == 1


async def test_homonym_merge_collapses_both_entries(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("苹果", 0.9, "科技"),
                _interest("苹果", 0.5, "美食"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "H1",
                    "op": "merge",
                    "members": ["苹果", "苹果"],
                    "canonical": "苹果",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=llm, data_dir=tmp_path)

    report = await consolidator.run(dry_run=False)

    interests = memory.get_layer("preference").data["interests"]
    assert len(interests) == 1
    assert interests[0]["name"] == "苹果"
    assert interests[0]["category"] == "科技"
    assert interests[0]["weight"] == 0.9
    assert report.merges and report.merges[0]["members"] == ["苹果", "苹果"]


async def test_homonym_partial_merge_preserves_distinct_category(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("苹果", 0.9, "科技"),
                _interest("苹果", 0.7, "资讯"),
                _interest("苹果", 0.5, "美食"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "H1",
                    "op": "merge",
                    "members": [
                        {"name": "苹果", "category": "科技"},
                        {"name": "苹果", "category": "资讯"},
                    ],
                    "canonical": "苹果公司",
                },
                {
                    "cluster_id": "H1",
                    "op": "keep",
                    "member": {"name": "苹果", "category": "美食"},
                },
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=llm, data_dir=tmp_path)

    report = await consolidator.run(dry_run=False)

    interests = memory.get_layer("preference").data["interests"]
    names_cats = {(item["name"], item["category"]) for item in interests}
    assert names_cats == {("苹果公司", "科技"), ("苹果", "美食")}
    assert report.merges and report.merges[0]["members"] == [
        {"name": "苹果", "category": "科技"},
        {"name": "苹果", "category": "资讯"},
    ]


async def test_homonym_partial_merge_keeps_same_name_canonical_distinct(
    tmp_path: Path,
) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("苹果", 0.9, "科技"),
                _interest("苹果", 0.7, "资讯"),
                _interest("苹果", 0.5, "美食"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "H1",
                    "op": "merge",
                    "members": [
                        {"name": "苹果", "category": "科技"},
                        {"name": "苹果", "category": "资讯"},
                    ],
                    "canonical": "苹果",
                },
                {
                    "cluster_id": "H1",
                    "op": "keep",
                    "member": {"name": "苹果", "category": "美食"},
                },
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=llm, data_dir=tmp_path)

    await consolidator.run(dry_run=False)

    interests = memory.get_layer("preference").data["interests"]
    names_cats = {(item["name"], item["category"]) for item in interests}
    assert names_cats == {("苹果", "科技"), ("苹果", "美食")}
    assert len(interests) == 2


def test_input_digest_changes_when_category_changes(tmp_path: Path) -> None:
    first_memory = _FakeMemory(
        {"interests": [_interest("苹果", 0.9, "科技")], "disliked_topics": []},
        data_dir=tmp_path,
    )
    second_memory = _FakeMemory(
        {"interests": [_interest("苹果", 0.9, "美食")], "disliked_topics": []},
        data_dir=tmp_path,
    )

    first_digest = ProfileConsolidator(
        memory=first_memory, llm_service=None, data_dir=tmp_path
    )._input_digest()
    second_digest = ProfileConsolidator(
        memory=second_memory, llm_service=None, data_dir=tmp_path
    )._input_digest()

    assert first_digest != second_digest


async def test_llm_merge_applies_weight_and_timestamps(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97, first_seen="2026-03-01T00:00:00"),
                _interest(
                    "智能体开发与实现",
                    0.88,
                    first_seen="2026-01-15T00:00:00",
                    last_seen="2026-06-10T00:00:00",
                ),
                _interest("篮球", 0.95, "体育"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "智能体开发与实现"],
                    "canonical": "智能体开发",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert llm.calls == 1
    interests = memory.get_layer("preference").data["interests"]
    names = [item["name"] for item in interests]
    assert names == ["智能体开发", "篮球"]
    merged = interests[0]
    assert merged["weight"] == 0.97
    assert merged["first_seen"] == "2026-01-15T00:00:00"
    assert merged["last_seen"] == "2026-06-10T00:00:00"
    assert report.merges and report.merges[0]["canonical"] == "智能体开发"
    # Run record written for revert
    runs = list((tmp_path / "consolidation_runs").glob("*.json"))
    assert len(runs) == 1
    record = json.loads(runs[0].read_text(encoding="utf-8"))
    assert record["kind"] == "consolidation"
    assert record["rename_map"] == {"智能体开发与实现": "智能体开发"}
    assert len(record["before"]["interests"]) == 3
    # Audit entry appended
    assert "画像整理" in (tmp_path / "soul_changelog.md").read_text(encoding="utf-8")


async def test_likes_judge_payload_carries_category(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97, "科技"),
                _interest("智能体开发与实现", 0.88, "科技"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "智能体开发与实现"],
                    "canonical": "智能体开发",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    await consolidator.run(dry_run=False)

    assert '"category": "科技"' in llm.last_user_input


async def test_forced_homonym_payload_distinguishes_by_category(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("苹果", 0.9, "科技"),
                _interest("苹果", 0.5, "美食"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {"cluster_id": "H1", "op": "keep", "name": "苹果"},
                {"cluster_id": "H1", "op": "keep", "name": "苹果"},
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=llm, data_dir=tmp_path)

    await consolidator.run(dry_run=False)

    assert '"category": "科技"' in llm.last_user_input
    assert '"category": "美食"' in llm.last_user_input


def test_consolidation_system_prompt_has_homonym_keep_rule() -> None:
    from openbiliclaw.llm.prompts import _PROFILE_CONSOLIDATION_SYSTEM_PROMPT

    assert "同名异义" in _PROFILE_CONSOLIDATION_SYSTEM_PROMPT
    assert "category" in _PROFILE_CONSOLIDATION_SYSTEM_PROMPT


async def test_full_boundary_surfaces_clusters_beyond_top512(tmp_path: Path) -> None:
    interests = [_interest(f"普通兴趣{i}", 1.0 - i * 0.001) for i in range(530)]
    interests[520]["name"] = "长尾同义A"
    interests[521]["name"] = "长尾同义B"
    memory = _FakeMemory({"interests": interests, "disliked_topics": []}, data_dir=tmp_path)
    embedding = _StubEmbedding([["长尾同义A", "长尾同义B"]])

    default_report = await ProfileConsolidator(
        memory=memory,
        llm_service=None,
        embedding_service=embedding,
        data_dir=tmp_path,
    ).run(dry_run=True)
    full_report = await ProfileConsolidator(
        memory=memory,
        llm_service=None,
        embedding_service=embedding,
        data_dir=tmp_path,
        likes_boundary=530,
    ).run(dry_run=True)

    assert default_report.clusters_sent == 0
    assert full_report.clusters_sent == 1


async def test_judge_batches_at_most_32_clusters_per_call(tmp_path: Path) -> None:
    interests, groups = _paired_interests(33)
    memory = _FakeMemory({"interests": interests, "disliked_topics": []}, data_dir=tmp_path)
    llm = _PayloadAwareMergingLLM()
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding(groups),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert llm.calls == 2
    for user_input in llm.user_inputs:
        likes = _extract_prompt_json_block(user_input, "likes_clusters")
        assert len(likes) <= 32
    assert len(report.merges) == 33


async def test_multi_batch_apply_writes_single_run_record_and_full_revert(tmp_path: Path) -> None:
    interests, groups = _paired_interests(31)
    memory = _FakeMemory({"interests": interests, "disliked_topics": []}, data_dir=tmp_path)
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=_PayloadAwareMergingLLM(),
        embedding_service=_StubEmbedding(groups),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    runs = list((tmp_path / "consolidation_runs").glob("*.json"))
    assert len(runs) == 1
    assert len(memory.get_layer("preference").data["interests"]) == 31
    assert consolidator.revert(report.run_id)
    assert len(memory.get_layer("preference").data["interests"]) == 62


async def test_single_batch_failure_isolated(tmp_path: Path) -> None:
    interests, groups = _paired_interests(33)
    memory = _FakeMemory({"interests": interests, "disliked_topics": []}, data_dir=tmp_path)
    llm = _PayloadAwareMergingLLM(fail_first=True)
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding(groups),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert llm.calls == 2
    assert len(report.merges) == 1
    assert not report.errors
    assert len(report.rejected_clusters) == 32


async def test_default_path_single_call_regression(tmp_path: Path) -> None:
    interests, groups = _paired_interests(2)
    memory = _FakeMemory({"interests": interests, "disliked_topics": []}, data_dir=tmp_path)
    llm = _PayloadAwareMergingLLM()
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding(groups),
        data_dir=tmp_path,
    )

    await consolidator.run(dry_run=False)

    assert llm.calls == 1


async def test_dislike_merge_keeps_frontmost_position(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [],
            "disliked_topics": ["新雷点", "偶像练习室物料", "中间项", "偶像团体练习室内容"],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [],
            "dislikes": [
                {
                    "cluster_id": "D1",
                    "op": "merge",
                    "members": ["偶像练习室物料", "偶像团体练习室内容"],
                    "canonical": "偶像练习室物料",
                }
            ],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["偶像练习室物料", "偶像团体练习室内容"]]),
        data_dir=tmp_path,
    )

    await consolidator.run(dry_run=False)

    assert memory.get_layer("preference").data["disliked_topics"] == [
        "新雷点",
        "偶像练习室物料",
        "中间项",
    ]


async def test_generalized_dislike_canonical_is_rejected(tmp_path: Path) -> None:
    topics = ["一个案例反复切悬念拖时长", "先抛结论再补一堆模糊概念"]
    memory = _FakeMemory(
        {"interests": [], "disliked_topics": list(topics)},
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [],
            "dislikes": [
                {
                    "cluster_id": "D1",
                    "op": "merge",
                    "members": list(topics),
                    "canonical": "低质内容",
                }
            ],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([list(topics)]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert memory.get_layer("preference").data["disliked_topics"] == topics
    assert report.merges == []
    assert report.rejected_clusters and "banned" in report.rejected_clusters[0]


async def test_hallucinated_member_rejects_cluster(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "不存在的标签"],
                    "canonical": "智能体开发",
                },
                {"cluster_id": "L1", "op": "keep", "name": "智能体开发与实现"},
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    names = [item["name"] for item in memory.get_layer("preference").data["interests"]]
    assert names == ["智能体开发", "智能体开发与实现"]
    assert report.rejected_clusters


async def test_incomplete_coverage_rejects_cluster(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("游戏资讯A", 0.9),
                _interest("游戏资讯B", 0.89),
                _interest("游戏资讯C", 0.88),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["游戏资讯A", "游戏资讯B"],
                    "canonical": "游戏资讯A",
                }
                # 游戏资讯C neither merged nor kept -> coverage violation
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["游戏资讯A", "游戏资讯B", "游戏资讯C"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert len(memory.get_layer("preference").data["interests"]) == 3
    assert report.rejected_clusters and "cover" in report.rejected_clusters[0]


async def test_dry_run_never_writes(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "智能体开发与实现"],
                    "canonical": "智能体开发",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=True)

    assert report.merges  # the proposal is reported...
    assert len(memory.get_layer("preference").data["interests"]) == 2  # ...but nothing changed
    assert memory.get_layer("preference").save_count == 0
    assert not (tmp_path / "consolidation_state.json").exists()
    assert not (tmp_path / "consolidation_runs").exists()


async def test_no_merge_memory_skips_judged_clusters(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("篮球", 0.95, "体育"),
                _interest("NBA", 0.9, "体育"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {"cluster_id": "L1", "op": "keep", "name": "篮球"},
                {"cluster_id": "L1", "op": "keep", "name": "NBA"},
            ],
            "dislikes": [],
        }
    )
    embedding = _StubEmbedding([["篮球", "NBA"]])
    consolidator = ProfileConsolidator(
        memory=memory, llm_service=llm, embedding_service=embedding, data_dir=tmp_path
    )

    first = await consolidator.run(dry_run=False)
    assert first.clusters_sent == 1
    assert llm.calls == 1

    second = await consolidator.run(dry_run=False)
    assert second.clusters_sent == 0
    assert llm.calls == 1  # no second LLM call


async def test_run_if_due_throttles_and_skips_clean_input(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [_interest("篮球", 0.95, "体育")],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    consolidator = ProfileConsolidator(
        memory=memory, llm_service=None, data_dir=tmp_path, min_interval_seconds=12 * 3600
    )
    t0 = datetime(2026, 6, 12, 3, 0, 0)

    first = await consolidator.run_if_due(now=t0)
    assert first.ran

    throttled = await consolidator.run_if_due(now=t0 + timedelta(hours=1))
    assert throttled.throttled

    clean = await consolidator.run_if_due(now=t0 + timedelta(hours=13))
    assert clean.skipped_clean

    # New input -> due again
    memory.get_layer("preference").data["disliked_topics"].append("新雷点")
    dirty = await consolidator.run_if_due(now=t0 + timedelta(hours=26))
    assert dirty.ran


async def test_apply_rebuilds_onion_tree(tmp_path: Path) -> None:
    from openbiliclaw.soul.profile import OnionProfile

    profile = OnionProfile()
    profile.populate_from_flat_preference(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        }
    )
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        },
        soul=profile.to_dict(),
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "智能体开发与实现"],
                    "canonical": "智能体开发",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    await consolidator.run(dry_run=False)

    rebuilt = OnionProfile.from_dict(dict(memory.get_layer("soul").data))
    specific_names = [spec.name for dom in rebuilt.interest.likes for spec in dom.specifics]
    assert "智能体开发与实现" not in specific_names
    assert memory.synced_profiles  # sync_profile_files invoked


async def test_pipeline_tick_runs_consolidator_and_records_cognition(tmp_path: Path) -> None:
    from openbiliclaw.soul.consolidator import ConsolidationReport
    from openbiliclaw.soul.pipeline import ProfileUpdatePipeline

    class _StubConsolidator:
        def __init__(self) -> None:
            self.calls = 0

        async def run_if_due(self, **_: Any) -> ConsolidationReport:
            self.calls += 1
            report = ConsolidationReport(ran=True, run_id="t1")
            report.merges = [
                {"scope": "likes", "members": ["A", "B"], "canonical": "A"},
                {"scope": "dislikes", "members": ["X", "Y"], "canonical": "X"},
            ]
            return report

    class _CogMemory(_FakeMemory):
        def __init__(self) -> None:
            super().__init__({"interests": [], "disliked_topics": []})
            self.cognition_updates: list[dict[str, Any]] = []

        def load_cognition_updates(self) -> list[dict[str, Any]]:
            return self.cognition_updates

        def save_cognition_updates(self, updates: list[dict[str, Any]]) -> None:
            self.cognition_updates = updates

    memory = _CogMemory()
    consolidator = _StubConsolidator()
    pipeline = ProfileUpdatePipeline(
        memory=memory,  # type: ignore[arg-type]
        preference_analyzer=SimpleNamespace(),  # type: ignore[arg-type]
        profile_builder=SimpleNamespace(),  # type: ignore[arg-type]
        profile_consolidator=consolidator,
    )

    result = await pipeline.tick()

    assert consolidator.calls == 1
    assert memory.cognition_updates
    card = memory.cognition_updates[0]
    assert card["kind"] == "profile_consolidation"
    assert "兴趣合并 1 组" in card["summary"]
    assert "避雷合并 1 组" in card["summary"]
    assert any("画像整理" in (u.changes[0] if u.changes else "") for u in result.layers_updated)


async def test_pipeline_tick_quiet_when_consolidator_throttled(tmp_path: Path) -> None:
    from openbiliclaw.soul.consolidator import ConsolidationReport
    from openbiliclaw.soul.pipeline import ProfileUpdatePipeline

    class _ThrottledConsolidator:
        async def run_if_due(self, **_: Any) -> ConsolidationReport:
            return ConsolidationReport(throttled=True)

    class _CogMemory(_FakeMemory):
        def __init__(self) -> None:
            super().__init__({"interests": [], "disliked_topics": []})
            self.cognition_updates: list[dict[str, Any]] = []

        def load_cognition_updates(self) -> list[dict[str, Any]]:
            return self.cognition_updates

        def save_cognition_updates(self, updates: list[dict[str, Any]]) -> None:
            self.cognition_updates = updates

    memory = _CogMemory()
    pipeline = ProfileUpdatePipeline(
        memory=memory,  # type: ignore[arg-type]
        preference_analyzer=SimpleNamespace(),  # type: ignore[arg-type]
        profile_builder=SimpleNamespace(),  # type: ignore[arg-type]
        profile_consolidator=_ThrottledConsolidator(),
    )

    result = await pipeline.tick()

    assert memory.cognition_updates == []
    assert result.layers_updated == []


def test_scheduler_config_consolidation_fields(tmp_path: Path) -> None:
    from openbiliclaw.config import load_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[llm]
default_provider = "ollama"

[scheduler]
profile_consolidation_enabled = false
profile_consolidation_interval_hours = 6
""",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.scheduler.profile_consolidation_enabled is False
    assert cfg.scheduler.profile_consolidation_interval_hours == 6

    defaults = load_config(tmp_path / "missing.toml")
    assert defaults.scheduler.profile_consolidation_enabled is True
    assert defaults.scheduler.profile_consolidation_interval_hours == 12


class _OverridesMemory(_FakeMemory):
    """Fake memory that also persists ProfileOverrides like the real one."""

    def __init__(self, preference: dict[str, Any], overrides_raw: dict[str, Any]) -> None:
        super().__init__(preference)
        self._overrides_raw = overrides_raw

    def load_profile_overrides(self) -> Any:
        from openbiliclaw.soul.overrides import ProfileOverrides

        return ProfileOverrides.from_dict(self._overrides_raw)

    def save_profile_overrides(self, overrides: Any) -> None:
        self._overrides_raw = overrides.to_dict()


async def test_merge_remaps_user_override_strings(tmp_path: Path) -> None:
    memory = _OverridesMemory(
        {
            "interests": [],
            "disliked_topics": ["偶像练习室物料", "偶像团体练习室内容"],
        },
        {
            "version": 1,
            "list_edits": {
                "interest.disliked_topics": {
                    "add": [],
                    # The user manually removed this exact topic string; a
                    # raw-store rename must follow it or the removal
                    # silently stops matching.
                    "remove": ["偶像团体练习室内容"],
                }
            },
        },
    )
    memory._data_dir = tmp_path
    llm = _StubLLM(
        {
            "likes": [],
            "dislikes": [
                {
                    "cluster_id": "D1",
                    "op": "merge",
                    "members": ["偶像练习室物料", "偶像团体练习室内容"],
                    "canonical": "偶像练习室物料",
                }
            ],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["偶像练习室物料", "偶像团体练习室内容"]]),
        data_dir=tmp_path,
    )

    await consolidator.run(dry_run=False)

    remove_list = memory._overrides_raw["list_edits"]["interest.disliked_topics"]["remove"]
    assert remove_list == ["偶像练习室物料"]
    # run record keeps the pre-remap overrides for revert
    record_path = next((tmp_path / "consolidation_runs").glob("*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["overrides_before"]["list_edits"]["interest.disliked_topics"]["remove"] == [
        "偶像团体练习室内容"
    ]


async def test_revert_restores_preference_overrides_and_pins_no_merge(tmp_path: Path) -> None:
    memory = _OverridesMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        },
        {"version": 1, "list_edits": {}},
    )
    memory._data_dir = tmp_path
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "智能体开发与实现"],
                    "canonical": "智能体开发",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)
    assert len(memory.get_layer("preference").data["interests"]) == 1

    ok = consolidator.revert(report.run_id)
    assert ok
    names = [item["name"] for item in memory.get_layer("preference").data["interests"]]
    assert names == ["智能体开发", "智能体开发与实现"]

    # The rolled-back merge is pinned distinct: a fresh run re-clusters the
    # pair but does not re-ask the LLM, so the merge is not redone.
    second = await consolidator.run(dry_run=False)
    assert second.clusters_sent == 0
    assert llm.calls == 1
    assert len(memory.get_layer("preference").data["interests"]) == 2


def test_revert_missing_run_id_returns_false(tmp_path: Path) -> None:
    memory = _FakeMemory({"interests": [], "disliked_topics": []})
    consolidator = ProfileConsolidator(memory=memory, llm_service=None, data_dir=tmp_path)
    assert consolidator.revert("nonexistent") is False


class _BatchAwareLLM:
    """Returns a valid merge op for every cluster in each call's payload."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @staticmethod
    def _likes_clusters(user_input: str) -> list[dict[str, Any]]:
        likes_json = user_input.split("<likes_clusters>")[1].split("</likes_clusters>")[0]
        parsed = json.loads(likes_json)
        return parsed if isinstance(parsed, list) else []

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        clusters = self._likes_clusters(str(kwargs.get("user_input", "")))
        self.calls.append([str(c["cluster_id"]) for c in clusters])
        ops = [
            {
                "cluster_id": c["cluster_id"],
                "op": "merge",
                "members": [m["name"] for m in c["members"]],
                "canonical": str(c["members"][0]["name"]),
            }
            for c in clusters
        ]
        return SimpleNamespace(
            content=json.dumps({"likes": ops, "dislikes": []}, ensure_ascii=False)
        )


def _paired_interest_fixture(pair_count: int) -> tuple[list[dict[str, Any]], list[list[str]]]:
    interests: list[dict[str, Any]] = []
    groups: list[list[str]] = []
    for i in range(pair_count):
        a, b = f"主题{i}甲", f"主题{i}乙"
        interests.append(_interest(a, 0.9 - i * 0.001))
        interests.append(_interest(b, 0.85 - i * 0.001))
        groups.append([a, b])
    return interests, groups


async def test_judge_batches_clusters_across_multiple_llm_calls(tmp_path: Path) -> None:
    pair_count = 40  # > _JUDGE_CLUSTER_BATCH (32), so judgement needs 2 calls
    interests, groups = _paired_interest_fixture(pair_count)
    memory = _FakeMemory({"interests": interests, "disliked_topics": []}, data_dir=tmp_path)
    llm = _BatchAwareLLM()
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding(groups),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert [len(ids) for ids in llm.calls] == [32, 8]
    assert report.clusters_sent == pair_count
    assert len(report.merges) == pair_count
    assert not report.errors
    assert len(memory.get_layer("preference").data["interests"]) == pair_count


async def test_judge_failed_batch_only_loses_its_own_clusters(tmp_path: Path) -> None:
    pair_count = 40
    interests, groups = _paired_interest_fixture(pair_count)
    memory = _FakeMemory({"interests": interests, "disliked_topics": []}, data_dir=tmp_path)

    class _FirstBatchFails(_BatchAwareLLM):
        async def complete_structured_task(self, **kwargs: Any) -> Any:
            if not self.calls:
                self.calls.append([])
                raise RuntimeError("provider timeout")
            return await super().complete_structured_task(**kwargs)

    llm = _FirstBatchFails()
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding(groups),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    # Second batch (8 clusters) still applies; the failed batch's 32
    # clusters are rejected ("no ops returned") and will re-cluster next run.
    assert len(report.merges) == 8
    assert len(report.rejected_clusters) == 32
    assert not report.errors
    assert len(memory.get_layer("preference").data["interests"]) == 2 * 32 + 8
