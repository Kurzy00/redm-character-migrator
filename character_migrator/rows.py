"""Map a source row onto the target table's schema.

Pure logic with no database access, so the rules can be unit-tested in isolation.
"""

from __future__ import annotations

from typing import Any

from .config import TableRules
from .schema import ColumnInfo


class MigrationError(RuntimeError):
    """The character can't be migrated safely; nothing was written."""


def resolve_row(
    table: str,
    source_row: dict[str, Any],
    source_schema: dict[str, ColumnInfo],
    target_schema: dict[str, ColumnInfo],
    rules: TableRules,
    strict: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Return (values to INSERT, human-readable notes explaining each non-trivial decision).

    For every target column, in order of precedence:
      1. skip_columns / generated columns are left out;
      2. overrides always win;
      3. a non-NULL source value is copied;
      4. a config default fills a NULL or missing source value;
      5. the column is omitted when the target can fill it (DEFAULT, AUTO_INCREMENT, NULL);
      6. otherwise the row cannot be built and, in strict mode, migration stops before INSERT.
    """
    result: dict[str, Any] = {}
    notes: list[str] = []

    for column, info in target_schema.items():
        if column in rules.skip or info.generated:
            notes.append(f"{column}: skipped")
            continue

        if column in rules.overrides:
            result[column] = rules.overrides[column]
            notes.append(f"{column}: override from config")
            continue

        in_source = column in source_row and column in source_schema
        value = source_row.get(column) if in_source else None

        if in_source and value is not None:
            result[column] = value
            continue

        # A NULL in the old schema should not clobber a default the new schema relies on,
        # so NULL is handled like a missing value, except that a nullable column keeps it.
        origin = "source NULL" if in_source else "missing in source"

        if column in rules.defaults:
            result[column] = rules.defaults[column]
            notes.append(f"{column}: {origin} -> default from config")
            continue

        if info.auto_increment or info.default is not None:
            notes.append(f"{column}: {origin} -> target DEFAULT")
            continue

        if info.nullable:
            if in_source:
                result[column] = None
            else:
                notes.append(f"{column}: {origin} -> target NULL")
            continue

        if strict:
            raise MigrationError(
                f"{table}.{column}: {origin} and the target column is NOT NULL without DEFAULT. "
                "Add a value to config.json -> defaults (used for NULL/missing) "
                "or overrides (always used)."
            )
        notes.append(f"{column}: {origin}, required by target, left out (strict mode off)")

    source_only = sorted(set(source_row) - set(target_schema))
    if source_only:
        notes.append("source-only columns dropped: " + ", ".join(source_only))

    return result, notes
