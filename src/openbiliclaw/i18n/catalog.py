"""Message catalogs and the translator used by Python-side surfaces.

Two catalog families live next to this module:

``catalogs/<locale>.json``
    Semantic messages used by the API, CLI, and prompt layer. Keys are stable
    dotted identifiers (``cli.app.help``); values are the localized text.
    ``zh-Hans`` is the source catalog: every other locale must cover the same
    keys, which the test suite enforces.

``ui/<locale>.json``
    Client-side UI strings for the web surfaces and the extension. Keys are the
    Simplified Chinese source text itself, because the browser runtime
    translates rendered DOM text by exact match — see
    ``src/openbiliclaw/web/shared/i18n.js``. That keeps retrofitting a 70k-line
    vanilla-JS codebase tractable without inventing thousands of key names.

Lookup always walks the locale chain from :func:`openbiliclaw.i18n.locales.locale_chain`,
so a partially translated locale degrades to the Chinese source instead of
rendering empty strings.
"""

# [INPUT]: locale id、消息 key、插值参数、catalogs/ 下的 JSON 目录
# [OUTPUT]: Translator（按 locale 绑定）与 translate() 顶层函数
# [POS]: Python 侧（API / CLI / prompt）统一的消息查表与回退实现
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from .locales import DEFAULT_LOCALE, locale_chain, normalize_locale

logger = logging.getLogger(__name__)

CATALOG_DIR = Path(__file__).resolve().parent / "catalogs"
UI_CATALOG_DIR = Path(__file__).resolve().parent / "ui"

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_catalog_lock = threading.Lock()


class CatalogError(RuntimeError):
    """Raised when a shipped catalog file is missing or malformed."""


def _catalog_path(locale: str) -> Path:
    return CATALOG_DIR / f"{locale}.json"


def _read_catalog(locale: str) -> dict[str, str]:
    path = _catalog_path(locale)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"missing message catalog: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid message catalog {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise CatalogError(f"message catalog {path} must be a JSON object")
    messages = raw.get("messages", raw)
    if not isinstance(messages, Mapping):
        raise CatalogError(f"message catalog {path}: 'messages' must be an object")
    cleaned: dict[str, str] = {}
    for key, value in messages.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise CatalogError(f"message catalog {path}: entries must be string pairs")
        if not value.strip():
            raise CatalogError(f"message catalog {path}: empty value for {key!r}")
        cleaned[key] = value
    return cleaned


@lru_cache(maxsize=16)
def load_messages(locale: str) -> Mapping[str, str]:
    """Load one locale's semantic catalog (cached, validated)."""

    resolved = normalize_locale(locale)
    path = _catalog_path(resolved)
    if not path.is_file():
        # A locale can be supported without a semantic catalog yet (the UI
        # catalog still covers clients). Only the default catalog is required.
        if resolved == DEFAULT_LOCALE:
            raise CatalogError(f"missing default message catalog: {path}")
        return {}
    return _read_catalog(resolved)


def catalog_locales() -> tuple[str, ...]:
    """Return locales that actually ship a semantic catalog file."""

    if not CATALOG_DIR.is_dir():
        return ()
    return tuple(sorted(path.stem for path in CATALOG_DIR.glob("*.json")))


def ui_catalog_locales() -> tuple[str, ...]:
    """Return locales that actually ship a client UI catalog file."""

    if not UI_CATALOG_DIR.is_dir():
        return ()
    return tuple(sorted(path.stem for path in UI_CATALOG_DIR.glob("*.json")))


def _interpolate(template: str, params: Mapping[str, Any]) -> str:
    if not params:
        return template

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            return match.group(0)
        return str(params[name])

    return _PLACEHOLDER.sub(replace, template)


def lookup(key: str, locale: str | None = None) -> str | None:
    """Return the first catalog hit for ``key`` along the fallback chain."""

    for candidate in locale_chain(locale or DEFAULT_LOCALE):
        messages = load_messages(candidate)
        value = messages.get(key)
        if value is not None:
            return value
    return None


def has_message(key: str, locale: str | None = None) -> bool:
    return lookup(key, locale) is not None


class Translator:
    """Locale-bound message lookup with ``{param}`` interpolation.

    Translators are cheap: catalogs are cached per locale process-wide, and
    every lookup walks the fallback chain without raising. A missing key
    returns the key itself so the gap is visible in output and easy to grep,
    rather than silently blanking a label.
    """

    __slots__ = ("locale",)

    def __init__(self, locale: str | None = None) -> None:
        self.locale = normalize_locale(locale)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Translator({self.locale!r})"

    def __call__(self, key: str, /, **params: Any) -> str:
        """Translate ``key``; never raises for a missing entry."""

        template = lookup(key, self.locale)
        if template is None:
            logger.debug("untranslated message key %r for locale %s", key, self.locale)
            return key
        return _interpolate(template, params)

    def text(self, key: str, /, **params: Any) -> str:
        return self(key, **params)

    def has(self, key: str) -> bool:
        return has_message(key, self.locale)

    def with_locale(self, locale: str) -> Translator:
        return Translator(locale)


@lru_cache(maxsize=32)
def _cached_translator(locale: str) -> Translator:
    return Translator(locale)


def get_translator(locale: str | None = None) -> Translator:
    """Return a cached translator for ``locale`` (defaults to zh-Hans)."""

    return _cached_translator(normalize_locale(locale))


def translate(key: str, /, *, locale: str | None = None, **params: Any) -> str:
    """One-shot convenience wrapper around :class:`Translator`."""

    return get_translator(locale)(key, **params)


def iter_message_keys(locale: str | None = None) -> Iterable[str]:
    """Yield every semantic key known in ``locale``'s catalog."""

    yield from sorted(load_messages(normalize_locale(locale)).keys())


def invalidate_catalogs() -> None:
    """Drop cached catalogs (tests and dev reload only)."""

    with _catalog_lock:
        load_messages.cache_clear()
        _cached_translator.cache_clear()
