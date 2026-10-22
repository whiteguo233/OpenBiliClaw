"""Pinned public-address transport for the shared cover-image fetcher.

Each redirect is resolved before send; *all* answers must be public. Connections
use only that frozen numeric address set, while HTTP Host and TLS verification
retain the original CDN hostname. A configured proxy is a trusted transport,
not a destination resolver: CONNECT / SOCKS requests carry numeric IPs too.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
from typing import TYPE_CHECKING
from urllib.request import getproxies, proxy_bypass

import httpcore
import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable


class UnsafeImageAddressError(ValueError):
    """An allowlisted hostname resolved outside the public Internet."""


def is_public_address(value: str) -> bool:
    """Reject local/special ranges, mixed-address tricks and transition tunnels."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Only ordinary global unicast; reject mapped IPv4, NAT64, 6to4,
        # Teredo and scope identifiers even if a Python version calls them global.
        return (
            address in ipaddress.ip_network("2000::/3")
            and address.sixtofour is None
            and address.teredo is None
            and address.scope_id is None
        )
    return True


async def resolve_public_addresses(host: str, *, proxy: str | None = None) -> tuple[str, ...]:
    """Resolve once; proxied CDNs use bounded, TLS-verified DoH via that proxy.

    Local DNS may return a proxy's fake-IP or a poisoned address. Never fall
    back to unvalidated remote hostname resolution: resolve the public CDN
    hostname (not its path/token) at the fixed Cloudflare 1.1.1.1 endpoint,
    then pin the returned public addresses. Direct traffic stays on local DNS.
    """
    if proxy:
        addresses = await _resolve_proxy_addresses(host, proxy)
    else:
        try:
            answers = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM),
                timeout=5.0,
            )
        except TimeoutError as exc:
            raise httpx.ConnectTimeout("Image DNS timed out") from exc
        except OSError as exc:
            raise httpx.ConnectError("Image DNS failed") from exc
        addresses = tuple(dict.fromkeys(str(row[4][0]) for row in answers))
    if not addresses or not all(is_public_address(address) for address in addresses):
        raise UnsafeImageAddressError("Image destination is not public")
    # Prefer IPv4 on dual-stack consumer networks; keep a bounded fallback set.
    return tuple(sorted(addresses, key=lambda address: ":" in address))[:4]


async def _resolve_proxy_addresses(host: str, proxy: str) -> tuple[str, ...]:
    """No redirects, cookies, URL tokens, local DNS or unbounded DNS responses."""
    addresses: list[str] = []
    try:
        async with (
            asyncio.timeout(5.0),
            httpx.AsyncClient(
                proxy=proxy,
                trust_env=False,
                timeout=5.0,
                follow_redirects=False,
            ) as client,
        ):
            client.headers.clear()
            for kind in (1, 28):
                async with client.stream(
                    "GET",
                    "https://1.1.1.1/dns-query",
                    params={"name": host, "type": str(kind)},
                    headers={"accept": "application/dns-json", "accept-encoding": "identity"},
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise httpx.ConnectError("Image DNS response too large")
                try:
                    data = json.loads(body)
                except (ValueError, UnicodeError) as exc:
                    raise httpx.ConnectError("Invalid image DNS response") from exc
                if (
                    not isinstance(data, dict)
                    or type(data.get("Status")) is not int
                    or data["Status"] != 0
                    or data.get("TC") is not False
                    or data.get("Question")
                    not in (
                        [{"name": host, "type": kind}],
                        [{"name": f"{host}.", "type": kind}],
                    )
                    or not isinstance(data.get("Answer", []), list)
                ):
                    raise httpx.ConnectError("Invalid image DNS response")
                for row in data.get("Answer", []):
                    if not isinstance(row, dict):
                        raise httpx.ConnectError("Invalid image DNS answer")
                    if row.get("type") not in {1, 28}:
                        continue
                    address = row.get("data")
                    if not isinstance(address, str) or not is_public_address(address):
                        raise UnsafeImageAddressError("Image destination is not public")
                    addresses.append(address)
    except TimeoutError as exc:
        raise httpx.ConnectTimeout("Image DNS timed out") from exc
    return tuple(dict.fromkeys(addresses))


def image_proxy_for_host(host: str, *, direct: bool) -> str | None:
    """Apply CN-direct / overseas policy per hop, before replacing DNS names."""
    if direct:
        return None
    from openbiliclaw.network import outbound_httpx_kwargs

    policy = outbound_httpx_kwargs()
    if policy.get("proxy"):
        return str(policy["proxy"])
    if policy.get("trust_env") and not proxy_bypass(host):
        proxies = getproxies()
        return proxies.get("https") or proxies.get("all")
    return None


async def _read_exact(
    stream: httpcore.AsyncNetworkStream, size: int, timeout: float | None
) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = await stream.read(size - len(result), timeout=timeout)
        if not chunk:
            raise httpcore.ProxyError("Image proxy closed the tunnel handshake")
        result.extend(chunk)
    return bytes(result)


async def _open_http_tunnel(
    stream: httpcore.AsyncNetworkStream, proxy: httpx.Proxy, address: str, timeout: float | None
) -> None:
    target = f"[{address}]:443" if ":" in address else f"{address}:443"
    headers = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if proxy.raw_auth is not None:
        auth = base64.b64encode(b":".join(proxy.raw_auth)).decode("ascii")
        headers.append(f"Proxy-Authorization: Basic {auth}")
    await stream.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"), timeout=timeout)
    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = await stream.read(1024, timeout=timeout)
        if not chunk or len(response) + len(chunk) > 16384:
            raise httpcore.ProxyError("Invalid image proxy tunnel response")
        response.extend(chunk)
    line = bytes(response).split(b"\r\n", 1)[0].split(b" ")
    if (
        len(line) < 2
        or line[0] not in {b"HTTP/1.0", b"HTTP/1.1"}
        or line[1] != b"200"
        or not response.endswith(b"\r\n\r\n")
    ):
        raise httpcore.ProxyError("Image proxy rejected the tunnel")


async def _open_socks_tunnel(
    stream: httpcore.AsyncNetworkStream, proxy: httpx.Proxy, address: str, timeout: float | None
) -> None:
    # RFC 1928/1929: send an IP literal, never ATYP=DOMAIN (remote DNS).
    auth = proxy.raw_auth
    method = 2 if auth is not None else 0
    await stream.write(bytes([5, 1, method]), timeout=timeout)
    if await _read_exact(stream, 2, timeout) != bytes([5, method]):
        raise httpcore.ProxyError("Image SOCKS authentication method rejected")
    if auth is not None:
        username, password = auth
        if not (0 < len(username) <= 255 and 0 < len(password) <= 255):
            raise httpcore.ProxyError("Invalid image SOCKS credentials")
        await stream.write(
            bytes([1, len(username)]) + username + bytes([len(password)]) + password,
            timeout=timeout,
        )
        if await _read_exact(stream, 2, timeout) != b"\x01\x00":
            raise httpcore.ProxyError("Image SOCKS authentication failed")
    ip = ipaddress.ip_address(address)
    await stream.write(
        bytes([5, 1, 0, 1 if ip.version == 4 else 4]) + ip.packed + b"\x01\xbb", timeout=timeout
    )
    reply = await _read_exact(stream, 4, timeout)
    if reply[:3] != b"\x05\x00\x00":
        raise httpcore.ProxyError("Image SOCKS tunnel rejected")
    if reply[3] == 1:
        length = 4
    elif reply[3] == 4:
        length = 16
    elif reply[3] == 3:
        length = (await _read_exact(stream, 1, timeout))[0]
    else:
        raise httpcore.ProxyError("Invalid image SOCKS address")
    await _read_exact(stream, length + 2, timeout)


class PinnedImageBackend(httpcore.AsyncNetworkBackend):
    """Connect only to validated IPs, keeping TLS hostname handling in HTTPCore."""

    def __init__(self, host: str, addresses: tuple[str, ...], proxy: str | None) -> None:
        self.host = host
        self.addresses = addresses
        self.proxy = httpx.Proxy(proxy) if proxy else None
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if (
            host != self.host
            or port != 443
            or not self.addresses
            or not all(is_public_address(address) for address in self.addresses)
        ):
            raise UnsafeImageAddressError("Unpinned image connection rejected")
        try:
            async with asyncio.timeout(timeout):
                last_error: Exception | None = None
                for address in self.addresses:
                    stream: httpcore.AsyncNetworkStream | None = None
                    try:
                        proxy = self.proxy
                        stream = await self.backend.connect_tcp(
                            proxy.url.host if proxy else address,
                            (
                                proxy.url.port
                                or {"https": 443, "socks5": 1080, "socks5h": 1080}.get(
                                    proxy.url.scheme, 80
                                )
                            )
                            if proxy
                            else 443,
                            timeout=timeout,
                            local_address=local_address,
                            socket_options=socket_options,
                        )
                        if proxy is not None:
                            if proxy.url.scheme == "https":
                                stream = await stream.start_tls(
                                    httpx.create_ssl_context(),
                                    server_hostname=proxy.url.host,
                                    timeout=timeout,
                                )
                            if proxy.url.scheme in {"http", "https"}:
                                await _open_http_tunnel(stream, proxy, address, timeout)
                            elif proxy.url.scheme in {"socks5", "socks5h"}:
                                await _open_socks_tunnel(stream, proxy, address, timeout)
                            else:
                                raise httpcore.ProxyError("Unsupported image proxy protocol")
                        return stream
                    except BaseException as exc:
                        if stream is not None:
                            await stream.aclose()
                        if not isinstance(exc, httpcore.ConnectError | httpcore.ConnectTimeout):
                            raise
                        last_error = exc
                raise httpcore.ConnectError("Image connection failed") from last_error
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("Image connection timed out") from exc


class _ImageStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response, pool: httpcore.AsyncConnectionPool) -> None:
        self.response = response
        self.pool = pool

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.response.aiter_stream():
                yield chunk
        except httpcore.TimeoutException as exc:
            raise httpx.ReadTimeout("Image read timed out") from exc
        except httpcore.NetworkError as exc:
            raise httpx.ReadError("Image read failed") from exc
        except httpcore.ProtocolError as exc:
            raise httpx.RemoteProtocolError("Invalid image response") from exc

    async def aclose(self) -> None:
        try:
            await self.response.aclose()
        finally:
            await self.pool.aclose()


class ImageTransport(httpx.AsyncBaseTransport):
    """One connection per validated hop; no origin reuse or second DNS lookup."""

    def __init__(self, is_direct: Callable[[str], bool]) -> None:
        self.is_direct = is_direct

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        addresses = request.extensions.get("image_addresses")
        if not isinstance(addresses, tuple) or not all(isinstance(ip, str) for ip in addresses):
            raise UnsafeImageAddressError("Image destination was not resolved")
        host = request.url.host
        direct = self.is_direct(host)
        proxy = request.extensions.get("image_proxy")
        if "image_proxy" not in request.extensions or (
            proxy is not None and not isinstance(proxy, str)
        ):
            raise UnsafeImageAddressError("Image route was not frozen")
        pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=not direct),
            max_keepalive_connections=0,
            network_backend=PinnedImageBackend(host, addresses, proxy),
        )
        try:
            response = await pool.handle_async_request(
                httpcore.Request(
                    method=request.method,
                    url=httpcore.URL(
                        scheme=request.url.raw_scheme,
                        host=request.url.raw_host,
                        port=request.url.port,
                        target=request.url.raw_path,
                    ),
                    headers=request.headers.raw,
                    content=request.stream,
                    extensions=request.extensions,
                )
            )
        except BaseException as exc:
            await pool.aclose()
            if isinstance(exc, httpcore.TimeoutException):
                raise httpx.ConnectTimeout("Image connection timed out") from exc
            if isinstance(
                exc, httpcore.NetworkError | httpcore.ProtocolError | httpcore.ProxyError
            ):
                raise httpx.ConnectError("Image connection failed") from exc
            raise
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_ImageStream(response, pool),
            extensions=response.extensions,
        )
