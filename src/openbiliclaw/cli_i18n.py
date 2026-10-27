"""CLI locale resolution and translated copy.

The CLI is the one surface whose Typer command tree is built at import time,
before a config file has been read, so command/option help strings stay in the
source language. Localized runtime output uses this module; legacy commands
still contain source-language copy:

* ``OPENBILICLAW_LANG`` (or ``OPENBILICLAW_LANGUAGE``) wins — handy for scripts,
  tests, and trying a language without touching config.toml.
* Otherwise ``[general].language`` decides, resolved through the same canonical
  locale contract as the web and extension surfaces.
* If the config cannot be read (first run, broken file), the CLI falls back to
  Simplified Chinese rather than failing because localization is unavailable.
"""

# [INPUT]: OPENBILICLAW_LANG 环境变量 / Config.language
# [OUTPUT]: 进程内缓存的 Translator、locale 展示名与持久化语言变更后的缓存失效
# [POS]: CLI 输出语言的唯一解析入口（Typer 静态 help 除外）
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

from __future__ import annotations

import os

from openbiliclaw.i18n import (
    DEFAULT_LOCALE,
    Translator,
    get_translator,
    native_name,
    normalize_locale,
)

LANGUAGE_ENV_VARS = ("OPENBILICLAW_LANG", "OPENBILICLAW_LANGUAGE")

_cached_locale: str | None = None


def reset_cli_locale_cache() -> None:
    """Refresh subsequent CLI translations after a persisted language change."""
    global _cached_locale
    _cached_locale = None


def resolve_cli_locale(config: object | None = None) -> str:
    """Resolve the CLI locale: env override, then config, then the default."""

    global _cached_locale
    for name in LANGUAGE_ENV_VARS:
        raw = os.environ.get(name)
        if raw and raw.strip():
            return normalize_locale(raw)
    if config is not None:
        return normalize_locale(getattr(config, "language", None))
    if _cached_locale is not None:
        return _cached_locale
    try:
        from openbiliclaw.config import load_config

        _cached_locale = normalize_locale(load_config().language)
    except Exception:
        _cached_locale = DEFAULT_LOCALE
    return _cached_locale


def cli_translator(config: object | None = None) -> Translator:
    """Return a translator bound to the CLI's effective locale."""

    return get_translator(resolve_cli_locale(config))


def locale_display(locale: str | None) -> str:
    """Return ``简体中文（zh-Hans）``-style labels for the current locale."""

    resolved = normalize_locale(locale)
    return f"{native_name(resolved)}（{resolved}）"
