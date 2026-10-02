"""Length-prefixed JSON framing for the Sponsored Runtime IPC channel.

Wire format (v1): 4-byte big-endian body length followed by one UTF-8 JSON
object. The provider and the mock runtime share these helpers so the framing
is tested once.

stdout carries frames only. Runtime diagnostics go to stderr.
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import TYPE_CHECKING, Any, BinaryIO

from .sponsored_errors import SponsoredProtocolError

if TYPE_CHECKING:
    from collections.abc import Mapping

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 16 * 1024 * 1024
_LENGTH_STRUCT = struct.Struct(">I")


def encode_frame(payload: Mapping[str, Any]) -> bytes:
    """Serialize one frame, rejecting oversized and non-JSON payloads."""

    try:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SponsoredProtocolError(f"frame is not JSON-serializable: {exc}") from exc
    if len(body) > MAX_FRAME_BYTES:
        raise SponsoredProtocolError(f"frame exceeds {MAX_FRAME_BYTES} bytes ({len(body)})")
    return _LENGTH_STRUCT.pack(len(body)) + body


def decode_frame(body: bytes) -> dict[str, Any]:
    """Decode one frame body into a JSON object."""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SponsoredProtocolError(f"frame body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SponsoredProtocolError("frame body must be a JSON object")
    return payload


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("runtime closed the IPC stream")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BinaryIO) -> dict[str, Any]:
    """Read one framed JSON object from a blocking binary stream."""

    header = _read_exact(stream, _LENGTH_STRUCT.size)
    (length,) = _LENGTH_STRUCT.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise SponsoredProtocolError(f"frame length {length} exceeds limit")
    return decode_frame(_read_exact(stream, length))


def write_frame(stream: BinaryIO, payload: Mapping[str, Any]) -> None:
    """Write one framed JSON object to a blocking binary stream."""

    stream.write(encode_frame(payload))
    stream.flush()


async def read_frame_async(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Async variant of :func:`read_frame` for provider-side pipes."""

    try:
        header = await reader.readexactly(_LENGTH_STRUCT.size)
    except asyncio.IncompleteReadError as exc:
        raise SponsoredProtocolError("runtime closed the IPC stream") from exc
    (length,) = _LENGTH_STRUCT.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise SponsoredProtocolError(f"frame length {length} exceeds limit")
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise SponsoredProtocolError("runtime sent a truncated frame") from exc
    return decode_frame(body)


async def write_frame_async(
    writer: asyncio.StreamWriter,
    payload: Mapping[str, Any],
) -> None:
    """Async variant of :func:`write_frame` for provider-side pipes."""

    writer.write(encode_frame(payload))
    await writer.drain()
