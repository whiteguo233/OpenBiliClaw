"""Loopback-only Jev protocol fixture, not a model or a production provider.

Answers and usage are synthetic. Capacity limits are canonical UTF-8 JSON bytes,
NOT official token accounting. The client bridge forwards only the fixed native
endpoint and a synthetic credential to a real loopback HTTP socket.
"""

from __future__ import annotations

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self

import httpx

SYNTHETIC_KEY = "synthetic-test-key"
_NATIVE_URL = "https://api.typesafe.ai/v1/systemone"
# Fixture resource bounds, not model capacity or quality calibration.
_MAX_BODY_BYTES = 2_000_000
_SOCKET_TIMEOUT_SECONDS = 2.0
_DEFAULT_CHOICES = {
    "reason": "interest",
    "style": "deep_focus",
    "temporal_class": "evergreen",
    "validity": "none",
    "franchise": "u",
    "evidence": "u",
    "deadline": "u",
}
_MODES = frozenset(
    {
        "ok",
        "low_score",
        "server_error",
        "missing_answer",
        "wrong_model",
        "unknown_choice",
        "bad_score_legend",
        "bad_distribution",
        "extra_answer",
    }
)


def _wire(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _fixture_answers(questions: dict[str, Any], *, low_score: bool = False) -> dict[str, Any]:
    """Build native-shaped fixtures independently of the production response parser."""
    answers = {}
    for key, question in questions.items():
        kind = question["type"]
        criteria = question.get("criteria")
        if kind == "choice":
            if not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255:
                raise ValueError("invalid choice criteria")
            field = key.rsplit(".", 1)[-1]
            selected = _DEFAULT_CHOICES.get(field)
            if field == "topic":
                selected = next((k for k, v in criteria.items() if v == "编程"), "u")
            if selected not in criteria:
                selected = "u" if "u" in criteria else sorted(criteria)[0]
            answers[key] = {
                "type": "choice",
                "choice": selected,
                "probabilities": {option: float(option == selected) for option in criteria},
                "confidence": 0.0,
            }
        elif kind == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise ValueError("invalid score criteria")
            # Fixed 0.75 normalized score is test input, never a quality estimate.
            value = 0.75 * (len(criteria) - 1)
            if low_score and key.endswith(".score"):
                value = 0.0
            low = math.floor(value)
            fraction = value - low
            probabilities = {str(i): 0.0 for i in range(len(criteria))}
            probabilities[str(low)] = 1.0 - fraction
            if fraction:
                probabilities[str(low + 1)] = fraction
            answers[key] = {
                "type": "score",
                "score": value,
                "legend": {str(i): text for i, text in enumerate(criteria)},
                "probabilities": probabilities,
                "confidence": 0.0,
            }
        elif kind == "noul":
            answers[key] = {"type": "noul", "noul": 1.0}
        else:
            raise ValueError("unsupported question type")
    return answers


class _LoopbackTransport(httpx.AsyncBaseTransport):
    """Test-only bridge for the default official URL, never an external forwarder."""

    def __init__(self, local_url: str) -> None:
        self._url = local_url
        self._http = httpx.AsyncHTTPTransport(trust_env=False, retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.method != "POST"
            or str(request.url) != _NATIVE_URL
            or request.headers.get("authorization") != f"Bearer {SYNTHETIC_KEY}"
        ):
            raise ValueError("fake bridge accepts only the native endpoint and synthetic key")
        local = httpx.Request(
            "POST",
            self._url,
            content=await request.aread(),
            headers={
                "Authorization": f"Bearer {SYNTHETIC_KEY}",
                "Content-Type": "application/json",
            },
            extensions=request.extensions,
        )
        return await self._http.handle_async_request(local)

    async def aclose(self) -> None:
        await self._http.aclose()


class FakeJevBackend:
    """Context-managed real HTTP server bound to an OS-assigned loopback port.

    ``requests`` contains received synthetic bodies; ``records`` contains safe
    size/status metrics. Neither is persisted. Call ``client()`` inside the
    context to borrow a strict native-to-loopback client for the real adapter.
    """

    def __init__(
        self,
        *,
        model: str = "jev-1.13.0",
        mode: str = "ok",
        max_request_bytes: int = 48_000,
        max_state_question_bytes: int = 24_000,
    ) -> None:
        if mode not in _MODES:
            raise ValueError("unsupported fake backend mode")
        if any(
            type(value) is not int or value <= 0
            for value in (
                max_request_bytes,
                max_state_question_bytes,
            )
        ):
            raise ValueError("fixture byte capacities must be positive integers")
        self.model = model
        self.mode = mode
        self.max_request_bytes = max_request_bytes
        self.max_state_question_bytes = max_state_question_bytes
        self.requests: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.url = ""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        if self._server is not None:
            raise RuntimeError("fake backend is already running")
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass  # Never log request bodies or headers.

            def do_POST(self) -> None:
                self.connection.settimeout(_SOCKET_TIMEOUT_SECONDS)
                if self.path != "/v1/systemone":
                    self.respond(404, {"error": "not_found"})
                    return
                if self.headers.get("Authorization") != f"Bearer {SYNTHETIC_KEY}":
                    self.respond(401, {"error": "synthetic_key_required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= _MAX_BODY_BYTES:
                        raise ValueError("invalid body length")
                    body = self.rfile.read(length)
                    payload = json.loads(body)
                    status, response = backend.reply(payload)
                except (ValueError, KeyError, TypeError, OSError, RecursionError):
                    status, response = 400, {"error": "invalid_native_request"}
                self.respond(status, response)

            def respond(self, status: int, payload: object) -> None:
                body = _wire(payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}/v1/systemone"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.02},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=_SOCKET_TIMEOUT_SECONDS)
            self._thread = None
        self.url = ""

    def client(self) -> httpx.AsyncClient:
        """Return an owned-by-caller client with no proxies or external route."""
        if self._server is None:
            raise RuntimeError("start the fake backend before creating a client")
        return httpx.AsyncClient(
            transport=_LoopbackTransport(self.url), trust_env=False, follow_redirects=False
        )

    def reply(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Validate/measure received native JSON and return deterministic fixtures."""
        if set(payload) != {"model", "state", "questions"}:
            raise ValueError("invalid native envelope")
        questions = payload["questions"]
        if not isinstance(questions, dict) or not questions:
            raise ValueError("questions required")
        answers = _fixture_answers(questions, low_score=self.mode == "low_score")
        self.requests.append(payload)
        record = {
            "questions": len(questions),
            "request_bytes": len(_wire(payload)),
            "max_state_question_bytes": max(
                len(_wire({**payload, "questions": {key: value}}))
                for key, value in questions.items()
            ),
            "status": 200,
        }
        self.records.append(record)
        if payload["model"] != self.model:
            record["status"] = 422
            return 422, {"error": "unsupported_fixture_model"}
        if (
            record["request_bytes"] > self.max_request_bytes
            or record["max_state_question_bytes"] > self.max_state_question_bytes
        ):
            record["status"] = 422
            return 422, {"error": "fixture_context_capacity_exceeded"}
        if self.mode == "server_error":
            record["status"] = 503
            return 503, {"error": "fixture_unavailable"}
        if self.mode == "missing_answer":
            answers.pop(next(iter(answers)))
        elif self.mode == "extra_answer":
            answers["unexpected.fixture"] = {"type": "noul", "noul": 1.0}
        elif self.mode in {"unknown_choice", "bad_distribution", "bad_score_legend"}:
            wanted = "score" if self.mode == "bad_score_legend" else "choice"
            answer = next((answer for answer in answers.values() if answer["type"] == wanted), None)
            if answer is None:
                record["status"] = 400
                return 400, {"error": "fixture_fault_requires_matching_question_type"}
            if self.mode == "unknown_choice":
                answer["choice"] = "outside_request"
                answer["probabilities"] = {"outside_request": 1.0}
            elif self.mode == "bad_distribution":
                answer["probabilities"] = {key: 0.0 for key in answer["probabilities"]}
            else:
                answer["legend"]["0"] = "fixture changed the caller's rubric"
        return 200, {
            "model": "jev-fixture-wrong-model" if self.mode == "wrong_model" else self.model,
            "answers": answers,
            "usage": {"input_tokens": 123, "output_tokens": 10},
        }
