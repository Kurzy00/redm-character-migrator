from pathlib import Path

from character_migrator import cli
from character_migrator.cli import main, read_charids
from character_migrator.db import parse_environments

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


# --- interactive wizard ---------------------------------------------------------------


def _profile(key, name, protected="0"):
    p = f"ENV_{key.upper()}"
    return {
        f"{p}_HOST": "db.example",
        f"{p}_USER": "u",
        f"{p}_PASSWORD": "p",
        f"{p}_NAME": name,
        f"{p}_PROTECTED": protected,
    }


REGISTRY = parse_environments(
    {
        "MIGRATOR_ENVS": "creative,live",
        "MIGRATOR_DEFAULT_FROM": "creative",
        "MIGRATOR_DEFAULT_TO": "live",
        **_profile("creative", "creative"),
        **_profile("live", "live", protected="1"),
    }
)


def wizard(monkeypatch, answers, tmp_path):
    """Run main() as the wizard with scripted console input; connecting is forbidden."""
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    monkeypatch.setattr(cli, "load_environments", lambda _: REGISTRY)
    monkeypatch.setattr(cli, "ROOT_DIR", tmp_path)

    def no_db(_):
        raise RuntimeError("would connect")

    monkeypatch.setattr(cli, "connect", no_db)
    return cli.main(["--config", str(EXAMPLE)])


def test_wizard_defaults_to_test_mode_and_reaches_connect(monkeypatch, tmp_path, capsys):
    assert wizard(monkeypatch, ["", "", ""], tmp_path) == 2
    out = capsys.readouterr().out
    assert "SOURCE: creative" in out
    assert "TARGET: live" in out
    assert "TEST (rollback)" in out
    assert "would connect" in out


def test_wizard_protected_target_needs_its_name(monkeypatch, tmp_path, capsys):
    assert wizard(monkeypatch, ["1", "1", "2", "nope"], tmp_path) == 2
    out = capsys.readouterr().out
    assert "MIGRATE (write)" in out
    assert "Name does not match" in out
    assert "would connect" not in out


def test_wizard_target_menu_hides_the_source(monkeypatch, tmp_path, capsys):
    # source "live" by name -> target menu has only "creative"
    assert wizard(monkeypatch, ["live", "1", ""], tmp_path) == 2
    assert "TARGET: creative" in capsys.readouterr().out


def test_ask_charids_splits_and_dedupes(monkeypatch):
    replies = iter(["", "AAA1, BBB2 AAA1;CCC3"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    assert cli.ask_charids(first=True) == ["AAA1", "BBB2", "CCC3"]


def test_ask_charids_reads_a_file_and_empty_input_ends(monkeypatch, tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("# comment\nAAA1\nBBB2\n", encoding="utf-8")
    replies = iter([str(ids), ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    assert cli.ask_charids(first=False) == ["AAA1", "BBB2"]
    assert cli.ask_charids(first=False) == []


def test_yes_with_protected_target_requires_explicit_to(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_environments", lambda _: REGISTRY)
    monkeypatch.setattr(cli, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    replies = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    assert cli.main(["--config", str(EXAMPLE), "--yes", "ABC12345"]) == 2
    assert "pass it explicitly as --to" in capsys.readouterr().out
