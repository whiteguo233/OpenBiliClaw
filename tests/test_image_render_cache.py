"""Repeated HTTP image hits must not decode/resize the same original again."""

from io import BytesIO
from unittest.mock import patch

from PIL import Image

from openbiliclaw.image_service import _resize_cover_for_mobile


def test_repeat_cover_render_decodes_only_once() -> None:
    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), (10, 95, 42)).save(buffer, format="PNG")
    data = buffer.getvalue()
    with patch.object(Image, "open", wraps=Image.open) as decode:
        first = _resize_cover_for_mobile(data, "image/png")
        second = _resize_cover_for_mobile(data, "image/png")
    assert first == second
    assert decode.call_count == 1
    assert first[1] == "image/jpeg"
    with Image.open(BytesIO(first[0])) as output:
        assert output.size == (640, 360)


def test_changed_source_bytes_do_not_reuse_old_render() -> None:
    outputs = []
    for color in [(33, 10, 100), (100, 33, 10)]:
        source = BytesIO()
        Image.new("RGB", (1280, 720), color).save(source, format="PNG")
        outputs.append(_resize_cover_for_mobile(source.getvalue(), "image/png"))
    assert outputs[0][0] != outputs[1][0]


def test_render_cache_is_bounded_and_evicts_oldest(monkeypatch) -> None:
    from collections import OrderedDict

    from openbiliclaw.runtime import image_cache

    monkeypatch.setattr(image_cache, "_render_cache", OrderedDict())
    monkeypatch.setattr(image_cache, "_RENDER_CACHE_MAX_ENTRIES", 1)
    sources = []
    for color in [(20, 40, 80), (80, 40, 20)]:
        source = BytesIO()
        Image.new("RGB", (1280, 720), color).save(source, format="PNG")
        sources.append(source.getvalue())
    with patch.object(Image, "open", wraps=Image.open) as decode:
        for data in [sources[0], sources[1], sources[0]]:
            _resize_cover_for_mobile(data, "image/png")
    assert decode.call_count == 3
    assert len(image_cache._render_cache) == 1
    image_cache._render_cache.clear()
    monkeypatch.setattr(image_cache, "_RENDER_CACHE_MAX_BYTES", 1)
    _resize_cover_for_mobile(sources[1], "image/png")
    assert not image_cache._render_cache  # oversize entry was not admitted


def test_invalid_image_and_small_cover_preserve_original() -> None:
    assert _resize_cover_for_mobile(b"not an image", "image/jpeg") == (
        b"not an image",
        "image/jpeg",
    )
    source = BytesIO()
    Image.new("RGB", (100, 50)).save(source, format="PNG")
    assert _resize_cover_for_mobile(source.getvalue(), "image/png") == (
        source.getvalue(),
        "image/png",
    )
