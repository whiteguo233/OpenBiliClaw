"""DNS-pinned TikTok cover downloads, retaining configured proxy and TLS identity."""

from __future__ import annotations

import ipaddress
import json

import httpx

from openbiliclaw.runtime.image_cache import (
    _FETCH_TIMEOUT_SECONDS,
    _MAX_REDIRECTS,
    _REDIRECT_STATUSES,
    MAX_IMAGE_BYTES,
    CoverFetchError,
    _httpx_client_kwargs,
    _parse_image_url,
    _upstream_headers_for_host,
    _validate_content_headers,
)

_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(prefix)
    for prefix in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


async def _dns_answers(url: httpx.URL) -> list[str]:
    # Resolve through the configured overseas route, not a potentially split
    # local resolver. Only the public CDN hostname is sent (never the signed
    # image URL or Cookie). Failure is closed; no unvalidated DNS fallback.
    try:
        async with (
            httpx.AsyncClient(
                **_httpx_client_kwargs(url.host),
                follow_redirects=False,
                timeout=_FETCH_TIMEOUT_SECONDS,
            ) as client,
            client.stream(
                "GET",
                "https://cloudflare-dns.com/dns-query",
                params={"name": url.host, "type": "A"},
                headers={"Accept": "application/dns-json"},
            ) as response,
        ):
            if response.status_code != 200:
                raise CoverFetchError(502, "Cover DNS lookup failed")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 32768:
                    raise CoverFetchError(502, "Cover DNS response is too large")
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("Status") != 0 or payload.get("TC"):
            raise CoverFetchError(502, "Invalid cover DNS response")
        answers = payload.get("Answer")
        if not isinstance(answers, list):
            raise CoverFetchError(502, "Cover DNS returned no addresses")
        return [
            str(row["data"])
            for row in answers
            if isinstance(row, dict) and row.get("type") == 1 and "data" in row
        ]
    except (httpx.HTTPError, ValueError) as exc:
        raise CoverFetchError(502, "Cover DNS lookup failed") from exc


async def _resolve_address(url: httpx.URL) -> str:
    records = await _dns_answers(url)
    addresses = set()
    for record in records:
        try:
            address = ipaddress.ip_address(record)
        except ValueError as exc:
            raise CoverFetchError(502, "Invalid cover DNS address") from exc
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or (
                isinstance(address, ipaddress.IPv6Address)
                and (
                    address.is_site_local
                    or address.ipv4_mapped is not None
                    or any(address in network for network in _TRANSITION_NETWORKS)
                )
            )
        ):
            raise CoverFetchError(403, "Cover DNS contains a non-public address")
        addresses.add(str(address))
    if not addresses:
        raise CoverFetchError(502, "Cover DNS returned no addresses")
    return sorted(addresses, key=lambda value: (":" in value, value))[0]


async def fetch_tiktok_cover(url: httpx.URL) -> tuple[bytes, str]:
    """Validate and pin every hop; never carry account cookies or bypass proxies.

    CONNECT_TO pins both direct and proxy tunnel destinations while preserving
    the original Host, TLS SNI and certificate checks. Each hop uses a fresh
    session so there is no cross-host cookie or pooled-connection reuse.
    """
    from curl_cffi import CurlOpt, ffi, lib
    from curl_cffi.requests import AsyncSession, RequestsError

    from openbiliclaw.network import outbound_cli_proxy_url

    seen: set[str] = set()
    for hop in range(_MAX_REDIRECTS + 1):
        url = _parse_image_url(str(url))
        if url.port not in {None, 443}:
            raise CoverFetchError(403, "Cover port is not allowed")
        if str(url) in seen:
            raise CoverFetchError(502, "Redirect loop")
        seen.add(str(url))
        address = await _resolve_address(url)
        target = f"[{address}]" if ":" in address else address
        policy = _httpx_client_kwargs(url.host)
        proxy = policy.get("proxy", "")
        if policy.get("trust_env", True):
            # libcurl does not read macOS System Settings itself; materialize
            # the same system policy used by the httpx DNS request.
            proxy = outbound_cli_proxy_url()
        # curl_cffi converts RESOLVE lists, but CONNECT_TO requires its native
        # slist pointer. Keep it alive through response/session close.
        connect_to = lib.curl_slist_append(ffi.NULL, f"{url.host}:443:{target}:443".encode("ascii"))
        if connect_to == ffi.NULL:
            raise CoverFetchError(502, "Unable to allocate pinned cover route")
        try:
            async with AsyncSession(
                proxy=proxy,
                curl_options={CurlOpt.CONNECT_TO: connect_to},
            ) as session:
                response = await session.get(
                    str(url),
                    headers=_upstream_headers_for_host(url.host),
                    allow_redirects=False,
                    timeout=_FETCH_TIMEOUT_SECONDS,
                    verify=True,
                    stream=True,
                    discard_cookies=True,
                )
                try:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location", "").strip()
                        if not location or hop == _MAX_REDIRECTS:
                            raise CoverFetchError(502, "Invalid cover redirect")
                        url = url.join(location)
                        continue
                    if not 200 <= response.status_code < 300:
                        raise CoverFetchError(502, "Cover upstream request failed")
                    mime = _validate_content_headers(httpx.Headers(response.headers.items()))
                    chunks = bytearray()
                    async for chunk in response.aiter_content():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_IMAGE_BYTES:
                            raise CoverFetchError(413, "Image too large")
                    if not chunks:
                        raise CoverFetchError(502, "Empty cover response")
                    return bytes(chunks), mime
                finally:
                    if response.quit_now is not None:
                        response.quit_now.set()
                    await response.aclose()
        except RequestsError as exc:
            raise CoverFetchError(502, "Pinned cover request failed") from exc
        finally:
            lib.curl_slist_free_all(connect_to)
    raise CoverFetchError(502, "Too many cover redirects")
