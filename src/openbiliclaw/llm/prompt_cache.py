"""Small helpers for stable, layered prompt rendering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

_PROFILE_CORE_KEYS = (
    "core_traits",
    "cognitive_style",
    "values",
    "motivational_drivers",
    "deep_needs",
    "mbti",
)
_PROFILE_LIFE_KEYS = ("current_phase", "life_stage")
_PROFILE_INTEREST_KEYS = ("interest_domains", "interests", "disliked_topics")
_PROFILE_STYLE_KEYS = (
    "style",
    "context",
    "exploration_openness",
    "source_platform_mix",
)
_PROFILE_RECENT_KEYS = (
    "recent_awareness",
    "active_insights",
    "speculative_interests",
)


def stable_json_digest(value: object) -> str:
    """Return a deterministic short digest for prompt-visible values."""

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except TypeError:
        text = str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def profile_prompt_layers(
    profile_summary: dict[str, object],
) -> list[tuple[str, dict[str, object]]]:
    """Split a profile summary into prompt layers from stable to volatile."""

    consumed: set[str] = set()

    def pick(keys: tuple[str, ...]) -> dict[str, object]:
        layer: dict[str, object] = {}
        for key in keys:
            if key in profile_summary:
                layer[key] = profile_summary[key]
                consumed.add(key)
        return layer

    layers: list[tuple[str, dict[str, object]]] = [
        ("profile_core", pick(_PROFILE_CORE_KEYS)),
        ("profile_life_context", pick(_PROFILE_LIFE_KEYS)),
        ("profile_interests", pick(_PROFILE_INTEREST_KEYS)),
        ("profile_style_context", pick(_PROFILE_STYLE_KEYS)),
        ("profile_recent_context", pick(_PROFILE_RECENT_KEYS)),
    ]
    extra = {key: profile_summary[key] for key in sorted(profile_summary) if key not in consumed}
    if extra:
        layers.append(("profile_extra", extra))
    return layers


@dataclass(frozen=True)
class _LayerEntry:
    digest: str
    text: str
    hits: int = 0
    misses: int = 1


class PromptLayerRenderCache:
    """Render JSON prompt blocks and reuse unchanged layers.

    The cache stores one latest rendered block per layer name. Callers still
    compute the layer payload from the current source of truth; the digest
    decides whether the rendered prompt text can be reused or must be updated.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _LayerEntry] = {}

    def render_json_layer(self, name: str, payload: object) -> str:
        """Return ``<name>`` JSON block text, reusing unchanged layer text."""

        digest = stable_json_digest(payload)
        entry = self._entries.get(name)
        if entry is not None and entry.digest == digest:
            self._entries[name] = _LayerEntry(
                digest=entry.digest,
                text=entry.text,
                hits=entry.hits + 1,
                misses=entry.misses,
            )
            return entry.text

        text = "\n\n".join(
            [
                f"<{name}>",
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                f"</{name}>",
            ]
        )
        previous_misses = entry.misses if entry is not None else 0
        previous_hits = entry.hits if entry is not None else 0
        self._entries[name] = _LayerEntry(
            digest=digest,
            text=text,
            hits=previous_hits,
            misses=previous_misses + 1,
        )
        return text

    def render_json_layers(self, layers: Sequence[tuple[str, object]]) -> list[str]:
        """Render multiple JSON layers in the given order."""

        return [self.render_json_layer(name, payload) for name, payload in layers]

    def layer_digest(self, name: str) -> str:
        """Return the current digest for one layer, or an empty string."""

        entry = self._entries.get(name)
        return entry.digest if entry is not None else ""

    def stats(self) -> dict[str, dict[str, Any]]:
        """Return lightweight cache stats for diagnostics and tests."""

        return {
            name: {
                "digest": entry.digest,
                "hits": entry.hits,
                "misses": entry.misses,
            }
            for name, entry in self._entries.items()
        }
