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
    "dialogue",
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
    "follow",
    "share",
}


class MemoryLayer:
    """Base class for a single memory layer."""

    def __init__(self, name: str, storage_path: Path) -> None:
        self.name = name
        self.storage_path = storage_path
        self._data: dict[str, Any] = {}
        self._loaded_mtime: float | None = None

    def load(self) -> None:
        """Load layer data from disk.

        Always reads as UTF-8. Without ``encoding="utf-8"`` Python uses
        the platform's locale encoding — which is GBK on Chinese
        Windows installs — and our JSON files contain Chinese profile
        text + emoji that GBK can't decode, raising UnicodeDecodeError
        on first /api/activity-feed or /api/delight/pending-batch hit.
        """
        if self.storage_path.exists():
            with open(self.storage_path, encoding="utf-8") as f:
                self._data = json.load(f)
            self._loaded_mtime = self.storage_path.stat().st_mtime
            logger.debug("Loaded %s layer from %s", self.name, self.storage_path)

    def _reload_if_stale(self) -> None:
        """Reload from disk if the file was modified by another process."""
        if not self.storage_path.exists():
            return
        try:
            current_mtime = self.storage_path.stat().st_mtime
        except OSError:
            return
        if self._loaded_mtime is None or current_mtime > self._loaded_mtime:
            logger.debug("Detected external change to %s layer, reloading", self.name)
            self.load()

    def save(self) -> None:
        """Persist layer data to disk.

        Always writes as UTF-8. ``ensure_ascii=False`` lets us emit
        Chinese / emoji content directly, but the file handle has to be
        opened in UTF-8 explicitly — otherwise GBK Windows hosts crash
        on the first non-ASCII write.
        """
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        self._loaded_mtime = self.storage_path.stat().st_mtime
        logger.debug("Saved %s layer to %s", self.name, self.storage_path)

    @property
    def data(self) -> dict[str, Any]:
        self._reload_if_stale()
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

    def __init__(self, data_dir: Path, *, database: Database | None = None) -> None:
        self._data_dir = data_dir
        self._layers: dict[str, MemoryLayer] = {}
        self._database = database or Database(data_dir / "openbiliclaw.db")
        self._feedback_state_path = data_dir / "memory" / "feedback_state.json"
        self._account_sync_state_path = data_dir / "memory" / "account_sync_state.json"
        self._discovery_runtime_state_path = data_dir / "memory" / "discovery_runtime.json"
        self._insight_candidates_path = data_dir / "memory" / "insight_candidates.json"
        self._cognition_updates_path = data_dir / "memory" / "cognition_updates.json"
        self._working_memory: dict[str, Any] = {}  # Session-only
        # Optional callback that fires after the soul layer is saved or
        # ``sync_profile_files`` runs. The runtime context wires this to
        # ``event_hub.publish({"type": "profile_updated"})`` so the
        # popup picks up profile changes regardless of which code path
        # ran the update (init, cognition cycle, manual rebuild, …).
        self._profile_change_callback: Any = None

        # Initialize the five layers
        layer_names = ["event", "preference", "awareness", "insight", "soul"]
        for name in layer_names:
            layer_path = data_dir / "memory" / f"{name}.json"
            self._layers[name] = MemoryLayer(name, layer_path)

    def set_profile_change_callback(self, callback: Any) -> None:
        """Register a callback fired after the soul layer is persisted.

        The callback may be sync or async (a coroutine function); the
        publisher schedules it via the running loop when present.
        """
        self._profile_change_callback = callback

    def _notify_profile_changed(self) -> None:
        """Best-effort dispatch of the registered profile-change callback."""
        cb = self._profile_change_callback
        if cb is None:
            return
        import asyncio as _asyncio

        try:
            result = cb()
            if _asyncio.iscoroutine(result):
                # If we're already inside a running loop, schedule it;
                # otherwise drop silently — the soul write still landed.
                try:
                    loop = _asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.create_task(result)
        except Exception:
            logger.debug("profile-change callback raised", exc_info=True)

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
        self._notify_profile_changed()

    def sync_profile_files(self, profile: object) -> None:
        """Write soul_profile.json + soul_profile.md dual files."""
        from openbiliclaw.soul.profile import OnionProfile
        from openbiliclaw.soul.profile_renderer import sync_profile_files

        if isinstance(profile, OnionProfile):
            sync_profile_files(profile, self._data_dir)
        elif isinstance(profile, dict):
            onion = OnionProfile.from_dict(profile)
            sync_profile_files(onion, self._data_dir)
        # ``sync_profile_files`` is the canonical "profile is now
        # current on disk" point — every code path that updates the
        # profile (init, cognition cycle, manual rebuild, dialogue
        # insight ingestion) ends here. Notify so the popup refetches.
        self._notify_profile_changed()

    def append_changelog(self, entry: str) -> None:
        """Append a changelog entry to soul_changelog.md."""
        from openbiliclaw.soul.profile_renderer import append_changelog

        append_changelog(entry, self._data_dir)

    def load_feedback_state(self) -> dict[str, object]:
        """Load feedback-processing cursor state from disk."""
        default_state = {
            "last_processed_feedback_event_id": 0,
            "last_feedback_reanalyzed_at": "",
        }
        if not self._feedback_state_path.exists():
            return default_state
        with open(self._feedback_state_path, encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            return default_state
        return {
            "last_processed_feedback_event_id": self._to_int(
                loaded.get("last_processed_feedback_event_id", 0)
            ),
            "last_feedback_reanalyzed_at": str(loaded.get("last_feedback_reanalyzed_at", "")),
        }

    def save_feedback_state(self, state: dict[str, object]) -> None:
        """Persist feedback-processing cursor state to disk."""
        self._feedback_state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_processed_feedback_event_id": self._to_int(
                state.get("last_processed_feedback_event_id", 0)
            ),
            "last_feedback_reanalyzed_at": str(state.get("last_feedback_reanalyzed_at", "")),
        }
        with open(self._feedback_state_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def load_account_sync_state(self) -> dict[str, object]:
        """Load account-side sync cursor state from disk."""
        default_state = {
            "last_history_view_at": 0,
            "last_history_bvid": "",
            "last_favorites_sync_at": "",
            "favorite_signature": "",
            "last_following_sync_at": "",
            "following_signature": "",
            "last_account_sync_at": "",
            "last_sync_error": "",
        }
        if not self._account_sync_state_path.exists():
            return default_state
        with open(self._account_sync_state_path, encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            return default_state
        return {
            "last_history_view_at": self._to_int(loaded.get("last_history_view_at", 0)),
            "last_history_bvid": str(loaded.get("last_history_bvid", "")),
            "last_favorites_sync_at": str(loaded.get("last_favorites_sync_at", "")),
            "favorite_signature": str(loaded.get("favorite_signature", "")),
            "last_following_sync_at": str(loaded.get("last_following_sync_at", "")),
            "following_signature": str(loaded.get("following_signature", "")),
            "last_account_sync_at": str(loaded.get("last_account_sync_at", "")),
            "last_sync_error": str(loaded.get("last_sync_error", "")),
        }

    def save_account_sync_state(self, state: dict[str, object]) -> None:
        """Persist account-side sync cursor state to disk."""
        self._account_sync_state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_history_view_at": self._to_int(state.get("last_history_view_at", 0)),
            "last_history_bvid": str(state.get("last_history_bvid", "")),
            "last_favorites_sync_at": str(state.get("last_favorites_sync_at", "")),
            "favorite_signature": str(state.get("favorite_signature", "")),
            "last_following_sync_at": str(state.get("last_following_sync_at", "")),
            "following_signature": str(state.get("following_signature", "")),
            "last_account_sync_at": str(state.get("last_account_sync_at", "")),
            "last_sync_error": str(state.get("last_sync_error", "")),
        }
        with open(self._account_sync_state_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def load_discovery_runtime_state(self) -> dict[str, object]:
        """Load continuous-discovery runtime state from disk."""
        default_state = {
            "last_event_refresh_at": "",
            "last_trending_refresh_at": "",
            "last_explore_refresh_at": "",
            "last_processed_event_id": 0,
            "last_notification_at": "",
            "last_discovered_count": 0,
            "last_replenished_count": 0,
            "recent_pool_topics": [],
            "probed_domains": {},
            "probed_axes": {},
            "probe_feedback_history": [],
        }
        if not self._discovery_runtime_state_path.exists():
            return default_state
        with open(self._discovery_runtime_state_path, encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            return default_state
        return {
            "last_event_refresh_at": str(loaded.get("last_event_refresh_at", "")),
            "last_trending_refresh_at": str(loaded.get("last_trending_refresh_at", "")),
            "last_explore_refresh_at": str(loaded.get("last_explore_refresh_at", "")),
            "last_processed_event_id": self._to_int(loaded.get("last_processed_event_id", 0)),
            "last_notification_at": str(loaded.get("last_notification_at", "")),
            "last_discovered_count": self._to_int(loaded.get("last_discovered_count", 0)),
            "last_replenished_count": self._to_int(loaded.get("last_replenished_count", 0)),
            "recent_pool_topics": self._as_str_list(loaded.get("recent_pool_topics", [])),
            "probed_domains": loaded.get("probed_domains", {}),
            "probed_axes": loaded.get("probed_axes", {}),
            "probe_feedback_history": self._as_dict_list(
                loaded.get("probe_feedback_history", [])
            )[-100:],
            "last_delight_notification_at": str(loaded.get("last_delight_notification_at", "")),
        }

    def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
        """Persist continuous-discovery runtime state to disk."""
        self._discovery_runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_event_refresh_at": str(state.get("last_event_refresh_at", "")),
            "last_trending_refresh_at": str(state.get("last_trending_refresh_at", "")),
            "last_explore_refresh_at": str(state.get("last_explore_refresh_at", "")),
            "last_processed_event_id": self._to_int(state.get("last_processed_event_id", 0)),
            "last_notification_at": str(state.get("last_notification_at", "")),
            "last_discovered_count": self._to_int(state.get("last_discovered_count", 0)),
            "last_replenished_count": self._to_int(state.get("last_replenished_count", 0)),
            "recent_pool_topics": self._as_str_list(state.get("recent_pool_topics", [])),
            "probed_domains": state.get("probed_domains", {}),
            "probed_axes": state.get("probed_axes", {}),
            "probe_feedback_history": self._as_dict_list(
                state.get("probe_feedback_history", [])
            )[-100:],
            "last_delight_notification_at": str(state.get("last_delight_notification_at", "")),
        }
        with open(self._discovery_runtime_state_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def load_insight_candidates(self) -> list[dict[str, object]]:
        """Load dialogue-derived insight candidates from disk."""
        if not self._insight_candidates_path.exists():
            return []
        with open(self._insight_candidates_path, encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    def save_insight_candidates(self, candidates: list[dict[str, object]]) -> None:
        """Persist dialogue-derived insight candidates to disk."""
        self._insight_candidates_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._insight_candidates_path, "w", encoding="utf-8") as file:
            json.dump(candidates, file, ensure_ascii=False, indent=2)

    def load_cognition_updates(self) -> list[dict[str, object]]:
        """Load cognition updates generated from preference/profile shifts."""
        if not self._cognition_updates_path.exists():
            return []
        with open(self._cognition_updates_path, encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
        """Persist cognition updates generated from preference/profile shifts."""
        self._cognition_updates_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._cognition_updates_path, "w", encoding="utf-8") as file:
            json.dump(updates, file, ensure_ascii=False, indent=2)

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

        # Support both onion format (nested "core" key) and legacy flat format
        is_onion = "core" in soul and isinstance(soul.get("core"), dict)
        if is_onion:
            core_data = soul.get("core", {})
            values_data = soul.get("values_layer", {})
            role_data = soul.get("role", {})
            interest_data = soul.get("interest", {})
            mbti_data = core_data.get("mbti", {})
            soul_summary: dict[str, Any] = {
                "personality_portrait": soul.get("personality_portrait", ""),
                "core_traits": self._as_str_list(core_data.get("core_traits", [])),
                "values": self._as_str_list(values_data.get("values", [])),
                "life_stage": str(role_data.get("life_stage", "")),
                "deep_needs": self._as_str_list(core_data.get("deep_needs", [])),
                "mbti_type": str(mbti_data.get("type", "")),
                "motivational_drivers": self._as_str_list(
                    values_data.get("motivational_drivers", [])
                ),
            }
            # Flatten interest tree for preference summary
            flat_interests: list[dict[str, object]] = []
            for dom in self._as_dict_list(interest_data.get("likes", [])):
                for spec in self._as_dict_list(dom.get("specifics", [])):
                    flat_interests.append(
                        {
                            "name": spec.get("name", ""),
                            "category": dom.get("domain", ""),
                            "weight": self._to_float(spec.get("weight", 0.0)),
                        }
                    )
                if not dom.get("specifics"):
                    flat_interests.append(
                        {
                            "name": dom.get("domain", ""),
                            "category": dom.get("domain", ""),
                            "weight": self._to_float(dom.get("weight", 0.0)),
                        }
                    )
            flat_disliked: list[str] = []
            for dom in self._as_dict_list(interest_data.get("dislikes", [])):
                flat_disliked.append(str(dom.get("domain", "")))
            preference_summary: dict[str, Any] = {
                "top_interests": self._top_interests(flat_interests),
                "style": preference.get("style", {}),
                "exploration_openness": preference.get("exploration_openness", 0.5),
                "disliked_topics": flat_disliked[:5],
                "favorite_up_users": self._as_str_list(interest_data.get("favorite_up_users", []))[
                    :5
                ],
            }
        else:
            soul_summary = {
                "personality_portrait": soul.get("personality_portrait", ""),
                "core_traits": self._as_str_list(soul.get("core_traits", [])),
                "values": self._as_str_list(soul.get("values", [])),
                "life_stage": str(soul.get("life_stage", "")),
                "deep_needs": self._as_str_list(soul.get("deep_needs", [])),
            }
            preference_summary = {
                "top_interests": self._top_interests(preference.get("interests", [])),
                "style": preference.get("style", {}),
                "exploration_openness": preference.get("exploration_openness", 0.5),
                "disliked_topics": self._as_str_list(preference.get("disliked_topics", []))[:5],
                "favorite_up_users": self._as_str_list(preference.get("favorite_up_users", []))[:5],
            }

        return {
            "soul_summary": soul_summary,
            "preference_summary": preference_summary,
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
    def _as_dict_list(raw_value: object) -> list[dict[str, Any]]:
        if not isinstance(raw_value, list):
            return []
        return [item for item in raw_value if isinstance(item, dict)]

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

    @staticmethod
    def _to_int(raw_value: object) -> int:
        if isinstance(raw_value, bool):
            return int(raw_value)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
        if isinstance(raw_value, str):
            try:
                return int(raw_value)
            except ValueError:
                return 0
        return 0

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
            # v0.3.23+: ``context`` is a natural-language string from
            # ``event_format.build_event()``. Default to empty string
            # (was ``{}`` in v0.3.22 and earlier) so insert_event's
            # smart encoder stores raw text instead of double-quoting
            # the empty dict literal.
            context=event.get("context", ""),
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
