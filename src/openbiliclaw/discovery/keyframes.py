"""Video keyframe extraction from Bilibili's pre-generated videoshot sprites.

Covers are UP-chosen marketing images and are frequently clickbait — they do
not represent what a video actually looks like. Keyframes do.

Bilibili already generates keyframe sprite sheets for every video (the
thumbnails shown when hovering the progress bar) and exposes them through an
unauthenticated endpoint::

    GET https://api.bilibili.com/x/player/videoshot?bvid=...&index=1

So extracting keyframes costs **one small JPEG download**, not a video download
plus ffmpeg. Measured on 30 real videos across 5 categories (45s–5106s): 100%
coverage, ~277 frames per video, sprite sheets 61 KB and up.

Two properties of the real responses drive this module's shape:

1. **Long videos return MULTIPLE sprite sheets** (up to 11 observed = 1100
   frames). Sampling only ``image[0]`` would cover just the opening minutes, so
   sampling is global across the concatenated frame sequence.
2. **Tile size is not fixed** — both 160x90 and 480x270 were observed in the
   wild. Always read ``img_x_size`` / ``img_y_size`` from the response rather
   than hardcoding.

Everything here is best-effort: any failure yields an empty result rather than
raising, because keyframes are an optional enrichment signal and must never
break discovery or the recommendation prewarm that calls into them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from io import BytesIO

import httpx
from PIL import Image, UnidentifiedImageError

from openbiliclaw.discovery.multimodal import _coerce_rgb
from openbiliclaw.runtime.image_cache import CoverFetchError, get_or_fetch_cover_bytes

logger = logging.getLogger(__name__)

_VIDEOSHOT_URL = "https://api.bilibili.com/x/player/videoshot"

# Frames sampled per video. Kept small: each frame is a separate embedding call,
# and adjacent keyframes in a sprite sheet are highly redundant. 4 spreads
# across the body of the video without paying for near-duplicates.
DEFAULT_MAX_FRAMES = 4

# Skip this fraction of the video at each end when sampling. Bilibili openings
# (片头) and endings (片尾/三连引导) are visually unrepresentative of content,
# and they are exactly where the first/last sprite tiles land.
_EDGE_SKIP_RATIO = 0.10

# Sprite sheets are larger than covers but still small; a hard ceiling keeps a
# malformed/huge response from stalling the prewarm loop.
_FETCH_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class VideoshotMeta:
    """Parsed videoshot response: sprite URLs plus the tile grid geometry."""

    image_urls: tuple[str, ...] = ()
    grid_x: int = 0
    grid_y: int = 0
    tile_width: int = 0
    tile_height: int = 0
    index: tuple[int, ...] = field(default=())

    @property
    def frames_per_sprite(self) -> int:
        return max(0, self.grid_x) * max(0, self.grid_y)

    @property
    def total_frames(self) -> int:
        return self.frames_per_sprite * len(self.image_urls)

    def is_usable(self) -> bool:
        return bool(
            self.image_urls
            and self.grid_x > 0
            and self.grid_y > 0
            and self.tile_width > 0
            and self.tile_height > 0
        )


def _complete_url(raw: str) -> str:
    """Videoshot URLs come back protocol-relative (``//i0.hdslb.com/...``)."""
    url = (raw or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://"):
        # CN CDNs serve https fine; prefer it so the fetch stays consistent
        # with the image-cache whitelist behaviour.
        return "https://" + url[len("http://") :]
    return url


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_videoshot_payload(payload: object) -> VideoshotMeta | None:
    """Parse a videoshot API payload into :class:`VideoshotMeta`.

    Returns ``None`` when the response is an error, malformed, or carries no
    usable sprite geometry. Pure function — unit-testable without network.
    """
    if not isinstance(payload, dict):
        return None
    if _as_int(payload.get("code"), default=-1) != 0:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    raw_images = data.get("image")
    if not isinstance(raw_images, list):
        return None
    urls = tuple(u for u in (_complete_url(str(item)) for item in raw_images) if u)
    if not urls:
        return None

    raw_index = data.get("index")
    index: tuple[int, ...] = ()
    if isinstance(raw_index, list):
        index = tuple(_as_int(item) for item in raw_index)

    meta = VideoshotMeta(
        image_urls=urls,
        grid_x=_as_int(data.get("img_x_len")),
        grid_y=_as_int(data.get("img_y_len")),
        tile_width=_as_int(data.get("img_x_size")),
        tile_height=_as_int(data.get("img_y_size")),
        index=index,
    )
    return meta if meta.is_usable() else None


async def fetch_videoshot_meta(
    bvid: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = _FETCH_TIMEOUT_SECONDS,
) -> VideoshotMeta | None:
    """Fetch and parse the videoshot metadata for one video.

    The endpoint needs no credentials and no WBI signing. Returns ``None`` on
    any failure (network, non-zero code, malformed payload) — never raises.
    """
    bv = (bvid or "").strip()
    if not bv:
        return None

    owned_client: httpx.AsyncClient | None = None
    try:
        if client is None:
            # api.bilibili.com is a CN endpoint: never inherit an env/system
            # proxy (pitfall rule 1) — an overseas exit IP gets risk-controlled.
            owned_client = httpx.AsyncClient(
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.bilibili.com",
                },
                timeout=timeout_seconds,
                trust_env=False,
            )
            http = owned_client
        else:
            http = client

        response = await http.get(
            _VIDEOSHOT_URL,
            params={"bvid": bv, "index": 1},
        )
        if response.status_code >= 400:
            logger.debug("videoshot HTTP %s for %s", response.status_code, bv)
            return None
        return parse_videoshot_payload(response.json())
    except Exception:
        logger.debug("videoshot fetch failed for %s", bv, exc_info=True)
        return None
    finally:
        if owned_client is not None:
            await owned_client.aclose()


def select_frame_positions(total_frames: int, max_frames: int) -> list[int]:
    """Pick evenly-spread frame indices, skipping the opening/closing edges.

    Openings and endings are visually unrepresentative, and they are exactly
    where the first and last tiles land. Returns an ascending, deduped list.
    """
    total = max(0, int(total_frames))
    want = max(0, int(max_frames))
    if total <= 0 or want <= 0:
        return []
    if total <= want:
        return list(range(total))

    lo = int(total * _EDGE_SKIP_RATIO)
    hi = total - 1 - int(total * _EDGE_SKIP_RATIO)
    if hi <= lo:
        lo, hi = 0, total - 1

    span = hi - lo
    positions: list[int] = []
    for i in range(want):
        # Sample at the midpoint of each of `want` equal buckets so the picks
        # stay clear of the (already trimmed) boundaries.
        offset = span * (2 * i + 1) // (2 * want)
        positions.append(lo + offset)

    deduped = sorted(set(positions))
    return [p for p in deduped if 0 <= p < total]


def crop_frames_from_sprite(
    sprite_bytes: bytes,
    meta: VideoshotMeta,
    local_positions: list[int],
    *,
    quality: int = 72,
) -> list[bytes]:
    """Crop the given tile positions out of one sprite sheet, as JPEG bytes.

    ``local_positions`` are frame indices *within this sprite* (row-major).
    Tiles that fall outside the actual image are skipped rather than raising —
    real sprite sheets are sometimes short on the final row.
    """
    if not sprite_bytes or not local_positions or not meta.is_usable():
        return []

    out: list[bytes] = []
    try:
        with Image.open(BytesIO(sprite_bytes)) as sheet:
            rgb = _coerce_rgb(sheet)
            sheet_w, sheet_h = rgb.size
            for pos in local_positions:
                col = pos % meta.grid_x
                row = pos // meta.grid_x
                left = col * meta.tile_width
                upper = row * meta.tile_height
                right = left + meta.tile_width
                lower = upper + meta.tile_height
                if right > sheet_w or lower > sheet_h:
                    # Trailing tiles on a partially-filled sheet.
                    continue
                tile = rgb.crop((left, upper, right, lower))
                buffer = BytesIO()
                tile.save(
                    buffer,
                    format="JPEG",
                    quality=max(1, min(95, int(quality))),
                    optimize=True,
                )
                encoded = buffer.getvalue()
                if encoded:
                    out.append(encoded)
    except (OSError, UnidentifiedImageError, ValueError):
        logger.debug("sprite crop failed", exc_info=True)
        return out
    return out


async def fetch_keyframes(
    bvid: str,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    quality: int = 72,
    client: httpx.AsyncClient | None = None,
) -> list[bytes]:
    """Fetch up to *max_frames* representative keyframes for a video.

    Returns JPEG bytes per frame, or ``[]`` when the video has no videoshot
    data or anything fails. Sampling is global across all sprite sheets, so a
    long video is covered end-to-end rather than only its opening.

    Only the sprite sheets that actually contain a sampled frame are
    downloaded — a 5000s video has 11 sheets but 4 frames touch at most 4.
    """
    meta = await fetch_videoshot_meta(bvid, client=client)
    if meta is None or not meta.is_usable():
        return []

    positions = select_frame_positions(meta.total_frames, max_frames)
    if not positions:
        return []

    per_sheet = meta.frames_per_sprite
    if per_sheet <= 0:
        return []

    # Group the globally-sampled positions by which sprite sheet holds them,
    # so each needed sheet is downloaded exactly once.
    by_sheet: dict[int, list[int]] = {}
    for pos in positions:
        sheet_idx = pos // per_sheet
        if sheet_idx >= len(meta.image_urls):
            continue
        by_sheet.setdefault(sheet_idx, []).append(pos % per_sheet)

    async def _frames_for_sheet(sheet_idx: int, local: list[int]) -> list[bytes]:
        url = meta.image_urls[sheet_idx]
        try:
            # Reuses the shared image cache: hdslb.com is already whitelisted
            # and pinned to a direct (non-proxied) fetch for CN CDNs.
            sprite_bytes, _content_type = await get_or_fetch_cover_bytes(url)
        except (CoverFetchError, OSError, ValueError):
            logger.debug("sprite fetch failed: %s", url[:100], exc_info=True)
            return []
        return await asyncio.to_thread(
            crop_frames_from_sprite, sprite_bytes, meta, local, quality=quality
        )

    results = await asyncio.gather(
        *(_frames_for_sheet(idx, local) for idx, local in sorted(by_sheet.items())),
        return_exceptions=True,
    )

    frames: list[bytes] = []
    for outcome in results:
        if isinstance(outcome, BaseException):
            logger.debug("keyframe sheet task failed: %s", type(outcome).__name__)
            continue
        frames.extend(outcome)
    return frames[:max_frames]
