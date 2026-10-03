"""Run each custom source in its own existing Discover quota slot."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openbiliclaw.sources.protocol import SourceFetchResult
from openbiliclaw.sources.workflow_service import WorkflowService

logger = logging.getLogger(__name__)


@dataclass
class CustomWorkflowProducer:
    """Persist parameters and progress, then use ordinary candidate admission."""

    database: Any
    source_id: str
    candidate_pipeline: Any
    soul_engine: Any = None
    candidate_evaluation_owned_by_coordinator: bool = False
    config: Any = None
    llm_service: Any = None
    keyword_fetch_coordinator: Any = None
    service: Any = None

    async def produce_if_due(
        self, *, limit: int | None = None, force: bool = False
    ) -> dict[str, Any]:
        service = self.service or WorkflowService(
            self.database,
            config=self.config,
            llm_service=self.llm_service,
        )
        source = service.sources.get_source(self.source_id)
        payload: dict[str, Any] = {
            "source_family": self.source_id,
            "ran": False,
            "discovered": 0,
            "enqueued": 0,
            "reason": "throttled",
        }
        if not source or not source["enabled"]:
            payload["reason"] = "disabled"
            return payload
        remaining = min(50, max(1, int(limit or 20)))
        coordinator = self.keyword_fetch_coordinator
        for workflow in service.store.list_workflows(self.source_id):
            if remaining <= 0:
                break
            claimed: list[Any] = []
            query = ""
            if workflow["query_mode"] == "interest":
                if coordinator is not None:
                    run_count = service.store.run_count(workflow["workflow_id"])
                    kind = "explore" if run_count % 2 else "regular"
                    claimed = coordinator.claim(self.source_id, n=1, keyword_kind=kind)
                    if not claimed:
                        claimed = coordinator.claim(
                            self.source_id,
                            n=1,
                            keyword_kind="regular" if kind == "explore" else "explore",
                        )
                if not claimed:
                    payload["reason"] = "keyword_unavailable"
                    continue
                query = claimed[0].keyword
            parameters = service.parameters(workflow, query)
            run = service.store.claim(
                workflow["workflow_id"],
                parameters,
                force=force,
                lease_seconds=900 if workflow["executor"] == "http" else 180,
            )
            if run is None:
                for keyword in claimed:
                    coordinator.rollback(keyword)
                continue
            payload["ran"] = True
            try:
                result = await service.fetch(run, parameters=parameters, limit=50)
            except Exception as exc:
                logger.warning(
                    "Custom workflow fetch failed: %s", run["workflow_id"], exc_info=True
                )
                result = SourceFetchResult(
                    status="failed",
                    scope_complete=False,
                    error_code="executor_error",
                    detail=type(exc).__name__,
                )
            for item in result.items:
                item.source_id = self.source_id
                item.source_name = source["name"]
                item.source_metadata.update(
                    workflow_id=run["workflow_id"],
                    workflow_version=run["version"],
                    run_id=run["run_id"],
                )
                if claimed:
                    item.source_keyword_id = claimed[0].id
                    item.source_metadata["keyword_id"] = claimed[0].id
                    item.source_metadata["keyword"] = query
            try:
                outcome = service.store.complete(
                    run,
                    status=result.status,
                    items=result.items,
                    enqueue=lambda items: int(
                        self.candidate_pipeline.enqueue_candidates(
                            items, source_context="web-workflow"
                        )
                        or 0
                    ),
                    interval_minutes=source["interval_minutes"],
                    limit=remaining,
                    error=result.error_code or result.detail,
                    scope_complete=result.scope_complete,
                    cursor={
                        "etag": result.etag,
                        "last_modified": result.last_modified,
                        "validator_url": result.validator_url,
                        "cursor": result.cursor,
                        "retry_cursor": result.retry_cursor,
                    },
                )
            except Exception:
                logger.warning("Custom workflow handoff failed: %s", run["run_id"], exc_info=True)
                outcome = service.store.complete(
                    run,
                    status="failed",
                    items=[],
                    enqueue=lambda _: 0,
                    interval_minutes=source["interval_minutes"],
                    error="candidate_enqueue_error",
                    scope_complete=False,
                )
            payload["discovered"] += outcome["discovered_count"]
            payload["enqueued"] += outcome["enqueued_count"]
            remaining -= outcome["enqueued_count"]
            payload["reason"] = outcome["status"]
            if claimed:
                if outcome["status"] in {"ok", "empty", "not_modified"}:
                    coordinator.mark_used(claimed)
                else:
                    for keyword in claimed:
                        coordinator.requeue_transient(keyword)
            if (
                outcome["status"] != "stale"
                and result.error_code == "extraction_unverified"
                and self.llm_service is not None
            ):
                await self._repair(service, workflow)
        if payload["enqueued"] and not self.candidate_evaluation_owned_by_coordinator:
            drain = getattr(self.candidate_pipeline, "drain_pending", None)
            get_profile = getattr(self.soul_engine, "get_profile", None)
            if callable(drain) and callable(get_profile):
                profile = await get_profile()
                if profile is not None:
                    payload.update(
                        await drain(profile=profile, batch_size=max(1, int(limit or 20)))
                    )
        return payload

    async def _repair(self, service: WorkflowService, workflow: dict[str, Any]) -> None:
        """At most two attempts per failing version; preserve its intent and scope."""
        current = service.store.get_workflow(workflow["workflow_id"])
        source = service.sources.get_source(self.source_id)
        if (
            current["version"] != workflow["version"]
            or current["consecutive_failures"] > 2
            or current["executor"] == "http"
            or not source
            or not source["enabled"]
            or source.get("archived")
        ):
            return
        draft = {
            key: current[key]
            for key in (
                "workflow_id",
                "source_id",
                "entry_url",
                "origin",
                "intent",
                "name",
                "session_id",
                "executor",
                "workflow",
                "query_mode",
                "query",
            )
        }
        draft.update(base_version=current["version"], status="draft", trial_success=False)
        for key in ("resource_origins", "login_origins", "requires_login"):
            if key in current:
                draft[key] = current[key]
        draft = service.store.save_draft(draft)
        try:
            repaired = await service.author_browser(draft)
            if repaired.get("status") == "ready":
                source = service.sources.get_source(self.source_id)
                # A repair cannot silently change account, dynamic query policy,
                # authentication requirement or the approved collection origin.
                old_auth = current["workflow"].get("auth", {})
                new_auth = repaired["workflow"].get("auth", {})
                fixed_query_unchanged = current["query_mode"] != "fixed" or (
                    repaired["query"] == current["query"]
                )
                if (
                    source
                    and source["enabled"]
                    and not source.get("archived")
                    and repaired["query_mode"] == current["query_mode"]
                    and fixed_query_unchanged
                    and bool(new_auth.get("required")) == bool(old_auth.get("required"))
                    and new_auth.get("login_origins", []) == old_auth.get("login_origins", [])
                ):
                    service.store.publish(repaired["draft_id"], self.source_id)
        except Exception:
            logger.warning(
                "Bounded workflow repair failed: %s", workflow["workflow_id"], exc_info=True
            )
