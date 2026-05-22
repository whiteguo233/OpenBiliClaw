#!/usr/bin/env python3
"""Regenerate all OpenBiliClaw PNG icons from the parametric design.

The canonical design lives at assets/openbiliclaw-icon.svg.  Both this
script and that file encode the same geometry; the SVG is the human
spec and this script renders it at native resolution for every PNG
target. We render natively (no upscaling) so the @2x and 1024px slots
stay crisp.

Targets:
    extension/icons/icon{16,48,128}.png         — WebExtension icons
    safari/Extension/Resources/icons/icon{16,48,128}.png
        — same files, after they've been copied into the Xcode project
    safari/App/Assets.xcassets/AppIcon.appiconset/icon_*.png
        — host app icon set (10 sizes from 16 through 1024)

Run from the repo root:
    python3 scripts/regenerate_icons.py
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw

# ── Design parameters (1024×1024 reference frame) ────────────────────
# These are the canonical coordinates; everything else is scaled.

REFERENCE = 1024.0

# Background (rounded-square / squircle approximation)
BG_COLOR = (255, 237, 241, 255)
BG_RADIUS_RATIO = 228 / REFERENCE  # corner radius / size

# Pink open ring
RING_COLOR = (251, 114, 153, 255)
RING_CENTER = (512 / REFERENCE, 544 / REFERENCE)
RING_RADIUS = 192 / REFERENCE
RING_STROKE = 40 / REFERENCE
# Visible arc: from this angle, sweeping by this many degrees, clockwise.
# Angles measured from the +x axis, going counter-clockwise (standard math).
# We want a gap in the upper-right; the visible arc covers ~290°.
RING_ARC_START = -110  # degrees
RING_ARC_SWEEP = 290   # degrees

# Blue accent dot
DOT_COLOR = (80, 160, 240, 255)
DOT_CENTER = (696 / REFERENCE, 344 / REFERENCE)
DOT_RADIUS = 74 / REFERENCE


def render(size: int) -> Image.Image:
    """Render the icon at the given size in pixels.

    We render into a buffer 4× the target size for anti-aliasing, then
    downscale with LANCZOS. The native-resolution geometry means we
    never upscale a low-res raster master.
    """
    scale = 4
    work = size * scale
    img = Image.new("RGBA", (work, work), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Background ──
    radius = int(BG_RADIUS_RATIO * work)
    draw.rounded_rectangle(
        (0, 0, work - 1, work - 1),
        radius=radius,
        fill=BG_COLOR,
    )

    # ── Pink ring (drawn as a thick arc with rounded caps) ──
    cx = RING_CENTER[0] * work
    cy = RING_CENTER[1] * work
    r = RING_RADIUS * work
    stroke = RING_STROKE * work

    # PIL's draw.arc doesn't support stroke-linecap=round, so we draw
    # the arc plus two circles at the endpoints for the cap effect.
    bbox = (cx - r, cy - r, cx + r, cy + r)
    # PIL angles: 0 = +x axis, going clockwise (180 = -x, 90 = +y down)
    # Our RING_ARC_START is also in that frame.
    draw.arc(
        bbox,
        start=RING_ARC_START,
        end=RING_ARC_START + RING_ARC_SWEEP,
        fill=RING_COLOR,
        width=int(stroke),
    )
    # Round caps: draw filled circles at each endpoint.
    for angle_deg in (RING_ARC_START, RING_ARC_START + RING_ARC_SWEEP):
        a = math.radians(angle_deg)
        ex = cx + r * math.cos(a)
        ey = cy + r * math.sin(a)
        rr = stroke / 2
        draw.ellipse(
            (ex - rr, ey - rr, ex + rr, ey + rr),
            fill=RING_COLOR,
        )

    # ── Blue accent dot ──
    dcx = DOT_CENTER[0] * work
    dcy = DOT_CENTER[1] * work
    dr = DOT_RADIUS * work
    draw.ellipse(
        (dcx - dr, dcy - dr, dcx + dr, dcy + dr),
        fill=DOT_COLOR,
    )

    return img.resize((size, size), Image.LANCZOS)


# ── Output targets ───────────────────────────────────────────────────

WEBEXTENSION_SIZES = [16, 48, 128]

# (filename, pixel size). Matches AppIcon.appiconset/Contents.json.
APPICON_SLOTS = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def main(repo_root: Path) -> int:
    extension_icons = repo_root / "extension" / "icons"
    safari_ext_icons = repo_root / "safari" / "Extension" / "Resources" / "icons"
    appiconset = repo_root / "safari" / "App" / "Assets.xcassets" / "AppIcon.appiconset"

    # Cache renders so the same size isn't computed twice.
    cache: dict[int, Image.Image] = {}

    def get(size: int) -> Image.Image:
        if size not in cache:
            cache[size] = render(size)
        return cache[size]

    # WebExtension icons (shared by the source folder and the Safari
    # extension's bundled copy).
    for size in WEBEXTENSION_SIZES:
        name = f"icon{size}.png"
        for out_dir in (extension_icons, safari_ext_icons):
            if not out_dir.exists():
                continue
            out_path = out_dir / name
            get(size).save(out_path, "PNG", optimize=True)
            print(f"  wrote {out_path.relative_to(repo_root)} ({size}×{size})")

    # macOS host app icon set.
    if appiconset.exists():
        for filename, size in APPICON_SLOTS:
            out_path = appiconset / filename
            get(size).save(out_path, "PNG", optimize=True)
            print(f"  wrote {out_path.relative_to(repo_root)} ({size}×{size})")

    return 0


if __name__ == "__main__":
    here = Path(__file__).resolve()
    # script lives at <repo>/scripts/regenerate_icons.py
    root = here.parent.parent
    sys.exit(main(root))
