"""Process-level single source of truth for overseas network routing.

``[network].mode`` selects ``direct`` (ignore env/system proxies), ``system``
(inherit them), or ``custom`` (use ``[network].proxy`` explicitly). Those
construction points do not all hold a ``Config`` reference, so the resolved
policy is mirrored here once and consumed by every supported HTTP stack.

CN-direct clients (bilibili / douyin / ollama / CN-CDN image cache) MUST NOT
read this module — that isolation is pinned by
``tests/test_network_proxy_isolation.py``.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit
from urllib.request import getproxies

if TYPE_CHECKING:
    from collections.abc import Mapping

OutboundProxyMode = Literal["direct", "system", "custom"]
_VALID_MODES = frozenset({"direct", "system", "custom"})
_outbound_proxy: str | None = None
# Pre-init sentinel, NOT the config default (``[network].mode`` defaults to
# ``system`` — see NetworkConfig). This is the value between module import and
# the first ``set_outbound_proxy`` call, which every entry point makes right
# after ``load_config()`` (cli ``_sync_outbound_proxy``, api ``create_app``) and
# before any consumer is constructed — every call site below lives inside a
# function, so nothing routes traffic during that window. It stays ``direct``
# so an uninitialized process never touches the user's environment, and so it
# agrees with the URL-only contract of ``set_outbound_proxy`` (empty URL and no
# mode ⇒ direct). The "user never configured it" case is resolved one layer up,
# in ``_build_network_config`` and the ``create_app`` mirror, both of which
# default to ``system``.
_outbound_mode: OutboundProxyMode = "direct"
logger = logging.getLogger(__name__)

# httpx/OpenSSL consumes the SSL_* pair while requests also honors the two
# bundle aliases. A deleted path makes trust_env=True clients fail during
# construction, before a request can be diagnosed or routed through a proxy.
_CA_FILE_ENV_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
_CA_DIR_ENV_VARS = ("SSL_CERT_DIR",)
_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_NO_PROXY_ENV_VARS = ("NO_PROXY", "no_proxy")

# Domestic (mainland-China) LLM gateways must ALWAYS connect directly, even
# when ``[network].mode`` is ``system`` / ``custom`` for reaching overseas
# models. A user who turns on a proxy to reach OpenAI must not have their
# DeepSeek / SenseNova / 通义 / … requests shoved through the same overseas
# ladder — that routes a domestic request out and back and reliably times
# out ("商汤请求总是超时"). The ``.cn`` TLD is caught by a wildcard below;
# this set covers Chinese gateways that live on non-``.cn`` domains.
_DOMESTIC_HOST_SUFFIXES = frozenset(
    {
        "deepseek.com",  # DeepSeek
        "moonshot.cn",  # Moonshot / Kimi (also .cn wildcard)
        "bigmodel.cn",  # 智谱 GLM / BigModel
        "zhipuai.cn",  # 智谱
        "aliyuncs.com",  # 阿里云 DashScope / 通义千问
        "dashscope.cn",  # 通义千问
        "baidubce.com",  # 百度 千帆 / 文心
        "tencentcloudapi.com",  # 腾讯 混元
        "hunyuan.cloud.tencent.com",  # 腾讯 混元 OpenAI-compat 网关
        "volces.com",  # 字节 火山方舟 / 豆包
        "xf-yun.com",  # 讯飞星火
        "minimax.chat",  # MiniMax
        "minimaxi.com",  # MiniMax
        "lingyiwanwu.com",  # 零一万物 Yi
        "stepfun.com",  # 阶跃星辰
        "baichuan-ai.com",  # 百川
        "siliconflow.com",  # 硅基流动 (also .cn wildcard)
        "infini-ai.com",  # 无问芯穹
        "ppinfra.com",  # PPIO 派欧算力
    }
)


def set_outbound_proxy(url: str, *, mode: str | None = None) -> None:
    """Set the process-wide overseas routing policy.

    ``mode=None`` preserves the original helper's call contract: a non-empty
    URL means ``custom`` and an empty URL means ``direct``. Config-aware call
    sites always pass the explicit mode.
    """
    global _outbound_mode, _outbound_proxy
    normalized_url = url.strip()
    resolved_mode = (mode or ("custom" if normalized_url else "direct")).strip().lower()
    if resolved_mode not in _VALID_MODES:
        raise ValueError(f"unsupported outbound proxy mode: {resolved_mode}")
    if resolved_mode == "custom" and not normalized_url:
        raise ValueError("custom outbound proxy mode requires a proxy URL")
    if resolved_mode == "system":
        _drop_invalid_ca_environment()
    _outbound_mode = cast("OutboundProxyMode", resolved_mode)
    _outbound_proxy = normalized_url if resolved_mode == "custom" else None


def _drop_invalid_ca_environment() -> None:
    """Ignore only CA overrides whose filesystem targets do not exist.

    ``system`` mode intentionally inherits proxy and certificate environment
    settings. A stale CA path is different from a valid private CA: OpenSSL
    cannot use it and httpx raises ``FileNotFoundError`` while constructing the
    client. Removing only nonexistent overrides preserves proxy inheritance and
    falls back to the normal verified CA store; TLS verification is never
    disabled and existing custom CA files/directories are left untouched.
    """
    for variable in _CA_FILE_ENV_VARS:
        value = os.environ.get(variable, "").strip()
        if value and not os.path.isfile(value):
            os.environ.pop(variable, None)
            logger.warning(
                "Ignoring invalid %s path %r; using the default verified CA store.",
                variable,
                value,
            )
    for variable in _CA_DIR_ENV_VARS:
        value = os.environ.get(variable, "").strip()
        if value and not os.path.isdir(value):
            os.environ.pop(variable, None)
            logger.warning(
                "Ignoring invalid %s path %r; using the default verified CA store.",
                variable,
                value,
            )


def outbound_proxy_mode() -> OutboundProxyMode:
    """Return the active routing mode."""
    return _outbound_mode


def outbound_proxy_url() -> str | None:
    """Return the active proxy URL, or ``None`` when disabled."""
    return _outbound_proxy


def outbound_httpx_kwargs() -> dict[str, Any]:
    """Return explicit kwargs for an ``httpx`` client construction."""
    return httpx_kwargs_for(_outbound_mode, _outbound_proxy or "")


def httpx_kwargs_for(mode: str, proxy: str = "") -> dict[str, Any]:
    """Resolve ``httpx`` kwargs for a routing policy without mutating globals."""
    normalized_mode = mode.strip().lower()
    if normalized_mode not in _VALID_MODES:
        raise ValueError(f"unsupported outbound proxy mode: {normalized_mode}")
    normalized_proxy = proxy.strip()
    if normalized_mode == "system":
        return {"trust_env": True}
    if normalized_mode == "custom":
        if not normalized_proxy:
            raise ValueError("custom outbound proxy mode requires a proxy URL")
        return {"proxy": normalized_proxy, "trust_env": False}
    return {"trust_env": False}


def outbound_trust_env() -> bool:
    """Whether SDK-owned clients should inherit environment proxy settings."""
    return _outbound_mode == "system"


def outbound_requests_proxies() -> dict[str, str] | None:
    """Return a ``requests``/scrapetube proxy mapping for the active mode.

    Empty per-scheme values deliberately override ``requests`` environment
    proxies. ``None`` is reserved for explicit ``system`` inheritance.
    """
    if _outbound_mode == "system":
        return None
    value = _outbound_proxy or ""
    return {"http": value, "https": value}


def outbound_ytdlp_proxy() -> str | None:
    """Return yt-dlp's proxy option (``""`` means force direct)."""
    if _outbound_mode == "system":
        return None
    return _outbound_proxy or ""


def curl_cffi_proxy_kwargs(proxy: str | None) -> dict[str, Any]:
    """Adapt system/explicit routing to libcurl without ambient overrides.

    ``None`` inherits OS/environment proxies; an empty string forces direct;
    a URL pins the proxy. libcurl does not read macOS System Settings, and
    curl_cffi drops ``proxy=""`` before configuring libcurl, so use a nonempty
    scheme mapping even for direct connections. Explicit proxies must also
    override ambient NO_PROXY, like the other custom-mode transports.
    """
    from curl_cffi import CurlOpt

    if proxy is not None:
        return {"proxies": {"all": proxy}, "curl_options": {CurlOpt.NOPROXY: ""}}
    system = getproxies()
    proxies = {key: system[key] for key in ("http", "https", "all") if system.get(key)}
    # Disable implicit libcurl environment discovery for unconfigured schemes.
    proxies.setdefault("all", "")
    return {"proxies": proxies, "curl_options": {CurlOpt.NOPROXY: system.get("no", "")}}


def outbound_cli_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an environment for an overseas third-party CLI.

    ``system`` preserves the caller's environment and materializes OS proxy
    settings (notably macOS System Settings) for subprocesses that only inspect
    environment variables. ``custom`` pins every common proxy variable to the
    configured URL. ``direct`` removes them so a child process cannot
    accidentally inherit an ambient proxy.
    """
    resolved = dict(os.environ if environment is None else environment)
    if _outbound_mode == "direct":
        for variable in _PROXY_ENV_VARS:
            resolved.pop(variable, None)
        return resolved
    if _outbound_mode == "custom":
        proxy = _outbound_proxy or ""
        for variable in _PROXY_ENV_VARS:
            resolved[variable] = proxy
        # ``custom`` means this overseas client must use the selected proxy,
        # independently of an ambient host bypass list.
        for variable in _NO_PROXY_ENV_VARS:
            resolved.pop(variable, None)
        return resolved

    system_proxies = getproxies()
    for scheme, upper, lower in (
        ("http", "HTTP_PROXY", "http_proxy"),
        ("https", "HTTPS_PROXY", "https_proxy"),
        ("all", "ALL_PROXY", "all_proxy"),
    ):
        value = str(system_proxies.get(scheme, "") or "").strip()
        if value and upper not in resolved and lower not in resolved:
            resolved[upper] = value
            resolved[lower] = value
    return resolved


def outbound_cli_proxy_url(
    *,
    environment: Mapping[str, str] | None = None,
    dedicated_variable: str = "",
) -> str | None:
    """Resolve one proxy URL for a CLI that accepts only a single proxy.

    A tool-specific variable (for example ``TWITTER_PROXY``) wins only in
    ``system`` mode. ``custom`` and ``direct`` remain authoritative.
    """
    original = os.environ if environment is None else environment
    if _outbound_mode == "system" and dedicated_variable:
        dedicated = str(original.get(dedicated_variable, "") or "").strip()
        if dedicated:
            return dedicated
    resolved = outbound_cli_environment(original)
    for variable in (
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        value = str(resolved.get(variable, "") or "").strip()
        if value:
            return value
    return None


def _endpoint_host(url: str) -> str:
    """Extract the lowercase host from a base URL or bare ``host[:port]``."""
    raw = (url or "").strip()
    if not raw:
        return ""
    # ``urlsplit`` only populates ``netloc`` when a scheme (or leading ``//``)
    # is present; base_urls like ``api.deepseek.com/v1`` have neither.
    if "://" not in raw:
        raw = "//" + raw
    try:
        netloc = urlsplit(raw).netloc or ""
    except ValueError:
        return ""
    if "@" in netloc:  # strip userinfo
        netloc = netloc.rsplit("@", 1)[1]
    if netloc.startswith("["):  # IPv6 literal, e.g. [::1]:8317
        end = netloc.find("]")
        host = netloc[1:end] if end != -1 else netloc[1:]
    else:
        host = netloc.split(":", 1)[0]
    return host.strip().lower().rstrip(".")


def is_domestic_endpoint(url: str) -> bool:
    """Whether ``url`` points at a mainland-China / local endpoint that must
    bypass the overseas proxy and connect directly.

    Covers loopback / private / link-local addresses (self-hosted gateways),
    the ``.cn`` TLD, and the known Chinese gateways on non-``.cn`` domains in
    :data:`_DOMESTIC_HOST_SUFFIXES`.
    """
    host = _endpoint_host(url)
    if not host:
        return False
    if host == "localhost" or host.endswith(".local") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return ip.is_loopback or ip.is_private or ip.is_link_local
    if host.endswith(".cn"):
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in _DOMESTIC_HOST_SUFFIXES)


def proxy_for_endpoint(base_url: str) -> str | None:
    """Resolve the proxy URL for a specific endpoint.

    Domestic / local endpoints always return ``None`` (direct); everything
    else follows the process-wide overseas policy.
    """
    if is_domestic_endpoint(base_url):
        return None
    return _outbound_proxy


def trust_env_for_endpoint(base_url: str) -> bool:
    """Whether an SDK client for ``base_url`` should inherit env/system proxies.

    Domestic / local endpoints never inherit env proxies (they must stay
    direct); overseas endpoints follow the process-wide ``system`` policy.
    """
    if is_domestic_endpoint(base_url):
        return False
    return _outbound_mode == "system"


def httpx_kwargs_for_endpoint(base_url: str) -> dict[str, Any]:
    """Return ``httpx`` client kwargs for a specific endpoint, honoring the
    domestic-direct carve-out."""
    if is_domestic_endpoint(base_url):
        return {"trust_env": False}
    return outbound_httpx_kwargs()


def reset_outbound_proxy_for_tests() -> None:
    """Reset global state. Test-only; not part of the runtime contract."""
    global _outbound_mode, _outbound_proxy
    _outbound_mode = "direct"
    _outbound_proxy = None
