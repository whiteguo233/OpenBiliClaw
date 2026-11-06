"""Deployable sponsored gateway. Only its private Rust worker contains provider keys.

Run behind the supplied Caddy HTTPS proxy, never expose the HTTP worker publicly.
Device enrollment is anonymous and bounded; it is not proof of a unique person.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from openbiliclaw.llm.relay_auth import RelayDeniedError, RelayLimitError, RelayStore, wire_json
from openbiliclaw.llm.sponsored_errors import SponsoredError
from openbiliclaw.llm.sponsored_pool import SponsoredProviderPool
from openbiliclaw.llm.sponsored_profiles import approved_registry

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

MAX_REQUEST_BYTES = 2 * 1024 * 1024


def create_relay(
    *, command: list[str], state_path: Path, provider: Any = None, worker_count: int = 12
) -> FastAPI:
    """Create a separately deployed gateway, not a route on the user's local API."""
    store = RelayStore(state_path)
    registry = approved_registry()
    worker = provider or SponsoredProviderPool(
        command,
        registry=registry,
        startup_lock=asyncio.Lock(),
        fallback_to_other_free_model=False,
        worker_count=worker_count,
        shared_queue=True,
    )
    active: dict[str, int] = {}
    permits = asyncio.Semaphore(worker_count)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        async with worker._worker("relay.startup"):
            pass
        yield
        await worker.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    async def read_body(request: Request) -> bytes:
        body = bytearray()
        async with asyncio.timeout(15):
            async for part in request.stream():
                body.extend(part)
                if len(body) > MAX_REQUEST_BYTES:
                    raise RelayDeniedError("request too large")
        return bytes(body)

    def failure(code: str, status: int = 400) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": code, "message": "sponsored relay request rejected"}},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/session")
    async def session(request: Request) -> JSONResponse:
        try:
            body = await read_body(request)
            if body != b"{}":
                return failure("PROTOCOL_ERROR")
            device = store.authenticate(
                request.headers,
                "/v1/session",
                body,
                ip=request.client.host if request.client else "",
            )
            return JSONResponse(store.session(device), headers={"Cache-Control": "no-store"})
        except RelayLimitError:
            return failure("RATE_LIMITED", 429)
        except (RelayDeniedError, TimeoutError, ValueError):
            return failure("AUTH_FAILED", 401)

    @app.post("/v1/task")
    async def task(request: Request) -> Any:
        try:
            body = await read_body(request)
            device = store.authenticate(request.headers, "/v1/task", body)
            message = json.loads(body)
            if not isinstance(message, dict) or set(message) != {
                "task_id",
                "contract_version",
                "input",
                "stream",
            }:
                return failure("PROTOCOL_ERROR")
            if (
                not isinstance(message["task_id"], str)
                or type(message["contract_version"]) is not int
                or type(message["stream"]) is not bool
                or not isinstance(message["input"], dict)
            ):
                return failure("PROTOCOL_ERROR")
            contract = worker._resolve_contract(
                caller="", task_id=message["task_id"], contract_version=message["contract_version"]
            )
            worker._serialize_payload(message["input"], contract, None)
            worker._check_context(contract, message["input"])
            if message["stream"] and contract.operation.startswith("embedding"):
                return failure("UNSUPPORTED_TASK")
        except RelayLimitError:
            return failure("RATE_LIMITED", 429)
        except RelayDeniedError:
            return failure("AUTH_FAILED", 401)
        except SponsoredError as exc:
            return failure(exc.code)
        except (ValueError, TypeError, TimeoutError):
            return failure("PROTOCOL_ERROR")

        # Fail fast rather than accumulate an unbounded queue. These limits
        # apply per process; run exactly one ASGI worker in this deployment.
        if active.get(device, 0) >= 3 or permits.locked():
            return failure("RATE_LIMITED", 429)
        await permits.acquire()
        active[device] = active.get(device, 0) + 1
        try:
            # UTF-8 byte length plus fixed prompt/output bounds deliberately
            # over-reserves. Do not trust client usage or refund uncertain calls.
            store.reserve(
                device,
                len(body)
                + len(contract.system_prompt.encode())
                + contract.max_output_tokens
                + 1024,
                # Pinned Rust HttpUpstream retries non-stream requests up to
                # three times. An ambiguous failure may still consume quota.
                attempts=1 if message["stream"] else 3,
            )
        except (RelayDeniedError, sqlite3.Error) as exc:
            active[device] -= 1
            if not active[device]:
                del active[device]
            permits.release()
            return (
                failure("QUOTA_EXCEEDED", 429)
                if isinstance(exc, RelayLimitError)
                else failure("UPSTREAM_ERROR", 503)
            )

        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                active[device] -= 1
                if not active[device]:
                    del active[device]
                permits.release()

        class TaskResponse(StreamingResponse):
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    # Includes disconnect before the body generator ever starts.
                    release()

        async def frames() -> Any:
            try:
                async with asyncio.timeout(180):
                    if message["stream"]:
                        async with worker._worker("chat.stream") as child:
                            stream = child._stream_task_once(contract, message["input"])
                            try:
                                async for chunk in stream:
                                    if chunk.delta:
                                        yield (
                                            wire_json({"type": "delta", "content": chunk.delta})
                                            + b"\n"
                                        )
                                    if chunk.response is not None:
                                        yield (
                                            wire_json({"type": "result", **chunk.response.raw})
                                            + b"\n"
                                        )
                            finally:
                                await stream.aclose()
                    else:
                        response = await worker.execute_task(
                            task_id=contract.task_id,
                            contract_version=contract.contract_version,
                            user_payload=message["input"],
                        )
                        yield wire_json({"type": "result", **response.raw}) + b"\n"
            except SponsoredError as exc:
                yield (
                    wire_json({"type": "result", "ok": False, "error": {"code": exc.code}}) + b"\n"
                )
            except (TimeoutError, OSError):
                yield (
                    wire_json({"type": "result", "ok": False, "error": {"code": "UPSTREAM_ERROR"}})
                    + b"\n"
                )
            finally:
                release()

        return TaskResponse(
            frames(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return app


def main() -> None:
    """Start on loopback; the Caddy sidecar owns the public listener."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revoke-device", default="")
    args = parser.parse_args()
    state_path = Path(os.environ.get("OBC_RELAY_STATE", "/state/relay.db"))
    if args.revoke_device:
        if len(args.revoke_device) != 64:
            raise SystemExit("device must be a 64-character public key id")
        RelayStore(state_path).revoke(args.revoke_device)
        print("Device revoked")
        return

    command = shlex.split(os.environ.get("OBC_RELAY_RUNTIME", ""))
    if not command:
        raise SystemExit("OBC_RELAY_RUNTIME must reference the server's signed runtime")
    app = create_relay(
        command=command,
        state_path=state_path,
        worker_count=int(os.environ.get("OBC_RELAY_WORKERS", "12")),
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8430,
        workers=1,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
