"""Canonical source-platform families shared across discovery and storage."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

PLATFORM_BILIBILI = "bilibili"
PLATFORM_XIAOHONGSHU = "xiaohongshu"
PLATFORM_DOUYIN = "douyin"
PLATFORM_YOUTUBE = "youtube"
PLATFORM_TWITTER = "twitter"
PLATFORM_ZHIHU = "zhihu"
PLATFORM_REDDIT = "reddit"
PLATFORM_BANGUMI = "bangumi"
PLATFORM_LINUXDO = "linuxdo"
PLATFORM_V2EX = "v2ex"
PLATFORM_WEIBO = "weibo"
PLATFORM_INSTAGRAM = "instagram"


@dataclass(frozen=True)
class SourceFamilyRule:
    """Aliases and discovery signals that identify one canonical platform."""

    family: str
    platform_aliases: frozenset[str]
    source_keys: frozenset[str] = frozenset()
    source_prefixes: tuple[str, ...] = ()
    url_hosts: tuple[str, ...] = ()
    # ── Overseas-egress classification (see the block comment below) ──
    requires_overseas_network: bool = False
    routed_by_network_mode: bool = False


# ── Overseas-egress classification ─────────────────────────────────────────
#
# Two INDEPENDENT facts per family. Both were verified against this repo's own
# transport code on 2026-07-19; neither may be re-derived in a frontend.
#
# ``requires_overseas_network`` — the platform's API / CDN lives outside the
#   GFW, so a mainland-China install needs some form of overseas egress:
#     • bangumi — api.bgm.tv + lain.bgm.tv are Cloudflare-fronted and resolve
#       overseas. A 2026-07-18 curl had direct = HTTP 000 (timeout) while the
#       same request through a proxy returned HTTP 200 in ~0.5s; see the
#       ``_DIRECT_FETCH_HOST_SUFFIXES`` comment in runtime/image_cache.py.
#     • youtube — youtube.com / ytimg.com / ggpht.com.
#     • twitter — x.com.       • reddit — reddit.com.
#   The CN-direct families are the exact opposite and MUST stay False:
#   pitfall rule 1 forces ``trust_env=False`` on bilibili / douyin (and the
#   xiaohongshu / zhihu extension paths run inside the user's own browser),
#   with tests/test_network_proxy_isolation.py pinning that no proxy leaks in.
#
# ``routed_by_network_mode`` — whether ``[network].mode`` actually governs
#   that source's requests, i.e. whether changing the setting changes anything.
#   True where the fetch goes through a client or CLI environment built from
#   ``openbiliclaw.network``:
#     • bangumi — sources/bangumi_client.py passes ``outbound_httpx_kwargs()``.
#     • youtube — youtube/client.py passes ``outbound_httpx_kwargs()`` /
#       ``outbound_requests_proxies()`` / ``outbound_ytdlp_proxy()``.
#     • twitter — sources/x_client.py resolves the route into twitter-cli's
#       dedicated ``TWITTER_PROXY`` session setup.
#     • reddit — sources/reddit_tasks.py passes ``outbound_cli_environment()``
#       to rdt/OpenCLI subprocesses and the bundled in-process rdt runner.
#   Browser-extension fallback requests still belong to the browser and follow
#   its own network settings; the backend cannot override them.
SOURCE_FAMILY_RULES = (
    SourceFamilyRule(
        family=PLATFORM_BILIBILI,
        platform_aliases=frozenset({"bilibili", "bili"}),
        source_keys=frozenset({"search", "related_chain", "trending", "explore"}),
        url_hosts=("bilibili.com", "b23.tv"),
    ),
    SourceFamilyRule(
        family=PLATFORM_XIAOHONGSHU,
        platform_aliases=frozenset({"xiaohongshu", "xhs", "rednote"}),
        source_prefixes=("xhs-", "xhs_", "xiaohongshu"),
        url_hosts=("xiaohongshu.com", "xhslink.com"),
    ),
    SourceFamilyRule(
        family=PLATFORM_DOUYIN,
        platform_aliases=frozenset({"douyin", "dy", "tiktok"}),
        source_prefixes=("dy-", "dy_", "douyin"),
        url_hosts=("douyin.com",),
    ),
    SourceFamilyRule(
        family=PLATFORM_YOUTUBE,
        platform_aliases=frozenset({"youtube", "yt"}),
        source_prefixes=("yt-", "yt_", "youtube"),
        url_hosts=("youtube.com", "youtu.be"),
        requires_overseas_network=True,
        routed_by_network_mode=True,
    ),
    SourceFamilyRule(
        family=PLATFORM_TWITTER,
        platform_aliases=frozenset({"twitter", "x"}),
        source_prefixes=("x-", "x_", "twitter"),
        url_hosts=("x.com", "twitter.com"),
        requires_overseas_network=True,
        routed_by_network_mode=True,
    ),
    SourceFamilyRule(
        family=PLATFORM_ZHIHU,
        platform_aliases=frozenset({"zhihu", "zh", "知乎"}),
        source_prefixes=("zhihu-", "zhihu_"),
        url_hosts=("zhihu.com",),
    ),
    SourceFamilyRule(
        family=PLATFORM_REDDIT,
        platform_aliases=frozenset({"reddit", "rd"}),
        source_prefixes=("reddit-", "reddit_"),
        url_hosts=("reddit.com", "redd.it"),
        requires_overseas_network=True,
        routed_by_network_mode=True,
    ),
    SourceFamilyRule(
        family=PLATFORM_BANGUMI,
        platform_aliases=frozenset({"bangumi", "bgm"}),
        source_prefixes=("bangumi-", "bangumi_"),
        url_hosts=("bgm.tv", "bangumi.tv"),
        requires_overseas_network=True,
        routed_by_network_mode=True,
    ),
    SourceFamilyRule(
        family=PLATFORM_LINUXDO,
        platform_aliases=frozenset({"linuxdo", "linux.do", "linux-do", "ldo", "l站"}),
        source_prefixes=("linuxdo-", "linuxdo_"),
        url_hosts=("linux.do",),
    ),
    SourceFamilyRule(
        family=PLATFORM_V2EX,
        platform_aliases=frozenset({"v2ex", "v2"}),
        source_prefixes=("v2ex-", "v2ex_"),
        url_hosts=("v2ex.com",),
    ),
    SourceFamilyRule(
        family=PLATFORM_WEIBO,
        platform_aliases=frozenset({"weibo", "wb", "微博"}),
        source_prefixes=("weibo-", "weibo_"),
        url_hosts=("weibo.com", "weibo.cn"),
    ),
    SourceFamilyRule(
        family=PLATFORM_INSTAGRAM,
        platform_aliases=frozenset({"instagram", "ig"}),
        source_prefixes=("instagram-", "instagram_"),
        url_hosts=("instagram.com",),
        requires_overseas_network=True,
        # Instagram requests run in the browser extension and therefore follow
        # the browser/system proxy rather than OpenBiliClaw's HTTP client mode.
        routed_by_network_mode=False,
    ),
)

CANONICAL_SOURCE_FAMILIES = tuple(rule.family for rule in SOURCE_FAMILY_RULES)

OVERSEAS_EGRESS_PLATFORMS = frozenset(
    rule.family for rule in SOURCE_FAMILY_RULES if rule.requires_overseas_network
)

# The user-facing copy lives HERE and nowhere else. Every surface (desktop-Web
# settings, extension popup settings, and any future one) renders whatever
# :func:`overseas_network_hint` returns verbatim and never re-derives the
# platform list or re-inspects ``[network].mode`` — pinned by
# tests/test_source_network_hints.py. Both strings name the settings control
# by its exact on-screen label, which is byte-identical on both surfaces
# ("海外网络模式" + the three option labels).
_OVERSEAS_ROUTED_DIRECT_HINT = (
    "该来源接口在海外，而「海外网络模式」当前是「直连（忽略系统代理）」，"
    "OpenBiliClaw 会绕开系统代理直接请求，国内通常直接超时。"
    "若这个来源一直拉不到内容，请到设置的「通用」里把「海外网络模式」"
    "改成「跟随系统代理」或「自定义代理」（即 config.toml 的 [network] mode）。"
)
_OVERSEAS_EXTERNAL_DIRECT_HINT = (
    "该来源在海外。它的取数不经过 OpenBiliClaw 的「海外网络模式」"
    "（由本地命令行工具或浏览器扩展按系统/环境代理发起），"
    "所以当前的「直连」设置不会影响它；"
    "若一直拉不到内容，请确认系统代理本身能访问该站点。"
)

# Same advice for a non-GUI caller: appended to the exception message raised at
# the point of failure, so the CLI smokes and any other consumer explain the
# timeout without each re-implementing the check. Kept short because
# ``BangumiAPIError`` truncates its message at 240 characters.
OVERSEAS_DIRECT_MODE_ERROR_SUFFIX = (
    "（该服务在海外，而 [network].mode 当前为 direct，"
    "OpenBiliClaw 会忽略系统代理直连，国内通常直接超时；"
    "改成 system 或 custom 后重试）"
)


def normalize_source_platform(value: object, *, default: str = "") -> str:
    """Return the canonical family for a known alias, preserving unknown keys."""
    key = str(value or "").strip().lower()
    if not key:
        return default
    for rule in SOURCE_FAMILY_RULES:
        if key in rule.platform_aliases:
            return rule.family
    return key


def source_family(source: object, source_platform: object = "") -> str:
    """Resolve pool source accounting from platform, exact strategy, or prefix."""
    platform = str(source_platform or "").strip().lower()
    raw_source = str(source or "").strip()
    source_key = raw_source.lower()
    normalized = normalize_source_platform(platform)
    if platform and normalized in CANONICAL_SOURCE_FAMILIES and normalized != PLATFORM_BILIBILI:
        return normalized
    for rule in SOURCE_FAMILY_RULES:
        if source_key in rule.source_keys:
            return rule.family
    for rule in SOURCE_FAMILY_RULES:
        if source_key.startswith(rule.source_prefixes):
            return rule.family
    if normalized == PLATFORM_BILIBILI:
        return normalized
    return raw_source or "unknown"


def infer_source_platform_from_url(url: object) -> str:
    """Infer a canonical family from an exact hostname or its subdomain."""
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower().rstrip(".")
    for rule in SOURCE_FAMILY_RULES:
        if any(host == base or host.endswith(f".{base}") for base in rule.url_hosts):
            return rule.family
    return ""


def requires_overseas_network(platform: object) -> bool:
    """Whether reaching this platform from mainland China needs overseas egress."""
    return normalize_source_platform(platform) in OVERSEAS_EGRESS_PLATFORMS


def overseas_network_hint(platform: object, *, network_mode: str) -> str:
    """Return the settings-page advisory for *platform*, or ``""`` when silent.

    Non-empty only when the platform needs overseas egress AND the user has
    explicitly chosen ``direct``. Any other mode (``system`` / ``custom``) is
    already a working posture, so staying silent beats a permanent banner
    nobody reads. Callers render the result verbatim.
    """
    if str(network_mode or "").strip().lower() != "direct":
        return ""
    family = normalize_source_platform(platform)
    for rule in SOURCE_FAMILY_RULES:
        if rule.family != family or not rule.requires_overseas_network:
            continue
        return (
            _OVERSEAS_ROUTED_DIRECT_HINT
            if rule.routed_by_network_mode
            else _OVERSEAS_EXTERNAL_DIRECT_HINT
        )
    return ""
