from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
from openbiliclaw.api.source_auth.write import credential_fingerprint
from openbiliclaw.runtime.keyword_fetch import ClaimedKeyword
from openbiliclaw.runtime.v2ex_producer import (
    V2EX_SOURCE_MODES,
    V2EX_SOURCE_WEIGHTS,
    V2EXDiscoveryProducer,
    _allocate_weighted,
    build_v2ex_external_search_provider,
)
from openbiliclaw.sources.v2ex_client import V2EXAPIError, V2EXPage
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database(tmp_path / "v2ex.db")
    database.initialize()
    return database


@pytest.fixture(autouse=True)
def clear_v2ex_probe_cache() -> Iterator[None]:
    LIVE_PROBES.clear("v2ex")
    yield
    LIVE_PROBES.clear("v2ex")


class _Soul:
    async def get_profile(self) -> dict[str, Any]:
        return {
            "preferences": {
                "interests": [{"name": "Agent"}],
                "v2ex_nodes": ["programmer", "python", "sandbox"],
            }
        }


def _topic(topic_id: int, *, node: str = "programmer", title: str = "主题") -> dict[str, object]:
    return {
        "id": topic_id,
        "title": f"{title} {topic_id}",
        "content": "正文",
        "member": {"username": f"member-{topic_id}"},
        "node": {"name": node, "title": node.title()},
        "replies": topic_id,
    }


@dataclass
class _Client:
    unauthorized: bool = False
    disabled: bool = False
    last_rate_limit: dict[str, int] | None = None
    has_access_token: bool = False

    async def search_topics(self, keyword: str, **kwargs: Any) -> V2EXPage:
        assert keyword == "Agent"
        return V2EXPage([_topic(101, title=keyword)], 1, int(kwargs["limit"]), 0)

    async def get_node_topics(self, node_name: str, **kwargs: Any) -> V2EXPage:
        rows = [_topic(201, node=node_name), _topic(202, node=node_name)]
        return V2EXPage(rows, len(rows), int(kwargs["limit"]), 0)

    async def get_tab(self, tab: str, **kwargs: Any) -> V2EXPage:
        return V2EXPage([_topic(301, node=tab, title=tab)], 1, int(kwargs["limit"]), 0)

    async def get_hot(self, **kwargs: Any) -> V2EXPage:
        if self.unauthorized:
            raise V2EXAPIError("unauthorized", "denied")
        return V2EXPage([_topic(401, node="hot", title="hot")], 1, int(kwargs["limit"]), 0)

    async def get_latest(self, **kwargs: Any) -> V2EXPage:
        return V2EXPage([_topic(501, node="latest", title="latest")], 1, int(kwargs["limit"]), 0)

    def disable_access_token(self) -> None:
        self.disabled = True
        self.has_access_token = False


class _Pipeline:
    def __init__(self) -> None:
        self.enqueued: list[tuple[list[Any], str]] = []

    def pool_full(self) -> bool:
        return False

    def pool_full_for_source(self, source_family: str) -> bool:
        assert source_family == "v2ex"
        return False

    def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
        self.enqueued.append((items, source_context))
        return len(items)

    async def drain_pending(self, *, profile: Any, batch_size: int) -> dict[str, int]:
        del profile
        return {"drained": batch_size}


class _DroppingPipeline(_Pipeline):
    def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
        self.enqueued.append((items, source_context))
        return 0


class _ExternalSearch:
    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []

    async def search(self, query: str, *, limit: int) -> list[SimpleNamespace]:
        self.queries.append((query, limit))
        return [
            SimpleNamespace(
                title="External recall",
                url="https://www.v2ex.com/t/701?utm_source=search",
                highlights=("search summary",),
            ),
            SimpleNamespace(
                title="Must be ignored",
                url="https://evil.example/t/702",
                highlights=("external",),
            ),
        ]


class _ExternalClient:
    has_access_token = False
    last_rate_limit: dict[str, int] = {}

    async def search_topics(self, keyword: str, **kwargs: Any) -> V2EXPage:
        raise AssertionError(f"official fallback should not run for {keyword}: {kwargs}")

    async def get_topic(self, topic_id: str) -> dict[str, object]:
        assert topic_id == "701"
        return _topic(701, node="programmer", title="Official detail")


class _EnrichmentClient:
    has_access_token = True
    last_rate_limit: dict[str, int] = {}

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_tab(self, tab: str, **kwargs: Any) -> V2EXPage:
        self.calls.append(f"tab:{tab}")
        return V2EXPage(
            [{"id": 601, "title": "incomplete", "replies": 2}],
            1,
            int(kwargs["limit"]),
            0,
        )

    async def get_topic(self, topic_id: str) -> dict[str, object]:
        self.calls.append(f"topic:{topic_id}")
        return {
            "id": int(topic_id),
            "title": "canonical",
            "content_rendered": "<p>main body</p>",
            "member": {"username": "alice"},
            "node": {"name": "programmer", "title": "程序员"},
            "replies": 2,
        }

    async def get_topic_replies(self, topic_id: str, **kwargs: Any) -> V2EXPage:
        self.calls.append(f"replies:{topic_id}")
        return V2EXPage(
            [
                {"content": "first reply", "member": {"username": "bob"}},
                {"content_rendered": "<b>second reply</b>"},
            ],
            2,
            int(kwargs["limit"]),
            0,
        )

    def disable_access_token(self) -> None:
        self.has_access_token = False


class _Keywords:
    def __init__(self) -> None:
        self.used: list[int] = []
        self.failed: list[int] = []
        self.rolled_back: list[int] = []

    def should_claim(self) -> bool:
        return True

    def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
        assert platform == "v2ex"
        return [ClaimedKeyword(id=7, keyword="Agent")][: n or 1]

    def mark_used(self, claimed: list[ClaimedKeyword]) -> None:
        self.used.extend(item.id for item in claimed)

    def mark_failed(self, claimed: list[ClaimedKeyword]) -> None:
        self.failed.extend(item.id for item in claimed)

    def rollback(self, claimed: ClaimedKeyword) -> None:
        self.rolled_back.append(claimed.id)


@pytest.mark.asyncio
async def test_producer_runs_all_public_modes_through_shared_pipeline(db: Database) -> None:
    pipeline = _Pipeline()
    keywords = _Keywords()
    producer = V2EXDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
        node_allowlist=("programmer",),
    )

    result = await producer.produce_if_due(limit=10)

    assert result["reason"] == "ok"
    assert result["discovered"] == 6
    assert result["enqueued"] == 6
    assert result["drained"] == 10
    assert keywords.used == [7]
    assert {context for _, context in pipeline.enqueued} == {
        "v2ex-search",
        "v2ex-node",
        "v2ex-tab",
        "v2ex-hot",
        "v2ex-latest",
    }
    assert all(item.source_platform == "v2ex" for group, _ in pipeline.enqueued for item in group)


@pytest.mark.asyncio
async def test_producer_blocks_sandbox_nodes_and_falls_back_after_pat_rejection(
    db: Database,
) -> None:
    client = _Client(unauthorized=True, has_access_token=True)
    producer = V2EXDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        access_token="pat",
        enabled=True,
        source_modes=("hot",),
        node_allowlist=("sandbox",),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=3)
    assert result["reason"] == "error"
    assert result["mode_results"] == {"hot": "unauthorized"}
    assert client.disabled is True
    assert producer._configured_nodes(await _Soul().get_profile()) == ()
    verdict = LIVE_PROBES.peek_matching(
        "v2ex",
        credential_fingerprint("v2ex", "pat"),
    )
    assert verdict is not None
    assert verdict.authenticated is False


@pytest.mark.asyncio
async def test_producer_uses_profile_nodes_when_allowlist_is_empty(db: Database) -> None:
    client = _Client()
    producer = V2EXDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("node",),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=2)
    assert result["reason"] == "ok"
    assert result["discovered"] == 2


@pytest.mark.asyncio
async def test_producer_bounded_enrichment_fills_topic_and_discussion_digest(
    db: Database,
) -> None:
    client = _EnrichmentClient()
    pipeline = _Pipeline()
    producer = V2EXDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        access_token="pat",
        enabled=True,
        source_modes=("tab",),
        tab_modes=("tech",),
        detail_fetch_limit=1,
        reply_enrichment_limit=1,
        max_topic_chars=100,
        max_reply_digest_chars=100,
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=1)

    assert result["reason"] == "ok"
    assert client.calls == ["tab:tech", "topic:601", "replies:601"]
    item = pipeline.enqueued[0][0][0]
    assert item.author_name == "alice"
    assert item.tags == ["programmer", "程序员"]
    assert item.body_text == "main body"
    assert item.description == "讨论摘要：bob：first reply；second reply"


@pytest.mark.asyncio
async def test_search_uses_configured_web_provider_then_official_topic_detail(db: Database) -> None:
    search = _ExternalSearch()
    pipeline = _Pipeline()
    producer = V2EXDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_ExternalClient(),
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        search_provider=search,
    )

    result = await producer.produce_if_due(limit=2)

    assert result["reason"] == "ok"
    assert result["discovered"] == 1
    assert search.queries == [("site:v2ex.com/t Agent", 2)]
    item = pipeline.enqueued[0][0][0]
    assert item.content_id == "701"
    assert item.title == "Official detail 701"
    assert item.body_text == "正文"
    assert item.content_url == "https://www.v2ex.com/t/701"
    assert item.score_threshold == 0.62


@pytest.mark.asyncio
async def test_daily_budget_counts_only_candidates_retained_by_shared_pool(db: Database) -> None:
    pipeline = _DroppingPipeline()
    producer = V2EXDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
        source_modes=("hot",),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=1)

    assert result["discovered"] == 1
    assert result["enqueued"] == 0
    assert producer.consumed_today("hot") == 0
    row = db.conn.execute(
        "SELECT units, discovered FROM v2ex_discovery_runs WHERE mode='hot'"
    ).fetchone()
    assert tuple(row) == (0, 1)


def test_node_downweight_threshold_and_profile_node_cap_are_effective(db: Database) -> None:
    producer = V2EXDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        max_profile_nodes=2,
    )

    normal = producer._normalize_rows(
        [_topic(801, node="programmer")],
        strategy="v2ex-node",
    )[0]
    downweighted = producer._normalize_rows(
        [_topic(802, node="deals")],
        strategy="v2ex-node",
    )[0]
    profile = {"preferences": {"v2ex_nodes": ["a", "b", "c", "d"]}}

    assert normal.score_threshold == 0.62
    assert downweighted.score_threshold == 0.72
    assert producer._configured_nodes(profile) == ("a", "b")


def test_node_affinity_follows_committed_profile_identity(db: Database) -> None:
    from openbiliclaw.sources.v2ex_affinity import V2EXNodeAffinityStore

    store = V2EXNodeAffinityStore(db)
    store.record_items(
        [{"scope": "public_topics", "topic_id": "1", "node_name": "alice-node"}],
        username="alice",
    )
    store.record_items(
        [{"scope": "public_topics", "topic_id": "2", "node_name": "bob-node"}],
        username="bob",
    )
    db.activate_v2ex_profile_identity("alice")
    producer = V2EXDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        identity_username="bob",
    )

    assert producer._configured_nodes({}) == ("alice-node",)


def test_default_mode_mix_matches_40_40_10_5_5_at_normal_batch_size() -> None:
    assert _allocate_weighted(100, V2EX_SOURCE_MODES, V2EX_SOURCE_WEIGHTS) == {
        "search": 40,
        "node": 40,
        "tab": 10,
        "hot": 5,
        "latest": 5,
    }
    assert all(
        value >= 1
        for value in _allocate_weighted(5, V2EX_SOURCE_MODES, V2EX_SOURCE_WEIGHTS).values()
    )


def test_external_search_provider_is_independent_of_keyword_generation_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected = object()

    def build(backends: object, **kwargs: object) -> object:
        captured["backends"] = backends
        return expected

    monkeypatch.setattr(
        "openbiliclaw.discovery.inspiration_provider.build_inspiration_search_provider",
        build,
    )
    config = SimpleNamespace(
        discovery=SimpleNamespace(
            inspiration_search_enabled=False,
            inspiration_search_backends=("local_cache", "exa", "you"),
        )
    )

    provider = build_v2ex_external_search_provider(config)

    assert provider is expected
    assert captured["backends"] == ("exa", "you")


def test_external_search_provider_includes_keyless_bing_rss_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    config = SimpleNamespace(
        discovery=SimpleNamespace(
            inspiration_search_enabled=True,
            inspiration_search_backends=("local_cache", "bing_rss", "exa", "you"),
            exa_api_key="",
            you_api_key="",
        )
    )

    provider = build_v2ex_external_search_provider(config)

    assert provider is not None
    assert provider.backend_alias == "bing_rss"


def test_external_search_provider_is_absent_without_configured_external_backend() -> None:
    config = SimpleNamespace(
        discovery=SimpleNamespace(
            inspiration_search_enabled=True,
            inspiration_search_backends=("local_cache", "platform_sources"),
        )
    )

    assert build_v2ex_external_search_provider(config) is None
