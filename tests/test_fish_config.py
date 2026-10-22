"""Blue Fish Lab config store: clamping, defaults, corruption tolerance."""

from __future__ import annotations

import json

from openbiliclaw.fish.config import (
    DEFAULT_PERSONA_PROMPT,
    FishConfig,
    fish_config_path,
    load_fish_config,
    parse_active_hours,
    parse_fish_config,
    save_fish_config,
)


def test_parse_uses_defaults_for_missing_keys() -> None:
    config, warnings = parse_fish_config({})

    assert config == FishConfig()
    assert warnings == []


def test_parse_clamps_out_of_range_numbers() -> None:
    config, warnings = parse_fish_config(
        {"max_chars": 99_999, "daily_limit": -3, "min_interval_minutes": 1}
    )

    assert config.max_chars == 300
    assert config.daily_limit == 0
    assert config.min_interval_minutes == 5
    assert any("max_chars" in warning for warning in warnings)
    assert any("daily_limit" in warning for warning in warnings)


def test_parse_falls_back_on_non_numeric_values() -> None:
    config, warnings = parse_fish_config({"max_chars": "abc", "daily_limit": None})

    assert config.max_chars == FishConfig().max_chars
    assert config.daily_limit == FishConfig().daily_limit
    # A missing key is normal and silent; only a present-but-invalid value warns.
    assert len(warnings) == 1
    assert "max_chars" in warnings[0]


def test_parse_keeps_custom_persona_and_drops_blank() -> None:
    custom, _ = parse_fish_config({"persona_prompt": "  你是一只害羞的鱼  "})
    blank, _ = parse_fish_config({"persona_prompt": "   "})

    assert custom.persona_prompt == "你是一只害羞的鱼"
    assert blank.persona_prompt == DEFAULT_PERSONA_PROMPT


def test_parse_dedupes_and_caps_blocked_words() -> None:
    words = ["剧透", "剧透", "  ", *[f"w{i}" for i in range(80)]]
    config, warnings = parse_fish_config({"blocked_words": words})

    assert config.blocked_words[:2] == ["剧透", "w0"]
    assert len(config.blocked_words) == 50
    assert any("blocked_words" in warning for warning in warnings)


def test_save_then_load_round_trips(tmp_path) -> None:
    config = FishConfig(enabled=True, dry_run=False, persona_name="小蓝", blocked_words=["剧透"])

    saved = save_fish_config(tmp_path, config)
    loaded = load_fish_config(tmp_path)

    assert saved.to_public_dict() == loaded.to_public_dict()
    assert loaded.enabled is True
    assert loaded.dry_run is False
    assert loaded.blocked_words == ["剧透"]


def test_load_returns_defaults_on_corrupt_json(tmp_path) -> None:
    path = fish_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_fish_config(tmp_path) == FishConfig()


def test_saved_file_is_readable_json(tmp_path) -> None:
    save_fish_config(tmp_path, FishConfig(persona_name="小蓝"))

    payload = json.loads(fish_config_path(tmp_path).read_text(encoding="utf-8"))

    assert payload["persona_name"] == "小蓝"
    assert payload["dry_run"] is True


def test_audience_prompt_defaults_to_unrestricted() -> None:
    config, _warnings = parse_fish_config({})

    assert config.audience_prompt == ""


def test_audience_prompt_is_trimmed_and_truncated() -> None:
    config, warnings = parse_fish_config({"audience_prompt": "  " + "想" * 400 + "  "})

    assert len(config.audience_prompt) == 300
    assert any("audience_prompt" in warning for warning in warnings)


def test_audience_prompt_round_trips_through_save(tmp_path) -> None:
    saved = save_fish_config(
        tmp_path,
        FishConfig(audience_prompt="只想回在找片单的人"),
    )

    assert saved.audience_prompt == "只想回在找片单的人"
    assert load_fish_config(tmp_path).audience_prompt == "只想回在找片单的人"


def test_owner_notification_defaults_to_off() -> None:
    config = FishConfig()

    assert config.notify_owner is False
    assert config.owner_name == ""
    assert config.notify_daily_limit == 1


def test_parse_clamps_the_owner_notification_fields() -> None:
    config, warnings = parse_fish_config(
        {"notify_owner": "yes", "owner_name": "  " + "主" * 40, "notify_daily_limit": 99}
    )

    assert config.notify_owner is True
    assert len(config.owner_name) == 30
    assert config.notify_daily_limit == 3
    assert any("notify_daily_limit" in warning for warning in warnings)


def test_owner_name_survives_a_save_round_trip(tmp_path) -> None:
    saved = save_fish_config(
        tmp_path,
        FishConfig(enabled=True, notify_owner=True, owner_name="老白", notify_daily_limit=2),
    )

    assert saved.notify_owner is True
    assert saved.owner_name == "老白"
    assert saved.notify_daily_limit == 2
    reloaded = load_fish_config(tmp_path)
    assert reloaded == saved


def test_notify_channel_is_whitelisted() -> None:
    both, _ = parse_fish_config({"notify_channel": "both"})
    dm, _ = parse_fish_config({"notify_channel": "DM"})
    bad, warnings = parse_fish_config({"notify_channel": "telegram"})

    assert both.notify_channel == "both"
    assert dm.notify_channel == "dm"
    assert bad.notify_channel == "comment"
    assert any("notify_channel" in warning for warning in warnings)


def test_active_hours_parses_and_rejects_bad_windows() -> None:
    good, _ = parse_fish_config({"active_hours": "09:00-23:00"})
    overnight, _ = parse_fish_config({"active_hours": "18:00-09:00"})
    bad, warnings = parse_fish_config({"active_hours": "晚上八点以后"})

    assert good.active_hours == "09:00-23:00"
    assert overnight.active_hours == "18:00-09:00"
    assert bad.active_hours == ""
    assert any("active_hours" in warning for warning in warnings)


def test_parse_active_hours_returns_minutes() -> None:
    assert parse_active_hours("09:30-23:05") == (570, 1385)
    assert parse_active_hours("24:00-25:00") is None
    assert parse_active_hours("") is None


def test_owner_mid_clamps_and_round_trips() -> None:
    parsed, _ = parse_fish_config({"owner_mid": 222156})
    negative, warnings = parse_fish_config({"owner_mid": -5})

    assert parsed.owner_mid == 222156
    assert negative.owner_mid == 0
    assert any("owner_mid" in warning for warning in warnings)
