import json
from pathlib import Path

import pytest

from character_migrator.config import ConfigError, load_config, parse_config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"


def test_example_config_is_valid():
    config = load_config(EXAMPLE)
    assert config.stop_if_player_exists is True
    assert "id" in config.rules("players").skip


def test_empty_config_uses_safe_defaults():
    config = parse_config({})
    assert config.stop_if_player_exists is True
    assert config.strict_required_columns is True
    assert config.migrate_all_outfits is False
    assert config.rules("players").overrides == {}


def test_malformed_embedded_json_is_rejected():
    # Real-world bug this check was written for: missing commas in a money override.
    broken = '{"bank":0.0,"tokbank":0.0"armbank":0.0}'
    with pytest.raises(ConfigError, match=r"overrides\.players\.money: invalid JSON"):
        parse_config({"overrides": {"players": {"money": broken}}})


def test_plain_strings_are_not_parsed_as_json():
    config = parse_config({"overrides": {"players": {"char_governorate": "none"}}})
    assert config.rules("players").overrides["char_governorate"] == "none"


def test_unknown_table_is_rejected():
    with pytest.raises(ConfigError, match="skip_columns.users: table is not supported"):
        parse_config({"skip_columns": {"users": ["id"]}})


def test_flags_must_be_booleans():
    with pytest.raises(ConfigError, match="migration.stop_if_player_exists"):
        parse_config({"migration": {"stop_if_player_exists": "yes"}})


def test_all_problems_are_reported_together():
    with pytest.raises(ConfigError) as exc:
        parse_config(
            {
                "typo_section": {},
                "skip_columns": {"players": "id"},
                "defaults": {"players": {"job": "{not json"}},
            }
        )
    message = str(exc.value)
    assert "typo_section" in message
    assert "skip_columns.players must be a list" in message
    assert "defaults.players.job: invalid JSON" in message


def test_load_config_reports_syntax_errors(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"migration": {', encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


def test_load_config_reads_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"migration": {"migrate_all_outfits": True}}), encoding="utf-8")
    assert load_config(path).migrate_all_outfits is True
