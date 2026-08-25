"""The overseas-egress advisory has exactly one author: the backend.

Two independent failures in this repo's history motivate these assertions:
the init admission check was reimplemented on three surfaces and all three
went blind at once, and the Bangumi token copy was pasted onto five screens
and drifted. An "which sources need a proxy" list is the same shape of hazard
— the next overseas platform would be added to the backend and silently
missed by the desktop page and the popup.

So: the platform classification AND both wordings live only in
``openbiliclaw.sources.platforms``; every surface renders
``SourceStatusItem.network_hint`` verbatim and is forbidden from naming a
platform anywhere near that rendering.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openbiliclaw.sources.platforms import (
    CANONICAL_SOURCE_FAMILIES,
    OVERSEAS_DIRECT_MODE_ERROR_SUFFIX,
    OVERSEAS_EGRESS_PLATFORMS,
    SOURCE_FAMILY_RULES,
    overseas_network_hint,
    requires_overseas_network,
)

ROOT = Path(__file__).resolve().parents[1]

# Every settings surface that renders a per-source block. Adding a surface here
# is cheaper than discovering it drifted.
SETTINGS_SURFACES = {
    "desktop js": ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js",
    "desktop html": ROOT / "src/openbiliclaw/web/desktop/index.html",
    "popup js": ROOT / "extension/popup/popup.js",
    "popup html": ROOT / "extension/popup/popup.html",
}

# Tokens that mark the hint machinery in a frontend file.
HINT_MARKERS = ("network_hint", "networkHint", "source-network-hint", "NetworkHint")

# A fragment unique to each wording. Needed because the copy is assembled from
# adjacent string literals and so never appears joined in the source, and
# useful because it also catches a partial paste that a whole-string check
# would miss. Each is asserted to still be part of the shipped copy.
HINT_FRAGMENTS = (
    "会绕开系统代理直接请求",  # app-routed wording
    "[network].mode 当前为 direct",  # exception-message suffix
)


def _hint_strings() -> tuple[str, ...]:
    """Rendered wording, taken from the backend rather than restated."""
    return (overseas_network_hint("bangumi", network_mode="direct"),)


def test_overseas_platform_list_is_the_verified_one() -> None:
    """Pins the classification a human verified against the transport code.

    bangumi / youtube / twitter / reddit / instagram sit outside the GFW;
    the CN-direct families must never join them, because pitfall rule 1
    forces those to ignore proxies entirely.
    """
    assert {
        "bangumi",
        "youtube",
        "twitter",
        "reddit",
        "instagram",
    } == OVERSEAS_EGRESS_PLATFORMS
    for family in ("bilibili", "xiaohongshu", "douyin", "zhihu"):
        assert not requires_overseas_network(family), f"{family} is CN-direct"
    # Aliases resolve too — a caller passing "bgm" / "x" / "yt" / "ig" must not slip
    # through as an unknown platform and silently lose the advisory.
    for alias in ("bgm", "x", "yt", "rd", "ig"):
        assert requires_overseas_network(alias), alias


def test_overseas_hints_distinguish_backend_and_browser_transports() -> None:
    """Advice that cannot fix the failure is worse than none (rule 7).

    ``[network].mode`` governs the overseas backend transports: HTTP clients,
    twitter-cli's curl session, and rdt/OpenCLI environments. Instagram is a
    browser-extension transport, so it must receive the external-network hint
    instead of being told that changing the backend setting will fix it.
    """
    routed = {rule.family for rule in SOURCE_FAMILY_RULES if rule.routed_by_network_mode}
    assert routed == {"bangumi", "youtube", "twitter", "reddit"}
    assert OVERSEAS_EGRESS_PLATFORMS - routed == {"instagram"}

    for family in routed:
        hint = overseas_network_hint(family, network_mode="direct")
        assert "改成「跟随系统代理」或「自定义代理」" in hint, family

    instagram_hint = overseas_network_hint("instagram", network_mode="direct")
    assert "不经过 OpenBiliClaw 的「海外网络模式」" in instagram_hint
    assert "系统代理本身能访问该站点" in instagram_hint


@pytest.mark.parametrize("mode", ["system", "custom", "", "DIRECT ", "unknown"])
def test_hint_is_silent_unless_the_user_chose_direct(mode: str) -> None:
    """Only ``direct`` is a broken posture; anything else must not nag.

    ``"DIRECT "`` is included because the mode is normalized before comparison,
    so a whitespace/case variant must still warn rather than fall silent.
    """
    expected_silent = mode.strip().lower() != "direct"
    for family in CANONICAL_SOURCE_FAMILIES:
        hint = overseas_network_hint(family, network_mode=mode)
        if expected_silent or family not in OVERSEAS_EGRESS_PLATFORMS:
            assert hint == "", f"{family} @ {mode!r}"
        else:
            assert hint, f"{family} @ {mode!r}"


def test_cn_direct_sources_never_get_an_advisory() -> None:
    """A proxy suggestion on a CN source would be an actively harmful hint."""
    for family in ("bilibili", "xiaohongshu", "douyin", "zhihu"):
        for mode in ("direct", "system", "custom"):
            assert overseas_network_hint(family, network_mode=mode) == ""


def test_no_surface_ships_its_own_copy_of_the_wording() -> None:
    """The copy is backend-owned; a pasted duplicate is how it drifts."""
    for name, path in SETTINGS_SURFACES.items():
        source = path.read_text(encoding="utf-8")
        for hint in (*_hint_strings(), OVERSEAS_DIRECT_MODE_ERROR_SUFFIX):
            assert hint not in source, f"{name} pasted the backend's hint copy"
        # Also catch a partial paste / hand-edited variant of either wording.
        for fragment in HINT_FRAGMENTS:
            assert fragment not in source, f"{name} restated the hint ({fragment})"


def test_the_wording_is_authored_in_exactly_one_module() -> None:
    """Grep the whole package: two authors means the drift already started.

    A reworded hint fails loudly on the first assertion instead of quietly
    matching nothing and passing for the wrong reason.
    """
    copies = (*_hint_strings(), OVERSEAS_DIRECT_MODE_ERROR_SUFFIX)
    for fragment, copy in zip(HINT_FRAGMENTS, copies, strict=True):
        assert fragment in copy, f"stale test fragment: {fragment!r}"
        owners = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src/openbiliclaw").rglob("*.py")
            if fragment in path.read_text(encoding="utf-8")
        )
        assert owners == ["src/openbiliclaw/sources/platforms.py"], (fragment, owners)


def test_frontends_render_the_hint_without_knowing_any_platform() -> None:
    """The load-bearing assertion: no ``key === "bangumi"`` style branching.

    Any line that touches the hint machinery must be platform-agnostic, so
    classifying a new platform stays a one-line backend change.
    """
    families = tuple(CANONICAL_SOURCE_FAMILIES) + ("bgm", "bgm.tv", "x.com")
    for name, path in SETTINGS_SURFACES.items():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not any(marker in line for marker in HINT_MARKERS):
                continue
            lowered = line.lower()
            for family in families:
                assert family not in lowered, f"{name}:{lineno} names {family!r}: {line.strip()}"


def test_both_frontends_actually_render_the_backend_field() -> None:
    """Guard the other direction: the wiring must exist, not just be clean."""
    for name in ("desktop js", "popup js"):
        source = SETTINGS_SURFACES[name].read_text(encoding="utf-8")
        assert "applySourceNetworkHint" in source, name
        # Rendered from the backend field. The call once read
        # ``(row, item.network_hint, …)``; it now also gates on whether a
        # credential is present, so a source with nothing configured does not
        # get told which network its (absent) credential needs. Match the
        # backend field reaching the call, not one particular argument shape —
        # pinning the shape makes any refinement of the gate look like a
        # regression.
        assert re.search(r"applySourceNetworkHint\(\s*row,[^)]*item\.network_hint", source), name
        # Rendered as text, never as HTML — the copy reaches the DOM verbatim.
        assert "node.textContent = text;" in source, name
        assert "node.innerHTML" not in source, name

    # Each surface needs its own style hook or the advisory renders as a plain
    # muted paragraph indistinguishable from the status line beneath it.
    desktop_css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(
        encoding="utf-8"
    )
    assert ".source-status-row .source-network-hint" in desktop_css
    assert ".source-network-hint {" in SETTINGS_SURFACES["popup html"].read_text(encoding="utf-8")


def test_the_network_mode_note_no_longer_enumerates_platforms() -> None:
    """The static note under the mode selector used to list only YouTube.

    Bangumi was equally proxy-dependent and unmentioned, which is how a user
    could read the whole network section and still not know their source was
    unreachable. It now defers to the per-source advisory instead of carrying
    a second list that would drift the same way.
    """
    for name in ("desktop html", "popup html"):
        source = SETTINGS_SURFACES[name].read_text(encoding="utf-8")
        marker = "仅作用于海外服务"
        assert marker in source, name
        note = source[source.index(marker) : source.index(marker) + 200]
        assert "YouTube" not in note, f"{name} still enumerates platforms"
        assert "需要海外出网的内容来源" in note, name
