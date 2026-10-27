"""The locale contract for every OpenBiliClaw surface.

One canonical vocabulary, one fallback order. Everything else in the
multilingual feature (catalogs, API payloads, prompt directives, the browser
runtime) reads its locale values from here, so adding Japanese or Korean later
means adding a locale id plus a catalog — not touching every surface.

Canonical ids are BCP-47 script-qualified:

* ``zh-Hans`` — Simplified Chinese (the product default, and what the legacy
  ``zh`` config value means).
* ``zh-Hant`` — Traditional Chinese with Taiwan vocabulary (``zh-TW``,
  ``zh-HK``, and ``zh-MO`` all resolve here for the first release).
* ``en`` — English.

The config field stays a free-form string on disk (older installs may contain
``zh`` or ``en-US``); it is resolved to a canonical id at the i18n boundary by
:func:`resolve_locale`, never silently mutated during load.
"""

# [INPUT]: 配置里的 language 字符串、HTTP Accept-Language、前端 locale 选择
# [OUTPUT]: 规范 locale、回退链、展示名、HTML lang、可选语言清单
# [POS]: 多语言能力的唯一 locale 口径，catalog / API / CLI / 前端 / prompt 都从这里取
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

DEFAULT_LOCALE = "zh-Hans"
SUPPORTED_LOCALES: tuple[str, ...] = ("zh-Hans", "zh-Hant", "en")

# Base language -> the variant new users get when no script/region is given.
_BASE_LOCALES: Mapping[str, str] = {
    "zh": DEFAULT_LOCALE,
    "en": "en",
}

# Region/script aliases seen in the wild: browser tags, OS settings, and the
# values earlier OpenBiliClaw releases wrote into ``[general].language``.
_ALIASES: Mapping[str, str] = {
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-my": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh-hans-cn": "zh-Hans",
    "zh-hans-sg": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh-mo": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh-hant-tw": "zh-Hant",
    "zh-hant-hk": "zh-Hant",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "en-au": "en",
    "en-ca": "en",
    "en-nz": "en",
    "en-ie": "en",
    "en-sg": "en",
}

# Human-facing names are deliberately *native* (each language names itself), so
# the picker reads the same no matter which locale is currently active.
_NATIVE_NAMES: Mapping[str, str] = {
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
    "en": "English",
}

_ENGLISH_NAMES: Mapping[str, str] = {
    "zh-Hans": "Simplified Chinese",
    "zh-Hant": "Traditional Chinese",
    "en": "English",
}

# Prompt-facing descriptions: models localize better when told the variant and
# vocabulary convention, not just the bare tag.
_MODEL_NAMES: Mapping[str, str] = {
    "zh-Hans": "Simplified Chinese (简体中文, mainland China usage)",
    "zh-Hant": "Traditional Chinese (繁體中文, Taiwan vocabulary and usage)",
    "en": "English (natural conversational English)",
}


@dataclass(frozen=True)
class LocaleOption:
    """One selectable locale as the API and pickers expose it."""

    id: str
    native_name: str
    english_name: str
    html_lang: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "native_name": self.native_name,
            "english_name": self.english_name,
            "html_lang": self.html_lang,
        }


def _clean(value: object) -> str:
    """Lowercase a tag and normalize ``zh_TW`` / ``zh TW`` to ``zh-tw``."""

    return str(value or "").strip().lower().replace("_", "-").replace(" ", "-")


def resolve_locale(value: object) -> str | None:
    """Return the canonical locale for ``value``, or ``None`` if unsupported.

    Accepts canonical ids, legacy ``zh``/``en`` values, browser-style regional
    tags (``zh-TW``, ``en-US``), and script-qualified tags. Never guesses past
    the documented aliases: an unknown tag is *not* silently treated as the
    default, because callers need to distinguish "fall back" from "asked for
    something we do not ship".
    """

    tag = _clean(value)
    if not tag:
        return None
    alias = _ALIASES.get(tag)
    if alias is not None:
        return alias
    base, _, rest = tag.partition("-")
    if not rest:
        return _BASE_LOCALES.get(base)
    # Unknown region for a known language: keep the script when it is
    # explicit (zh-hant-xx), otherwise fall back to the language's default
    # variant. This keeps ``zh-CN``-style tags working without a table entry
    # for every region.
    if base == "zh":
        if "hant" in rest.split("-") or {"tw", "hk", "mo"} & set(rest.split("-")):
            return "zh-Hant"
        if "hans" in rest.split("-"):
            return "zh-Hans"
        return DEFAULT_LOCALE
    if base == "en":
        return "en"
    return None


def normalize_locale(value: object, *, default: str = DEFAULT_LOCALE) -> str:
    """Resolve ``value`` to a supported locale, falling back to ``default``."""

    resolved = resolve_locale(value)
    if resolved is not None:
        return resolved
    fallback = resolve_locale(default)
    return fallback if fallback is not None else DEFAULT_LOCALE


def is_supported_locale(value: object) -> bool:
    return resolve_locale(value) is not None


def base_language(locale: str) -> str:
    """Return the base language subtag (``zh-Hant`` -> ``zh``)."""

    return normalize_locale(locale).split("-", 1)[0]


def locale_chain(locale: object) -> tuple[str, ...]:
    """Return the catalog lookup order for ``locale``, most specific first.

    A Traditional Chinese reader gets Traditional, then the shared Chinese
    source strings, then English as the last-resort shipped catalog. English
    readers fall back straight to the Chinese source, which is always complete
    because it *is* the source.
    """

    resolved = normalize_locale(locale)
    ordered: list[str] = [resolved]
    base = resolved.split("-", 1)[0]
    base_default = _BASE_LOCALES.get(base)
    if base_default is not None:
        ordered.append(base_default)
    ordered.append(DEFAULT_LOCALE)
    ordered.append("en")
    seen: list[str] = []
    for candidate in ordered:
        if candidate not in seen:
            seen.append(candidate)
    return tuple(seen)


def html_lang(locale: object) -> str:
    """Return the BCP-47 value for ``<html lang>`` and ``lang`` attributes."""

    return normalize_locale(locale)


def native_name(locale: object) -> str:
    return _NATIVE_NAMES.get(normalize_locale(locale), DEFAULT_LOCALE)


def english_name(locale: object) -> str:
    return _ENGLISH_NAMES.get(normalize_locale(locale), "Simplified Chinese")


def model_name(locale: object) -> str:
    """Describe a locale to an LLM inside an output-language directive."""

    return _MODEL_NAMES.get(normalize_locale(locale), _MODEL_NAMES[DEFAULT_LOCALE])


def supported_locales() -> tuple[str, ...]:
    return SUPPORTED_LOCALES


def locale_options() -> tuple[LocaleOption, ...]:
    return tuple(
        LocaleOption(
            id=locale,
            native_name=_NATIVE_NAMES[locale],
            english_name=_ENGLISH_NAMES[locale],
            html_lang=locale,
        )
        for locale in SUPPORTED_LOCALES
    )


def iter_locales() -> Iterator[str]:
    yield from SUPPORTED_LOCALES
