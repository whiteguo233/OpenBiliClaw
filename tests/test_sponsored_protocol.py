"""Tests for the Sponsored Runtime IPC framing and error taxonomy."""

from __future__ import annotations

import asyncio
import io

import pytest

from openbiliclaw.llm.sponsored_errors import (
    SponsoredAuthError,
    SponsoredBalanceLowError,
    SponsoredContractMismatchError,
    SponsoredProtocolError,
    SponsoredQuotaExceededError,
    SponsoredRateLimitedError,
    SponsoredUpstreamError,
    error_from_payload,
    is_fallback_allowed,
)
from openbiliclaw.llm.sponsored_protocol import (
    MAX_FRAME_BYTES,
    decode_frame,
    encode_frame,
    read_frame,
    read_frame_async,
    write_frame,
)


def test_sync_frame_roundtrip() -> None:
    stream = io.BytesIO()
    write_frame(stream, {"type": "hello", "core": "0.3.224"})
    stream.seek(0)
    assert read_frame(stream) == {"type": "hello", "core": "0.3.224"}


def test_encode_frame_rejects_oversized_payload() -> None:
    with pytest.raises(SponsoredProtocolError, match="exceeds"):
        encode_frame({"x": "a" * (MAX_FRAME_BYTES + 1)})


def test_decode_frame_rejects_non_object() -> None:
    with pytest.raises(SponsoredProtocolError, match="JSON object"):
        decode_frame(b"[1, 2, 3]")


async def test_async_frame_roundtrip() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame({"type": "result", "ok": True}))
    reader.feed_eof()
    assert await read_frame_async(reader) == {"type": "result", "ok": True}


async def test_async_read_rejects_truncated_frame() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x00\x00\x00\x10{}")
    reader.feed_eof()
    with pytest.raises(SponsoredProtocolError, match="truncated|closed"):
        await read_frame_async(reader)


@pytest.mark.parametrize(
    ("code", "expected_type"),
    [
        ("CONTRACT_MISMATCH", SponsoredContractMismatchError),
        ("QUOTA_EXCEEDED", SponsoredQuotaExceededError),
        ("RATE_LIMITED", SponsoredRateLimitedError),
        ("AUTH_FAILED", SponsoredAuthError),
        ("BALANCE_LOW", SponsoredBalanceLowError),
    ],
)
def test_error_payload_maps_to_typed_error(code: str, expected_type: type[Exception]) -> None:
    error = error_from_payload({"code": code, "message": "boom"}, task_id="soul.consolidation")
    assert isinstance(error, expected_type)
    assert error.task_id == "soul.consolidation"
    assert is_fallback_allowed(error) is (code != "CONTRACT_MISMATCH")


def test_unknown_error_code_maps_to_upstream_error() -> None:
    error = error_from_payload({"code": "SOMETHING_NEW", "message": "who knows"})
    assert isinstance(error, SponsoredUpstreamError)
    assert error.actual_code == "SOMETHING_NEW"
    assert is_fallback_allowed(error)
