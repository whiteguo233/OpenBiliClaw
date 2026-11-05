"""Source metadata must reach evaluation without exposing provider internals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbiliclaw.discovery.candidate_pool import (
    discovered_content_to_candidate_write,
    row_to_discovered_content,
)
from openbiliclaw.discovery.engine import (
    _batch_evaluation_content_item,
    _single_evaluation_content_summary,
)
from openbiliclaw.discovery.eval_payload import build_canonical_evaluation_batch
from openbiliclaw.discovery.jev_context import candidate_text_view
from openbiliclaw.llm.evaluation_wire import (
    EvaluationWireError,
    encode_evaluation_row_wire,
    project_evaluation_source_metadata,
    validate_canonical_evaluation_envelope,
)
from openbiliclaw.sources.github import github_repository_to_content
from openbiliclaw.sources.v2ex import v2ex_topic_to_content
from openbiliclaw.storage.database import Database


def test_v2ex_native_replies_survive_storage_and_sparse_evaluation(tmp_path: Path) -> None:
    content = v2ex_topic_to_content(
        {"id": 123, "title": "Agent 上下文", "replies": 36, "content": "主题正文"},
        strategy="v2ex-hot",
    )
    assert content is not None
    db = Database(tmp_path / "metadata.db")
    db.initialize()
    try:
        write = discovered_content_to_candidate_write(content)
        db.enqueue_discovery_candidates([write])
        row = db.conn.execute("SELECT * FROM discovery_candidates").fetchone()
        restored = row_to_discovered_content(dict(row))
        item = _batch_evaluation_content_item(restored, source_context="search")
        canonical = build_canonical_evaluation_batch([item]).items[0]
        assert canonical["reply_count"] == 36
        assert "comment_count" not in canonical
    finally:
        db.close()


def test_github_activity_and_license_reach_every_evaluator_without_identity() -> None:
    fixture = Path(__file__).parent / "fixtures/github/search_repositories_page.json"
    repo = json.loads(fixture.read_text())["items"][0]
    repo.update({"archived": True, "fork": False, "disabled": False})
    content = github_repository_to_content(repo, strategy="github-search")
    assert content is not None
    content.source_metadata["access_token"] = "must-never-reach-the-model"
    for item in (
        _single_evaluation_content_summary(content),
        _batch_evaluation_content_item(content, source_context="search"),
    ):
        metadata = item["source_metadata"]
        assert metadata["pushed_at"] == repo["pushed_at"]
        assert metadata["archived"] is True
        assert metadata["fork"] is False
        assert metadata["license"]["spdx_id"] == repo["license"]["spdx_id"]
        assert "repository_id" not in metadata
        assert "access_token" not in metadata
        view = candidate_text_view(item)
        assert view is not None
        assert view.content["source_metadata"] == metadata
        batch = build_canonical_evaluation_batch([item])
        assert batch.items[0]["source_metadata"] == metadata
        assert batch.items[0]["published_at"] == repo["created_at"]
        detached = batch.as_payload()
        detached["items"][0]["source_metadata"]["license"]["spdx_id"] = "mutated"
        assert batch.items[0]["source_metadata"]["license"]["spdx_id"] != "mutated"


@pytest.mark.parametrize("field", ["reply_count", "retweet_count", "bookmark_count"])
def test_native_interaction_counts_are_preserved_and_cannot_silently_enter_v1(field: str) -> None:
    batch = build_canonical_evaluation_batch(
        [{"source_platform": "twitter", "content_type": "post", "title": "t", field: 7}]
    )
    assert batch.items[0][field] == 7
    with pytest.raises(EvaluationWireError, match=f"does not support {field}"):
        encode_evaluation_row_wire(batch.as_payload())


def test_relative_publication_label_is_preserved_without_inventing_a_date() -> None:
    item = {"source_platform": "douyin", "content_type": "video", "published_label": "昨天"}
    assert build_canonical_evaluation_batch([item]).items[0]["published_label"] == "昨天"
    item["published_at"] = "2026-11-05T08:00:00Z"
    assert "published_label" not in build_canonical_evaluation_batch([item]).items[0]


def test_source_metadata_projection_rejects_unknown_or_invalid_values() -> None:
    unsafe = {
        "archived": "false",
        "fork": False,
        "forks_count": True,
        "open_issues_count": -1,
        "watchers_count": 0,
        "language": "Py\x00thon",
        "license": {"spdx_id": "MIT", "url": "private-url"},
        "access_token": "secret",
        "repository_id": "internal-id",
    }
    projected = project_evaluation_source_metadata("github", unsafe)
    assert projected == {
        "fork": False,
        "watchers_count": 0,
        "language": "Python",
        "license": {"spdx_id": "MIT"},
    }
    assert project_evaluation_source_metadata("tiktok", unsafe) == {}
    envelope = build_canonical_evaluation_batch(
        [
            {"source_platform": "github", "content_type": "repository", "source_metadata": unsafe},
        ]
    ).as_payload()
    envelope["items"][0]["source_metadata"]["access_token"] = "secret"
    with pytest.raises(EvaluationWireError, match="canonical source fields"):
        validate_canonical_evaluation_envelope(envelope)


def test_sparse_system_prompt_matches_reviewed_golden() -> None:
    from openbiliclaw.llm.prompts import _SPARSE_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT

    golden = Path(__file__).parent / "golden/evaluation/sparse-system-v7.txt"
    assert golden.read_text() == _SPARSE_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
