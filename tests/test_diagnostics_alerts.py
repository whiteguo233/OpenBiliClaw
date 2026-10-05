"""异常报警（diagnostics alerts）单元与接口测试。

覆盖：
- DiagnosticsAlertBuffer 的记录 / 合并 / 快照 / 发布行为
- LLMRegistry 失败路径（限流、鉴权失败、全部实例失败）自动记录
- EmbeddingService 失败路径（单次失败、熔断触发）自动记录
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.diagnostics_alerts import (
    DiagnosticsAlertBuffer,
    get_diagnostics_alert_buffer,
    reset_diagnostics_alert_buffer,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _isolated_buffer() -> Iterator[DiagnosticsAlertBuffer]:
    """每个用例拿到干净的全局 buffer，结束后恢复默认单例。"""
    fresh = DiagnosticsAlertBuffer()
    reset_diagnostics_alert_buffer(fresh)
    try:
        yield fresh
    finally:
        reset_diagnostics_alert_buffer()


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        return True

    async def drain(self) -> None:
        """让 fire-and-forget 任务有机会执行。"""
        for _ in range(4):
            await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Buffer 单元行为
# ---------------------------------------------------------------------------


def test_record_appends_and_snapshot_returns_newest_first() -> None:
    buffer = DiagnosticsAlertBuffer()
    buffer.record(category="llm", code="rate_limited", message="HTTP 429", source="gw-a")
    buffer.record(
        category="embedding",
        code="provider_error",
        message="empty vector",
        source="bge-m3",
        severity="error",
    )

    snapshot = buffer.snapshot()
    assert [row["code"] for row in snapshot["alerts"]] == ["provider_error", "rate_limited"]
    assert snapshot["summary"] == {"total": 2, "errors": 1, "warnings": 1}
    assert snapshot["alerts"][0]["severity"] == "error"


def test_identical_alerts_coalesce_within_window() -> None:
    buffer = DiagnosticsAlertBuffer(coalesce_window_seconds=60.0)
    for _ in range(5):
        payload = buffer.record(
            category="llm",
            code="rate_limited",
            message="HTTP 429",
            source="gw-a",
        )

    assert payload is not None
    assert payload["count"] == 5
    snapshot = buffer.snapshot()
    assert len(snapshot["alerts"]) == 1
    assert snapshot["alerts"][0]["count"] == 5
    # 合并行保持最早出现时间。
    assert snapshot["alerts"][0]["first_seen"] == pytest.approx(payload["first_seen"])


def test_alternating_alert_pairs_merge_by_identity_not_tail_only() -> None:
    """每轮请求写入交替的两条告警时，各自仍应合并而不是无限追加。

    真实场景：LLM 链每次失败先记 per-provider auth_failed，再记
    all_providers_failed；只看 entries[-1] 的合并会让两类交替互相打断，
    每次重试都新增两行（真实环境 E2E 发现）。
    """
    buffer = DiagnosticsAlertBuffer(coalesce_window_seconds=60.0)
    for _ in range(3):
        buffer.record(
            category="llm",
            code="auth_failed",
            message="HTTP 401: Forbidden",
            source="gw-a",
        )
        buffer.record(
            category="llm",
            code="all_providers_failed",
            message="所有 LLM 实例均请求失败（gw-a）",
            source="gw-a",
            severity="error",
        )

    snapshot = buffer.snapshot()
    assert snapshot["summary"] == {"total": 2, "errors": 1, "warnings": 1}
    codes = {row["code"]: row for row in snapshot["alerts"]}
    assert codes["auth_failed"]["count"] == 3
    assert codes["all_providers_failed"]["count"] == 3


def test_different_source_does_not_coalesce() -> None:
    buffer = DiagnosticsAlertBuffer()
    buffer.record(category="llm", code="rate_limited", message="429", source="gw-a")
    buffer.record(category="llm", code="rate_limited", message="429", source="gw-b")

    snapshot = buffer.snapshot()
    assert len(snapshot["alerts"]) == 2
    assert {row["source"] for row in snapshot["alerts"]} == {"gw-a", "gw-b"}


def test_severity_escalates_to_error_on_merge() -> None:
    buffer = DiagnosticsAlertBuffer()
    buffer.record(category="llm", code="timeout", message="slow", source="gw")
    buffer.record(
        category="llm",
        code="timeout",
        message="slower",
        source="gw",
        severity="error",
    )

    row = buffer.snapshot()["alerts"][0]
    assert row["severity"] == "error"
    assert row["message"] == "slower"


def test_ring_buffer_keeps_bounded_entries() -> None:
    buffer = DiagnosticsAlertBuffer(max_entries=3, coalesce_window_seconds=0.0)
    for index in range(6):
        buffer.record(category="llm", code=f"code-{index}", message=str(index), source="gw")

    snapshot = buffer.snapshot()
    assert len(snapshot["alerts"]) == 3
    # 最新的留在缓冲里，最旧的被淘汰。
    assert snapshot["alerts"][0]["code"] == "code-5"


def test_since_id_filters_older_rows() -> None:
    buffer = DiagnosticsAlertBuffer(coalesce_window_seconds=0.0)
    first = buffer.record(category="llm", code="a", message="", source="gw")
    second = buffer.record(category="llm", code="b", message="", source="gw")
    assert first is not None and second is not None
    assert second["id"] > first["id"]

    snapshot = buffer.snapshot(since_id=first["id"])
    assert [row["id"] for row in snapshot["alerts"]] == [second["id"]]


@pytest.mark.asyncio
async def test_publisher_receives_live_event() -> None:
    publisher = _RecordingPublisher()
    buffer = DiagnosticsAlertBuffer()
    buffer.set_publisher(publisher.publish)

    payload = buffer.record(category="llm", code="rate_limited", message="429", source="gw")
    await publisher.drain()

    assert payload is not None
    assert len(publisher.events) == 1
    assert publisher.events[0]["type"] == "diagnostics.alert"
    assert publisher.events[0]["source"] == "gw"

    # 合并后的重复告警同样推送最新计数。
    buffer.record(category="llm", code="rate_limited", message="429", source="gw")
    await publisher.drain()
    assert len(publisher.events) == 2
    assert publisher.events[1]["count"] == 2


@pytest.mark.asyncio
async def test_record_never_raises_even_with_broken_publisher() -> None:
    class _BrokenPublisher:
        async def publish(self, event: dict[str, Any]) -> bool:  # noqa: ARG002
            raise RuntimeError("hub down")

    buffer = DiagnosticsAlertBuffer()
    buffer.set_publisher(_BrokenPublisher().publish)

    payload = buffer.record(category="llm", code="timeout", message="", source="gw")
    await asyncio.sleep(0)
    assert payload is not None
    assert payload["category"] == "llm"


def test_singleton_reset_roundtrip() -> None:
    buffer = get_diagnostics_alert_buffer()
    buffer.record(category="llm", code="timeout", message="", source="gw")
    assert buffer.snapshot()["summary"]["total"] == 1

    reset_diagnostics_alert_buffer()
    assert get_diagnostics_alert_buffer().snapshot()["summary"]["total"] == 0


# ---------------------------------------------------------------------------
# LLMRegistry 钩子
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """最小 provider 替身：按脚本抛错或返回固定响应。"""

    def __init__(self, name: str, error: Exception | None = None) -> None:
        self._name = name
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    async def complete(
        self,
        messages: list[dict[str, str]],  # noqa: ARG002
        *,
        temperature: float = 0.7,  # noqa: ARG002
        max_tokens: int = 4096,  # noqa: ARG002
        json_mode: bool = False,  # noqa: ARG002
        reasoning_effort: str | None = None,  # noqa: ARG002
        model: str | None = None,  # noqa: ARG002
    ) -> Any:
        if self._error is not None:
            raise self._error
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content="ok", provider=self._name, model="fake")


@pytest.mark.asyncio
async def test_rate_limit_failure_records_llm_alert() -> None:
    from openbiliclaw.llm.base import LLMRateLimitError, LLMRegistry

    registry = LLMRegistry()
    registry.register(
        _ScriptedProvider("gw-a", LLMRateLimitError("HTTP 429: quota exceeded")),
        name="gw-a",
        default=True,
    )
    registry.register(_ScriptedProvider("gw-b"), name="gw-b")
    registry.configure_chain(["gw-a", "gw-b"])

    response = await registry.complete([{"role": "user", "content": "hi"}])

    # 主实例被限流后回退到备用实例成功。
    assert response.instance_id == "gw-b"
    rows = get_diagnostics_alert_buffer().snapshot()["alerts"]
    assert len(rows) == 1
    row = rows[0]
    assert row["category"] == "llm"
    assert row["code"] == "rate_limited"
    assert row["source"] == "gw-a"
    assert "429" in row["message"]
    assert row["severity"] == "warning"


@pytest.mark.asyncio
async def test_all_providers_failed_records_error_alert() -> None:
    from openbiliclaw.llm.base import LLMAuthError, LLMFallbackError, LLMRegistry

    registry = LLMRegistry()
    registry.register(
        _ScriptedProvider("gw-a", LLMAuthError("401 unauthorized")),
        name="gw-a",
        default=True,
    )
    registry.register(
        _ScriptedProvider("gw-b", LLMAuthError("401 unauthorized")),
        name="gw-b",
    )
    registry.configure_chain(["gw-a", "gw-b"])

    with pytest.raises(LLMFallbackError):
        await registry.complete([{"role": "user", "content": "hi"}])

    rows = get_diagnostics_alert_buffer().snapshot()["alerts"]
    codes = {row["code"] for row in rows}
    assert "auth_failed" in codes
    assert "all_providers_failed" in codes
    terminal = next(row for row in rows if row["code"] == "all_providers_failed")
    assert terminal["severity"] == "error"


# ---------------------------------------------------------------------------
# EmbeddingService 钩子
# ---------------------------------------------------------------------------


class _FailingEmbedProvider:
    name = "fake-embed"

    def __init__(self, *, vector: list[float] | None = None, error: Exception | None = None):
        self._vector = vector if vector is not None else [0.1, 0.2]
        self._error = error
        self.calls = 0

    async def embed(self, text: str, *, model: str = "") -> list[float]:  # noqa: ARG002
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._vector


@pytest.mark.asyncio
async def test_embedding_single_failure_records_warning_alert() -> None:
    from openbiliclaw.llm.embedding import EmbeddingService

    provider = _FailingEmbedProvider(error=RuntimeError("ConnectError"))
    service = EmbeddingService(
        provider,  # type: ignore[arg-type]
        model="bge-m3",
        breaker_failure_threshold=3,
    )

    assert await service.embed("one") == []

    rows = get_diagnostics_alert_buffer().snapshot()["alerts"]
    assert len(rows) == 1
    row = rows[0]
    assert row["category"] == "embedding"
    assert row["code"] == "provider_error"
    assert row["severity"] == "warning"
    assert "bge-m3" in row["source"]


@pytest.mark.asyncio
async def test_embedding_breaker_trip_records_error_alert() -> None:
    from openbiliclaw.llm.embedding import EmbeddingService

    provider = _FailingEmbedProvider(vector=[])
    service = EmbeddingService(
        provider,  # type: ignore[arg-type]
        model="bge-m3",
        breaker_failure_threshold=2,
    )

    assert await service.embed("one") == []
    assert await service.embed("two") == []
    assert await service.embed("three") == []  # 熔断生效，不再触达 provider。
    assert provider.calls == 2

    rows = get_diagnostics_alert_buffer().snapshot()["alerts"]
    assert rows
    # snapshot 以最新在前排序；熔断告警是最后记录的。
    terminal = rows[0]
    assert terminal["category"] == "embedding"
    assert terminal["code"] == "breaker_open"
    assert terminal["severity"] == "error"
