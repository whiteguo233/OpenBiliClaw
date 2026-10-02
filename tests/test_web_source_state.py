"""Durable cadence, lease and item ledger for public Web recipes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openbiliclaw.storage.database import Database


def _db(tmp_path) -> Database:
    database = Database(tmp_path / "web-state.db")
    database.initialize()
    return database


def _save_web(
    database: Database,
    recipe_id: str,
    *,
    enabled: bool = True,
    interval_minutes: int = 30,
) -> None:
    database.save_source_recipe(
        {
            "id": recipe_id,
            "source_type": "web",
            "name": recipe_id,
            "strategy": "feed",
            "config": {
                "entry_url": f"https://{recipe_id}.example.com/feed.xml",
                "allowed_origins": [f"https://{recipe_id}.example.com"],
                "interval_minutes": interval_minutes,
                "max_items": 20,
                "max_pages": 1,
                "detail_fetch_limit": 10,
                "plan_version": 1,
            },
            "enabled": enabled,
        }
    )


def test_enabled_recipe_filter_is_source_scoped(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    database.save_source_recipe(
        {"id": "bili-a", "source_type": "bilibili", "name": "b", "strategy": "search"}
    )
    assert [row["id"] for row in database.get_enabled_recipes(source_type="web")] == ["web-a"]


def test_claim_due_web_recipes_is_leased_and_excludes_disabled(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    _save_web(database, "web-b")
    _save_web(database, "web-disabled", enabled=False)
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)

    first = database.claim_due_web_recipes(limit=1, now=now, lease_seconds=120)
    second = database.claim_due_web_recipes(limit=5, now=now, lease_seconds=120)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0]["id"] != second[0]["id"]
    assert {first[0]["id"], second[0]["id"]} == {"web-a", "web-b"}
    assert first[0]["lease_token"]


def test_completion_is_token_fenced_and_success_commits_validators(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claimed = database.claim_due_web_recipes(limit=1, now=now)[0]

    assert (
        database.complete_web_recipe_fetch(
            "web-a",
            lease_token="stale-token",
            status="ok",
            discovered_count=2,
            enqueued_count=1,
            interval_minutes=30,
            etag='"v1"',
            last_modified="Wed, 12 Aug 2026 11:00:00 GMT",
            cursor={"watermark": "item-2"},
            scope_complete=True,
            now=now,
        )
        is False
    )
    assert database.get_web_source_state("web-a")["status"] == "running"

    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claimed["lease_token"],
        status="ok",
        discovered_count=2,
        enqueued_count=1,
        interval_minutes=30,
        etag='"v1"',
        last_modified="Wed, 12 Aug 2026 11:00:00 GMT",
        cursor={"watermark": "item-2"},
        scope_complete=True,
        now=now,
    )
    state = database.get_web_source_state("web-a")
    assert state["status"] == "ok"
    assert state["etag"] == '"v1"'
    assert state["cursor"] == {"watermark": "item-2"}
    assert state["last_success_at"]
    assert state["last_productive_at"]
    assert state["next_fetch_at"] == (now + timedelta(minutes=30)).isoformat()


def test_degraded_partial_result_preserves_committed_cursor_and_validators(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a", interval_minutes=5)
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    first = database.claim_due_web_recipes(limit=1, now=now)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=first["lease_token"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=5,
        etag='"good"',
        validator_url="https://web-a.example.com/feed.xml",
        cursor={"page": 1},
        now=now,
    )

    later = now + timedelta(minutes=6)
    second = database.claim_due_web_recipes(limit=1, now=later)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=second["lease_token"],
        status="degraded",
        discovered_count=2,
        enqueued_count=1,
        interval_minutes=5,
        etag='"partial"',
        validator_url="https://redirected.example.com/feed.xml",
        cursor={"page": 2},
        retry_cursor={"sitemap_entry_offset": 2},
        error_code="parse_partial",
        scope_complete=False,
        now=later,
    )
    state = database.get_web_source_state("web-a")
    assert state["status"] == "degraded"
    assert state["etag"] == '"good"'
    assert state["validator_url"] == "https://web-a.example.com/feed.xml"
    assert state["cursor"] == {"page": 1}
    assert state["retry_cursor"] == {"sitemap_entry_offset": 2}
    assert state["last_error_code"] == "parse_partial"
    assert state["last_productive_at"]


def test_complete_scope_clears_degraded_retry_cursor(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a", interval_minutes=5)
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    first = database.claim_due_web_recipes(limit=1, now=now)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=first["lease_token"],
        plan_fingerprint=first["plan_fingerprint"],
        status="degraded",
        discovered_count=0,
        enqueued_count=0,
        interval_minutes=5,
        retry_cursor={"sitemap_entry_offset": 1},
        scope_complete=False,
        now=now,
    )
    assert database.get_web_source_state("web-a")["retry_cursor"] == {"sitemap_entry_offset": 1}

    later = now + timedelta(minutes=6)
    second = database.claim_due_web_recipes(limit=1, now=later)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=second["lease_token"],
        plan_fingerprint=second["plan_fingerprint"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=5,
        cursor={"sitemap_entry_offset": 0},
        scope_complete=True,
        now=later,
    )
    state = database.get_web_source_state("web-a")
    assert state["cursor"] == {"sitemap_entry_offset": 0}
    assert state["retry_cursor"] == {}


def test_not_modified_without_response_validators_preserves_committed_values(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a", interval_minutes=5)
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    first = database.claim_due_web_recipes(limit=1, now=now)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=first["lease_token"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=5,
        etag='"good"',
        validator_url="https://web-a.example.com/feed.xml",
        last_modified="Wed, 12 Aug 2026 11:00:00 GMT",
        now=now,
    )
    later = now + timedelta(minutes=6)
    second = database.claim_due_web_recipes(limit=1, now=later)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=second["lease_token"],
        status="not_modified",
        discovered_count=0,
        enqueued_count=0,
        interval_minutes=5,
        etag="",
        validator_url="https://redirected.example.com/feed.xml",
        last_modified="",
        now=later,
    )

    state = database.get_web_source_state("web-a")
    assert state["etag"] == '"good"'
    assert state["validator_url"] == "https://web-a.example.com/feed.xml"
    assert state["last_modified"] == "Wed, 12 Aug 2026 11:00:00 GMT"


def test_safe_200_rebinds_validator_url(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a", interval_minutes=5)
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    first = database.claim_due_web_recipes(limit=1, now=now)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=first["lease_token"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=5,
        validator_url="https://web-a.example.com/feed.xml",
        now=now,
    )
    later = now + timedelta(minutes=6)
    second = database.claim_due_web_recipes(limit=1, now=later)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=second["lease_token"],
        status="empty",
        discovered_count=0,
        enqueued_count=0,
        interval_minutes=5,
        validator_url="https://web-a.example.com/canonical-feed.xml",
        now=later,
    )
    assert (
        database.get_web_source_state("web-a")["validator_url"]
        == "https://web-a.example.com/canonical-feed.xml"
    )


def test_initialize_adds_new_columns_to_existing_web_state_table(tmp_path) -> None:
    database = _db(tmp_path)
    database.conn.execute("ALTER TABLE web_source_state DROP COLUMN validator_url")
    database.conn.execute("ALTER TABLE web_source_state DROP COLUMN plan_fingerprint")
    database.conn.execute("ALTER TABLE web_source_state DROP COLUMN retry_cursor_json")
    database.conn.commit()
    database.close()

    reopened = Database(tmp_path / "web-state.db")
    reopened.initialize()

    columns = {
        str(row["name"])
        for row in reopened.conn.execute("PRAGMA table_info(web_source_state)").fetchall()
    }
    assert "validator_url" in columns
    assert "plan_fingerprint" in columns
    assert "retry_cursor_json" in columns


def test_expired_lease_cannot_renew_or_complete(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claimed = database.claim_due_web_recipes(
        limit=1,
        now=now,
        lease_seconds=30,
    )[0]
    expired = now + timedelta(seconds=31)

    assert not database.renew_web_recipe_lease(
        "web-a",
        lease_token=claimed["lease_token"],
        lease_seconds=300,
        now=expired,
    )
    assert not database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claimed["lease_token"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        now=expired,
    )


def test_execution_plan_change_atomically_resets_state_and_seen(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claimed = database.claim_due_web_recipes(limit=1, now=now)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claimed["lease_token"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        etag='"old"',
        cursor={"offset": 7},
        plan_fingerprint=claimed["plan_fingerprint"],
        seen_items=[{"item_key": "web:url-old", "canonical_url": "https://web-a.example.com/old"}],
        now=now,
    )
    recipe = database.get_enabled_recipes(source_type="web")[0]
    database.save_source_recipe({**recipe, "name": "Display only", "target_share": 2})
    assert database.get_web_source_state("web-a")["etag"] == '"old"'

    replacement = {
        **recipe,
        "name": "Replacement",
        "strategy": "sitemap",
        "config": {
            **recipe["config"],
            "entry_url": "https://other.example.com/sitemap.xml",
            "allowed_origins": ["https://other.example.com"],
        },
    }
    database.save_source_recipe(replacement)

    assert database.get_web_source_state("web-a") is None
    assert database.web_seen_item_keys("web-a", ["web:url-old"]) == set()


def test_interval_change_is_immediately_due_without_resetting_fetch_state(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claim = database.claim_due_web_recipes(limit=1, now=now)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claim["lease_token"],
        plan_fingerprint=claim["plan_fingerprint"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        etag='"stable"',
        cursor={"offset": 4},
        seen_items=[
            {
                "item_key": "web:url-stable",
                "canonical_url": "https://web-a.example.com/stable",
            }
        ],
        now=now,
    )
    recipe = database.get_enabled_recipes(source_type="web")[0]

    database.save_source_recipe({**recipe, "config": {**recipe["config"], "interval_minutes": 60}})

    state = database.get_web_source_state("web-a")
    assert state["next_fetch_at"] == ""
    assert state["etag"] == '"stable"'
    assert state["cursor"] == {"offset": 4}
    assert database.web_seen_item_keys("web-a", ["web:url-stable"]) == {"web:url-stable"}
    assert database.claim_due_web_recipes(limit=1, now=now + timedelta(seconds=1))


def test_inflight_completion_uses_latest_recipe_interval(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claim = database.claim_due_web_recipes(limit=1, now=now, lease_seconds=120)[0]
    recipe = database.get_enabled_recipes(source_type="web")[0]
    database.save_source_recipe({**recipe, "config": {**recipe["config"], "interval_minutes": 5}})

    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claim["lease_token"],
        plan_fingerprint=claim["plan_fingerprint"],
        status="empty",
        discovered_count=0,
        enqueued_count=0,
        # This is the stale claim snapshot. Completion must ignore it in
        # favour of the current recipe config read inside the transaction.
        interval_minutes=30,
        now=now + timedelta(seconds=1),
    )
    state = database.get_web_source_state("web-a")
    assert state["next_fetch_at"] == (now + timedelta(minutes=5, seconds=1)).isoformat()


def test_fetch_handoff_cannot_recreate_seen_after_plan_replacement(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    old_claim = database.claim_due_web_recipes(limit=1, now=now)[0]
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
    replacement_claim = database.claim_due_web_recipes(limit=1, now=now)[0]

    assert not database.complete_web_recipe_fetch(
        "web-a",
        lease_token=old_claim["lease_token"],
        plan_fingerprint=old_claim["plan_fingerprint"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        cursor={"offset": 99},
        seen_items=[
            {
                "item_key": "web:url-old-worker",
                "canonical_url": "https://web-a.example.com/old-worker",
            }
        ],
        now=now,
    )

    assert database.web_seen_item_keys("web-a", ["web:url-old-worker"]) == set()
    state = database.get_web_source_state("web-a")
    assert state["status"] == "running"
    assert state["lease_token"] == replacement_claim["lease_token"]
    assert state["cursor"] == {}


def test_fetch_handoff_rejects_changed_plan_even_if_lease_row_survives(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claim = database.claim_due_web_recipes(limit=1, now=now)[0]
    recipe = database.get_enabled_recipes(source_type="web")[0]
    database.update_recipe(
        "web-a",
        strategy="sitemap",
        config={
            **recipe["config"],
            "entry_url": "https://replacement.example.com/sitemap.xml",
            "allowed_origins": ["https://replacement.example.com"],
        },
    )

    assert not database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claim["lease_token"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        cursor={"offset": 99},
        now=now,
    )
    assert database.get_web_source_state("web-a")["cursor"] == {}


def test_fetch_handoff_rejects_missing_claimed_plan_fingerprint(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claim = database.claim_due_web_recipes(limit=1, now=now)[0]
    database.conn.execute(
        "UPDATE web_source_state SET plan_fingerprint = '' WHERE recipe_id = 'web-a'"
    )
    database.conn.commit()

    assert not database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claim["lease_token"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        seen_items=[
            {
                "item_key": "web:url-unfenced",
                "canonical_url": "https://web-a.example.com/unfenced",
            }
        ],
        now=now,
    )
    assert database.web_seen_item_keys("web-a", ["web:url-unfenced"]) == set()
    assert database.get_web_source_state("web-a")["status"] == "running"


def test_rate_limit_retry_after_controls_next_fetch(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claimed = database.claim_due_web_recipes(limit=1, now=now)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claimed["lease_token"],
        status="rate_limited",
        discovered_count=0,
        enqueued_count=0,
        interval_minutes=30,
        retry_after_seconds=900,
        error_code="http_429",
        scope_complete=False,
        now=now,
    )
    state = database.get_web_source_state("web-a")
    assert state["next_fetch_at"] == (now + timedelta(minutes=15)).isoformat()
    assert database.claim_due_web_recipes(limit=1, now=now + timedelta(minutes=14)) == []


def test_robots_retry_after_controls_failed_recipe_cooldown(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a", interval_minutes=5)
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    claimed = database.claim_due_web_recipes(limit=1, now=now)[0]

    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claimed["lease_token"],
        plan_fingerprint=claimed["plan_fingerprint"],
        status="failed",
        discovered_count=0,
        enqueued_count=0,
        interval_minutes=5,
        retry_after_seconds=3_600,
        error_code="robots_unavailable",
        scope_complete=False,
        now=now,
    )

    state = database.get_web_source_state("web-a")
    assert state["status"] == "failed"
    assert state["last_error_code"] == "robots_unavailable"
    assert state["next_fetch_at"] == (now + timedelta(hours=1)).isoformat()
    assert database.claim_due_web_recipes(limit=1, now=now + timedelta(minutes=59)) == []


def test_web_seen_ledger_is_recipe_scoped_and_delete_cleans_state(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    claim = database.claim_due_web_recipes(limit=1)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claim["lease_token"],
        plan_fingerprint=claim["plan_fingerprint"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
        seen_items=[
            {
                "item_key": "web:url:abc",
                "canonical_url": "https://web-a.example.com/post/1",
                "content_hash": "hash-1",
            }
        ],
    )
    assert database.web_seen_item_keys("web-a", ["web:url:abc", "web:url:def"]) == {"web:url:abc"}
    assert database.get_web_source_state("web-a") is not None

    assert database.delete_recipe("web-a") is True
    assert database.get_web_source_state("web-a") is None
    assert database.web_seen_item_keys("web-a", ["web:url:abc"]) == set()


def test_web_seen_ledger_has_a_per_recipe_retention_bound(tmp_path) -> None:
    database = _db(tmp_path)
    _save_web(database, "web-a")
    claim = database.claim_due_web_recipes(limit=1)[0]
    assert database.complete_web_recipe_fetch(
        "web-a",
        lease_token=claim["lease_token"],
        plan_fingerprint=claim["plan_fingerprint"],
        status="ok",
        discovered_count=105,
        enqueued_count=105,
        interval_minutes=30,
        seen_items=[
            {
                "item_key": f"web:url:{index:03d}",
                "canonical_url": f"https://web-a.example.com/{index}",
            }
            for index in range(105)
        ],
        seen_keep=100,
    )

    count = database.conn.execute(
        "SELECT COUNT(*) FROM web_source_seen WHERE recipe_id = 'web-a'"
    ).fetchone()[0]
    assert count == 100
