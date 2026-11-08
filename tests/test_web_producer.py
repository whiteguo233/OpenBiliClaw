"""Formal recipe scheduler -> candidate pipeline handoff for public Web."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openbiliclaw.discovery.candidate_pool import candidate_key_for
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.runtime.web_producer import WebDiscoveryProducer
from openbiliclaw.sources.protocol import SourceFetchResult
from openbiliclaw.sources.web_public import web_content_id
from openbiliclaw.sources.web_recipe import build_web_recipe
from openbiliclaw.storage.database import Database


def _db(tmp_path) -> Database:
    database = Database(tmp_path / "web-producer.db")
    database.initialize()
    return database


def _save(database: Database, recipe_id: str, host: str, *, share: int = 1) -> None:
    database.save_source_recipe(
        build_web_recipe(
            {
                "name": recipe_id,
                "strategy": "feed",
                "config": {
                    "entry_url": f"https://{host}/feed.xml",
                    "interval_minutes": 30,
                    "max_items": 10,
                    "detail_fetch_limit": 5,
                },
                "target_share": share,
            },
            recipe_id=recipe_id,
        )
    )


def _item(url: str, title: str) -> DiscoveredContent:
    return DiscoveredContent(
        content_id="url-placeholder",
        content_url=url,
        title=title,
        source_platform="web",
        source_strategy="web-feed",
        content_type="article",
    )


class _Adapter:
    def __init__(self, outcomes: dict[str, SourceFetchResult | BaseException]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, int, dict[str, object]]] = []

    async def fetch_result(self, recipe, _profile, *, limit: int, state):
        self.calls.append((recipe.id, limit, state))
        outcome = self.outcomes[recipe.id]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Pipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[list[DiscoveredContent], str]] = []

    def enqueue_candidates(self, items, *, source_context: str) -> int:
        self.calls.append((list(items), source_context))
        return len(items)


@pytest.mark.asyncio
async def test_producer_reads_due_recipes_isolates_failure_and_records_provenance(tmp_path) -> None:
    database = _db(tmp_path)
    _save(database, "bad", "bad.example.com")
    _save(database, "good", "good.example.com")
    adapter = _Adapter(
        {
            "bad": RuntimeError("boom"),
            "good": SourceFetchResult(
                items=[_item("https://good.example.com/post/1", "One")],
                status="ok",
                etag='"v1"',
            ),
        }
    )
    pipeline = _Pipeline()
    producer = WebDiscoveryProducer(
        database=database,
        adapter=adapter,
        candidate_pipeline=pipeline,
        enabled=True,
    )

    result = await producer.produce_if_due(limit=4, force=True)

    assert result["ran"] is True
    assert result["made_progress"] is True
    assert result["productive_supply"] is True
    assert result["discovered"] == 1
    assert result["enqueued"] == 1
    assert result["recipe_results"]["bad"]["status"] == "failed"
    assert result["recipe_results"]["good"]["status"] == "ok"
    assert database.get_web_source_state("bad")["status"] == "failed"
    assert database.get_web_source_state("good")["etag"] == '"v1"'
    handed = pipeline.calls[0][0][0]
    assert handed.source_recipe_id == "good"
    assert handed.source_site == "good.example.com"
    assert pipeline.calls[0][1] == "web-feed"
    assert database.web_seen_item_keys("good", [candidate_key_for(handed)]) == {
        candidate_key_for(handed)
    }


@pytest.mark.asyncio
async def test_producer_seen_ledger_suppresses_repeat_inside_recipe(tmp_path) -> None:
    database = _db(tmp_path)
    _save(database, "web-a", "a.example.com")
    item = _item("https://a.example.com/post/1", "One")
    item.content_id = web_content_id(item.content_url)
    claim = database.claim_due_web_recipes(limit=1)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claim["lease_token"],
        plan_fingerprint=claim["plan_fingerprint"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        seen_items=[{"item_key": candidate_key_for(item), "canonical_url": item.content_url}],
    )
    pipeline = _Pipeline()
    producer = WebDiscoveryProducer(
        database=database,
        adapter=_Adapter({"web-a": SourceFetchResult(items=[item], status="ok")}),
        candidate_pipeline=pipeline,
        enabled=True,
    )
    result = await producer.produce_if_due(limit=3, force=True)
    assert result["discovered"] == 1
    assert result["enqueued"] == 0
    assert result["productive_supply"] is False
    assert pipeline.calls == []


@pytest.mark.asyncio
async def test_feed_scan_window_drains_unseen_rows_before_committing_validator(tmp_path) -> None:
    database = _db(tmp_path)
    _save(database, "web-a", "a.example.com")
    first = _item("https://a.example.com/post/1", "One")
    second = _item("https://a.example.com/post/2", "Two")

    class _StableFeedAdapter:
        def __init__(self) -> None:
            self.limits: list[int] = []

        async def fetch_result(self, _recipe, _profile, *, limit: int, state):
            self.limits.append(limit)
            return SourceFetchResult(
                items=[first, second],
                status="ok",
                etag='"feed-v1"',
                validator_url="https://a.example.com/feed.xml",
            )

    adapter = _StableFeedAdapter()
    pipeline = _Pipeline()
    producer = WebDiscoveryProducer(
        database=database,
        adapter=adapter,
        candidate_pipeline=pipeline,
        enabled=True,
    )

    first_run = await producer.produce_if_due(limit=1, force=True)
    first_state = database.get_web_source_state("web-a")
    second_run = await producer.produce_if_due(limit=1, force=True)
    second_state = database.get_web_source_state("web-a")

    assert adapter.limits == [10, 10]
    assert first_run["enqueued"] == 1
    assert first_run["recipe_results"]["web-a"]["scope_complete"] is False
    assert first_state["etag"] == ""
    assert second_run["enqueued"] == 1
    assert second_run["recipe_results"]["web-a"]["scope_complete"] is True
    assert second_state["etag"] == '"feed-v1"'
    handed_urls = [call[0][0].content_url for call in pipeline.calls]
    assert handed_urls == [first.content_url, second.content_url]


@pytest.mark.asyncio
async def test_producer_no_recipe_does_not_claim_run_or_supply(tmp_path) -> None:
    producer = WebDiscoveryProducer(
        database=_db(tmp_path),
        adapter=_Adapter({}),
        candidate_pipeline=_Pipeline(),
        enabled=True,
    )
    assert await producer.produce_if_due(limit=5) == {
        "source_family": "web",
        "reason": "no_recipes",
        "ran": False,
        "made_progress": False,
        "productive_supply": False,
        "discovered": 0,
        "enqueued": 0,
        "recipe_results": {},
    }


@pytest.mark.asyncio
async def test_legacy_web_extract_row_is_not_a_formal_public_web_recipe(tmp_path) -> None:
    from openbiliclaw.runtime.web_producer import web_source_status

    database = _db(tmp_path)
    database.save_source_recipe(
        {
            "id": "legacy",
            "source_type": "web",
            "name": "Legacy extractor",
            "strategy": "web_extract",
            "config": {"url": "https://example.com/article"},
            "enabled": True,
        }
    )
    adapter = _Adapter({})
    producer = WebDiscoveryProducer(
        database=database,
        adapter=adapter,
        candidate_pipeline=_Pipeline(),
        enabled=True,
    )

    result = await producer.produce_if_due(limit=5, force=True)

    assert result["reason"] == "no_recipes"
    assert adapter.calls == []
    assert database.claim_due_web_recipes(limit=1, force=True) == []
    assert web_source_status(database, enabled=True)["state"] == "unverified"


@pytest.mark.asyncio
async def test_producer_respects_global_switch(tmp_path) -> None:
    database = _db(tmp_path)
    _save(database, "web-a", "a.example.com")
    producer = WebDiscoveryProducer(
        database=database,
        adapter=_Adapter({}),
        candidate_pipeline=_Pipeline(),
        enabled=False,
    )
    result = await producer.produce_if_due(limit=5, force=True)
    assert result["reason"] == "disabled"
    assert result["ran"] is False


def test_producer_status_summary_distinguishes_no_recipe_and_degraded(tmp_path) -> None:
    database = _db(tmp_path)
    from openbiliclaw.runtime.web_producer import web_source_status

    assert web_source_status(database, enabled=True)["state"] == "unverified"
    assert web_source_status(database, enabled=False)["state"] == "disabled"
    _save(database, "web-a", "a.example.com")
    claimed = database.claim_due_web_recipes(limit=1)[0]
    database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claimed["lease_token"],
        status="degraded",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        error_code="schema_changed",
        scope_complete=False,
    )
    status = web_source_status(database, enabled=True)
    assert status["state"] == "partial"
    assert status["recipe_counts"] == {"degraded": 1}


def test_source_fetch_result_shape_is_mapping_independent() -> None:
    result = SourceFetchResult(status="empty")
    assert SimpleNamespace(**result.__dict__).status == "empty"


def test_recipe_allocation_is_weighted_and_respects_recipe_ceiling() -> None:
    from openbiliclaw.runtime.web_producer import _allocate_recipe_limits

    recipes = [
        {"id": "low", "target_share": 1, "config": {"max_items": 10}},
        {"id": "high", "target_share": 20, "config": {"max_items": 2}},
    ]
    assert _allocate_recipe_limits(recipes, 5) == {"low": 3, "high": 2}


def test_recipe_allocation_rotates_equal_weight_tie_across_cycles() -> None:
    from openbiliclaw.runtime.web_producer import _allocate_recipe_limits

    recipes = [
        {"id": "a", "target_share": 1, "config": {"max_items": 10}},
        {"id": "b", "target_share": 1, "config": {"max_items": 10}},
    ]
    assert _allocate_recipe_limits(recipes, 3, start_index=0) == {"a": 2, "b": 1}
    assert _allocate_recipe_limits(recipes, 3, start_index=1) == {"a": 1, "b": 2}


def test_run_lease_budget_matches_strategy_transport_calls() -> None:
    from openbiliclaw.runtime.web_producer import _run_lease_seconds

    config = {"max_pages": 5, "detail_fetch_limit": 20}
    assert (
        _run_lease_seconds(
            config,
            strategy="feed",
            configured_lease=30,
            request_timeout=15,
        )
        == 75
    )
    assert (
        _run_lease_seconds(
            config,
            strategy="listing",
            configured_lease=30,
            request_timeout=15,
        )
        == 375
    )
    assert (
        _run_lease_seconds(
            config,
            strategy="sitemap",
            configured_lease=30,
            request_timeout=15,
        )
        == 450
    )


@pytest.mark.asyncio
async def test_force_cycle_never_reclaims_an_already_attempted_recipe(tmp_path) -> None:
    database = _db(tmp_path)
    _save(database, "a", "a.example.com")
    _save(database, "b", "b.example.com")
    claims = database.claim_due_web_recipes(limit=2)
    for claim in claims:
        assert database.complete_web_recipe_fetch(
            str(claim["id"]),
            lease_token=claim["lease_token"],
            plan_fingerprint=claim["plan_fingerprint"],
            status="empty",
            discovered_count=0,
            enqueued_count=0,
            interval_minutes=30,
        )
    database.conn.execute(
        "UPDATE web_source_state SET last_attempt_at = '2000-01-01T00:00:00+00:00' "
        "WHERE recipe_id = 'a'"
    )
    database.conn.execute(
        "UPDATE web_source_state SET last_attempt_at = '2099-01-01T00:00:00+00:00' "
        "WHERE recipe_id = 'b'"
    )
    database.conn.commit()
    adapter = _Adapter(
        {
            "a": SourceFetchResult(status="empty"),
            "b": SourceFetchResult(status="empty"),
        }
    )
    producer = WebDiscoveryProducer(
        database=database,
        adapter=adapter,
        candidate_pipeline=_Pipeline(),
        enabled=True,
    )

    await producer.produce_if_due(limit=2, force=True)

    assert [recipe_id for recipe_id, _limit, _state in adapter.calls] == ["a", "b"]


@pytest.mark.asyncio
async def test_producer_degrades_instead_of_crashing_on_off_origin_candidate(tmp_path) -> None:
    database = _db(tmp_path)
    _save(database, "web-a", "a.example.com")
    producer = WebDiscoveryProducer(
        database=database,
        adapter=_Adapter(
            {
                "web-a": SourceFetchResult(
                    items=[_item("https://evil.example.net/post/1", "Off origin")],
                    status="ok",
                )
            }
        ),
        candidate_pipeline=_Pipeline(),
        enabled=True,
    )

    result = await producer.produce_if_due(limit=3, force=True)

    assert result["recipe_results"]["web-a"]["status"] == "degraded"
    assert result["recipe_results"]["web-a"]["error_code"] == "invalid_candidate_url"
    assert result["enqueued"] == 0


@pytest.mark.asyncio
async def test_producer_lost_lease_never_mutates_candidate_or_seen(tmp_path) -> None:
    database = _db(tmp_path)
    _save(database, "web-a", "a.example.com")

    class _LeaseProxy:
        def __init__(self, delegate) -> None:
            self.delegate = delegate
            self.renewals = iter((True, False))

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def renew_web_recipe_lease(self, *_args, **_kwargs):
            return next(self.renewals)

    pipeline = _Pipeline()
    producer = WebDiscoveryProducer(
        database=_LeaseProxy(database),
        adapter=_Adapter(
            {
                "web-a": SourceFetchResult(
                    items=[_item("https://a.example.com/post/1", "One")],
                    status="ok",
                )
            }
        ),
        candidate_pipeline=pipeline,
        enabled=True,
    )

    result = await producer.produce_if_due(limit=3, force=True)

    assert result["recipe_results"]["web-a"]["error_code"] == "lease_lost"
    assert result["enqueued"] == 0
    assert pipeline.calls == []
    assert database.web_seen_item_keys("web-a", ["web:url-any"]) == set()


@pytest.mark.asyncio
async def test_old_worker_cannot_finalize_after_second_renew_and_plan_reclaim(tmp_path) -> None:
    database = _db(tmp_path)
    _save(database, "web-a", "a.example.com")

    class _TrackingDatabase:
        def __init__(self, delegate) -> None:
            self.delegate = delegate
            self.renewals = 0

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def renew_web_recipe_lease(self, *args, **kwargs):
            renewed = self.delegate.renew_web_recipe_lease(*args, **kwargs)
            if renewed:
                self.renewals += 1
            return renewed

    tracking_database = _TrackingDatabase(database)

    class _PlanReplacingPipeline(_Pipeline):
        def __init__(self) -> None:
            super().__init__()
            self.replacement_claim = None

        def enqueue_candidates(self, items, *, source_context: str) -> int:
            assert tracking_database.renewals == 2
            inserted = super().enqueue_candidates(items, source_context=source_context)
            recipe = database.get_enabled_recipes(source_type="web")[0]
            database.save_source_recipe(
                {
                    **recipe,
                    "strategy": "sitemap",
                    "config": {
                        **recipe["config"],
                        "entry_url": "https://replacement.example.com/sitemap.xml",
                        "allowed_origins": ["https://replacement.example.com"],
                    },
                }
            )
            self.replacement_claim = database.claim_due_web_recipes(limit=1)[0]
            return inserted

    pipeline = _PlanReplacingPipeline()
    item = _item("https://a.example.com/post/1", "One")
    producer = WebDiscoveryProducer(
        database=tracking_database,
        adapter=_Adapter(
            {"web-a": SourceFetchResult(items=[item], status="ok", cursor={"page": 2})}
        ),
        candidate_pipeline=pipeline,
        enabled=True,
    )

    result = await producer.produce_if_due(limit=1, force=True)

    recipe_result = result["recipe_results"]["web-a"]
    assert pipeline.calls
    assert recipe_result["status"] == "failed"
    assert recipe_result["error_code"] == "lease_lost"
    assert recipe_result["state_committed"] is False
    item.content_id = web_content_id(item.content_url)
    assert database.web_seen_item_keys("web-a", [candidate_key_for(item)]) == set()
    state = database.get_web_source_state("web-a")
    assert state["status"] == "running"
    assert state["lease_token"] == pipeline.replacement_claim["lease_token"]
    assert state["cursor"] == {}
