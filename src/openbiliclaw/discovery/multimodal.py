"""Cover-image preparation for multimodal discovery evaluation."""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError

from openbiliclaw.runtime.image_cache import CoverFetchError, get_or_fetch_cover_bytes

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedCoverImage:
    """A compressed cover image ready for an image-capable LLM message."""

    content_id: str
    data_url: str
    mime_type: str = "image/jpeg"

    def to_llm_input(self) -> dict[str, str]:
        return {
            "content_id": self.content_id,
            "data_url": self.data_url,
            "mime_type": self.mime_type,
        }


def _coerce_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _compress_cover_image(data: bytes, *, max_px: int, quality: int) -> bytes:
    with Image.open(BytesIO(data)) as image:
        rgb = _coerce_rgb(image)
        rgb.thumbnail(
            (max(1, int(max_px)), max(1, int(max_px))),
            Image.Resampling.LANCZOS,
        )
        output = BytesIO()
        rgb.save(
            output,
            format="JPEG",
            quality=max(1, min(95, int(quality))),
            optimize=True,
        )
        return output.getvalue()


async def prepare_cover_image_input(
    *,
    content_id: str,
    cover_url: str,
    max_px: int,
    quality: int,
    timeout_seconds: int,
) -> PreparedCoverImage | None:
    """Fetch, resize, JPEG-compress, and base64-encode one cover image."""
    url = (cover_url or "").strip()
    cid = (content_id or "").strip()
    if not url or not cid:
        return None

    try:
        async with asyncio.timeout(max(1, int(timeout_seconds))):
            data, _content_type = await get_or_fetch_cover_bytes(url)
        compressed = _compress_cover_image(data, max_px=max_px, quality=quality)
    except (
        CoverFetchError,
        OSError,
        UnidentifiedImageError,
        ValueError,
        TimeoutError,
    ) as exc:
        logger.info("Skipping cover image for %s: %s", cid, exc)
        return None

    encoded = base64.b64encode(compressed).decode("ascii")
    return PreparedCoverImage(
        content_id=cid,
        data_url=f"data:image/jpeg;base64,{encoded}",
    )


async def prepare_cover_image_inputs(
    contents: list[DiscoveredContent],
    *,
    max_px: int,
    quality: int,
    timeout_seconds: int,
) -> list[PreparedCoverImage]:
    """Prepare every available cover image in a batch."""
    tasks = [
        prepare_cover_image_input(
            content_id=str(content.content_id or content.bvid or ""),
            cover_url=content.cover_url,
            max_px=max_px,
            quality=quality,
            timeout_seconds=timeout_seconds,
        )
        for content in contents
        if (content.cover_url or "").strip()
    ]
    if not tasks:
        return []
    prepared = await asyncio.gather(*tasks)
    return [item for item in prepared if item is not None]
