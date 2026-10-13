from pathlib import Path

from character_migrator.cli import main, read_charids

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"


def test_read_charids_merges_dedupes_and_keeps_order():
    lines = ["# header comment", "", "  XYZ54321  ", "ABC12345", "   # indented comment"]
    assert read_charids(["ABC12345", "DEF67890"], lines) == ["ABC12345", "DEF67890", "XYZ54321"]


def test_check_config_needs_no_database(capsys):
    assert main(["--check-config", "--config", str(EXAMPLE)]) == 0
    assert "is valid" in capsys.readouterr().out


def test_invalid_config_exits_with_error(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text('{"overrides": {"players": {"money": "{bad"}}}', encoding="utf-8")
    assert main(["--check-config", "--config", str(path)]) == 2
    assert "invalid JSON" in capsys.readouterr().out


def test_missing_charids_exits_before_connecting(capsys):
    assert main(["--config", str(EXAMPLE)]) == 2
    assert "Pass at least one citizenid" in capsys.readouterr().out
