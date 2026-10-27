#!/usr/bin/env python3
"""Build the client-side UI catalogs for the web apps and the extension.

The web surfaces and the extension are large vanilla-JS/HTML apps that were
written with Simplified Chinese copy inline. Rather than invent thousands of
message keys and rewrite every call site, the browser runtime
(``src/openbiliclaw/web/shared/i18n.js``) localizes *rendered DOM text* by exact
match against ``src/openbiliclaw/i18n/ui/<locale>.json``, where the key is the
Simplified Chinese source string.

This script keeps that catalog honest:

1. Scan the templates and scripts for Chinese UI strings.
2. Write ``ui/zh-Hans.json`` — the inventory (source text is the key).
3. Regenerate ``ui/zh-Hant.json`` with OpenCC's ``s2twp`` profile (Taiwan
   vocabulary), so Traditional coverage is complete and machine-checkable.
4. Leave ``ui/en.json`` untouched (it is human/AI-authored) and report which
   inventory strings still lack an English translation.

Run from the repository root:

    python scripts/build_i18n_catalogs.py            # regenerate
    python scripts/build_i18n_catalogs.py --check    # fail if catalogs drift
    python scripts/build_i18n_catalogs.py --report   # list missing English

``opencc-python-reimplemented`` is a dev dependency; the generated catalogs are
committed, so the runtime never imports OpenCC.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "src" / "openbiliclaw" / "web"
UI_CATALOG_DIR = ROOT / "src" / "openbiliclaw" / "i18n" / "ui"
MESSAGE_CATALOG_DIR = ROOT / "src" / "openbiliclaw" / "i18n" / "catalogs"

HTML_SOURCES = (
    WEB_DIR / "desktop" / "index.html",
    WEB_DIR / "setup" / "index.html",
    WEB_DIR / "index.html",
    ROOT / "extension" / "popup" / "popup.html",
)
JS_SOURCES = (
    tuple(sorted(WEB_DIR.rglob("*.js")))
    + tuple(sorted((ROOT / "extension" / "popup").glob("*.js")))
    # The service worker renders badge titles, notifications, and connection
    # copy. Content scripts are deliberately excluded: their Chinese strings
    # are platform DOM selectors/aria labels used for matching, not our copy.
    + tuple(sorted((ROOT / "extension" / "src" / "background").rglob("*.ts")))
)

# Attributes that render as user-visible copy. ``value`` is excluded on
# purpose: in these apps it carries submitted data and option codes, not copy.
TRANSLATABLE_ATTRS = frozenset({"placeholder", "title", "aria-label", "alt"})

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# Three separate patterns instead of one alternation: a template literal may
# contain single/double quotes (`` `<div class="x">…</div>` ``), so a combined
# character class would truncate at the first inner quote and lose the copy.
QUOTED_PATTERNS = (
    re.compile(r"'((?:[^'\\\n]|\\.)*)'"),
    re.compile(r'"((?:[^"\\\n]|\\.)*)"'),
    re.compile(r"`((?:[^`\\]|\\.)*)`", re.DOTALL),
)
SCRIPT_TAG = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
STYLE_TAG = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
WHITESPACE = re.compile(r"\s+")
# Strings longer than this are prose blobs (prompt templates, doc copy), not UI
# labels; translating them by DOM text match cannot work anyway.
MAX_STRING_LEN = 400
# Language-picker labels name each language in its own script on purpose; the
# DOM runtime must never "translate" 简体中文 into 簡體中文 for a zh-Hant reader.
SKIP_TEXTS = frozenset(
    {
        "简体中文",
        "繁體中文",
        "语言 / Language",
        "语言 / 語言 / Language",
        # Template-literal fragment from a CSS/JS expression, not rendered copy.
        "1 时保留候选（非 B 站不降权）。",
    }
)

# Copy that is really code/plumbing even though it contains Chinese.
SKIP_PATTERNS = (
    re.compile(r"^https?://"),
    re.compile(r"^[-a-zA-Z0-9_]+$"),
    re.compile(r"^\s*[.#][\w-]+\s*$"),  # selectors
    re.compile(r"[<>]{2}|=>"),  # code snippets
    # Template literals can span code; these shapes are markup/code fragments,
    # not copy: dangling attribute text, JS statements, string concatenation.
    re.compile(r'\w+="|">|<[a-zA-Z/]'),
    re.compile(r"\bfunction\b|\bconst\b|\breturn\b|\bdocument\."),
    re.compile(r"^[a-z]+, "),
    re.compile(r"[{}]\s*[),;:]|[:=]\s*[\"']"),
    re.compile(r"^[),;:}]|\"\)$"),
)


class _HTMLStringCollector(HTMLParser):
    """Collect visible text nodes and translatable attribute values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.strings: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "pre", "code", "textarea"}:
            self._skip_depth += 1
            return
        for name, value in attrs:
            if name in TRANSLATABLE_ATTRS and value:
                self.strings.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "pre", "code", "textarea"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.strings.append(data)


def _trim_partial_tags(value: str) -> str:
    """Drop attribute tails / tag heads left over from template splitting.

    ``" data-tab="skill">功能角色`` is not copy; ``功能角色`` is. Applied after
    HTML extraction too, because a malformed leading fragment makes the parser
    emit the attribute tail as one data chunk.
    """

    if re.search(r'\w+="[^"]*"\s*>', value):
        value = value.rsplit(">", 1)[1]
    if re.search(r"<[a-zA-Z/][^>]*$", value):
        value = value.rsplit("<", 1)[0]
    return value


def _normalize_source(value: str) -> str:
    """Canonical catalog key for a rendered string (trimmed, single-spaced)."""

    return WHITESPACE.sub(" ", _trim_partial_tags(value)).strip()


def _is_ui_string(value: str) -> bool:
    text = _normalize_source(value)
    if not text or text in SKIP_TEXTS or not CJK.search(text):
        return False
    if len(text) < 2:
        # Single-character fragments come from split string literals, not from
        # rendered labels; translating them would rewrite unrelated text.
        return False
    if len(text) > MAX_STRING_LEN:
        return False
    if text.startswith(("<", ">")):
        # Tag fragments left over from template splitting never match a text node.
        return False
    if any(pattern.search(text) for pattern in SKIP_PATTERNS):
        return False
    # Template literals with interpolation never match rendered text exactly.
    if "${" in text or "%s" in text or "%d" in text:
        return False
    # Leftovers from JS expressions (``` : it.actionLabel)}``) contain an
    # unpaired backtick or a property chain. Paired backticks are legitimate:
    # the copy quotes a CLI command (``run `ollama serve` first``).
    if "`" in text and (text.count("`") % 2 or text.lstrip().startswith("`")):
        return False
    if re.search(r"\bit\.[A-Za-z_]", text):
        return False
    # JS string literals that are themselves markup get localized through the
    # element text they produce, not as one opaque catalog entry.
    return not re.search(r"<[a-zA-Z/][^>]*>", text)


def _clean(value: str) -> str:
    return WHITESPACE.sub(" ", value).strip()


def _html_strings(path: Path) -> Iterator[str]:
    raw = path.read_text(encoding="utf-8")
    # Inline scripts/styles are scanned with the JS/CSS-aware path below, not as
    # visible copy, so strip them before handing the markup to the parser.
    markup = STYLE_TAG.sub("", SCRIPT_TAG.sub("", raw))
    collector = _HTMLStringCollector()
    collector.feed(markup)
    yield from collector.strings

    # Attributes such as placeholder="" inside the stripped scripts are rare,
    # but inline scripts do contain UI copy as JS literals — reuse the JS scan
    # *and* the template splitter so interpolated copy yields real fragments.
    for script in SCRIPT_TAG.findall(raw):
        for literal in _js_strings_from_text(script):
            yield from _explode_js_literal(literal)


def _js_strings_from_text(text: str) -> Iterator[str]:
    for pattern in QUOTED_PATTERNS:
        for match in pattern.finditer(text):
            yield match.group(1)


def _js_strings(path: Path) -> Iterator[str]:
    return _js_strings_from_text(path.read_text(encoding="utf-8"))


def _explode_js_literal(value: str, _depth: int = 0) -> Iterator[str]:
    """Yield UI copy from one JS string literal.

    Many render functions build markup inside template strings
    (`` `<div class="empty-state">还没有新的动态</div>` ``). Skipping those
    outright loses real copy, and adding the whole fragment as a catalog key
    can never match a text node. Parse the fragment instead and emit the text
    nodes / translatable attributes it renders.
    """

    unescaped = value.replace("\\n", " ").replace('\\"', '"').replace("\\'", "'")
    if "${" in unescaped and _depth < 3:
        # Interpolated templates never match a text node as a whole, but their
        # literal segments do (e.g. the copy around a rendered link). Nested
        # braces (``${foo({a: 1})}``) don't match; those strings fall through
        # and are rejected by ``_is_ui_string`` instead of recursing forever.
        parts = re.split(r"\$\{[^{}]*\}", unescaped)
        if len(parts) > 1:
            for segment in parts:
                yield from _explode_js_literal(segment, _depth + 1)
            return
    # Splitting on ``${…}`` can leave the tail of an opening tag
    # (``" data-tab="skill">功能角色</button>…``) or the head of a closing tag.
    # Trim to the text between tags so the parser sees real copy instead of one
    # giant malformed data chunk.
    first_lt = unescaped.find("<")
    first_gt = unescaped.find(">")
    if (
        first_gt != -1
        and (first_lt == -1 or first_gt < first_lt)
        # Only a *tag* tail starts with attributes (``id="x"``); a bare ``>``
        # in prose (``session_ttl_hours>0``) must stay in the copy.
        and re.search(r'(?:\w+="[^"]*"|["\'])\s*$', unescaped[:first_gt])
    ):
        unescaped = unescaped[first_gt + 1 :]
    if unescaped.count("<") > unescaped.count(">"):
        unescaped = unescaped.rsplit("<", 1)[0]
    if re.search(r"<[a-zA-Z/][^>]*>", unescaped):
        collector = _HTMLStringCollector()
        try:
            collector.feed(unescaped)
        except Exception:  # malformed fragment: treat it as plain text
            yield unescaped
            return
        yield from collector.strings
        return
    yield unescaped


_INTERPOLATION = re.compile(r"\$\{([^{}]*)\}")
# Numeric/date suffixes need reformatting rather than literal substitution
# (「3 万」 is not "3 ten-thousand" in English); leave those in the source
# language until a locale-aware formatter exists.
_PATTERN_SKIP = re.compile(
    r"^\{\d+\}(?:万|亿|月|日|号|时|分|秒)$|^\{0\}月\{1\}日$|^\{0\}年\{1\}月"
)
# Structural joiners with no literal Chinese (the captures carry the copy).
# Only this narrow shape is allowlisted: other placeholder-only templates are
# URLs, CSS, ids, or code.
_STRUCTURAL_PATTERNS = frozenset({"{0} · {1}"})
_PATTERN_JUNK = re.compile(
    r"`|=>|\bfunction\b|\bconst\b|\breturn\b|\bdocument\.|\bit\.|\$\{|<[a-zA-Z/][^>]*>"
)


def _literal_to_pattern(value: str) -> str | None:
    """Turn a JS template literal into ``模型：{0}`` style pattern source.

    Only single-level interpolations are considered; nested ternaries and
    markup produce fragments that can't be matched safely against one text
    node, so they're rejected.
    """

    if "${" not in value:
        return None
    unescaped = value.replace("\\n", " ").replace('\\"', '"').replace("\\'", "'")
    if len(unescaped) > 300 or "<" in unescaped or ">" in unescaped:
        return None
    if not CJK.search(unescaped):
        return None
    counter = itertools.count()
    pattern = _INTERPOLATION.sub(lambda _match: f"{{{next(counter)}}}", unescaped)
    if "${" in pattern:
        return None
    pattern = WHITESPACE.sub(" ", pattern).strip()
    if CJK.search(pattern) is None and pattern not in _STRUCTURAL_PATTERNS:
        return None
    if len(pattern) < 3:
        return None
    if _PATTERN_JUNK.search(pattern) or _PATTERN_SKIP.search(pattern):
        return None
    return pattern


def collect_patterns() -> dict[str, str]:
    """Return ``{pattern_source: pattern_source}`` for interpolated UI copy."""

    found: set[str] = set()
    for path in HTML_SOURCES:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        for script in SCRIPT_TAG.findall(raw):
            for literal in _js_strings_from_text(script):
                pattern = _literal_to_pattern(literal)
                if pattern is not None:
                    found.add(pattern)
    for path in JS_SOURCES:
        if not path.is_file():
            continue
        for literal in _js_strings(path):
            pattern = _literal_to_pattern(literal)
            if pattern is not None:
                found.add(pattern)
    return {pattern: pattern for pattern in sorted(found)}


def collect_inventory() -> dict[str, str]:
    """Return ``{source_text: source_text}`` for every Chinese UI string."""

    found: set[str] = set()
    for path in HTML_SOURCES:
        if not path.is_file():
            raise SystemExit(f"missing HTML source: {path}")
        for value in _html_strings(path):
            normalized = _normalize_source(value)
            if _is_ui_string(normalized):
                found.add(normalized)
    for path in JS_SOURCES:
        if not path.is_file():
            continue
        for value in _js_strings(path):
            for part in _explode_js_literal(value):
                for segment in part.split("\u2028"):
                    normalized = _normalize_source(segment)
                    if _is_ui_string(normalized):
                        found.add(normalized)
    return {text: text for text in sorted(found)}


def _opencc() -> object:
    try:
        import opencc  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dev tooling guard
        raise SystemExit(
            "opencc-python-reimplemented is required to regenerate zh-Hant; "
            'install it with: pip install -e ".[dev]"'
        ) from exc
    return opencc.OpenCC("s2twp")


def build_traditional(inventory: Iterable[str], converter: object) -> dict[str, str]:
    convert = converter.convert
    return {text: str(convert(text)) for text in inventory}


def _read_catalog(path: Path, section: str = "messages") -> dict[str, str]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get(section, {} if section != "messages" else raw)
    if not isinstance(entries, dict):
        raise SystemExit(f"{path}: {section!r} must be an object")
    return {str(key): str(value) for key, value in entries.items()}


def _render(
    locale: str,
    messages: dict[str, str],
    patterns: dict[str, str] | None = None,
    note: str = "",
) -> str:
    payload: dict[str, object] = {"locale": locale}
    if note:
        payload["note"] = note
    payload["messages"] = dict(sorted(messages.items()))
    if patterns is not None:
        payload["patterns"] = dict(sorted(patterns.items()))
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if catalogs drift")
    parser.add_argument("--report", action="store_true", help="print English coverage")
    args = parser.parse_args(argv)

    inventory = collect_inventory()
    patterns = collect_patterns()
    converter = _opencc()
    traditional = build_traditional(inventory, converter)
    convert = converter.convert
    # Pattern keys stay Simplified (they match the rendered source text);
    # only the replacement values are converted.
    traditional_patterns = {key: str(convert(value)) for key, value in patterns.items()}

    UI_CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    hans_path = UI_CATALOG_DIR / "zh-Hans.json"
    hant_path = UI_CATALOG_DIR / "zh-Hant.json"
    en_path = UI_CATALOG_DIR / "en.json"

    hans_text = _render(
        "zh-Hans", inventory, patterns, "Generated by scripts/build_i18n_catalogs.py"
    )
    hant_text = _render(
        "zh-Hant",
        traditional,
        traditional_patterns,
        "Generated from zh-Hans with OpenCC s2twp; re-run the build script after copy changes.",
    )

    # Semantic catalogs (CLI/API) are authored in zh-Hans and machine-converted
    # for Traditional Chinese, exactly like the UI inventory.
    hans_messages_path = MESSAGE_CATALOG_DIR / "zh-Hans.json"
    hant_messages_path = MESSAGE_CATALOG_DIR / "zh-Hant.json"
    hans_messages = _read_catalog(hans_messages_path)
    # Convert per key, not per unique value: two keys may share the same
    # Chinese text (e.g. 「关闭」 for both a switch and a pause state) and a
    # value-keyed dict would collapse them.
    convert = converter.convert
    hant_messages = {key: str(convert(value)) for key, value in hans_messages.items()}
    hant_messages_text = _render(
        "zh-Hant",
        hant_messages,
        None,
        "Generated from catalogs/zh-Hans.json with OpenCC s2twp; re-run the build script after copy changes.",
    )

    if args.check:
        drifted: list[str] = []
        if not hans_path.is_file() or hans_path.read_text(encoding="utf-8") != hans_text:
            drifted.append(str(hans_path.relative_to(ROOT)))
        if not hant_path.is_file() or hant_path.read_text(encoding="utf-8") != hant_text:
            drifted.append(str(hant_path.relative_to(ROOT)))
        if (
            not hant_messages_path.is_file()
            or hant_messages_path.read_text(encoding="utf-8") != hant_messages_text
        ):
            drifted.append(str(hant_messages_path.relative_to(ROOT)))
        if drifted:
            print("UI catalogs are stale; run python scripts/build_i18n_catalogs.py:")
            for path in drifted:
                print(f"  - {path}")
            return 1
        print(f"UI catalogs up to date ({len(inventory)} strings).")
        return 0

    hans_path.write_text(hans_text, encoding="utf-8")
    hant_path.write_text(hant_text, encoding="utf-8")
    hant_messages_path.write_text(hant_messages_text, encoding="utf-8")
    if not en_path.is_file():
        en_path.write_text(
            _render("en", {}, {}, "Hand-authored; missing keys fall back to zh-Hans"),
            encoding="utf-8",
        )

    english = _read_catalog(en_path)
    english_patterns = _read_catalog(en_path, "patterns")
    missing = [text for text in inventory if text not in english]
    unknown = [text for text in english if text not in inventory]
    missing_patterns = [text for text in patterns if text not in english_patterns]
    unknown_patterns = [text for text in english_patterns if text not in patterns]

    print(f"inventory: {len(inventory)} strings / {len(patterns)} patterns")
    print(f"zh-Hant:   {len(traditional)} strings / {len(traditional_patterns)} patterns")
    print(
        f"en:        {len(english)} strings, {len(missing)} missing; "
        f"{len(english_patterns)} patterns, {len(missing_patterns)} missing"
    )
    if unknown:
        print(f"en strings no longer present in source: {len(unknown)}")
        for text in unknown[:20]:
            print(f"  - {text[:80]}")
    if unknown_patterns:
        print(f"en patterns no longer present in source: {len(unknown_patterns)}")
        for text in unknown_patterns[:20]:
            print(f"  - {text[:80]}")
    if args.report:
        for text in missing:
            print(f"MISSING {text}")
        for text in missing_patterns:
            print(f"MISSING_PATTERN {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
