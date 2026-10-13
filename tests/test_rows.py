import pytest

from character_migrator.config import TableRules
from character_migrator.rows import MigrationError, resolve_row
from character_migrator.schema import ColumnInfo


def schema(*columns: ColumnInfo) -> dict[str, ColumnInfo]:
    return {c.name: c for c in columns}


def col(name: str, **kwargs) -> ColumnInfo:
    return ColumnInfo(name, **kwargs)


NO_RULES = TableRules()


def run(source_row, source_schema, target_schema, rules=NO_RULES, strict=True):
    return resolve_row("players", source_row, source_schema, target_schema, rules, strict)


def test_copies_values_present_on_both_sides():
    s = schema(col("citizenid"), col("name"))
    row, notes = run({"citizenid": "ABC12345", "name": "Arthur"}, s, s)
    assert row == {"citizenid": "ABC12345", "name": "Arthur"}
    assert notes == []


def test_skip_columns_and_generated_columns_are_left_out():
    s = schema(col("id", extra="auto_increment"), col("name"))
    t = schema(
        col("id", extra="auto_increment"), col("name"), col("slug", extra="VIRTUAL GENERATED")
    )
    row, notes = run({"id": 7, "name": "Arthur"}, s, t, TableRules(skip=frozenset({"id"})))
    assert row == {"name": "Arthur"}
    assert "id: skipped" in notes
    assert "slug: skipped" in notes


def test_override_wins_over_source_value():
    s = schema(col("money"))
    row, _ = run({"money": '{"cash": 900}'}, s, s, TableRules(overrides={"money": '{"cash": 0}'}))
    assert row == {"money": '{"cash": 0}'}


def test_source_null_uses_config_default():
    s = schema(col("job"))
    row, notes = run({"job": None}, s, s, TableRules(defaults={"job": "unemployed"}))
    assert row == {"job": "unemployed"}
    assert notes == ["job: source NULL -> default from config"]


def test_source_null_is_omitted_when_target_has_default():
    s = schema(col("slots"))
    t = schema(col("slots", default="25"))
    row, notes = run({"slots": None}, s, t)
    assert row == {}
    assert notes == ["slots: source NULL -> target DEFAULT"]


def test_source_null_is_kept_for_nullable_target_column():
    s = schema(col("telegram_id", nullable=True))
    row, _ = run({"telegram_id": None}, s, s)
    assert row == {"telegram_id": None}


def test_source_null_into_required_column_stops_in_strict_mode():
    s = schema(col("weight"))
    with pytest.raises(MigrationError, match="players.weight"):
        run({"weight": None}, s, s)


def test_column_missing_in_source_uses_config_default():
    s = schema(col("citizenid"))
    t = schema(col("citizenid"), col("slots"))
    row, notes = run({"citizenid": "ABC12345"}, s, t, TableRules(defaults={"slots": 25}))
    assert row == {"citizenid": "ABC12345", "slots": 25}
    assert notes == ["slots: missing in source -> default from config"]


@pytest.mark.parametrize(
    "target_column",
    [
        col("id", extra="auto_increment"),
        col("created_at", default="current_timestamp()"),
        col("note", nullable=True),
    ],
)
def test_column_missing_in_source_is_left_to_target(target_column):
    s = schema(col("citizenid"))
    t = schema(col("citizenid"), target_column)
    row, _ = run({"citizenid": "ABC12345"}, s, t)
    assert row == {"citizenid": "ABC12345"}


def test_new_required_column_stops_in_strict_mode():
    s = schema(col("citizenid"))
    t = schema(col("citizenid"), col("governorate"))
    with pytest.raises(MigrationError, match="players.governorate: missing in source"):
        run({"citizenid": "ABC12345"}, s, t)


def test_new_required_column_is_left_out_when_strict_mode_is_off():
    s = schema(col("citizenid"))
    t = schema(col("citizenid"), col("governorate"))
    row, notes = run({"citizenid": "ABC12345"}, s, t, strict=False)
    assert row == {"citizenid": "ABC12345"}
    assert "strict mode off" in notes[0]


def test_source_only_columns_are_reported():
    s = schema(col("citizenid"), col("legacy_flag"))
    t = schema(col("citizenid"))
    _, notes = run({"citizenid": "ABC12345", "legacy_flag": 1}, s, t)
    assert notes == ["source-only columns dropped: legacy_flag"]
