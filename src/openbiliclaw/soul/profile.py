"""User profile data models.

Defines the structured representation of user understanding at each layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class InterestTag:
    """A weighted interest tag with time decay."""

    name: str
    category: str  # Top-level category (e.g., "科技", "游戏")
    weight: float = 1.0  # 0.0 - 1.0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    source: str = ""  # How this tag was inferred


@dataclass
class StylePreference:
    """Content style preferences."""

    preferred_duration: str = ""  # "short" | "medium" | "long"
    preferred_pace: str = ""  # "fast" | "moderate" | "slow"
    quality_sensitivity: float = 0.5  # How much production quality matters
    humor_preference: float = 0.5  # Preference for humorous content
    depth_preference: float = 0.5  # Preference for in-depth analysis


@dataclass
class ContextMode:
    """Contextual usage patterns."""

    weekday_patterns: str = ""  # Description of weekday usage
    weekend_patterns: str = ""  # Description of weekend usage
    time_of_day_patterns: str = ""  # Morning vs night preferences
    session_type: str = ""  # "browsing" | "deep_dive" | "background"


@dataclass
class PreferenceLayer:
    """Preference Layer — structured preferences extracted from behavior."""

    interests: list[InterestTag] = field(default_factory=list)
    style: StylePreference = field(default_factory=StylePreference)
    context: ContextMode = field(default_factory=ContextMode)
    exploration_openness: float = 0.5  # How open to new domains (0-1)
    disliked_topics: list[str] = field(default_factory=list)
    favorite_up_users: list[str] = field(default_factory=list)
    source_platform_mix: dict[str, float] = field(default_factory=dict)


@dataclass
class AwarenessNote:
    """A single awareness observation."""

    date: str = ""
    observation: str = ""  # What was observed
    trend: str = ""  # What trend this suggests
    emotion_guess: str = ""  # Guessed emotional state


@dataclass
class InsightHypothesis:
    """An insight or hypothesis about the user."""

    hypothesis: str = ""  # The insight itself
    evidence: list[str] = field(default_factory=list)  # Supporting observations
    confidence: float = 0.5  # 0.0 - 1.0
    validated: bool = False  # Has this been confirmed?
    created_at: str = ""


@dataclass
class SoulProfile:
    """Soul Layer — the deepest understanding of who the user is.

    This is the natural language personality portrait that the agent
    maintains, written as if by a close friend who truly understands
    this person.
    """

    # Soul layer — the personality portrait
    personality_portrait: str = ""  # Long-form natural language description
    core_traits: list[str] = field(default_factory=list)
    cognitive_style: list[str] = field(default_factory=list)
    motivational_drivers: list[str] = field(default_factory=list)
    current_phase: str = ""
    values: list[str] = field(default_factory=list)
    life_stage: str = ""  # Current life stage/situation
    deep_needs: list[str] = field(default_factory=list)  # Unmet psychological needs

    # Embedded preference summary (for LLM context)
    preferences: PreferenceLayer = field(default_factory=PreferenceLayer)

    # Recent awareness notes
    recent_awareness: list[AwarenessNote] = field(default_factory=list)

    # Active insights / hypotheses
    active_insights: list[InsightHypothesis] = field(default_factory=list)

    # Metadata
    created_at: str = ""
    updated_at: str = ""
    version: int = 0

    def to_llm_context(self) -> str:
        """Generate a natural language summary for LLM context.

        Returns a rich description that can be injected into LLM prompts
        to give the agent full understanding of the user.
        """
        parts = []

        if self.personality_portrait:
            parts.append(f"## 用户画像\n{self.personality_portrait}")

        if self.core_traits:
            parts.append(f"## 核心特质\n{', '.join(self.core_traits)}")

        if self.cognitive_style:
            parts.append(f"## 认知风格\n{', '.join(self.cognitive_style)}")

        if self.motivational_drivers:
            parts.append(f"## 内在驱动力\n{', '.join(self.motivational_drivers)}")

        if self.current_phase:
            parts.append(f"## 当前阶段\n{self.current_phase}")

        if self.deep_needs:
            parts.append(f"## 深层需求\n{', '.join(self.deep_needs)}")

        if self.active_insights:
            insights_text = "\n".join(
                f"- {i.hypothesis} (置信度: {i.confidence:.0%})" for i in self.active_insights
            )
            parts.append(f"## 当前洞察\n{insights_text}")

        if self.recent_awareness:
            notes = "\n".join(f"- [{n.date}] {n.observation}" for n in self.recent_awareness[:5])
            parts.append(f"## 近期观察\n{notes}")

        if len(self.preferences.source_platform_mix) > 1:
            mix_line = _format_source_mix_line(self.preferences.source_platform_mix)
            if mix_line:
                parts.append(f"## 来源分布\n{mix_line}")

        return "\n\n".join(parts) if parts else "（尚未建立用户画像）"

    def to_dict(self) -> dict[str, object]:
        """Serialize the soul profile into JSON-friendly dictionaries."""
        return {
            "personality_portrait": self.personality_portrait,
            "core_traits": self.core_traits,
            "cognitive_style": self.cognitive_style,
            "motivational_drivers": self.motivational_drivers,
            "current_phase": self.current_phase,
            "values": self.values,
            "life_stage": self.life_stage,
            "deep_needs": self.deep_needs,
            "preferences": preference_layer_to_dict(self.preferences),
            "recent_awareness": [awareness_note_to_dict(note) for note in self.recent_awareness],
            "active_insights": [insight_hypothesis_to_dict(item) for item in self.active_insights],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, raw_data: dict[str, object]) -> SoulProfile:
        """Build a SoulProfile from persisted JSON data."""
        return cls(
            personality_portrait=str(raw_data.get("personality_portrait", "")),
            core_traits=_as_str_list(raw_data.get("core_traits")),
            cognitive_style=_as_str_list(raw_data.get("cognitive_style")),
            motivational_drivers=_as_str_list(raw_data.get("motivational_drivers")),
            current_phase=str(raw_data.get("current_phase", "")),
            values=_as_str_list(raw_data.get("values")),
            life_stage=str(raw_data.get("life_stage", "")),
            deep_needs=_as_str_list(raw_data.get("deep_needs")),
            preferences=preference_layer_from_dict(raw_data.get("preferences")),
            recent_awareness=[
                awareness_note_from_dict(item)
                for item in _as_list(raw_data.get("recent_awareness"))
                if isinstance(item, dict)
            ],
            active_insights=[
                insight_hypothesis_from_dict(item)
                for item in _as_list(raw_data.get("active_insights"))
                if isinstance(item, dict)
            ],
            created_at=str(raw_data.get("created_at", "")),
            updated_at=str(raw_data.get("updated_at", "")),
            version=_as_int(raw_data.get("version", 0)),
        )


def preference_layer_to_dict(layer: PreferenceLayer) -> dict[str, object]:
    """Serialize a preference layer into JSON-friendly dictionaries."""
    return {
        "interests": [interest_tag_to_dict(item) for item in layer.interests],
        "style": style_preference_to_dict(layer.style),
        "context": context_mode_to_dict(layer.context),
        "exploration_openness": layer.exploration_openness,
        "disliked_topics": layer.disliked_topics,
        "favorite_up_users": layer.favorite_up_users,
        "source_platform_mix": dict(layer.source_platform_mix),
    }


def preference_layer_from_dict(raw_value: object) -> PreferenceLayer:
    """Build a preference layer from persisted JSON data."""
    data = raw_value if isinstance(raw_value, dict) else {}
    raw_mix = data.get("source_platform_mix")
    mix: dict[str, float] = {}
    if isinstance(raw_mix, dict):
        for key, value in raw_mix.items():
            if not isinstance(key, str):
                continue
            mix[key] = _as_float(value, 0.0)
    return PreferenceLayer(
        interests=[
            interest_tag_from_dict(item)
            for item in _as_list(data.get("interests"))
            if isinstance(item, dict)
        ],
        style=style_preference_from_dict(data.get("style")),
        context=context_mode_from_dict(data.get("context")),
        exploration_openness=_as_float(data.get("exploration_openness", 0.5), 0.5),
        disliked_topics=_as_str_list(data.get("disliked_topics")),
        favorite_up_users=_as_str_list(data.get("favorite_up_users")),
        source_platform_mix=mix,
    )


def interest_tag_to_dict(tag: InterestTag) -> dict[str, object]:
    """Serialize an interest tag."""
    return {
        "name": tag.name,
        "category": tag.category,
        "weight": tag.weight,
        "first_seen": tag.first_seen.isoformat() if tag.first_seen else "",
        "last_seen": tag.last_seen.isoformat() if tag.last_seen else "",
        "source": tag.source,
    }


def interest_tag_from_dict(raw_data: dict[str, object]) -> InterestTag:
    """Build an interest tag from persisted JSON data."""
    return InterestTag(
        name=str(raw_data.get("name", "")),
        category=str(raw_data.get("category", "")),
        weight=_as_float(raw_data.get("weight", 1.0), 1.0),
        source=str(raw_data.get("source", "")),
    )


def style_preference_to_dict(style: StylePreference) -> dict[str, object]:
    return {
        "preferred_duration": style.preferred_duration,
        "preferred_pace": style.preferred_pace,
        "quality_sensitivity": style.quality_sensitivity,
        "humor_preference": style.humor_preference,
        "depth_preference": style.depth_preference,
    }


def style_preference_from_dict(raw_value: object) -> StylePreference:
    data = raw_value if isinstance(raw_value, dict) else {}
    return StylePreference(
        preferred_duration=str(data.get("preferred_duration", "")),
        preferred_pace=str(data.get("preferred_pace", "")),
        quality_sensitivity=_as_float(data.get("quality_sensitivity", 0.5), 0.5),
        humor_preference=_as_float(data.get("humor_preference", 0.5), 0.5),
        depth_preference=_as_float(data.get("depth_preference", 0.5), 0.5),
    )


def context_mode_to_dict(context: ContextMode) -> dict[str, object]:
    return {
        "weekday_patterns": context.weekday_patterns,
        "weekend_patterns": context.weekend_patterns,
        "time_of_day_patterns": context.time_of_day_patterns,
        "session_type": context.session_type,
    }


def context_mode_from_dict(raw_value: object) -> ContextMode:
    data = raw_value if isinstance(raw_value, dict) else {}
    return ContextMode(
        weekday_patterns=str(data.get("weekday_patterns", "")),
        weekend_patterns=str(data.get("weekend_patterns", "")),
        time_of_day_patterns=str(data.get("time_of_day_patterns", "")),
        session_type=str(data.get("session_type", "")),
    )


def awareness_note_to_dict(note: AwarenessNote) -> dict[str, object]:
    return {
        "date": note.date,
        "observation": note.observation,
        "trend": note.trend,
        "emotion_guess": note.emotion_guess,
    }


def awareness_note_from_dict(raw_data: dict[str, object]) -> AwarenessNote:
    return AwarenessNote(
        date=str(raw_data.get("date", "")),
        observation=str(raw_data.get("observation", "")),
        trend=str(raw_data.get("trend", "")),
        emotion_guess=str(raw_data.get("emotion_guess", "")),
    )


def insight_hypothesis_to_dict(item: InsightHypothesis) -> dict[str, object]:
    return {
        "hypothesis": item.hypothesis,
        "evidence": item.evidence,
        "confidence": item.confidence,
        "validated": item.validated,
        "created_at": item.created_at,
    }


def insight_hypothesis_from_dict(raw_data: dict[str, object]) -> InsightHypothesis:
    return InsightHypothesis(
        hypothesis=str(raw_data.get("hypothesis", "")),
        evidence=_as_str_list(raw_data.get("evidence")),
        confidence=_as_float(raw_data.get("confidence", 0.5), 0.5),
        validated=bool(raw_data.get("validated", False)),
        created_at=str(raw_data.get("created_at", "")),
    )


def _as_list(raw_value: object) -> list[object]:
    return raw_value if isinstance(raw_value, list) else []


def _as_str_list(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    return [str(item) for item in raw_value]


def _as_float(raw_value: object, default: float) -> float:
    if isinstance(raw_value, bool):
        return float(raw_value)
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if isinstance(raw_value, str):
        try:
            return float(raw_value)
        except ValueError:
            return default
    return default


def _as_int(raw_value: object) -> int:
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


def _as_source_mix(raw_value: object) -> dict[str, float]:
    if not isinstance(raw_value, dict):
        return {}
    mix: dict[str, float] = {}
    for key, value in raw_value.items():
        if not isinstance(key, str) or not key:
            continue
        mix[key] = _as_float(value, 0.0)
    return mix


def _format_source_mix_line(mix: dict[str, float]) -> str:
    items = sorted(mix.items(), key=lambda kv: kv[1], reverse=True)
    parts = [f"{name} {share * 100:.0f}%" for name, share in items if share > 0]
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Onion Model — five-layer personality profile
# ---------------------------------------------------------------------------


@dataclass
class MBTIDimension:
    """A single MBTI dimension with pole and strength."""

    pole: str = ""  # "E"|"I", "S"|"N", "T"|"F", "J"|"P"
    strength: float = 0.5  # 0.0 - 1.0


@dataclass
class MBTI:
    """MBTI personality type with dimensional strengths."""

    type: str = ""  # e.g. "INTJ"
    dimensions: dict[str, MBTIDimension] = field(default_factory=dict)
    confidence: float = 0.0
    inferred_from: list[str] = field(default_factory=list)


@dataclass
class CoreLayer:
    """Innermost layer — stable personality traits and deep needs."""

    core_traits: list[str] = field(default_factory=list)
    deep_needs: list[str] = field(default_factory=list)
    mbti: MBTI = field(default_factory=MBTI)


@dataclass
class ValuesLayer:
    """Values and motivational drivers."""

    values: list[str] = field(default_factory=list)
    motivational_drivers: list[str] = field(default_factory=list)


@dataclass
class InterestSpecific:
    """A narrow interest within a broad domain."""

    name: str = ""
    weight: float = 0.5


@dataclass
class InterestDomain:
    """A broad interest domain containing narrow specifics."""

    domain: str = ""
    weight: float = 0.5
    specifics: list[InterestSpecific] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    source: str = ""


@dataclass
class InterestLayer:
    """Likes, dislikes (tree-shaped), and favorite creators."""

    likes: list[InterestDomain] = field(default_factory=list)
    dislikes: list[InterestDomain] = field(default_factory=list)
    favorite_up_users: list[str] = field(default_factory=list)


@dataclass
class RoleLayer:
    """Life stage and current phase."""

    life_stage: str = ""
    current_phase: str = ""


@dataclass
class SurfaceLayer:
    """Outermost layer — observable cognitive style and content preferences."""

    cognitive_style: list[str] = field(default_factory=list)
    style: StylePreference = field(default_factory=StylePreference)
    context: ContextMode = field(default_factory=ContextMode)
    exploration_openness: float = 0.5


@dataclass
class OnionProfile:
    """Five-layer onion model personality profile.

    Layers (inner to outer): Core → Values → Interest → Role → Surface.
    """

    core: CoreLayer = field(default_factory=CoreLayer)
    values_layer: ValuesLayer = field(default_factory=ValuesLayer)
    interest: InterestLayer = field(default_factory=InterestLayer)
    role: RoleLayer = field(default_factory=RoleLayer)
    surface: SurfaceLayer = field(default_factory=SurfaceLayer)

    personality_portrait: str = ""

    recent_awareness: list[AwarenessNote] = field(default_factory=list)
    active_insights: list[InsightHypothesis] = field(default_factory=list)

    # Normalized {platform: share} computed from observed events. Downstream
    # prompts read this to know whether the user is single- or multi-source.
    source_platform_mix: dict[str, float] = field(default_factory=dict)

    created_at: str = ""
    updated_at: str = ""
    version: int = 2

    # -- Backward-compatible shim properties ----------------------------------

    @property
    def core_traits(self) -> list[str]:
        return self.core.core_traits

    @property
    def deep_needs(self) -> list[str]:
        return self.core.deep_needs

    @property
    def cognitive_style(self) -> list[str]:
        return self.surface.cognitive_style

    @property
    def motivational_drivers(self) -> list[str]:
        return self.values_layer.motivational_drivers

    @property
    def values(self) -> list[str]:
        return self.values_layer.values

    @property
    def life_stage(self) -> str:
        return self.role.life_stage

    @property
    def current_phase(self) -> str:
        return self.role.current_phase

    @property
    def preferences(self) -> PreferenceLayer:
        """Synthesize a flat PreferenceLayer from onion layers."""
        flat_interests: list[InterestTag] = []
        for dom in self.interest.likes:
            # Always include the domain itself as a top-level interest
            flat_interests.append(
                InterestTag(name=dom.domain, category=dom.domain, weight=dom.weight)
            )
            for spec in dom.specifics:
                flat_interests.append(
                    InterestTag(name=spec.name, category=dom.domain, weight=spec.weight)
                )
        flat_disliked: list[str] = []
        for dom in self.interest.dislikes:
            flat_disliked.append(dom.domain)
            for spec in dom.specifics:
                flat_disliked.append(spec.name)
        return PreferenceLayer(
            interests=flat_interests,
            style=self.surface.style,
            context=self.surface.context,
            exploration_openness=self.surface.exploration_openness,
            disliked_topics=flat_disliked,
            favorite_up_users=self.interest.favorite_up_users,
            source_platform_mix=dict(self.source_platform_mix),
        )

    # -- Mutation helpers ------------------------------------------------------

    def populate_from_flat_preference(self, preference_data: dict[str, object]) -> None:
        """Update interest and surface layers from a flat preference dict."""
        pref = preference_layer_from_dict(preference_data)
        # Build interest tree from flat tags
        domain_map: dict[str, InterestDomain] = {}
        for tag in pref.interests:
            key = tag.category or tag.name
            if key not in domain_map:
                domain_map[key] = InterestDomain(
                    domain=key,
                    weight=tag.weight,
                    source=tag.source,
                )
            dom = domain_map[key]
            if tag.name != key:
                dom.specifics.append(InterestSpecific(name=tag.name, weight=tag.weight))
            if tag.weight > dom.weight:
                dom.weight = tag.weight
        self.interest = InterestLayer(
            likes=list(domain_map.values()),
            dislikes=[InterestDomain(domain=topic, weight=0.9) for topic in pref.disliked_topics],
            favorite_up_users=list(pref.favorite_up_users),
        )
        self.surface.style = pref.style
        self.surface.context = pref.context
        self.surface.exploration_openness = pref.exploration_openness
        if pref.source_platform_mix:
            self.source_platform_mix = dict(pref.source_platform_mix)

    # -- Serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "personality_portrait": self.personality_portrait,
            "core": _core_layer_to_dict(self.core),
            "values_layer": _values_layer_to_dict(self.values_layer),
            "interest": _interest_layer_to_dict(self.interest),
            "role": _role_layer_to_dict(self.role),
            "surface": _surface_layer_to_dict(self.surface),
            "source_platform_mix": dict(self.source_platform_mix),
            "recent_awareness": [awareness_note_to_dict(n) for n in self.recent_awareness],
            "active_insights": [insight_hypothesis_to_dict(h) for h in self.active_insights],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw_data: dict[str, object]) -> OnionProfile:
        if "core" not in raw_data and "core_traits" in raw_data:
            return cls.from_legacy(SoulProfile.from_dict(raw_data))
        return cls(
            version=_as_int(raw_data.get("version", 2)),
            personality_portrait=str(raw_data.get("personality_portrait", "")),
            core=_core_layer_from_dict(raw_data.get("core")),
            values_layer=_values_layer_from_dict(raw_data.get("values_layer")),
            interest=_interest_layer_from_dict(raw_data.get("interest")),
            role=_role_layer_from_dict(raw_data.get("role")),
            surface=_surface_layer_from_dict(raw_data.get("surface")),
            source_platform_mix=_as_source_mix(raw_data.get("source_platform_mix")),
            recent_awareness=[
                awareness_note_from_dict(item)
                for item in _as_list(raw_data.get("recent_awareness"))
                if isinstance(item, dict)
            ],
            active_insights=[
                insight_hypothesis_from_dict(item)
                for item in _as_list(raw_data.get("active_insights"))
                if isinstance(item, dict)
            ],
            created_at=str(raw_data.get("created_at", "")),
            updated_at=str(raw_data.get("updated_at", "")),
        )

    @classmethod
    def from_legacy(cls, soul: SoulProfile) -> OnionProfile:
        """Migrate a flat SoulProfile into the onion structure."""
        # Group flat InterestTags into tree by category
        domain_map: dict[str, InterestDomain] = {}
        for tag in soul.preferences.interests:
            key = tag.category or tag.name
            if key not in domain_map:
                domain_map[key] = InterestDomain(
                    domain=key,
                    weight=tag.weight,
                    first_seen=tag.first_seen.isoformat() if tag.first_seen else "",
                    last_seen=tag.last_seen.isoformat() if tag.last_seen else "",
                    source=tag.source,
                )
            dom = domain_map[key]
            if tag.name != key:
                dom.specifics.append(InterestSpecific(name=tag.name, weight=tag.weight))
            if tag.weight > dom.weight:
                dom.weight = tag.weight

        likes = list(domain_map.values())

        dislikes = [
            InterestDomain(domain=topic, weight=0.9) for topic in soul.preferences.disliked_topics
        ]

        # Extract MBTI if the builder attached raw data
        raw_mbti = getattr(soul, "_raw_mbti", None)
        mbti = _mbti_from_dict(raw_mbti) if raw_mbti else MBTI()

        return cls(
            core=CoreLayer(
                core_traits=list(soul.core_traits),
                deep_needs=list(soul.deep_needs),
                mbti=mbti,
            ),
            values_layer=ValuesLayer(
                values=list(soul.values),
                motivational_drivers=list(soul.motivational_drivers),
            ),
            interest=InterestLayer(
                likes=likes,
                dislikes=dislikes,
                favorite_up_users=list(soul.preferences.favorite_up_users),
            ),
            role=RoleLayer(
                life_stage=soul.life_stage,
                current_phase=soul.current_phase,
            ),
            surface=SurfaceLayer(
                cognitive_style=list(soul.cognitive_style),
                style=soul.preferences.style,
                context=soul.preferences.context,
                exploration_openness=soul.preferences.exploration_openness,
            ),
            personality_portrait=soul.personality_portrait,
            recent_awareness=list(soul.recent_awareness),
            active_insights=list(soul.active_insights),
            created_at=soul.created_at,
            updated_at=soul.updated_at,
            version=2,
        )

    def to_llm_context(self) -> str:
        parts: list[str] = []
        if self.personality_portrait:
            parts.append(f"## 用户画像\n{self.personality_portrait}")
        if self.core.core_traits:
            parts.append(f"## 核心特质\n{', '.join(self.core.core_traits)}")
        if self.core.mbti.type:
            mbti = self.core.mbti
            parts.append(f"## MBTI\n{mbti.type} (置信度: {mbti.confidence:.0%})")
        if self.values_layer.values:
            parts.append(f"## 价值观\n{', '.join(self.values_layer.values)}")
        if self.values_layer.motivational_drivers:
            parts.append(f"## 内在驱动力\n{', '.join(self.values_layer.motivational_drivers)}")
        if self.role.current_phase:
            parts.append(f"## 当前阶段\n{self.role.current_phase}")
        if self.core.deep_needs:
            parts.append(f"## 深层需求\n{', '.join(self.core.deep_needs)}")
        if self.interest.likes:
            lines: list[str] = []
            for dom in self.interest.likes[:5]:
                spec_names = ", ".join(s.name for s in dom.specifics[:3])
                detail = f" ({spec_names})" if spec_names else ""
                lines.append(f"- {dom.domain}{detail}")
            parts.append("## 兴趣\n" + "\n".join(lines))
        if self.interest.dislikes:
            dislike_names = ", ".join(d.domain for d in self.interest.dislikes[:5])
            parts.append(f"## 不喜欢\n{dislike_names}")
        if len(self.source_platform_mix) > 1:
            mix_line = _format_source_mix_line(self.source_platform_mix)
            if mix_line:
                parts.append(f"## 来源分布\n{mix_line}")
        # Speculative interests (set externally via attach_speculations)
        speculations = getattr(self, "_active_speculations", None)
        if speculations:
            spec_lines = [
                f"- {s.get('domain', '')}（{s.get('reason', '')}）"
                if isinstance(s, dict)
                else f"- {s.domain}（{s.reason}）"
                for s in speculations[:5]
            ]
            parts.append("## 猜测兴趣（待验证）\n" + "\n".join(spec_lines))
        if self.active_insights:
            insights_text = "\n".join(
                f"- {i.hypothesis} (置信度: {i.confidence:.0%})" for i in self.active_insights
            )
            parts.append(f"## 当前洞察\n{insights_text}")
        if self.recent_awareness:
            notes = "\n".join(f"- [{n.date}] {n.observation}" for n in self.recent_awareness[:5])
            parts.append(f"## 近期观察\n{notes}")
        return "\n\n".join(parts) if parts else "（尚未建立用户画像）"


# -- Onion layer serialization helpers ----------------------------------------


def _mbti_dimension_to_dict(dim: MBTIDimension) -> dict[str, object]:
    return {"pole": dim.pole, "strength": dim.strength}


def _mbti_dimension_from_dict(raw: object) -> MBTIDimension:
    data = raw if isinstance(raw, dict) else {}
    return MBTIDimension(
        pole=str(data.get("pole", "")),
        strength=_as_float(data.get("strength", 0.5), 0.5),
    )


def _mbti_to_dict(mbti: MBTI) -> dict[str, object]:
    return {
        "type": mbti.type,
        "dimensions": {k: _mbti_dimension_to_dict(v) for k, v in mbti.dimensions.items()},
        "confidence": mbti.confidence,
        "inferred_from": mbti.inferred_from,
    }


def _mbti_from_dict(raw: object) -> MBTI:
    data = raw if isinstance(raw, dict) else {}
    raw_dims = data.get("dimensions")
    dims: dict[str, MBTIDimension] = {}
    if isinstance(raw_dims, dict):
        for k, v in raw_dims.items():
            dims[str(k)] = _mbti_dimension_from_dict(v)
    return MBTI(
        type=str(data.get("type", "")),
        dimensions=dims,
        confidence=_as_float(data.get("confidence", 0.0), 0.0),
        inferred_from=_as_str_list(data.get("inferred_from")),
    )


def _core_layer_to_dict(layer: CoreLayer) -> dict[str, object]:
    return {
        "core_traits": layer.core_traits,
        "deep_needs": layer.deep_needs,
        "mbti": _mbti_to_dict(layer.mbti),
    }


def _core_layer_from_dict(raw: object) -> CoreLayer:
    data = raw if isinstance(raw, dict) else {}
    return CoreLayer(
        core_traits=_as_str_list(data.get("core_traits")),
        deep_needs=_as_str_list(data.get("deep_needs")),
        mbti=_mbti_from_dict(data.get("mbti")),
    )


def _values_layer_to_dict(layer: ValuesLayer) -> dict[str, object]:
    return {
        "values": layer.values,
        "motivational_drivers": layer.motivational_drivers,
    }


def _values_layer_from_dict(raw: object) -> ValuesLayer:
    data = raw if isinstance(raw, dict) else {}
    return ValuesLayer(
        values=_as_str_list(data.get("values")),
        motivational_drivers=_as_str_list(data.get("motivational_drivers")),
    )


def _interest_specific_to_dict(spec: InterestSpecific) -> dict[str, object]:
    return {"name": spec.name, "weight": spec.weight}


def _interest_specific_from_dict(raw: object) -> InterestSpecific:
    data = raw if isinstance(raw, dict) else {}
    return InterestSpecific(
        name=str(data.get("name", "")),
        weight=_as_float(data.get("weight", 0.5), 0.5),
    )


def _interest_domain_to_dict(dom: InterestDomain) -> dict[str, object]:
    return {
        "domain": dom.domain,
        "weight": dom.weight,
        "specifics": [_interest_specific_to_dict(s) for s in dom.specifics],
        "first_seen": dom.first_seen,
        "last_seen": dom.last_seen,
        "source": dom.source,
    }


def _interest_domain_from_dict(raw: object) -> InterestDomain:
    data = raw if isinstance(raw, dict) else {}
    return InterestDomain(
        domain=str(data.get("domain", "")),
        weight=_as_float(data.get("weight", 0.5), 0.5),
        specifics=[
            _interest_specific_from_dict(item)
            for item in _as_list(data.get("specifics"))
            if isinstance(item, dict)
        ],
        first_seen=str(data.get("first_seen", "")),
        last_seen=str(data.get("last_seen", "")),
        source=str(data.get("source", "")),
    )


def _interest_layer_to_dict(layer: InterestLayer) -> dict[str, object]:
    return {
        "likes": [_interest_domain_to_dict(d) for d in layer.likes],
        "dislikes": [_interest_domain_to_dict(d) for d in layer.dislikes],
        "favorite_up_users": layer.favorite_up_users,
    }


def _interest_layer_from_dict(raw: object) -> InterestLayer:
    data = raw if isinstance(raw, dict) else {}
    return InterestLayer(
        likes=[
            _interest_domain_from_dict(item)
            for item in _as_list(data.get("likes"))
            if isinstance(item, dict)
        ],
        dislikes=[
            _interest_domain_from_dict(item)
            for item in _as_list(data.get("dislikes"))
            if isinstance(item, dict)
        ],
        favorite_up_users=_as_str_list(data.get("favorite_up_users")),
    )


def _role_layer_to_dict(layer: RoleLayer) -> dict[str, object]:
    return {
        "life_stage": layer.life_stage,
        "current_phase": layer.current_phase,
    }


def _role_layer_from_dict(raw: object) -> RoleLayer:
    data = raw if isinstance(raw, dict) else {}
    return RoleLayer(
        life_stage=str(data.get("life_stage", "")),
        current_phase=str(data.get("current_phase", "")),
    )


def _surface_layer_to_dict(layer: SurfaceLayer) -> dict[str, object]:
    return {
        "cognitive_style": layer.cognitive_style,
        "style": style_preference_to_dict(layer.style),
        "context": context_mode_to_dict(layer.context),
        "exploration_openness": layer.exploration_openness,
    }


def _surface_layer_from_dict(raw: object) -> SurfaceLayer:
    data = raw if isinstance(raw, dict) else {}
    return SurfaceLayer(
        cognitive_style=_as_str_list(data.get("cognitive_style")),
        style=style_preference_from_dict(data.get("style")),
        context=context_mode_from_dict(data.get("context")),
        exploration_openness=_as_float(data.get("exploration_openness", 0.5), 0.5),
    )
