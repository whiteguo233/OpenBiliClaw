"""Classification + pull behavior of llm/ollama_diagnostics (v0.3.155+).

Field context (2026-07-05): a user's bge-m3 returned HTTP 500 for an hour
while the UI offered only a dead retry — these tests pin down that every
failure shape maps to a distinct, actionable code.
"""

from __future__ import annotations

import json

import httpx

from openbiliclaw.llm.ollama_diagnostics import (
    DIAG_DISK_FULL,
    DIAG_ERROR,
    DIAG_MODEL_BROKEN,
    DIAG_MODEL_MISSING,
    DIAG_MODEL_OOM,
    DIAG_MODEL_PATH_ENCODING,
    DIAG_NETWORK,
    DIAG_NOT_RUNNING,
    DIAG_OK,
    _looks_like_disk_full,
    _looks_like_model_oom,
    _looks_like_native_access_violation,
    _looks_like_network_failure,
    diagnose_ollama_embedding,
    native_root,
    ollama_embedding_disk_space_error,
    pull_ollama_model,
)

BASE_URL = "http://localhost:11434/v1"


def _tags_response(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n} for n in names]})


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_native_root_strips_v1_suffix() -> None:
    assert native_root("http://localhost:11434/v1") == "http://localhost:11434"
    assert native_root("http://localhost:11434/v1/") == "http://localhost:11434"
    assert native_root("http://localhost:11434") == "http://localhost:11434"


async def test_diagnose_not_running_on_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_NOT_RUNNING
    assert "ollama serve" in detail
    # A plain refusal is "not started" — no proxy/IPv6 hint.
    assert "127.0.0.1" not in detail


async def test_diagnose_connect_timeout_adds_proxy_and_ipv6_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_NOT_RUNNING
    # ConnectTimeout (vs refused) surfaces the TUN-proxy / IPv6 root-cause hint.
    assert "127.0.0.1" in detail
    assert "TUN" in detail


async def test_diagnose_model_missing_when_absent_from_tags(monkeypatch) -> None:
    monkeypatch.setattr(
        "openbiliclaw.llm.ollama_diagnostics.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 10 * 1024 * 1024 * 1024})(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return _tags_response("qwen2.5:7b")

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_MISSING
    assert "ollama pull bge-m3" in detail


async def test_diagnose_matches_tag_suffix_variants() -> None:
    """``bge-m3:latest`` in tags satisfies a config that says ``bge-m3``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3:latest")
        return httpx.Response(200, json={"embedding": [0.1, 0.2]})

    code, _ = await diagnose_ollama_embedding(BASE_URL, "bge-m3", transport=_transport(handler))
    assert code == DIAG_OK


async def test_diagnose_model_broken_when_installed_but_500s() -> None:
    """The exact field-log shape: model listed, /api/embeddings 500s forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(500, json={"error": "failed to load model"})

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_BROKEN
    assert "500" in detail
    assert "failed to load model" in detail


async def test_diagnose_model_path_encoding_on_mojibake_windows_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(
            500,
            json={
                "error": (
                    "llama-server process has terminated: exit status 1: "
                    "error loading model: llama_model_loader: failed to load model "
                    "from C:\\Users\\��\\ .ollama\\models\\blobs\\sha256-abc"
                )
            },
        )

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_PATH_ENCODING
    assert "OLLAMA_MODELS" in detail
    assert "重新下载不能解决" in detail


async def test_diagnose_plain_load_failure_stays_model_broken() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(
            500,
            json={
                "error": (
                    "llama_model_loader: failed to load model from "
                    "C:\\Users\\alice\\.ollama\\models\\blobs\\sha256-abc"
                )
            },
        )

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_BROKEN
    assert "500" in detail


async def test_diagnose_memory_ish_500_is_model_oom() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(500, json={"error": "failed to allocate: out of memory"})

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_OOM
    assert "重新下载无效" in detail


async def test_diagnose_access_violation_gets_ordered_checklist() -> None:
    """0xc0000005 is a native llama-server crash — not just a corrupt download."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(
            500,
            json={
                "error": (
                    "llama-server process has terminated: exit status 0xc0000005: "
                    "The instruction at 0x7ffa referenced memory at 0x0. "
                    "The memory could not be read."
                )
            },
        )

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_BROKEN
    assert "0xc0000005" in detail
    assert "重新拉取" in detail
    assert "内存与虚拟内存" in detail
    assert "最新安装包" in detail


async def test_diagnose_path_encoding_wins_before_model_oom() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(
            500,
            json={
                "error": (
                    "llama_model_loader: failed to load model from "
                    "C:\\Users\\��\\.ollama\\models\\blobs\\sha256-abc; "
                    "failed to allocate memory"
                )
            },
        )

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_PATH_ENCODING
    assert "OLLAMA_MODELS" in detail


async def test_diagnose_disk_full_500_is_disk_full() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(500, json={"error": "write blob: no space left on device"})

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_DISK_FULL
    assert "磁盘空间不足" in detail


async def test_diagnose_network_500_is_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(
            500,
            json={"error": "pull model manifest: Get https://registry.ollama.ai: i/o timeout"},
        )

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_NETWORK
    assert "下载源" in detail


def test_disk_full_signature_helper_is_conservative() -> None:
    assert _looks_like_disk_full("write /models/blob: no space left on device")
    assert _looks_like_disk_full("pull failed: ENOSPC")
    assert _looks_like_disk_full("磁盘空间不足")
    assert not _looks_like_disk_full("failed to allocate: insufficient memory")
    assert not _looks_like_disk_full("llama_model_loader failed to load model")


def test_network_signature_helper_is_conservative() -> None:
    assert _looks_like_network_failure("dial tcp: lookup registry.ollama.ai: no such host")
    assert _looks_like_network_failure("TLS handshake timeout while contacting registry")
    assert _looks_like_network_failure("connection refused by registry.ollama.ai")
    assert not _looks_like_network_failure("llama_model_loader failed to load model")
    assert not _looks_like_network_failure("timed out while cold-loading local model")
    assert not _looks_like_network_failure("C:\\Users\\��\\.ollama\\models")


def test_model_oom_signature_helper_is_conservative() -> None:
    assert _looks_like_model_oom("cudaMalloc failed: out of memory")
    assert _looks_like_model_oom("OutOfMemoryError: failed to allocate")
    assert _looks_like_model_oom("内存不足，无法加载模型")
    assert not _looks_like_model_oom("insufficient space on disk")
    assert not _looks_like_model_oom("C:\\Users\\��\\.ollama\\models")


def test_native_access_violation_helper_is_conservative() -> None:
    assert _looks_like_native_access_violation("exit status 0xc0000005: access violation")
    assert _looks_like_native_access_violation("llama-server access violation at 0x0")
    assert not _looks_like_native_access_violation("failed to load model")
    assert not _looks_like_native_access_violation("out of memory")
    assert not _looks_like_native_access_violation("access violation in some other app")


def test_disk_space_precheck_reports_disk_full(monkeypatch, tmp_path) -> None:
    models_dir = tmp_path / "models"
    monkeypatch.setenv("OLLAMA_MODELS", str(models_dir))
    monkeypatch.setattr(
        "openbiliclaw.llm.ollama_diagnostics.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 128 * 1024 * 1024})(),
    )

    result = ollama_embedding_disk_space_error()

    assert result is not None
    code, detail = result
    assert code == DIAG_DISK_FULL
    assert "至少" in detail


async def test_diagnose_model_broken_on_empty_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(200, json={"embedding": []})

    code, _ = await diagnose_ollama_embedding(BASE_URL, "bge-m3", transport=_transport(handler))
    assert code == DIAG_MODEL_BROKEN


async def test_diagnose_error_on_unexpected_tags_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "busy"})

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_ERROR
    assert "busy" in detail


async def test_pull_streams_progress_and_succeeds() -> None:
    lines = [
        {"status": "pulling manifest"},
        {"status": "downloading", "completed": 100, "total": 400},
        {"status": "downloading", "completed": 400, "total": 400},
        {"status": "success"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pull"
        body = "\n".join(json.dumps(line) for line in lines)
        return httpx.Response(200, text=body)

    seen: list[tuple[str, int, int]] = []
    ok, error = await pull_ollama_model(
        BASE_URL,
        "bge-m3",
        on_progress=lambda s, c, t: seen.append((s, c, t)),
        transport=_transport(handler),
    )
    assert ok is True
    assert error == ""
    assert ("downloading", 400, 400) in seen
    assert seen[-1] == ("success", 0, 0)


async def test_pull_surfaces_stream_error_line() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"error": "pull model manifest: file not found"}
        return httpx.Response(200, text=json.dumps(payload))

    ok, error = await pull_ollama_model(BASE_URL, "bge-m3", transport=_transport(handler))
    assert ok is False
    assert "manifest" in error


async def test_pull_reports_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "disk full"})

    ok, error = await pull_ollama_model(BASE_URL, "bge-m3", transport=_transport(handler))
    assert ok is False
    assert "disk full" in error


async def test_pull_without_success_status_is_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"status": "downloading"}))

    ok, error = await pull_ollama_model(BASE_URL, "bge-m3", transport=_transport(handler))
    assert ok is False
    assert error
