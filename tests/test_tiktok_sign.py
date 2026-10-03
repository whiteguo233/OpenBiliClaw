"""Behavior tests for the vendored TikTok web signer.

These pin the *contract* the web backend relies on (output structure,
parameter ordering, no re-encoding, msToken passthrough). Algorithm
correctness is upstream's responsibility (Evil0ctal/Douyin_TikTok_Download_API,
verified byte-for-byte against the genuine SDK there).
"""

from __future__ import annotations

from openbiliclaw.sources import tiktok_sign

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def test_sign_appends_four_signature_parameters_in_order() -> None:
    query, parameters = tiktok_sign.sign([("aid", "1988"), ("keyword", "cat")], _UA)

    assert set(parameters) == {"X-Dynosaur", "msToken", "X-Bogus", "X-Gnarly"}
    assert parameters["X-Bogus"] == "1"
    # Business query first, signature parameters appended after it, fixed order.
    assert query.startswith("aid=1988&keyword=cat&X-Dynosaur=")
    assert "&msToken=" in query
    assert "&X-Bogus=1&X-Gnarly=" in query
    # The returned query ends with the gnarly seal.
    assert query.rsplit("&X-Gnarly=", 1)[-1] == parameters["X-Gnarly"]


def test_sign_does_not_reencode_business_query() -> None:
    # A pre-encoded CJK value must pass through byte for byte; re-encoding
    # it would break the seal the server verifies.
    query, _ = tiktok_sign.sign([("keyword", "%E7%8C%AB")], _UA)
    assert query.startswith("keyword=%E7%8C%AB&")


def test_sign_ms_token_empty_and_real_passthrough() -> None:
    empty_query, empty_params = tiktok_sign.sign([("aid", "1988")], _UA, ms_token="")
    assert empty_params["msToken"] == ""
    assert "&msToken=&" in empty_query

    token = "t" * 128
    real_query, real_params = tiktok_sign.sign([("aid", "1988")], _UA, ms_token=token)
    assert real_params["msToken"] == token
    assert f"&msToken={token}&" in real_query


def test_sign_is_per_call_randomized_but_structurally_stable() -> None:
    first, _ = tiktok_sign.sign([("aid", "1988")], _UA)
    second, _ = tiktok_sign.sign([("aid", "1988")], _UA)
    # Per-call keys make byte equality impossible; structure stays.
    assert first != second
    assert first.count("&") == second.count("&")


def test_pick_ms_token_reads_only_real_cookie() -> None:
    assert tiktok_sign.pick_ms_token({"msToken": "abc"}) == "abc"
    assert tiktok_sign.pick_ms_token({}) == ""
    assert tiktok_sign.pick_ms_token(None) == ""
