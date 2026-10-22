"""Server-side QR rendering for the Blue Fish login panel.

B站's web login flow already hands back a login URL; this module turns it into
a ``data:`` URL SVG so the desktop and popup panels can show it with a plain
``<img>``. Keeping the encoder on the backend means:

* no JavaScript QR library has to be bundled or kept in sync;
* the QR never leaves the local API — no third-party image service;
* the panels only need to set ``img.src``.

``qrcode`` is imported lazily: the lab is optional and the rest of the daemon
should not pay for it at import time.
"""

from __future__ import annotations

import base64
import io


def qr_svg_data_url(value: str) -> str:
    """Render ``value`` as a scannable SVG QR code wrapped in a data URL."""

    import qrcode
    import qrcode.image.svg

    image = qrcode.make(str(value), image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
