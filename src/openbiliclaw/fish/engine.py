"""Blue Fish Lab engine — one fish, one comment-section turn at a time.

The engine is an **action layer**, not a second pipeline. It inherits every
decision the main flow already made:

* which videos are worth visiting comes from the ranked recommendation pool
  (``Database.get_recommendations(exclude_processed=True)``), not from a
  re-scoring pass of its own;
* what happened is recorded in the fish's own append-only ledger — and
  *nowhere else*. The fish's engagement is not the owner's engagement: it does
  not write recommendation feedback, does not touch the recommendation rows,
  and must never move the owner's soul profile.

What is genuinely new is the *external* act: pick one comment, write one
reply, post it with an ``@`` mention.

The engine is deliberately dependency-injected (LLM, candidate provider,
Bilibili client, clock) so the whole loop is unit-testable without a
network, a real account, or a real model.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.llm.json_utils import extract_llm_json_object
from openbiliclaw.llm.prompts import (
    build_blue_fish_notice_prompt,
    build_blue_fish_reply_prompt,
)

from .comments import usable_comments
from .config import FishConfig, load_fish_config
from .guard import (
    check_can_run,
    check_reply_text,
    mention_prefix,
    sanitize_reply,
)

if TYPE_CHECKING:
    from openbiliclaw.bilibili.api import CommentInfo

    from .account import FishAccount, FishAccountStatus
    from .plan import FishPlan, FishPlanStore
    from .store import FishLedgerView, FishStore

logger = logging.getLogger(__name__)

_LOOP_INTERVAL_SECONDS = 300


class _SupportsStructuredTask(Protocol):
    async def complete_structured_task(self, **kwargs: Any) -> Any: ...


class _SupportsComments(Protocol):
    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]: ...

    async def post_comment(
        self,
        bvid: str,
        *,
        message: str,
        root: int | None = None,
        parent: int | None = None,
    ) -> dict[str, Any]: ...

    async def send_private_message(self, uid: int, text: str) -> dict[str, Any]: ...

    async def close(self) -> None: ...


ClientFactory = Callable[[str], _SupportsComments]
CandidateProvider = Callable[[int], Awaitable[list["FishCandidate"]]]


@dataclass(frozen=True)
class FishCandidate:
    """A video the fish may visit, taken from the existing ranked pool.

    ``recommendation_id`` is a trace id for the ledger: it says which row the
    main pipeline scored and explained. It is never written back.
    """

    bvid: str
    title: str
    up_name: str = ""
    score: float = 0.0
    description: str = ""
    recommendation_id: int = 0
    topic: str = ""
    # Copied from the pool row so the optional quality gates can judge it
    # without another API call. 0 / "" means the pool could not tell.
    view_count: int = 0
    published_at: str = ""
    # How this video reached the engine: ``pool`` (the historical ranked-pool
    # pick) or ``plan`` (the comment-thread discovery route handed it over).
    # Recorded in the ledger so "did discovery actually drive this?" is
    # answerable from the file alone.
    origin: str = "pool"


@dataclass
class FishRunResult:
    """Outcome of one engine pass."""

    ok: bool
    status: str
    message: str
    bvid: str = ""
    target_rpid: int = 0
    reply: str = ""
    reply_rpid: int = 0
    record: dict[str, Any] = field(default_factory=dict)


class BlueFishEngine:
    """Runs one fish turn: gate → ranked candidates → LLM → guard → post."""

    def __init__(
        self,
        *,
        data_dir: Any,
        account: FishAccount,
        store: FishStore,
        llm: _SupportsStructuredTask | None,
        candidate_provider: CandidateProvider,
        client_factory: ClientFactory,
        clock: Callable[[], datetime] | None = None,
        main_account_provider: Callable[[], Awaitable[tuple[int, str, str]]] | None = None,
        plan_store: FishPlanStore | None = None,
        local_tz: tzinfo | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._account = account
        self._store = store
        self._llm = llm
        self._candidate_provider = candidate_provider
        self._client_factory = client_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._main_account_provider = main_account_provider
        # Plans are produced by the comment-thread discovery route. The engine
        # consumes them newest-first; ``None`` keeps the historical behaviour
        # (pick straight from the ranked pool), which is what tests and any
        # composition without the route get.
        self._plan_store = plan_store
        # Hour windows are wall-clock, so they need a local timezone even
        # though every stored timestamp stays UTC.
        self._local_tz: tzinfo = local_tz or datetime.now().astimezone().tzinfo or UTC
        self._lock = asyncio.Lock()

    # ── read-only surface ────────────────────────────────────────────────

    @property
    def llm_available(self) -> bool:
        """Whether an LLM is wired.

        The discovery tick reads B站 comment pages; without a model the action
        pass can only answer ``llm_unavailable``, so spending those requests
        would be pure waste.
        """

        return self._llm is not None

    @property
    def has_pending_plan(self) -> bool:
        """Whether a fresh discovery plan is already waiting to be acted on.

        The loop uses this to avoid scanning comment sections every tick
        while it is not allowed to speak (interval / quota): one plan in hand
        is enough, and ``take_fresh`` expires the ones that go stale.
        """

        if self._plan_store is None:
            return False
        return bool(self._plan_store.pending(now=self._clock()))

    async def status(self) -> dict[str, Any]:
        """Return everything the settings panel needs in one call."""
        config = load_fish_config(self._data_dir)
        today = self._today()
        stats = self._store.view(today).stats
        account, _owner_name, _owner_mid = await self._account_status()
        return {
            "ok": True,
            "config": config.to_public_dict(),
            "account": {
                "has_cookie": account.has_cookie,
                "authenticated": account.authenticated,
                "mid": account.mid,
                "uname": account.uname,
                "message": account.message,
                "isolated": account.isolated,
            },
            "state": {
                "xp": stats.xp,
                "level": stats.level,
                "published_total": stats.published_total,
                "today_published": stats.today_published,
                "today_notified": stats.today_notified,
                "last_run_at": stats.last_run_at,
            },
            "recent": self._store.recent(limit=20),
            # Human-readable view of the same acts; the ledger stays the
            # authoritative record.
            "today_report": self._store.read_report(today),
        }

    # ── run ─────────────────────────────────────────────────────────────

    async def run_once(
        self,
        *,
        trigger: str = "manual",
        dry_run: bool | None = None,
        manual: bool | None = None,
    ) -> FishRunResult:
        """Run one pass. Never raises — every failure becomes a record."""
        async with self._lock:
            config = load_fish_config(self._data_dir)
            # Off is off: do not read the fish cookie, do not call nav, do not
            # touch the ledger. A disabled lab must have zero external effect,
            # even when someone hits /api/fish/run manually.
            if not config.enabled:
                return FishRunResult(False, "disabled", "「养蓝色大肥鱼」还没开启。")
            effective_dry_run = config.dry_run if dry_run is None else bool(dry_run)
            effective_manual = trigger == "manual" if manual is None else bool(manual)
            now = self._clock()
            today = self._today(now)

            account, owner_name, owner_mid = await self._account_status()
            view = self._store.view(today)
            gate = check_can_run(
                config,
                authenticated=account.authenticated,
                isolated=account.isolated,
                today_published=view.stats.today_published,
                last_published_at=view.stats.last_published_at,
                now=now,
                dry_run=effective_dry_run,
                manual=effective_manual,
                failure_streak=view.stats.failure_streak,
                last_failure_at=view.stats.last_failure_at,
                local_now=now.astimezone(self._local_tz),
            )
            if not gate.ok:
                return FishRunResult(False, gate.code, gate.message)

            if self._llm is None:
                return FishRunResult(False, "llm_unavailable", "当前没有可用的模型，先配置 LLM。")

            try:
                candidates = await self._candidate_provider(config.max_candidates)
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                logger.warning("Blue Fish candidate lookup failed: %s", exc)
                candidates = []

            raw_candidates = list(candidates)
            if config.min_view_count > 0 or config.min_publish_age_days > 0:
                candidates = [
                    candidate
                    for candidate in candidates
                    if _candidate_passes_quality(candidate, config=config, now=now)
                ]
                if raw_candidates and not candidates:
                    return FishRunResult(
                        False,
                        "quality_gate",
                        "候选都被质量门槛挡住了：把「播放量」或「发布天数」调低试试。",
                    )

            client = self._client_factory(self._account.load_cookie())
            try:
                # One public act per pass. The owner notification is the only
                # act that touches the owner's identity, so it goes first when
                # it is due; the reply path (plan or pool) waits for the next
                # round, and the min-interval gate sees the notification
                # because the ledger view includes it.
                if _owner_notice_due(config, view=view) and candidates:
                    notified = await self._notify_owner(
                        client,
                        candidates[0],
                        config=config,
                        dry_run=effective_dry_run,
                        owner_name=owner_name,
                        owner_mid=owner_mid,
                    )
                    # A failed notice surfaces as this round's result. It
                    # must NOT fall through to the ordinary reply: the owner
                    # asked for a notice (possibly a private DM), and silently
                    # publishing a public reply instead is a different act.
                    # The failure is recorded and the breaker sees it.
                    if notified is not None:
                        return notified

                # A discovery-produced plan is one-shot: the comment-thread
                # route already fetched this comment section, so consuming the
                # plan here removes a second fetch and keeps discovery and the
                # public act in the same round. The gates above already ran;
                # the ledger is re-checked inside ``_visit``.
                plan = (
                    self._plan_store.take_fresh(now=now) if self._plan_store is not None else None
                )
                if plan is None and not candidates:
                    return FishRunResult(
                        False,
                        "no_candidates",
                        "没有可去的视频：推荐池还是空的，先去跑一轮发现。",
                    )
                if plan is not None:
                    result = await self._visit(
                        client,
                        _candidate_from_plan(plan),
                        config=config,
                        view=view,
                        dry_run=effective_dry_run,
                        fish_mid=account.mid,
                        comments=_comments_from_plan(plan),
                    )
                    if result is not None:
                        return result
                for candidate in candidates:
                    result = await self._visit(
                        client,
                        candidate,
                        config=config,
                        view=view,
                        dry_run=effective_dry_run,
                        fish_mid=account.mid,
                    )
                    if result is not None:
                        return result
            finally:
                await client.close()

            return FishRunResult(
                False,
                "nothing_to_say",
                "看了一圈，这批评论里没有能自然接上的，这次就不说话了。",
            )

    async def _visit(
        self,
        client: _SupportsComments,
        candidate: FishCandidate,
        *,
        config: FishConfig,
        view: FishLedgerView,
        dry_run: bool,
        fish_mid: int,
        comments: list[CommentInfo] | None = None,
    ) -> FishRunResult | None:
        """Try one video. Returns a result only when the run should stop.

        ``comments`` carries a discovery-produced snapshot: the route already
        paid for that fetch, so the engine reviews it instead of asking B站
        again. ``None`` keeps the historical fetch-per-visit behaviour.
        """
        # ``per_video_total`` is all-time, not today-only: once the fish has
        # spoken under a video it does not walk back in. The recommendation row
        # stays untouched, so this ledger check is the only thing stopping it
        # from repeating itself there.
        if not dry_run and view.per_video_total.get(candidate.bvid, 0) >= config.per_video_limit:
            return None

        if comments is None:
            try:
                comments = await client.get_video_comments(
                    candidate.bvid, limit=config.comments_per_video
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Blue Fish comment fetch failed for %s: %s", candidate.bvid, exc)
                # Recorded, not just returned: the failure-streak breaker reads
                # these lines, and "nothing happened" must stay inspectable.
                self._record(
                    candidate,
                    status="failed",
                    dry_run=dry_run,
                    target_rpid=0,
                    target_uname="",
                    target_text="",
                    reply="",
                    reason="",
                    error=f"拉不到评论：{exc}",
                )
                return FishRunResult(
                    False,
                    "comment_fetch_failed",
                    f"拉不到 {candidate.bvid} 的评论：{exc}",
                    bvid=candidate.bvid,
                )

        usable = usable_comments(
            comments,
            config=config,
            answered=view.answered,
            bvid=candidate.bvid,
            fish_mid=fish_mid,
        )
        if not usable:
            return None

        payload = [
            {
                "rpid": int(item.rpid),
                "uname": item.uname,
                "like_count": int(item.like_count),
                "message": item.message,
            }
            for item in usable
        ]
        messages = build_blue_fish_reply_prompt(
            persona_name=config.persona_name,
            persona_prompt=config.persona_prompt,
            reply_rules=config.reply_rules,
            audience=config.audience_prompt,
            trigger=config.trigger_prompt,
            video={
                "bvid": candidate.bvid,
                "title": candidate.title,
                "up_name": candidate.up_name,
                "description": candidate.description[:500],
                # Carried over from the ranked pool row: reuse the main
                # pipeline's topic label instead of guessing one again.
                "topic": candidate.topic,
            },
            comments=payload,
            max_chars=config.max_chars,
            allow_top_level=config.allow_top_level,
        )
        decision = await self._ask_model(messages, config=config)
        if decision is None:
            return None

        action, target_rpid, reply, reason = decision
        if action == "skip":
            self._record(
                candidate,
                status="skipped",
                dry_run=dry_run,
                target_rpid=0,
                target_uname="",
                target_text="",
                reply="",
                reason=reason,
                error="",
            )
            return None

        if action == "top_level":
            # Code-level gate, not a prompt promise: the model may ignore the
            # constraint, and a top-level comment is a different, noisier act
            # than answering inside a thread.
            if not config.allow_top_level:
                self._record(
                    candidate,
                    status="skipped",
                    dry_run=dry_run,
                    target_rpid=0,
                    target_uname="",
                    target_text="",
                    reply=reply,
                    reason=reason,
                    error="模型想发顶层评论，但配置不允许（allow_top_level=false）",
                )
                return None
            target = None
        else:
            target = next((item for item in usable if int(item.rpid) == target_rpid), None)
            if target is None:
                self._record(
                    candidate,
                    status="skipped",
                    dry_run=dry_run,
                    target_rpid=target_rpid,
                    target_uname="",
                    target_text="",
                    reply=reply,
                    reason=reason,
                    error="模型挑了一个不在候选里的 rpid",
                )
                return None

        body = sanitize_reply(reply)
        text_gate = check_reply_text(config, body)
        if not text_gate.ok:
            self._record(
                candidate,
                status="skipped",
                dry_run=dry_run,
                target_rpid=int(target.rpid) if target is not None else 0,
                target_uname=target.uname if target is not None else "",
                target_text=target.message if target is not None else "",
                reply=body,
                reason=reason,
                error=text_gate.message,
            )
            return None

        if target is None:
            message = body
            root: int | None = None
            parent: int | None = None
            mode = "top_level"
        else:
            message = f"{mention_prefix(target.uname)}{body}"
            root = int(target.root) if int(target.root) > 0 else int(target.rpid)
            parent = int(target.rpid)
            mode = "reply"
        record_target_rpid = int(target.rpid) if target is not None else 0
        record_target_uname = target.uname if target is not None else ""
        record_target_text = target.message if target is not None else ""

        if dry_run:
            record = self._record(
                candidate,
                status="preview",
                dry_run=True,
                target_rpid=record_target_rpid,
                target_uname=record_target_uname,
                target_text=record_target_text,
                reply=message,
                reason=reason,
                error="",
                mode=mode,
            )
            return FishRunResult(
                True,
                "preview",
                "预览完成（没有发出去）。",
                bvid=candidate.bvid,
                target_rpid=record_target_rpid,
                reply=message,
                record=record,
            )

        try:
            posted = await client.post_comment(
                candidate.bvid, message=message, root=root, parent=parent
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Blue Fish publish failed for %s: %s", candidate.bvid, exc)
            record = self._record(
                candidate,
                status="failed",
                dry_run=False,
                target_rpid=record_target_rpid,
                target_uname=record_target_uname,
                target_text=record_target_text,
                reply=message,
                reason=reason,
                error=str(exc),
                mode=mode,
            )
            return FishRunResult(
                False,
                "publish_failed",
                f"发出去的时候失败了：{exc}",
                bvid=candidate.bvid,
                target_rpid=record_target_rpid,
                reply=message,
                record=record,
            )

        # The comment is public at this point. The only durable write is the
        # fish's own ledger line: the owner's profile / recommendation state
        # must stay exactly as it was before the run.
        reply_rpid = _posted_rpid(posted)
        record = self._record(
            candidate,
            status="published",
            dry_run=False,
            target_rpid=record_target_rpid,
            target_uname=record_target_uname,
            target_text=record_target_text,
            reply=message,
            reason=reason,
            error="",
            mode=mode,
            reply_rpid=reply_rpid,
        )
        return FishRunResult(
            True,
            "published",
            f"发出去了：{message}",
            bvid=candidate.bvid,
            target_rpid=record_target_rpid,
            reply=message,
            reply_rpid=reply_rpid,
            record=record,
        )

    # ── helpers ─────────────────────────────────────────────────────────

    async def _account_status(self) -> tuple[FishAccountStatus, str, int]:
        main_mid = 0
        main_cookie = ""
        owner_name = ""
        if self._main_account_provider is not None:
            try:
                main_mid, main_cookie, owner_name = await self._main_account_provider()
            except Exception:  # noqa: BLE001 - isolation degrades to "unknown"
                main_mid, main_cookie, owner_name = 0, "", ""
        status = await self._account.status(main_mid=main_mid, main_cookie=main_cookie)
        return status, owner_name, main_mid

    async def _notify_owner(
        self,
        client: _SupportsComments,
        candidate: FishCandidate,
        *,
        config: FishConfig,
        dry_run: bool,
        owner_name: str,
        owner_mid: int,
    ) -> FishRunResult | None:
        """Tell the owner about one video: comment @, private message, or both.

        Returns ``None`` when there is nothing to send (no nickname, or a
        DM-only channel with no owner mid), letting the pass fall through to
        the reply flow. Both texts are deterministic — no extra model call.

        ``both`` mirrors the subagent-style flow: the public comment is the
        primary act, so a failed comment never falls through to a DM.
        """

        name = str(config.owner_name or owner_name or "").strip()
        if not name:
            logger.info("Blue Fish owner notice skipped: no owner nickname")
            return None
        channel = str(config.notify_channel or "comment")
        want_comment = channel in {"comment", "both"}
        want_dm = channel in {"dm", "both"}
        if want_dm and owner_mid <= 0:
            if not want_comment:
                logger.warning(
                    "Blue Fish owner notice skipped: notify_channel=dm needs the owner mid "
                    "(log in the main account first)"
                )
                return None
            want_dm = False
        notice_line = await self._notice_line(candidate, config=config, owner_name=name)
        public_text = _owner_notice_text(name, candidate, line=notice_line)
        dm_text = _owner_dm_text(candidate, line=notice_line)
        shown_text = public_text if want_comment else dm_text

        if dry_run:
            record = self._record(
                candidate,
                status="preview",
                dry_run=True,
                target_rpid=0,
                target_uname=name,
                target_text="",
                reply=shown_text,
                reason="",
                error="",
                mode="notify",
                delivery=_delivery_label(comment=want_comment, dm=want_dm),
            )
            return FishRunResult(
                True,
                "preview",
                "预览完成（通知没有发出去）。",
                bvid=candidate.bvid,
                reply=shown_text,
                record=record,
            )

        delivered: list[str] = []
        errors: list[str] = []
        reply_rpid = 0
        if want_comment:
            try:
                posted = await client.post_comment(candidate.bvid, message=public_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Blue Fish owner comment failed for %s: %s", candidate.bvid, exc)
                record = self._record(
                    candidate,
                    status="failed",
                    dry_run=False,
                    target_rpid=0,
                    target_uname=name,
                    target_text="",
                    reply=public_text,
                    reason="",
                    error=f"通知评论没发出去：{exc}",
                    mode="notify",
                )
                return FishRunResult(
                    False,
                    "notify_failed",
                    f"通知没发出去：{exc}",
                    bvid=candidate.bvid,
                    reply=public_text,
                    record=record,
                )
            delivered.append("comment")
            reply_rpid = _posted_rpid(posted)

        if want_dm:
            try:
                await client.send_private_message(owner_mid, dm_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Blue Fish owner DM failed for %s: %s", owner_mid, exc)
                errors.append(f"私信没发出去：{exc}")
                if not delivered:
                    record = self._record(
                        candidate,
                        status="failed",
                        dry_run=False,
                        target_rpid=0,
                        target_uname=name,
                        target_text="",
                        reply=dm_text,
                        reason="",
                        error=errors[-1],
                        mode="notify",
                    )
                    return FishRunResult(
                        False,
                        "notify_failed",
                        errors[-1],
                        bvid=candidate.bvid,
                        reply=dm_text,
                        record=record,
                    )
            else:
                delivered.append("dm")

        record = self._record(
            candidate,
            status="notified",
            dry_run=False,
            target_rpid=0,
            target_uname=name,
            target_text="",
            reply=shown_text,
            reason="",
            error="; ".join(errors),
            mode="notify",
            reply_rpid=reply_rpid,
            delivery=_delivery_label(comment="comment" in delivered, dm="dm" in delivered),
        )
        return FishRunResult(
            True,
            "notified",
            "已经通知你了" + ("（部分失败）" if errors else "") + "。",
            bvid=candidate.bvid,
            reply=shown_text,
            reply_rpid=reply_rpid,
            record=record,
        )

    async def _notice_line(
        self,
        candidate: FishCandidate,
        *,
        config: FishConfig,
        owner_name: str,
    ) -> str:
        """Ask the model for the one-liner that tells the owner about a video.

        The notice is the only text the owner reads from the fish, so it gets
        the same persona treatment as a reply instead of a fixed template.
        Returns ``""`` when the model is unavailable or ignores the contract;
        the caller falls back to the deterministic template.
        """

        if self._llm is None:
            return ""
        messages = build_blue_fish_notice_prompt(
            persona_name=config.persona_name,
            persona_prompt=config.persona_prompt,
            reply_rules=config.reply_rules,
            owner_name=owner_name,
            video={
                "title": candidate.title,
                "up_name": candidate.up_name,
                "description": candidate.description[:300],
                "topic": candidate.topic,
            },
        )
        try:
            response = await self._llm.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=256,
                temperature=0.9,
                caller="fish.owner_notice",
                reasoning_effort=None,
                inject_core_memory=False,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to the template
            logger.warning("Blue Fish notice generation failed: %s", exc)
            return ""
        payload = extract_llm_json_object(str(getattr(response, "content", "") or ""))
        if payload is None:
            return ""
        text = " ".join(str(payload.get("notice") or "").split())
        if not text:
            return ""
        return text[:60]

    async def _ask_model(
        self, messages: list[dict[str, str]], *, config: FishConfig
    ) -> tuple[str, int, str, str] | None:
        assert self._llm is not None
        try:
            response = await self._llm.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=1024,
                temperature=0.8,
                caller="fish.comment_reply",
                reasoning_effort=None,
                inject_core_memory=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Blue Fish LLM call failed: %s", exc)
            return None
        raw = str(getattr(response, "content", "") or "").strip()
        payload = extract_llm_json_object(raw)
        if payload is None:
            logger.warning("Blue Fish LLM returned no JSON object")
            return None
        return _parse_decision(payload, max_chars=config.max_chars)

    def _record(
        self,
        candidate: FishCandidate,
        *,
        status: str,
        dry_run: bool,
        target_rpid: int,
        target_uname: str,
        target_text: str,
        reply: str,
        reason: str,
        error: str,
        mode: str = "reply",
        reply_rpid: int = 0,
        delivery: str = "",
    ) -> dict[str, Any]:
        now = self._clock()
        record: dict[str, Any] = {
            "ts": now.isoformat(),
            "day": self._today(now).isoformat(),
            "recommendation_id": int(candidate.recommendation_id),
            "bvid": candidate.bvid,
            "video_title": candidate.title,
            "via": str(getattr(candidate, "origin", "pool") or "pool"),
            "mode": mode,
            "target_rpid": int(target_rpid),
            "target_uname": target_uname,
            "target_text": target_text[:300],
            "reply": reply,
            "reply_rpid": int(reply_rpid),
            "reason": reason,
            "status": status,
            "dry_run": bool(dry_run),
            "error": error,
        }
        if delivery:
            record["delivery"] = delivery
        self._store.append(record)
        self._store.append_report(record, when=now)
        return record

    def _today(self, now: datetime | None = None) -> date:
        return (now or self._clock()).date()


def _posted_rpid(payload: Any) -> int:
    """Reply id from ``/x/v2/reply/add``, tolerating int/str/missing fields."""
    if not isinstance(payload, dict):
        return 0
    for key in ("rpid", "rpid_str"):
        try:
            value = int(str(payload.get(key) or 0).strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _parse_decision(payload: dict[str, Any], *, max_chars: int) -> tuple[str, int, str, str] | None:
    """Validate the model's JSON decision against a strict whitelist."""
    raw_action = str(payload.get("action") or "reply").strip().lower()
    action = raw_action if raw_action in {"reply", "top_level", "skip"} else "reply"
    raw_rpid = payload.get("target_rpid")
    try:
        target_rpid = int(raw_rpid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        target_rpid = 0
    reply = str(payload.get("reply") or "")
    reason = str(payload.get("reason") or "").strip()[:200]
    if action == "reply" and target_rpid <= 0:
        return None
    if action != "skip" and len(reply.strip()) > max_chars * 4:
        # A wildly over-budget reply means the model ignored the contract;
        # let the length gate record it rather than silently trimming.
        logger.warning("Blue Fish reply is far over budget (%d chars)", len(reply))
    return action, target_rpid, reply, reason


def _candidate_passes_quality(
    candidate: FishCandidate, *, config: FishConfig, now: datetime
) -> bool:
    """Apply the optional supply-quality gates (0 = gate off).

    An enabled gate fails when the pool cannot prove the value (0 views or an
    unparseable date) — the same fail-closed rule the rest of the pipeline
    uses for unparseable publication times.
    """

    if config.min_view_count > 0 and int(candidate.view_count) < config.min_view_count:
        return False
    if config.min_publish_age_days > 0:
        published = _parse_published(candidate.published_at)
        if published is None:
            return False
        if now - published < timedelta(days=config.min_publish_age_days):
            return False
    return True


def _parse_published(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _delivery_label(*, comment: bool, dm: bool) -> str:
    """Stable label for the ledger: ``comment`` / ``dm`` / ``comment+dm``."""

    return "+".join(name for name, wanted in (("comment", comment), ("dm", dm)) if wanted)


def _owner_dm_text(candidate: FishCandidate, *, line: str = "") -> str:
    """Private-message body: no @ prefix, and the link is clickable here."""

    title = " ".join(str(candidate.title or "").split())[:60] or "这条视频"
    lines = [" ".join(str(line or "").split()) or f"推荐你看看这条：《{title}》。"]
    bvid = str(candidate.bvid or "").strip()
    if bvid:
        lines.append(f"https://www.bilibili.com/video/{bvid}")
    return "\n".join(lines)


def _owner_notice_due(config: FishConfig, *, view: FishLedgerView) -> bool:
    """Whether this pass should spend its one public act on the owner notice."""

    if not config.notify_owner:
        return False
    limit = int(config.notify_daily_limit)
    if limit <= 0:
        return False
    return view.stats.today_notified < limit


def _owner_notice_text(owner_name: str, candidate: FishCandidate, *, line: str = "") -> str:
    """Build the deterministic owner-notification comment.

    B站 resolves the leading ``@昵称`` into a real mention (the server fills
    the reply's ``content.members`` from the text), which is what turns this
    comment into a notification on the owner's account.
    """

    name = str(owner_name or "").strip()
    title = " ".join(str(candidate.title or "").split())[:40] or "这条视频"
    body = " ".join(str(line or "").split()) or f"推荐你看看这条：《{title}》。我觉得正合你口味。"
    return f"@{name} {body}"


def _candidate_from_plan(plan: FishPlan) -> FishCandidate:
    """Project a discovery-produced plan onto the pool candidate shape."""

    return FishCandidate(
        bvid=plan.bvid,
        title=plan.title,
        up_name=plan.up_name,
        description=plan.description,
        recommendation_id=plan.recommendation_id,
        topic=plan.topic,
        origin="plan",
    )


def _comments_from_plan(plan: FishPlan) -> list[CommentInfo]:
    """Rebuild the comment snapshot the discovery route fetched."""

    from openbiliclaw.bilibili.api import CommentInfo

    return [
        CommentInfo(
            mid=0,
            uname=comment.uname,
            message=comment.message,
            like_count=comment.like_count,
            ctime=comment.ctime,
            rpid=comment.rpid,
            root=comment.root,
            parent=comment.parent,
        )
        for comment in plan.comments
    ]


async def run_fish_loop(
    engine: BlueFishEngine,
    *,
    data_dir: Any,
    interval_seconds: int = _LOOP_INTERVAL_SECONDS,
    discovery_tick: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    """Background loop: discover, let the engine decide, sleep again.

    ``discovery_tick`` runs the comment-thread route through the owning
    discovery engine before the action pass. It is optional so compositions
    without a discovery engine keep the historical behaviour; the tick itself
    is inert unless the lab is enabled and has its own cookie.
    """
    while True:
        try:
            config = load_fish_config(data_dir)
            if config.enabled and not config.dry_run:
                if (
                    discovery_tick is not None
                    and engine.llm_available
                    and not engine.has_pending_plan
                ):
                    try:
                        await discovery_tick()
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - discovery is best-effort
                        logger.warning("Blue Fish discovery tick failed", exc_info=True)
                result = await engine.run_once(trigger="schedule", manual=False)
                if result.status not in {"ok", "preview", "published"}:
                    logger.debug("Blue Fish scheduled pass: %s", result.message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lab loop must never kill the host
            logger.warning("Blue Fish loop tick failed", exc_info=True)
        await asyncio.sleep(max(30, int(interval_seconds)))
