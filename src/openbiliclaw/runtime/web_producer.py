"""Formal scheduler producer for explicit anonymous public-Web recipes."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from openbiliclaw.discovery.candidate_pool import candidate_key_for
from openbiliclaw.sources.protocol import SourceFetchResult, SourceRecipe
from openbiliclaw.sources.web_public import (
    WebFetchError,
    canonicalize_web_url,
    web_content_id,
)
from openbiliclaw.sources.web_recipe import is_compiled_public_web_recipe

logger = logging.getLogger(__name__)


@dataclass
class WebDiscoveryProducer:
    """Claim due Web recipes, fetch them fairly, and hand off canonical rows."""

    database: Any
    adapter: Any
    candidate_pipeline: Any | None
    enabled: bool = True
    lease_seconds: int = 300
    request_timeout_seconds: int = 15
    max_recipes_per_cycle: int = 8
    soul_engine: Any | None = None
    candidate_evaluation_owned_by_coordinator: bool = False
    allocation_round: int = 0

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        requested_limit = max(1, int(limit or 20))
        if not self.enabled:
            return _empty_result("disabled")
        enabled_recipes = [
            recipe
            for recipe in self.database.get_enabled_recipes(source_type="web")
            if is_compiled_public_web_recipe(recipe)
        ]
        if not enabled_recipes:
            return _empty_result("no_recipes")
        claim_limit = min(self.max_recipes_per_cycle, requested_limit, len(enabled_recipes))
        allocations = _allocate_recipe_limits(
            enabled_recipes,
            requested_limit,
            start_index=self.allocation_round,
        )
        self.allocation_round = (self.allocation_round + 1) % len(enabled_recipes)

        recipe_results: dict[str, dict[str, object]] = {}
        discovered_total = 0
        enqueued_total = 0
        ran = False
        claimed_any = False
        attempted_ids: set[str] = set()

        for _ in range(claim_limit):
            # Claim immediately before execution.  A batch-wide lease would
            # age while earlier recipes perform their bounded network work.
            claimed = self.database.claim_due_web_recipes(
                limit=1,
                lease_seconds=self.lease_seconds,
                force=force,
                exclude_recipe_ids=tuple(attempted_ids),
            )
            if not claimed:
                break
            claimed_any = True
            raw_recipe = claimed[0]
            recipe_id = str(raw_recipe["id"])
            attempted_ids.add(recipe_id)
            allocation = max(1, allocations.get(recipe_id, 1))
            lease_token = str(raw_recipe.get("lease_token") or "")
            plan_fingerprint = str(raw_recipe.get("plan_fingerprint") or "")
            if not lease_token or not plan_fingerprint:
                recipe_results[recipe_id] = _lost_lease_result()
                continue
            config = raw_recipe.get("config") if isinstance(raw_recipe.get("config"), dict) else {}
            interval_minutes = max(5, int(config.get("interval_minutes", 30)))
            run_lease_seconds = _run_lease_seconds(
                config,
                strategy=str(raw_recipe.get("strategy") or ""),
                configured_lease=self.lease_seconds,
                request_timeout=self.request_timeout_seconds,
            )
            if not self.database.renew_web_recipe_lease(
                recipe_id,
                lease_token=lease_token,
                lease_seconds=run_lease_seconds,
            ):
                recipe_results[recipe_id] = _lost_lease_result()
                continue
            recipe = SourceRecipe(
                **{
                    key: value
                    for key, value in raw_recipe.items()
                    if key in SourceRecipe.__dataclass_fields__
                }
            )
            state = self.database.get_web_source_state(recipe_id) or {}
            scan_limit = allocation
            if recipe.strategy in {"feed", "listing"}:
                # Feed/listing order is not a durable cursor. Scan the whole
                # bounded recipe window, then advance its unseen frontier in
                # allocation-sized handoffs below. This prevents a stable
                # first row from starving later rows forever.
                scan_limit = max(
                    allocation,
                    min(50, max(1, int(config.get("max_items", 20)))),
                )
            try:
                ran = True
                raw_result = await self.adapter.fetch_result(
                    recipe,
                    None,
                    limit=scan_limit,
                    state=state,
                )
                result = _coerce_result(raw_result)
            except Exception as exc:  # noqa: BLE001 - isolate one recipe from peers
                logger.warning("Web recipe fetch crashed: recipe=%s", recipe_id, exc_info=True)
                result = SourceFetchResult(
                    status="failed",
                    scope_complete=False,
                    error_code="adapter_error",
                    detail=str(exc)[:240],
                )

            discovered_total += len(result.items)
            # Re-fence after network I/O and extend through the short durable
            # handoff.  An expired/replaced worker must not enqueue or mark seen.
            if not self.database.renew_web_recipe_lease(
                recipe_id,
                lease_token=lease_token,
                lease_seconds=max(60, self.lease_seconds),
            ):
                recipe_results[recipe_id] = _lost_lease_result(discovered=len(result.items))
                continue
            new_items, seen_rows, normalization_error = _prepare_items(
                result.items,
                recipe_id=recipe_id,
                allowed_origins=[str(value) for value in config.get("allowed_origins", ())],
            )
            if normalization_error:
                result.status = "degraded"
                result.scope_complete = False
                result.error_code = normalization_error

            item_keys = [candidate_key_for(item) for item in new_items]
            seen = self.database.web_seen_item_keys(recipe_id, item_keys)
            unseen_items = [item for item in new_items if candidate_key_for(item) not in seen]
            unseen_rows = [
                row
                for item, row in zip(new_items, seen_rows, strict=True)
                if candidate_key_for(item) not in seen
            ]
            handoff_backlog = len(unseen_items) > allocation
            retained = unseen_items[:allocation]
            retained_rows = unseen_rows[:allocation]
            if handoff_backlog:
                # Do not commit the new HTTP validator until every row in the
                # current bounded window has either been handed off or was
                # already seen. The next run must refetch the same window so
                # the source-scoped seen ledger can expose the next slice.
                result.scope_complete = False
            enqueued = 0
            enqueue_error = ""
            handoff_rows: list[dict[str, str]] = []
            if retained:
                try:
                    enqueued = self._enqueue_by_strategy(retained)
                    handoff_rows = retained_rows
                except Exception:  # noqa: BLE001 - preserve fetch rows but fence cursor
                    logger.warning(
                        "Web candidate handoff failed: recipe=%s", recipe_id, exc_info=True
                    )
                    enqueue_error = "candidate_enqueue_error"
                    result.status = "degraded"
                    result.scope_complete = False
                    result.error_code = enqueue_error
            enqueued_total += enqueued
            completed = self.database.complete_web_recipe_fetch(
                recipe_id,
                lease_token=lease_token,
                plan_fingerprint=plan_fingerprint,
                status=result.status,
                discovered_count=len(result.items),
                enqueued_count=enqueued,
                interval_minutes=interval_minutes,
                etag=result.etag,
                last_modified=result.last_modified,
                validator_url=result.validator_url,
                cursor=result.cursor,
                retry_cursor=result.retry_cursor,
                error_code=result.error_code,
                retry_after_seconds=result.retry_after_seconds,
                scope_complete=result.scope_complete,
                seen_items=handoff_rows,
            )
            if not completed:
                recipe_results[recipe_id] = _lost_lease_result(
                    discovered=len(result.items),
                    new=len(retained),
                    enqueued=enqueued,
                )
                continue
            recipe_results[recipe_id] = {
                "status": result.status,
                "discovered": len(result.items),
                "new": len(retained),
                "enqueued": enqueued,
                "error_code": result.error_code or enqueue_error,
                "scope_complete": result.scope_complete,
                "state_committed": bool(completed),
            }

        productive = enqueued_total > 0
        if not claimed_any:
            return _empty_result("throttled")
        payload: dict[str, object] = {
            "source_family": "web",
            "reason": _overall_reason(recipe_results, productive=productive),
            "ran": ran,
            "made_progress": productive,
            "productive_supply": productive,
            "discovered": discovered_total,
            "enqueued": enqueued_total,
            "recipe_results": recipe_results,
        }
        if productive and not self.candidate_evaluation_owned_by_coordinator:
            await self._drain_inline(payload, batch_size=requested_limit)
        return payload

    def _enqueue_by_strategy(self, items: list[Any]) -> int:
        if self.candidate_pipeline is None:
            raise RuntimeError("Web candidate pipeline is not configured")
        inserted = 0
        strategies = dict.fromkeys(str(item.source_strategy or "web-listing") for item in items)
        for strategy in strategies:
            grouped = [item for item in items if str(item.source_strategy) == strategy]
            value = self.candidate_pipeline.enqueue_candidates(grouped, source_context=strategy)
            inserted += min(len(grouped), max(0, int(value or 0)))
        return inserted

    async def _drain_inline(self, payload: dict[str, object], *, batch_size: int) -> None:
        drain = getattr(self.candidate_pipeline, "drain_pending", None)
        get_profile = getattr(self.soul_engine, "get_profile", None)
        if not callable(drain) or not callable(get_profile):
            return
        try:
            profile = await get_profile()
            if profile is not None:
                payload.update(await drain(profile=profile, batch_size=batch_size))
        except Exception:
            logger.debug("inline Web candidate evaluation failed", exc_info=True)


def web_source_status(database: Any, *, enabled: bool) -> dict[str, object]:
    """Aggregate per-recipe health without performing any outbound request."""
    if not enabled:
        return {"state": "disabled", "detail": "Web 公开发现全局开关未启用。"}
    getter = getattr(database, "get_enabled_recipes", None)
    if not callable(getter):
        return {"state": "unverified", "detail": "Web recipe 状态存储不可用。"}
    recipes = [
        recipe for recipe in getter(source_type="web") if is_compiled_public_web_recipe(recipe)
    ]
    if not recipes:
        return {"state": "unverified", "detail": "尚未添加启用的 Web recipe。"}
    states = [database.get_web_source_state(str(recipe["id"])) for recipe in recipes]
    counts = Counter(str(state.get("status") or "never_run") for state in states if state)
    never_run = len(recipes) - sum(counts.values())
    if never_run:
        counts["never_run"] += never_run
    if counts.get("rate_limited"):
        state = "rate_limited"
    elif counts.get("failed"):
        state = "error"
    elif counts.get("degraded") or counts.get("login_required"):
        state = "partial"
    elif (
        counts.get("ok")
        or counts.get("empty")
        or counts.get("not_modified")
        or counts.get("running")
    ):
        state = "ready"
    else:
        state = "unverified"
    return {
        "state": state,
        "detail": f"{len(recipes)} 个 Web recipe；最近状态：{dict(counts)}。",
        "recipe_counts": dict(counts),
    }


def _empty_result(reason: str) -> dict[str, object]:
    return {
        "source_family": "web",
        "reason": reason,
        "ran": False,
        "made_progress": False,
        "productive_supply": False,
        "discovered": 0,
        "enqueued": 0,
        "recipe_results": {},
    }


def _allocate_recipe_limits(
    recipes: list[dict[str, Any]],
    total: int,
    *,
    start_index: int = 0,
) -> dict[str, int]:
    """Give every recipe one slot, then use bounded weighted fair queuing."""
    if not recipes:
        return {}
    offset = int(start_index) % len(recipes)
    recipes = [*recipes[offset:], *recipes[:offset]]
    allocations = {str(recipe["id"]): 1 for recipe in recipes}
    weights = {
        str(recipe["id"]): max(1, min(20, int(recipe.get("target_share", 1)))) for recipe in recipes
    }
    ceilings = {
        str(recipe["id"]): max(
            1,
            min(50, int((recipe.get("config") or {}).get("max_items", 20))),
        )
        for recipe in recipes
    }
    for _ in range(max(0, total - len(recipes))):
        eligible = [
            recipe_id for recipe_id in allocations if allocations[recipe_id] < ceilings[recipe_id]
        ]
        if not eligible:
            break
        recipe_id = min(
            eligible,
            key=lambda candidate: allocations[candidate] / weights[candidate],
        )
        allocations[recipe_id] += 1
    return allocations


def _run_lease_seconds(
    config: dict[str, Any],
    *,
    strategy: str,
    configured_lease: int,
    request_timeout: int,
) -> int:
    strategy_pages = max(1, int(config.get("max_pages", 1)))
    details = max(0, int(config.get("detail_fetch_limit", 0)))
    normalized_strategy = str(strategy).strip().lower()
    if normalized_strategy == "feed":
        request_budget = 1
    elif normalized_strategy == "listing":
        request_budget = 1 + details
    else:
        # Sitemap performs one root fetch, up to max_pages child sitemap
        # fetches, then the bounded detail fetches. Unknown future strategies
        # use this conservative envelope until they define their own budget.
        request_budget = 1 + strategy_pages + details
    return min(
        3_600,
        max(int(configured_lease), request_budget * max(1, int(request_timeout)) + 60),
    )


def _lost_lease_result(
    *,
    discovered: int = 0,
    new: int = 0,
    enqueued: int = 0,
) -> dict[str, object]:
    return {
        "status": "failed",
        "discovered": max(0, int(discovered)),
        "new": max(0, int(new)),
        "enqueued": max(0, int(enqueued)),
        "error_code": "lease_lost",
        "scope_complete": False,
        "state_committed": False,
    }


def _coerce_result(value: Any) -> SourceFetchResult:
    if not isinstance(value, SourceFetchResult):
        raise TypeError("Web adapter fetch_result returned an invalid outcome")
    return value


def _prepare_items(
    items: list[Any],
    *,
    recipe_id: str,
    allowed_origins: list[str],
) -> tuple[list[Any], list[dict[str, str]], str]:
    prepared: list[Any] = []
    rows: list[dict[str, str]] = []
    error = ""
    for item in items:
        try:
            canonical_url = canonicalize_web_url(
                item.content_url,
                allowed_origins=allowed_origins,
            )
        except WebFetchError:
            error = "invalid_candidate_url"
            continue
        item.content_url = canonical_url
        item.content_id = web_content_id(canonical_url)
        item.source_platform = "web"
        item.content_type = "article"
        item.source_recipe_id = recipe_id
        item.source_site = str(urlsplit(canonical_url).hostname or "")
        prepared.append(item)
        content_material = "\x00".join(
            [
                item.title or "",
                item.description or "",
                item.body_text or "",
                item.published_at or "",
            ]
        )
        rows.append(
            {
                "item_key": candidate_key_for(item),
                "canonical_url": canonical_url,
                "content_hash": hashlib.sha256(content_material.encode("utf-8")).hexdigest(),
            }
        )
    return prepared, rows, error


def _overall_reason(results: dict[str, dict[str, object]], *, productive: bool) -> str:
    if productive:
        return "ok"
    statuses = {str(result.get("status") or "") for result in results.values()}
    if statuses <= {"empty", "not_modified", "ok"}:
        return "valid_empty"
    if statuses & {"degraded", "login_required", "rate_limited"}:
        return "partial"
    return "error"
