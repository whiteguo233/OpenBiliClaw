"""Tests for canonical sparse evaluator payloads and local result IDs."""

from __future__ import annotations

import json

import pytest

from openbiliclaw.discovery.eval_payload import (
    CanonicalEvaluationPayloadError,
    build_canonical_evaluation_batch,
    decode_sparse_evaluation_json,
    render_sparse_evaluation_json,
    resolve_local_evaluation_results,
)


def test_canonical_sparse_batch_omits_empty_duplicate_and_global_fields() -> None:
    batch = build_canonical_evaluation_batch(
        [
            {
                "bvid": "BV-private",
                "content_id": "global-private",
                "content_url": "https://example.com/private",
                "cover_url": "https://example.com/cover.jpg",
                "source_platform": "twitter",
                "source_context": "search",
                "source_strategy": "x-search",
                "content_type": "thread",
                "title": "保留完整标题",
                "up_name": "duplicate author",
                "author_name": "canonical author",
                "body_text": "完整正文\n第二行",
                "published_at": "2026-08-04T08:00:00Z",
                "description": "",
                "duration": 0,
                "view_count": 120,
                "like_count": 0,
                "reply_count": 99,
                "retweet_count": 88,
                "bookmark_count": 77,
                "tags": ["AI", "系统"],
                "rating_score": 0.0,
                "rating_count": 5,
                "related_interests": ["架构"],
                "cover_image_ref": "cover:global-private",
            }
        ]
    )

    assert batch.defaults == {
        "mode": "normal",
        "source_platform": "twitter",
        "content_type": "thread",
    }
    assert batch.items == (
        {
            "id": "0",
            "title": "保留完整标题",
            "author": "canonical author",
            "body_text": "完整正文\n第二行",
            "published_at": "2026-08-04T08:00:00Z",
            "view_count": 120,
            "rating_count": 5,
            "tags": ["AI", "系统"],
            "related_interests": ["架构"],
            "cover_image_ref": "cover:0",
        },
    )
    assert batch.local_ids == ("0",)
    assert batch.local_id_to_index == {"0": 0}
    wire = render_sparse_evaluation_json(batch)
    assert "BV-private" not in wire
    assert "global-private" not in wire
    assert "example.com" not in wire
    assert "reply_count" not in wire
    assert "retweet_count" not in wire
    assert "bookmark_count" not in wire


def test_canonical_sparse_batch_uses_mixed_fields_and_explore_only_mode() -> None:
    batch = build_canonical_evaluation_batch(
        [
            {
                "source_platform": "bilibili",
                "content_type": "video",
                "source_strategy": "search",
                "title": "A",
                "author_name": "",
            },
            {
                "source_platform": "twitter",
                "content_type": "thread",
                "source_strategy": "explore",
                "title": "B",
                "up_name": "作者 B",
            },
        ]
    )

    assert batch.defaults == {"mode": "normal"}
    assert batch.items == (
        {
            "id": "0",
            "title": "A",
            "author": "",
            "source_platform": "bilibili",
            "content_type": "video",
        },
        {
            "id": "1",
            "title": "B",
            "author": "作者 B",
            "source_platform": "twitter",
            "content_type": "thread",
            "mode": "explore",
        },
    )


def test_canonical_sparse_batch_only_grants_exact_explore_strategy_mode() -> None:
    batch = build_canonical_evaluation_batch(
        [
            {
                "source_platform": "xiaohongshu",
                "content_type": "note",
                "source_strategy": "xhs-extension-explore",
                "source_context": "",
                "title": "not the admission exception",
                "author_name": "author",
            },
            {
                "source_platform": "xiaohongshu",
                "content_type": "note",
                "source_strategy": "search",
                "source_context": " Explore ",
                "title": "exact normalized effective explore",
                "author_name": "author",
            },
        ]
    )

    assert "mode" not in batch.items[0]
    assert batch.items[1]["mode"] == "explore"


def test_canonical_sparse_batch_only_emits_valid_positive_numeric_shapes() -> None:
    batch = build_canonical_evaluation_batch(
        [
            {
                "source_platform": "twitter",
                "content_type": "thread",
                "title": "metrics",
                "author_name": "author",
                "duration": 1.5,
                "view_count": True,
                "like_count": 3,
                "rating_score": float("inf"),
                "rating_count": 2,
                "source_rank": -1,
            }
        ]
    )

    assert batch.items[0]["like_count"] == 3
    assert batch.items[0]["rating_count"] == 2
    for omitted in (
        "duration",
        "view_count",
        "rating_score",
        "source_rank",
    ):
        assert omitted not in batch.items[0]


@pytest.mark.parametrize("field", ["source_platform", "content_type"])
def test_canonical_sparse_batch_rejects_empty_routing_semantics(field: str) -> None:
    item = {
        "source_platform": "twitter",
        "content_type": "thread",
        "title": "candidate",
        "author_name": "author",
    }
    item[field] = ""

    with pytest.raises(CanonicalEvaluationPayloadError, match=field):
        build_canonical_evaluation_batch([item])


def test_canonical_sparse_batch_sanitizes_wire_rejected_control_characters() -> None:
    """One dirty upstream item must not fail the whole batch (#284)."""

    batch = build_canonical_evaluation_batch(
        [
            {
                "source_platform": "bilibili",
                "content_type": "video",
                "title": "正常标题",
                "author_name": "UP 主",
            },
            {
                "source_platform": "bilibili",
                "content_type": "video",
                "title": "标题\x0b含\x7f控制符",
                "author_name": "UP 主\x00",
                "description": "简介\x00夹带 NUL\t保留制表\n保留换行",
                "body_text": "正文\x1f unit separator",
                "tags": ["正常", "\x00", "标\x0b签"],
                "related_interests": ["兴\x7f趣"],
            },
        ]
    )

    assert len(batch.items) == 2
    dirty = batch.items[1]
    assert dirty["title"] == "标题含控制符"
    assert dirty["author"] == "UP 主"
    assert dirty["description"] == "简介夹带 NUL\t保留制表\n保留换行"
    assert dirty["body_text"] == "正文 unit separator"
    assert dirty["tags"] == ["正常", "标签"]
    assert dirty["related_interests"] == ["兴趣"]
    # The sanitized batch passes wire validation end to end.
    assert decode_sparse_evaluation_json(render_sparse_evaluation_json(batch)) == batch


def test_canonical_sparse_batch_omits_list_field_when_all_entries_sanitize_empty() -> None:
    batch = build_canonical_evaluation_batch(
        [
            {
                "source_platform": "bilibili",
                "content_type": "video",
                "title": "candidate",
                "author_name": "author",
                "tags": ["\x00", "\x0b\x7f"],
            }
        ]
    )

    assert "tags" not in batch.items[0]


def test_canonical_sparse_batch_replaces_lone_surrogates() -> None:
    batch = build_canonical_evaluation_batch(
        [
            {
                "source_platform": "bilibili",
                "content_type": "video",
                "title": "标题\ud800孤立代理",
                "author_name": "author",
            }
        ]
    )

    title = batch.items[0]["title"]
    assert isinstance(title, str)
    assert "\ud800" not in title
    title.encode("utf-8")


def test_sparse_json_is_deterministic_and_strictly_round_trips() -> None:
    left = build_canonical_evaluation_batch(
        [
            {
                "title": "雪\t与换行\n仍保留",
                "author_name": "作者",
                "source_platform": "twitter",
                "content_type": "tweet",
                "tags": ["z", "a"],
                "like_count": 3,
            }
        ]
    )
    right = build_canonical_evaluation_batch(
        [
            {
                "like_count": 3,
                "tags": ["z", "a"],
                "content_type": "tweet",
                "source_platform": "twitter",
                "author_name": "作者",
                "title": "雪\t与换行\n仍保留",
            }
        ]
    )

    left_wire = render_sparse_evaluation_json(left)
    assert left_wire == render_sparse_evaluation_json(right)
    assert "雪" in left_wire
    assert "\\u96ea" not in left_wire
    assert " " not in left_wire
    assert decode_sparse_evaluation_json(left_wire) == left
    assert json.loads(left_wire) == left.as_payload()


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"defaults":{},"items":[],"extra":true}',
        '{"defaults":{"mode":"normal"},"items":[{"id":"1","title":"x","author":""}]}',
        '{"defaults":{"mode":"normal"},"items":[{"id":"0","title":"x","author":"","unknown":1}]}',
        '{"defaults":{"mode":"normal"},"items":[{"id":"0","title":"x","author":"","source_platform":"twitter","content_type":"tweet"}]}',
        '{"defaults":{"mode":"normal","source_platform":"twitter","content_type":"tweet"},"items":[{"id":"0","title":"x","author":"","mode":"normal"}]}',
    ],
)
def test_sparse_json_decoder_rejects_noncanonical_payloads(payload: str) -> None:
    with pytest.raises(CanonicalEvaluationPayloadError):
        decode_sparse_evaluation_json(payload)


def test_local_result_resolution_forbids_multi_member_positional_fallback() -> None:
    resolved = resolve_local_evaluation_results(
        [
            {"score": 0.91},
            {"id": "1", "score": 0.72},
            {"id": "unknown", "score": 0.99},
        ],
        ("0", "1"),
    )

    assert resolved == [None, {"id": "1", "score": 0.72}]


def test_local_result_resolution_invalidates_duplicate_id_but_keeps_sibling() -> None:
    resolved = resolve_local_evaluation_results(
        [
            {"id": "0", "score": 0.1},
            {"id": "0", "score": 0.9},
            {"id": "1", "score": 0.7},
        ],
        ("0", "1"),
    )

    assert resolved == [None, {"id": "1", "score": 0.7}]


def test_local_result_resolution_singleton_fallback_requires_no_explicit_id() -> None:
    assert resolve_local_evaluation_results([{"score": 0.8}], ("0",)) == [{"score": 0.8}]
    assert resolve_local_evaluation_results([{"id": "wrong", "score": 0.8}], ("0",)) == [None]
    assert resolve_local_evaluation_results([{"id": 0, "score": 0.8}], ("0",)) == [None]
