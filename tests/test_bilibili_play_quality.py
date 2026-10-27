"""Playback quality is selected before the preferred codec within that quality."""

from unittest.mock import AsyncMock

import pytest

from openbiliclaw.bilibili.api import BilibiliAPIClient


@pytest.mark.parametrize(
    ("qn", "preferred_codec", "expected_qn", "expected_codec"),
    [
        (32, "avc", 32, "avc1.64001F"),
        (32, "hev", 32, "hev1.1.6.L120.90"),
        (64, "avc", 64, "hev1.1.6.L120.90"),
        (120, "avc", 80, "avc1.640032"),
    ],
)
async def test_play_info_honors_quality_before_codec(
    monkeypatch: pytest.MonkeyPatch,
    qn: int,
    preferred_codec: str,
    expected_qn: int,
    expected_codec: str,
) -> None:
    client = BilibiliAPIClient(cookie="test-cookie")
    # Bilibili returns all qualities, ordered high to low, even for qn=32.
    # Deliberately place HEVC before AVC at 480P to verify both preferences.
    streams = [
        {"id": 80, "codecs": "avc1.640032", "height": 1080},
        {"id": 64, "codecs": "hev1.1.6.L120.90", "height": 720},
        {"id": 32, "codecs": "hev1.1.6.L120.90", "height": 480},
        {"id": 32, "codecs": "avc1.64001F", "height": 480},
    ]
    request = AsyncMock(
        side_effect=[
            {"dash": {"video": streams}},
            [{"cid": 435050022, "page": 2, "part": "second"}],
            {},
        ]
    )
    monkeypatch.setattr(client, "_get_json", request)
    monkeypatch.setattr(client, "_get_wbi_keys", AsyncMock(return_value=("a" * 32, "b" * 32)))
    try:
        result = await client.get_play_info(
            "BV1Eb411u7Fw", cid=435050022, qn=qn, preferred_codec=preferred_codec
        )
    finally:
        await client.close()
    assert result["video"]["qn"] == expected_qn
    assert result["video"]["codec"] == expected_codec
    assert int(request.call_args_list[0].kwargs["params"]["qn"]) == qn
