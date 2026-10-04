"""Ollama embedding diagnostics + self-repair (v0.3.155+).

Answers "为什么向量模型不可用" with a *classified* cause instead of a bare
retry-and-fail, and can re-pull the embedding model in place. Extracted
from field logs (2026-07-05): a user's ``bge-m3`` returned HTTP 500 for
an hour while the UI only offered a dead「重试」button — nothing said
whether Ollama was down, the model was missing, or the model was broken.

Pure functions over ``base_url`` + ``model`` so both the provider-backed
path (EmbeddingService is built) and the config-only path (registry
returned ``None``) can use them, and tests can inject an
``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from typing import TYPE_CHECKING, Any

import httpx

from .ollama_embedding_runtime import cpu_fallback_active, embedding_vector, post_embedding

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Diagnosis codes surfaced to the UI (init-status prerequisites and the
# repair endpoint). Keep in sync with the popup's EMBEDDING_CHECK_TEXT.
DIAG_OK = "ok"
DIAG_NOT_RUNNING = "not_running"
DIAG_MODEL_MISSING = "model_missing"
DIAG_MODEL_BROKEN = "model_broken"
DIAG_MODEL_PATH_ENCODING = "model_path_encoding"
DIAG_DISK_FULL = "disk_full"
DIAG_NETWORK = "network"
DIAG_MODEL_OOM = "model_oom"
DIAG_ERROR = "error"

_TAGS_TIMEOUT_SECONDS = 5.0
# One embed probe on an installed model: absorbs the cold-load from disk
# (same rationale as OllamaProvider's embed timeout, but a diagnosis can
# afford to be a bit less patient than production traffic).
_PROBE_TIMEOUT_SECONDS = 60.0
# /api/pull streams NDJSON progress lines; between-chunk gaps are bounded
# by network stalls, not model size, so a per-read timeout is the right
# shape (a 568MB pull can legitimately take many minutes overall).
_PULL_READ_TIMEOUT_SECONDS = 120.0
_EMBEDDING_MODEL_MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024


def native_root(base_url: str) -> str:
    """Strip the OpenAI-compat ``/v1`` suffix to reach Ollama's native API root."""
    return base_url.rstrip("/").rsplit("/v1", 1)[0]


def _model_names_match(installed: str, wanted: str) -> bool:
    """``bge-m3:latest`` (tags) matches ``bge-m3`` (config), and vice versa."""
    return installed.split(":", 1)[0] == wanted.split(":", 1)[0]


def _error_snippet(response: httpx.Response) -> str:
    """Ollama puts the useful part in ``{"error": ...}`` — surface it."""
    try:
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])[:200]
    except Exception:
        pass
    return response.text[:200]


def _looks_like_path_encoding_failure(text: str) -> bool:
    """Detect the Windows non-ASCII model path failure without overmatching OOMs."""
    if not ("failed to load model" in text or "llama_model_loader" in text):
        return False
    if "�" in text:
        return True
    for match in re.finditer(r"[A-Za-z]:\\Users\\([^\\/\r\n]+)", text):
        user_fragment = match.group(1)
        if any(ord(ch) > 127 for ch in user_fragment):
            return True
    return False


def _looks_like_disk_full(text: str) -> bool:
    """Detect disk-capacity failures from Ollama pull/probe output."""
    lower = text.lower()
    if any(
        token in lower
        for token in (
            "no space left",
            "disk full",
            "enospc",
            "insufficient disk space",
            "insufficient storage",
            "insufficient space",
            "not enough space",
        )
    ):
        return True
    return "磁盘" in text and any(token in text for token in ("不足", "空间", "已满", "满了"))


def _looks_like_network_failure(text: str) -> bool:
    """Detect model-registry/network failures without confusing local Ollama down."""
    lower = text.lower()
    registry_context = any(
        token in lower for token in ("registry", "ollama.ai", "http://", "https://", "manifest")
    )
    if any(
        token in lower
        for token in (
            "i/o timeout",
            "io timeout",
            "no such host",
            "name resolution",
            "temporary failure in name resolution",
            "dns",
            "tls handshake",
            "certificate verify",
            "network is unreachable",
            "connection reset",
            "connection aborted",
        )
    ):
        return True
    if any(token in lower for token in ("timed out", "timeout")):
        return registry_context
    return "connection refused" in lower and any(
        token in lower for token in ("registry", "ollama.ai", "docker.io")
    )


def _looks_like_model_oom(text: str) -> bool:
    """Detect memory exhaustion that re-pulling cannot fix."""
    lower = text.lower()
    return any(
        token in lower
        for token in (
            "out of memory",
            "outofmemory",
            "cudamalloc",
            "failed to allocate",
            "insufficient memory",
            "no memory",
            "内存不足",
        )
    )


def _looks_like_native_access_violation(text: str) -> bool:
    """Detect the Windows llama-server access-violation crash signature.

    Ollama wraps the runner's ``STATUS_ACCESS_VIOLATION`` (0xc0000005) as a
    plain HTTP 500. Re-pulling *can* fix a corrupt blob, but the same signature
    also shows up for memory pressure, pagefile/AV interference and Ollama
    version bugs — so the detail must walk the user through those instead of
    claiming it is only a download problem.
    """
    lower = text.lower()
    return "0xc0000005" in lower or ("access violation" in lower and "llama" in lower)


def _format_gib(value: int) -> str:
    return f"{value / (1024 * 1024 * 1024):.1f} GB"


def _ollama_models_disk_root() -> str:
    try:
        from openbiliclaw.runtime.ollama_supervisor import managed_models_dir

        managed = managed_models_dir()
    except Exception:
        managed = None
    root = managed or os.environ.get("OLLAMA_MODELS") or os.path.join("~", ".ollama")
    return os.path.abspath(os.path.expanduser(root))


def _nearest_existing_path(path: str) -> str:
    current = os.path.abspath(os.path.expanduser(path))
    while current and not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current or os.path.abspath(os.path.expanduser("~"))


def _with_raw_detail(detail: str, raw: str) -> str:
    snippet = raw.strip()[:160]
    return f"{detail} 原始错误：{snippet}" if snippet else detail


def _disk_full_detail(model: str, *, free_bytes: int | None = None, raw: str = "") -> str:
    required = _format_gib(_EMBEDDING_MODEL_MIN_FREE_BYTES)
    available = f"当前可用约 {_format_gib(free_bytes)}，" if free_bytes is not None else ""
    detail = (
        f"磁盘空间不足：拉取 {model} 向量模型至少需要 {required} 可用空间，"
        f"{available}请清理磁盘或把 OLLAMA_MODELS 迁移到空间充足的纯英文路径后重试；"
        "空间不足不是重新下载能解决的问题。"
    )
    return _with_raw_detail(detail, raw)


def _network_detail(model: str = "bge-m3", *, raw: str = "") -> str:
    detail = (
        "无法访问模型下载源（registry.ollama.ai）。请检查网络、代理或 Ollama 镜像源配置，"
        f"确认终端可运行 `ollama pull {model}`；这不同于本地模型损坏，重复重拉通常仍会卡在下载源。"
    )
    return _with_raw_detail(detail, raw)


def _model_oom_detail(model: str, *, raw: str = "") -> str:
    detail = (
        f"内存不足以加载 {model}，重新下载无效。"
        "请关闭占用内存的程序，或换更小的 embedding 模型 / 增加内存后重试。"
    )
    return _with_raw_detail(detail, raw)


def _native_access_violation_detail(model: str, *, raw: str = "") -> str:
    detail = (
        f"{model} 已安装，但 llama-server 进程发生 Windows 访问违规崩溃（0xc0000005）。"
        "请按顺序尝试：① 点一键修复重新拉取，排除模型文件损坏；"
        "② 重启应用 / Ollama；"
        "③ 检查内存与虚拟内存（任务管理器 → 性能 → 内存，确认分页文件为系统托管），"
        "关闭高占用程序；"
        "④ 给 Ollama 与模型目录加杀毒软件白名单；"
        "⑤ 仍复现则升级到最新安装包（或更新 Ollama）。"
    )
    return _with_raw_detail(detail, raw)


def _actionable_error_detail(text: str, model: str) -> str:
    if _looks_like_model_oom(text):
        return _model_oom_detail(model, raw=text)
    if _looks_like_disk_full(text):
        return _disk_full_detail(model, raw=text)
    if _looks_like_network_failure(text):
        return _network_detail(model, raw=text)
    return text[:200]


def ollama_embedding_disk_space_error(
    model: str = "bge-m3",
    *,
    required_bytes: int = _EMBEDDING_MODEL_MIN_FREE_BYTES,
) -> tuple[str, str] | None:
    """Return a disk_full diagnosis when the Ollama model volume is too small."""
    root = _ollama_models_disk_root()
    try:
        usage = shutil.disk_usage(_nearest_existing_path(root))
    except OSError:
        logger.debug("Unable to inspect Ollama model disk space at %s", root, exc_info=True)
        return None
    free = int(usage.free)
    if free >= required_bytes:
        return None
    return (DIAG_DISK_FULL, _disk_full_detail(model, free_bytes=free))


async def diagnose_ollama_embedding(
    base_url: str,
    model: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, str]:
    """Classify why the Ollama embedding path is (or isn't) working.

    Returns ``(code, detail)``; ``detail`` is a short human-readable
    Chinese hint ("" when ok). Codes include ``ok`` / ``not_running`` /
    ``model_missing`` / ``model_broken`` / ``model_path_encoding`` /
    ``disk_full`` / ``network`` / ``model_oom`` / ``error``.

    Order matters: /api/tags first (definitive "is it running / is the
    model installed"), then one real embed probe — a model can be listed
    yet fail to load (incomplete download, OOM), which is exactly the
    500-forever case this exists to name.
    """
    root = native_root(base_url)
    # trust_env=False for the same reason as OllamaProvider.embed: user
    # proxies must not hijack localhost calls.
    client_kwargs: dict[str, Any] = {"trust_env": False}
    if transport is not None:
        client_kwargs["transport"] = transport

    async with httpx.AsyncClient(**client_kwargs) as client:
        try:
            tags = await client.get(f"{root}/api/tags", timeout=_TAGS_TIMEOUT_SECONDS)
        except Exception as exc:
            # ConnectTimeout（而非 ConnectError/拒绝）通常不是"没启动"：
            # ① 系统级 TUN 代理 (Clash/V2Ray 增强模式) 在网卡层劫持了
            #    127.0.0.1，trust_env=False 拦不住它 → 需把本机地址加直连白名单；
            # ② base_url 用了 localhost，被解析到 IPv6 (::1)，而 Ollama 只听
            #    IPv4 → 改成 127.0.0.1。所以超时时额外给出这条排查提示。
            timeout_hint = ""
            if isinstance(exc, httpx.ConnectTimeout | httpx.PoolTimeout):
                timeout_hint = (
                    "（超时而非拒绝：Ollama 多半在跑，但连不上——"
                    "若开了 Clash/V2Ray 等代理的 TUN/增强模式，请把 127.0.0.1 加入直连白名单；"
                    "或把 base_url 里的 localhost 改成 127.0.0.1 以避开 IPv6 解析。）"
                )
            return (
                DIAG_NOT_RUNNING,
                f"Ollama 服务无法连接（{root}）：{type(exc).__name__}。"
                f"{timeout_hint}"
                "请启动 Ollama（或运行 `ollama serve`）；"
                "还没安装的话，去 ollama.com/download 下载，"
                "或在终端运行 `openbiliclaw setup-embedding` 一键装好。",
            )
        if tags.status_code != 200:
            return (
                DIAG_ERROR,
                f"Ollama 响应异常（GET /api/tags -> {tags.status_code}）：{_error_snippet(tags)}",
            )
        try:
            models = tags.json().get("models") or []
            installed = [str(m.get("name") or "") for m in models if isinstance(m, dict)]
        except Exception:
            installed = []
        if not any(_model_names_match(name, model) for name in installed):
            if disk_error := ollama_embedding_disk_space_error(model):
                return disk_error
            return (
                DIAG_MODEL_MISSING,
                f"Ollama 已在运行，但没有安装 {model} 模型。"
                f"可一键修复自动拉取，或手动运行 `ollama pull {model}`。",
            )

        try:
            probe = await post_embedding(
                client,
                root,
                model,
                "ping",
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if cpu_fallback_active(root, model):
                return (
                    DIAG_MODEL_BROKEN,
                    f"{model} 已切换 CPU，但调用失败（{type(exc).__name__}）。"
                    "请查看 Ollama 运行日志，检查服务连接与可用内存后重试。",
                )
            return (
                DIAG_MODEL_BROKEN,
                f"{model} 已安装但调用失败（{type(exc).__name__}）。"
                f"建议 `ollama pull {model}` 重新拉取，或重启 Ollama。",
            )
        if probe.status_code != 200:
            snippet = _error_snippet(probe)
            if _looks_like_path_encoding_failure(snippet):
                return (
                    DIAG_MODEL_PATH_ENCODING,
                    f"{model} 已安装，但模型路径含非 ASCII 字符（常见于中文 Windows 用户名），"
                    "llama-server 无法从该路径加载模型，重新下载不能解决；"
                    "可一键迁移模型目录修复，或手动设置系统环境变量 "
                    "OLLAMA_MODELS 为纯英文路径（如 D:\\ollama\\models）后重启 Ollama 并重新拉取。",
                )
            if _looks_like_model_oom(snippet):
                cpu_hint = "已切换 CPU，仍然内存不足。" if cpu_fallback_active(root, model) else ""
                return (DIAG_MODEL_OOM, cpu_hint + _model_oom_detail(model))
            if _looks_like_disk_full(snippet):
                return (DIAG_DISK_FULL, _disk_full_detail(model))
            if _looks_like_network_failure(snippet):
                return (DIAG_NETWORK, _network_detail(model))
            if cpu_fallback_active(root, model):
                return (
                    DIAG_MODEL_BROKEN,
                    f"{model} 自动模式运行失败，已切换 CPU，但 CPU 调用仍失败"
                    f"（HTTP {probe.status_code}：{snippet}）。"
                    "重复重新拉取不一定解决进程崩溃；请查看 Ollama 运行日志，"
                    "检查内存与虚拟内存，或升级到最新安装包 / 更新 Ollama 后重试。",
                )
            if "0xc0000409" in snippet.lower():
                return (
                    DIAG_MODEL_BROKEN,
                    f"{model} 的 llama-server 进程崩溃（0xc0000409）。"
                    "请检查 Ollama 运行日志与 GPU 驱动，尝试 CPU 模式或更新 Ollama。",
                )
            if _looks_like_native_access_violation(snippet):
                return (DIAG_MODEL_BROKEN, _native_access_violation_detail(model, raw=snippet))
            return (
                DIAG_MODEL_BROKEN,
                f"{model} 已安装但调用返回 HTTP {probe.status_code}"
                f"（{snippet}）。可能下载不完整或内存不足："
                f"可一键修复重新拉取，或重启 Ollama 后重试。",
            )
        if not embedding_vector(probe):
            mode = "CPU 模式下" if cpu_fallback_active(root, model) else ""
            return (
                DIAG_MODEL_BROKEN,
                f"{model} {mode}返回了空向量或无效向量。请查看 Ollama 运行日志。",
            )
        return (DIAG_OK, "")


async def pull_ollama_model(
    base_url: str,
    model: str,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bool, str]:
    """(Re-)pull ``model`` via Ollama's native ``/api/pull``, streaming progress.

    ``on_progress(status, completed, total)`` fires per NDJSON line
    (total may be 0 while Ollama resolves the manifest). Re-pulling an
    installed-but-corrupt model is safe: Ollama re-verifies layer digests
    and re-downloads what's broken.

    Returns ``(ok, error_detail)``.
    """
    root = native_root(base_url)
    client_kwargs: dict[str, Any] = {"trust_env": False}
    if transport is not None:
        client_kwargs["transport"] = transport
    timeout = httpx.Timeout(_PULL_READ_TIMEOUT_SECONDS, connect=10.0)

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:  # noqa: SIM117
            async with client.stream(
                "POST",
                f"{root}/api/pull",
                json={"name": model, "stream": True},
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    snippet = _error_snippet(response)
                    return (
                        False,
                        f"HTTP {response.status_code}: {_actionable_error_detail(snippet, model)}",
                    )
                succeeded = False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        return (False, _actionable_error_detail(str(event["error"]), model))
                    status = str(event.get("status") or "")
                    if on_progress is not None:
                        on_progress(
                            status,
                            int(event.get("completed") or 0),
                            int(event.get("total") or 0),
                        )
                    if status == "success":
                        succeeded = True
                if succeeded:
                    return (True, "")
                return (False, "拉取流结束但未收到 success 状态")
    except Exception as exc:
        logger.warning("Ollama pull %s failed", model, exc_info=True)
        detail = f"{type(exc).__name__}: {exc}"
        return (False, _actionable_error_detail(detail, model))
