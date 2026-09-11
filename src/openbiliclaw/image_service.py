"""Standalone OpenBiliClaw image proxy service.

This process owns cover-image fetching, disk caching and mobile compression.
It runs on a separate port from the main API so heavy image work cannot
squeeze recommendation serving / reshuffle / chats in the API process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from io import BytesIO

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response

from openbiliclaw.runtime import image_cache
from openbiliclaw.runtime.image_fetch import ImageFetchCoordinator

logger = logging.getLogger(__name__)

app = FastAPI(title="OpenBiliClaw Image Proxy")
coordinator = ImageFetchCoordinator()


def _resize_cover_for_mobile(data: bytes, content_type: str) -> tuple[bytes, str]:
    """Downscale/compress proxied cover images for mobile bandwidth."""
    if not data or "image" not in content_type:
        return data, content_type
    try:
        from PIL import Image

        image: Image.Image = Image.open(BytesIO(data))
        if image.width <= 640:
            return data, content_type
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "P", "LA"}:
            image = image.convert("RGB")
        out = BytesIO()
        image.save(out, format="JPEG", quality=80, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return data, content_type


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "openbiliclaw-image-proxy"}


@app.get("/api/image-proxy", response_model=None)
async def image_proxy(url: str = Query(...)) -> Response:
    started = time.monotonic()
    try:
        result = await coordinator.fetch(url)
    except image_cache.CoverFetchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    data, content_type = await asyncio.to_thread(
        _resize_cover_for_mobile,
        result.data,
        result.content_type,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not result.cache_hit and elapsed_ms > 800:
        logger.debug(
            "image-service MISS elapsed_ms=%d host=%s",
            elapsed_ms,
            url[:64],
        )
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "X-Image-Cache": "hit" if result.cache_hit else "miss",
        },
    )


def main() -> None:
    port = int(os.environ.get("OPENBILICLAW_IMAGE_SERVICE_PORT", "8421"))
    host = os.environ.get("OPENBILICLAW_IMAGE_SERVICE_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
