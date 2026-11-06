"""Bounded draft connection checks through the complete native Jev evaluator."""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from openbiliclaw.config import build_jev_evaluation_config, update_decision_config
from openbiliclaw.decision.context_budget import resolve_jev_context
from openbiliclaw.decision.registry import build_decision_provider
from openbiliclaw.discovery.eval_payload import InputBudgetFailure, InvalidEvaluationEvidence
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.soul.profile_views import build_profile_catalog

from .models import ConfigServiceProbeResponse

if TYPE_CHECKING:
    from openbiliclaw.config import Config
    from openbiliclaw.decision.base import DecisionProvider
    from openbiliclaw.decision.models import DecisionEvidence, DecisionRequest


class _ProbeProvider:
    """Retain only the typed verdict so a failed probe keeps its safe cause."""

    def __init__(self, provider: DecisionProvider) -> None:
        self.provider = provider
        self.evidence: DecisionEvidence | None = None

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        self.evidence = await self.provider.evaluate(request)
        return self.evidence

    async def aclose(self) -> None:
        await self.provider.aclose()


async def probe_jev_config(config: Config, update: dict[str, object]) -> ConfigServiceProbeResponse:
    """Check one synthetic candidate, with no persistence or real profile loading.

    A click permits one HTTP attempt. The normal selected-model budget, complete
    ten-question contract and native evidence validation still apply.
    """
    started = time.perf_counter()
    cfg = deepcopy(config)

    def result(ok: bool, message: str, error: str = "") -> ConfigServiceProbeResponse:
        return ConfigServiceProbeResponse(
            ok=ok,
            kind="jev",
            provider="jev",
            model=cfg.decision.providers.jev.model,
            message=message,
            error=error,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    try:
        if "decision" in update:
            cfg.decision = update_decision_config(cfg.decision, update["decision"])
        discovery = update.get("discovery")
        if isinstance(discovery, dict) and "jev_evaluation" in discovery:
            changes = discovery["jev_evaluation"]
            if not isinstance(changes, dict):
                raise ValueError("invalid evaluator settings")
            cfg.discovery.jev_evaluation = build_jev_evaluation_config(
                {**asdict(cfg.discovery.jev_evaluation), **changes}
            )
        settings = cfg.decision.providers.jev
        budget = resolve_jev_context(settings.model, settings.context_limits)
        settings.max_attempts = 1
        # User-triggered diagnostics have a 15s transport / 20s total ceiling;
        # these are resource bounds, not recommendation-quality thresholds.
        settings.timeout_seconds = min(settings.timeout_seconds, 15.0)
        evaluation = replace(cfg.discovery.jev_evaluation, max_calls=1, budget_seconds=15.0)
        budget.with_byte_guards(
            max_request_bytes=evaluation.max_request_bytes,
            max_state_question_bytes=evaluation.max_state_question_bytes,
        )
        native = build_decision_provider(cfg.decision)
        if native is None:
            return result(False, "请先启用 Jev 服务连接。", "disabled")
    except ValueError:
        return result(False, "请检查 Jev 地址、Token、固定模型和上下文容量。", "invalid_config")

    provider = _ProbeProvider(native)
    try:
        evaluator = JevContentEvaluator(
            provider, model=settings.model, config=evaluation, context_budget=budget
        )
        profile = SoulProfile(
            preferences=PreferenceLayer(interests=[InterestTag("算法与数据结构", "编程", 0.9)])
        )
        async with asyncio.timeout(20):
            rows = await evaluator.evaluate(
                profile_summary=build_profile_catalog(profile),
                content_items=[
                    {
                        "content_id": "jev-connection-probe",
                        "source_platform": "probe",
                        "content_type": "article",
                        "title": "算法与数据结构原理",
                        "description": "合成连接测试：讲解排序算法的基本概念与适用范围。",
                    }
                ],
            )
        if rows and isinstance(rows[0], InputBudgetFailure):
            return result(
                False,
                "上下文容量不足，未发送请求；请检查所选模型的容量设置。",
                "input_budget_exhausted",
            )
        if rows and isinstance(rows[0], dict):
            return result(True, "连接及完整评估测试通过（1 条测试内容、10 题）。测试不会保存配置。")
        if rows and isinstance(rows[0], InvalidEvaluationEvidence):
            return result(
                False,
                "服务已返回，但时效答案矛盾或缺少依据；未重复请求。",
                "invalid_evaluation_evidence",
            )
        evidence = provider.evidence
        code = evidence.reason_code if evidence else "invalid_evaluation"
        if evidence and evidence.http_status in {401, 403}:
            return result(False, "服务拒绝认证，请检查 Token 和模型访问权限。", code)
        status = f"（HTTP {evidence.http_status}）" if evidence and evidence.http_status else ""
        return result(False, f"连接或评估返回校验失败{status}，请检查服务地址及模型协议。", code)
    except TimeoutError:
        return result(False, "Jev 测试超时，请检查连接或调整请求超时设置。", "timeout")
    except ValueError:
        return result(False, "请检查固定模型和上下文容量。", "invalid_config")
    finally:
        await provider.aclose()
