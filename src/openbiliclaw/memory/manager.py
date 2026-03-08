"""Memory Manager — coordinates the multi-layer networked memory system.

Manages the five memory layers and four memory types, handling
cross-layer updates, bidirectional corrections, and self-editing.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

logger = logging.getLogger(__name__)
_EVENT_TYPES = {
    "view",
    "pause",
    "seek",
    "search",
    "favorite",
    "like",
    "coin",
    "comment",
    "click",
    "scroll",
    "hover",
    "snapshot",
    "feedback",
}


class MemoryLayer:
    """Base class for a single memory layer."""

    def __init__(self, name: str, storage_path: Path) -> None:
        self.name = name
        self.storage_path = storage_path
        self._data: dict[str, Any] = {}

    def load(self) -> None:
        """Load layer data from disk."""
        if self.storage_path.exists():
            with open(self.storage_path) as f:
                self._data = json.load(f)
            logger.debug("Loaded %s layer from %s", self.name, self.storage_path)

    def save(self) -> None:
        """Persist layer data to disk."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "w") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        logger.debug("Saved %s layer to %s", self.name, self.storage_path)

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def update(self, key: str, value: Any) -> None:
        """Update a specific key in the layer."""
        self._data[key] = value


class MemoryManager:
    """Manages the five-layer networked memory architecture.

    Layers (bottom to top):
      1. Event Layer    — raw behavioral facts
      2. Preference Layer — extracted preferences
      3. Awareness Layer  — daily observations and trends
      4. Insight Layer    — motivational analysis and hypotheses
      5. Soul Layer       — personality portrait

    Memory types:
      - Core Memory     — always in agent context (Soul + Preference summary)
      - Episodic Memory  — specific interaction episodes
      - Semantic Memory  — factual knowledge about the user
      - Working Memory   — current session context (in-memory only)

    Interactions are bidirectional: new events flow up, and top-level
    understanding flows down to guide interpretation.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._layers: dict[str, MemoryLayer] = {}
        self._database = Database(data_dir / "openbiliclaw.db")
        self._working_memory: dict[str, Any] = {}  # Session-only

        # Initialize the five layers
        layer_names = ["event", "preference", "awareness", "insight", "soul"]
        for name in layer_names:
            layer_path = data_dir / "memory" / f"{name}.json"
            self._layers[name] = MemoryLayer(name, layer_path)

    def initialize(self) -> None:
        """Load all layers from disk."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._database.initialize()
        for layer in self._layers.values():
            layer.load()
        logger.info("Memory manager initialized with %d layers.", len(self._layers))

    def save_all(self) -> None:
        """Persist all layers to disk."""
        for layer in self._layers.values():
            layer.save()

    def get_layer(self, name: str) -> MemoryLayer:
        """Get a specific memory layer by name."""
        if name not in self._layers:
            raise KeyError(f"Unknown memory layer: {name}")
        return self._layers[name]

    # --- Core Memory (always in context) ---

    def get_core_memory(self) -> dict[str, Any]:
        """Get core memory for LLM context injection.

        Core memory includes the Soul layer and a summary of the Preference layer.
        This is always provided to the LLM as part of the system prompt.
        """
        soul = self._layers["soul"].data
        preference = self._layers["preference"].data
        awareness = self._layers["awareness"].data.get("notes", [])
        insights = self._layers["insight"].data.get("hypotheses", [])

        return {
            "soul_summary": {
                "personality_portrait": soul.get("personality_portrait", ""),
                "core_traits": self._as_str_list(soul.get("core_traits", [])),
                "values": self._as_str_list(soul.get("values", [])),
                "life_stage": str(soul.get("life_stage", "")),
                "deep_needs": self._as_str_list(soul.get("deep_needs", [])),
            },
            "preference_summary": {
                "top_interests": self._top_interests(preference.get("interests", [])),
                "style": preference.get("style", {}),
                "exploration_openness": preference.get("exploration_openness", 0.5),
                "disliked_topics": self._as_str_list(preference.get("disliked_topics", []))[:5],
                "favorite_up_users": self._as_str_list(
                    preference.get("favorite_up_users", [])
                )[:5],
            },
            "recent_awareness": self._recent_awareness(awareness),
            "active_insights": self._active_insights(insights),
        }

    def render_core_memory_prompt(self) -> str:
        """Render core memory into stable prompt text."""
        core_memory = self.get_core_memory()
        soul = core_memory["soul_summary"]
        preference_summary = core_memory["preference_summary"]
        recent_awareness = core_memory["recent_awareness"]
        active_insights = core_memory["active_insights"]

        has_soul = any(soul.values())
        has_preference = bool(
            preference_summary.get("top_interests")
            or preference_summary.get("disliked_topics")
            or preference_summary.get("favorite_up_users")
        )
        if not has_soul and not has_preference and not recent_awareness and not active_insights:
            return "（尚未建立完整画像）"

        sections: list[str] = []
        portrait = soul.get("personality_portrait")
        if portrait:
            sections.append(f"## 用户画像\n{portrait}")

        preference_lines: list[str] = []
        top_interests = preference_summary.get("top_interests", [])
        if top_interests:
            interest_text = ", ".join(
                item["name"]
                for item in top_interests
                if isinstance(item, dict) and item.get("name")
            )
            if interest_text:
                preference_lines.append(f"兴趣标签: {interest_text}")
        disliked_topics = preference_summary.get("disliked_topics", [])
        if disliked_topics:
            preference_lines.append(f"不喜欢: {', '.join(disliked_topics)}")
        favorite_up_users = preference_summary.get("favorite_up_users", [])
        if favorite_up_users:
            preference_lines.append(f"常看UP主: {', '.join(favorite_up_users)}")
        if preference_lines:
            sections.append("## 偏好摘要\n" + "\n".join(preference_lines))

        if recent_awareness:
            awareness_text = "\n".join(
                f"- [{item.get('date', '')}] {item.get('observation', '')}".strip()
                for item in recent_awareness
            )
            sections.append(f"## 近期观察\n{awareness_text}")

        if active_insights:
            insights_text = "\n".join(
                f"- {item.get('hypothesis', '')} (置信度: {float(item.get('confidence', 0.0)):.0%})"
                for item in active_insights
            )
            sections.append(f"## 当前洞察\n{insights_text}")

        return "\n\n".join(sections)

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item) for item in raw_value]

    @staticmethod
    def _to_float(raw_value: object) -> float:
        if isinstance(raw_value, bool):
            return float(raw_value)
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            try:
                return float(raw_value)
            except ValueError:
                return 0.0
        return 0.0

    def _top_interests(self, raw_value: object) -> list[dict[str, object]]:
        if not isinstance(raw_value, list):
            return []
        interests = [item for item in raw_value if isinstance(item, dict)]
        return sorted(
            interests,
            key=lambda item: self._to_float(item.get("weight", 0.0)),
            reverse=True,
        )[:5]

    @staticmethod
    def _recent_awareness(raw_value: object) -> list[dict[str, object]]:
        if not isinstance(raw_value, list):
            return []
        notes = [item for item in raw_value if isinstance(item, dict)]
        return notes[:5]

    def _active_insights(self, raw_value: object) -> list[dict[str, object]]:
        if not isinstance(raw_value, list):
            return []
        insights = [item for item in raw_value if isinstance(item, dict)]
        return sorted(
            insights,
            key=lambda item: self._to_float(item.get("confidence", 0.0)),
            reverse=True,
        )[:5]

    # --- Working Memory (session-only) ---

    def set_working(self, key: str, value: Any) -> None:
        """Set a value in working memory (session only, not persisted)."""
        self._working_memory[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        """Get a value from working memory."""
        return self._working_memory.get(key, default)

    def clear_working(self) -> None:
        """Clear all working memory."""
        self._working_memory.clear()

    # --- Cross-layer operations ---

    async def propagate_event(self, event: dict[str, Any]) -> None:
        """Propagate a new event upward through the memory layers.

        This is the main entry point for new behavioral data. The event
        is stored in the Event layer and may trigger updates in higher layers.

        Args:
            event: Behavioral event data.
        """
        event_type = str(event.get("event_type") or event.get("type") or "").strip()
        if event_type not in _EVENT_TYPES:
            raise ValueError(f"Unsupported event type: {event_type or 'unknown'}")

        self._database.insert_event(
            event_type,
            url=event.get("url", ""),
            title=event.get("title", ""),
            context=event.get("context", {}),
            metadata=event.get("metadata", {}),
        )
        # TODO: Check if preference layer needs updating
        # TODO: Check if this triggers awareness observations
        # TODO: Check for significant events that bypass to soul layer
        logger.debug("Event propagated: %s", event_type)

    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        keyword: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query persisted events from the SQLite-backed event layer."""
        return self._database.query_events(
            event_types=event_types,
            start_time=start_time,
            end_time=end_time,
            keyword=keyword,
            limit=limit,
        )

    def get_event_stats(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, int]:
        """Return grouped event counts for the given time range."""
        return self._database.count_events_by_type(
            start_time=start_time,
            end_time=end_time,
        )

    async def top_down_reinterpret(self) -> None:
        """Use top-level understanding to reinterpret lower layers.

        Soul-level personality understanding can change how we interpret
        behavioral patterns at the preference and awareness layers.
        """
        # TODO: Implement top-down reinterpretation
        logger.debug("Top-down reinterpretation triggered.")
