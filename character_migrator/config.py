from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import ALLOWED_TABLES

_FLAGS = {
    "stop_if_player_exists": True,
    "migrate_all_outfits": False,
    "strict_required_columns": True,
}
_SECTIONS = ("skip_columns", "defaults", "overrides")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TableRules:
    skip: frozenset[str] = frozenset()
    defaults: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MigrationConfig:
    stop_if_player_exists: bool = True
    migrate_all_outfits: bool = False
    strict_required_columns: bool = True
    tables: dict[str, TableRules] = field(default_factory=dict)

    def rules(self, table: str) -> TableRules:
        return self.tables.get(table, TableRules())


def load_config(path: Path) -> MigrationConfig:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    return parse_config(raw)


def parse_config(raw: Any) -> MigrationConfig:
    """Validate the whole file up front and report every problem at once."""
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a JSON object.")

    errors: list[str] = []

    unknown = set(raw) - {"migration", *_SECTIONS}
    if unknown:
        errors.append(f"unknown top-level keys: {', '.join(sorted(unknown))}")

    flags = _parse_flags(raw.get("migration", {}), errors)

    per_table: dict[str, dict[str, Any]] = {}
    for section in _SECTIONS:
        entries = raw.get(section, {})
        if not isinstance(entries, dict):
            errors.append(f"{section} must be an object keyed by table name")
            continue
        for table, value in entries.items():
            where = f"{section}.{table}"
            if table not in ALLOWED_TABLES:
                errors.append(f"{where}: table is not supported")
                continue
            if section == "skip_columns":
                if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
                    errors.append(f"{where} must be a list of column names")
                    continue
            else:
                if not isinstance(value, dict):
                    errors.append(f"{where} must be an object of column -> value")
                    continue
                for column, column_value in value.items():
                    errors.extend(_check_embedded_json(f"{where}.{column}", column_value))
            per_table.setdefault(table, {})[section] = value

    if errors:
        raise ConfigError("Invalid config:\n  - " + "\n  - ".join(errors))

    tables = {
        table: TableRules(
            skip=frozenset(sections.get("skip_columns", [])),
            defaults=dict(sections.get("defaults", {})),
            overrides=dict(sections.get("overrides", {})),
        )
        for table, sections in per_table.items()
    }
    return MigrationConfig(**flags, tables=tables)


def _parse_flags(migration: Any, errors: list[str]) -> dict[str, bool]:
    if not isinstance(migration, dict):
        errors.append("migration must be an object")
        return dict(_FLAGS)

    unknown = set(migration) - set(_FLAGS)
    if unknown:
        errors.append(f"unknown migration options: {', '.join(sorted(unknown))}")

    flags = {}
    for name, default in _FLAGS.items():
        value = migration.get(name, default)
        if not isinstance(value, bool):
            errors.append(f"migration.{name} must be true or false")
            value = default
        flags[name] = value
    return flags


def _check_embedded_json(where: str, value: Any) -> list[str]:
    # Columns such as players.money/job/inventory hold JSON text that the game server
    # decodes on character load. A typo here corrupts every migrated character, so a
    # string that looks like a JSON document has to parse.
    if not isinstance(value, str) or value.lstrip()[:1] not in ("{", "["):
        return []
    try:
        json.loads(value)
    except json.JSONDecodeError as exc:
        return [f"{where}: invalid JSON ({exc.msg} at character {exc.pos})"]
    return []
