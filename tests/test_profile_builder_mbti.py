"""MBTI output normalization.

The real-account failure: a type letter rested on EI=0.55 (a coin flip), and
the declared type could disagree with the dimension poles that shipped with it.
``ProfileBuilder._normalize_mbti_payload`` reconciles the two and caps
confidence on dead-zone dimensions — it never invents or flips a pole.
"""

from __future__ import annotations

from typing import Any

from openbiliclaw.soul.profile_builder import ProfileBuilder


def _payload(**mbti: Any) -> dict[str, object]:
    return {"mbti": dict(mbti)}


def _dimensions(**strengths: float) -> dict[str, dict[str, object]]:
    poles = {"EI": "E", "SN": "N", "TF": "T", "JP": "P"}
    return {axis: {"pole": poles[axis], "strength": strengths.get(axis, 0.8)} for axis in poles}


def test_declared_type_is_recomputed_from_dimension_poles() -> None:
    payload = _payload(
        type="INTJ",
        confidence=0.7,
        dimensions=_dimensions(),
    )

    ProfileBuilder._normalize_mbti_payload(payload)

    mbti = payload["mbti"]
    assert isinstance(mbti, dict)
    assert mbti["type"] == "ENTP"


def test_weak_dimension_caps_confidence() -> None:
    payload = _payload(type="ENTP", confidence=0.9, dimensions=_dimensions(EI=0.55))

    ProfileBuilder._normalize_mbti_payload(payload)

    mbti = payload["mbti"]
    assert isinstance(mbti, dict)
    assert mbti["type"] == "ENTP"  # letter kept, certainty removed
    assert mbti["confidence"] == 0.55


def test_strong_dimensions_keep_declared_confidence() -> None:
    payload = _payload(type="ENTP", confidence=0.8, dimensions=_dimensions(EI=0.75))

    ProfileBuilder._normalize_mbti_payload(payload)

    mbti = payload["mbti"]
    assert isinstance(mbti, dict)
    assert mbti["confidence"] == 0.8


def test_invalid_pole_is_dropped_not_coerced() -> None:
    dimensions = _dimensions()
    dimensions["EI"] = {"pole": "X", "strength": 0.9}
    payload = _payload(type="ENTP", confidence=0.8, dimensions=dimensions)

    ProfileBuilder._normalize_mbti_payload(payload)

    mbti = payload["mbti"]
    assert isinstance(mbti, dict)
    assert "EI" not in mbti["dimensions"]
    assert mbti["type"] == "NTP"


def test_strength_outside_range_is_clamped() -> None:
    payload = _payload(type="ENTP", confidence=0.8, dimensions=_dimensions(EI=0.2))

    ProfileBuilder._normalize_mbti_payload(payload)

    mbti = payload["mbti"]
    assert isinstance(mbti, dict)
    assert mbti["dimensions"]["EI"]["strength"] == 0.5
    # Clamped to the midpoint, so it is dead-zone evidence and caps confidence.
    assert mbti["confidence"] == 0.55


def test_missing_mbti_is_left_alone() -> None:
    payload: dict[str, object] = {"personality_portrait": "x"}

    ProfileBuilder._normalize_mbti_payload(payload)

    assert "mbti" not in payload
