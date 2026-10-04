"""Shared local embedding execution policy: automatic acceleration, then CPU.

Mode belongs to an endpoint/model pair, not a provider instance: health probes,
config reloads and production calls must not keep reloading a crashing GPU
runner. No daemon restarts or global environment changes are needed. Ollama's
native ``options.num_gpu=0`` reloads only the requested model for CPU execution.
"""

from __future__ import annotations

import ipaddress
import logging
import math
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

# Process-local, sticky even when the CPU attempt fails or is cancelled. New
# requests must not keep crashing the automatic runner. Restarting the app
# retries automatic selection; changing endpoint/model gets an independent key.
_cpu_models: set[tuple[str, str]] = set()


def _local_key(root: str, model: str) -> tuple[str, str] | None:
    parsed = urlparse(root.rstrip("/"))
    host = (parsed.hostname or "").lower()
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return None
        except ValueError:
            return None
    # Standard loopback aliases address the same daemon. Keep nonstandard
    # 127.x addresses separate, as they can host independent services.
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path.removesuffix("/v1").rstrip("/")
    return f"{parsed.scheme}://{host}:{port}{path}", model.removesuffix(":latest")


def cpu_fallback_active(root: str, model: str) -> bool:
    """Whether this local endpoint/model has switched to CPU in this process."""
    key = _local_key(root, model)
    return key is not None and key in _cpu_models


def _runner_failure(response: httpx.Response) -> bool:
    if response.status_code != 500:
        return False
    try:
        payload = response.json()
        detail = str(payload.get("error", "")) if isinstance(payload, dict) else ""
    except ValueError:
        detail = response.text
    lower = detail.lower()
    if any(
        token in lower
        for token in (
            "failed to load model from",
            "failed to open",
            "no such file",
            "file does not exist",
            "invalid model",
            "invalid magic",
            "checksum",
            "digest mismatch",
        )
    ):
        return False
    # Field report 2026-10-02: RX 7700 XT/Vulkan crashes in bge-m3 warmup
    # with 0xc0000409 despite ample VRAM. Retry native crashes and explicit
    # accelerator/OOM failures, never missing models/auth/path errors or a
    # slow cold load. A bounded CPU trial distinguishes recoverable execution
    # failures without claiming all native crashes are caused by the GPU.
    return any(
        token in lower
        for token in (
            "0xc0000409",
            "0xc0000005",
            "llama-server process has terminated",
            "llama runner process has terminated",
            "runner process has terminated",
            "out of memory",
            "unable to allocate",
            "failed to allocate",
            "cuda error",
            "cuda out of memory",
            "hip error",
            "vulkan error",
            "vk_error",
            "no compatible gpu",
            "gpu memory",
        )
    )


async def post_embedding(
    client: httpx.AsyncClient,
    root: str,
    model: str,
    prompt: str,
    *,
    timeout: float | None = None,
) -> httpx.Response:
    """Send an embedding request, retrying a local runner failure once on CPU.

    Returns the final HTTP response (including failure) for the caller to
    classify. Transport errors/cancellation propagate; a timeout alone is not
    evidence of a GPU failure. Successful HTTP status is not a readiness claim:
    callers must validate the returned vector with :func:`embedding_vector`.
    """
    key = _local_key(root, model)
    cpu = key is not None and key in _cpu_models
    body: dict[str, Any] = {"model": model, "prompt": prompt}
    if cpu:
        body["options"] = {"num_gpu": 0}
    kwargs: dict[str, Any] = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    response = await client.post(f"{root}/api/embeddings", json=body, **kwargs)
    invalid_vector = response.status_code == 200 and not embedding_vector(response)
    if cpu or key is None or not (_runner_failure(response) or invalid_vector):
        return response

    # Publish before awaiting: concurrent/new calls and cancelled health
    # probes must retain the CPU choice. In-flight automatic calls may each
    # retry once; Ollama itself coordinates their model loads.
    _cpu_models.add(key)
    logger.warning(
        "Ollama embedding automatic runner failed; switching model=%s to CPU "
        "for this application session (num_gpu=0)",
        model,
    )
    body["options"] = {"num_gpu": 0}
    response = await client.post(f"{root}/api/embeddings", json=body, **kwargs)
    if response.status_code == 200 and embedding_vector(response):
        logger.info("Ollama embedding CPU fallback verified (model=%s)", model)
    else:
        logger.warning("Ollama embedding CPU fallback failed (model=%s)", model)
    return response


def embedding_vector(response: httpx.Response) -> list[float]:
    """Read a nonempty, entirely numeric finite vector; reject malformed data."""
    try:
        payload = response.json()
        vector = payload.get("embedding") if isinstance(payload, dict) else None
        if not isinstance(vector, list) or not vector:
            return []
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in vector):
            return []
        values = [float(v) for v in vector]
        return values if all(math.isfinite(v) for v in values) else []
    except (ValueError, TypeError, OverflowError):
        return []
