from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mysql.connector import MySQLConnection

# Table names are interpolated into SQL, so every query is gated by this allow-list.
ALLOWED_TABLES = frozenset(
    {
        "players",
        "playerskins",
        "murphy_creator",
        "murphy_barber",
        "murphy_barber_preset",
        "murphy_clothes",
        "murphy_outfits",
    }
)


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    nullable: bool = False
    default: Any = None
    extra: str = ""

    @property
    def auto_increment(self) -> bool:
        return "auto_increment" in self.extra.lower()

    @property
    def generated(self) -> bool:
        return "generated" in self.extra.lower()


def validate_table(table: str) -> None:
    if table not in ALLOWED_TABLES:
        raise ValueError(f"Table not allowed: {table}")


def get_columns(conn: MySQLConnection, table: str) -> dict[str, ColumnInfo]:
    validate_table(table)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"SHOW COLUMNS FROM `{table}`")
        rows = cursor.fetchall()
    finally:
        cursor.close()

    if not rows:
        raise RuntimeError(f"Table {table} does not exist or has no columns.")

    return {
        row["Field"]: ColumnInfo(
            name=row["Field"],
            nullable=row["Null"] == "YES",
            default=row["Default"],
            extra=row.get("Extra") or "",
        )
        for row in rows
    }
