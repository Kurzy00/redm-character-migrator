from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import MigrationConfig
from .rows import MigrationError, resolve_row
from .schema import ColumnInfo, get_columns, validate_table

if TYPE_CHECKING:
    from mysql.connector import MySQLConnection


@dataclass
class TablePayload:
    table: str
    rows: list[dict[str, Any]]
    notes: list[list[str]] = field(default_factory=list)


@dataclass
class CharacterPayload:
    charid: str
    tables: list[TablePayload]


class CharacterMigrator:
    def __init__(
        self,
        source: MySQLConnection,
        target: MySQLConnection,
        config: MigrationConfig,
        logger: logging.Logger,
    ) -> None:
        self.source = source
        self.target = target
        self.config = config
        self.logger = logger
        self._schemas: dict[tuple[str, str], dict[str, ColumnInfo]] = {}

    def check_connections(self) -> dict[str, str]:
        return {"source": _describe(self.source), "target": _describe(self.target)}

    # --- reading -------------------------------------------------------------------

    def _schema(self, side: str, table: str) -> dict[str, ColumnInfo]:
        key = (side, table)
        if key not in self._schemas:
            conn = self.source if side == "source" else self.target
            self._schemas[key] = get_columns(conn, table)
        return self._schemas[key]

    def _select(
        self,
        table: str,
        where: dict[str, Any],
        order: tuple[str, str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """SELECT from the source; identifiers are checked against the allow-list and schema."""
        validate_table(table)
        schema = self._schema("source", table)
        columns = list(where) + ([order[0]] if order else [])
        for column in columns:
            if column not in schema:
                raise MigrationError(f"Source table {table} has no column {column}.")

        sql = f"SELECT * FROM `{table}` WHERE " + " AND ".join(f"`{c}` = %s" for c in where)
        if order:
            column, direction = order
            if direction not in ("ASC", "DESC"):
                raise ValueError(f"Bad sort direction: {direction}")
            sql += f" ORDER BY `{column}` {direction}"
        if limit:
            sql += f" LIMIT {int(limit)}"

        cur = self.source.cursor(dictionary=True)
        try:
            cur.execute(sql, tuple(where.values()))
            return list(cur.fetchall())
        finally:
            cur.close()

    def _latest(self, table: str, key: str, charid: str, order_column: str) -> list[dict[str, Any]]:
        return self._select(table, {key: charid}, (order_column, "DESC"), limit=1)

    def load_character(self, charid: str) -> CharacterPayload:
        players = self._latest("players", "citizenid", charid, "id")
        if not players:
            raise MigrationError(f"{charid} not found in source players table.")

        clothes = self._latest("murphy_clothes", "charid", charid, "outfit_id")
        if self.config.migrate_all_outfits:
            outfits = self._select("murphy_outfits", {"charid": charid}, ("outfit_id", "ASC"))
        elif clothes and clothes[0].get("outfit_id") is not None:
            # Default: only the outfit the current clothes record points to.
            outfit_id = clothes[0]["outfit_id"]
            outfits = self._select(
                "murphy_outfits", {"charid": charid, "outfit_id": outfit_id}, limit=1
            )
        else:
            outfits = []

        return CharacterPayload(
            charid=charid,
            tables=[
                TablePayload("players", players),
                TablePayload("playerskins", self._latest("playerskins", "citizenid", charid, "id")),
                TablePayload(
                    "murphy_creator", self._latest("murphy_creator", "charid", charid, "id")
                ),
                TablePayload(
                    "murphy_barber", self._latest("murphy_barber", "charid", charid, "outfit_id")
                ),
                TablePayload(
                    "murphy_barber_preset",
                    self._latest("murphy_barber_preset", "charid", charid, "outfit_id"),
                ),
                TablePayload("murphy_clothes", clothes),
                TablePayload("murphy_outfits", outfits),
            ],
        )

    # --- mapping -------------------------------------------------------------------

    def prepare(self, payload: CharacterPayload) -> CharacterPayload:
        """Map every row onto the target schema. Raises before anything is written."""
        prepared = []
        for item in payload.tables:
            rows, notes = [], []
            for source_row in item.rows:
                row, row_notes = resolve_row(
                    item.table,
                    source_row,
                    self._schema("source", item.table),
                    self._schema("target", item.table),
                    self.config.rules(item.table),
                    strict=self.config.strict_required_columns,
                )
                rows.append(row)
                notes.append(row_notes)
            prepared.append(TablePayload(item.table, rows, notes))
        return CharacterPayload(payload.charid, prepared)

    # --- writing -------------------------------------------------------------------

    def _target_has_player(self, charid: str) -> bool:
        if "citizenid" not in self._schema("target", "players"):
            raise MigrationError("Target players table has no citizenid column.")
        cur = self.target.cursor()
        try:
            cur.execute("SELECT 1 FROM `players` WHERE `citizenid` = %s LIMIT 1", (charid,))
            return cur.fetchone() is not None
        finally:
            cur.close()

    def migrate(self, prepared: CharacterPayload, dry_run: bool = False) -> None:
        """INSERT one character in a single transaction; any failure rolls back all its rows."""
        charid = prepared.charid
        if self.config.stop_if_player_exists and self._target_has_player(charid):
            raise MigrationError(
                f"{charid} already exists in target players table. Nothing inserted."
            )

        for item in prepared.tables:
            for row_notes in item.notes:
                for note in row_notes:
                    self.logger.info("%s | %s | %s", charid, item.table, note)

        if dry_run:
            self.logger.info("%s | DRY RUN | no changes in target", charid)
            return

        cursor = self.target.cursor()
        try:
            for item in prepared.tables:
                for row in item.rows:
                    _insert(cursor, item.table, row)
                    self.logger.info("%s | INSERT OK | %s", charid, item.table)
            self.target.commit()
            self.logger.info("%s | COMMIT OK", charid)
        except Exception:
            self.target.rollback()
            self.logger.exception("%s | ROLLBACK", charid)
            raise
        finally:
            cursor.close()


def _insert(cursor, table: str, row: dict[str, Any]) -> None:
    validate_table(table)
    if not row:
        raise MigrationError(f"Prepared row for {table} is empty.")
    columns = list(row)
    sql = "INSERT INTO `{}` ({}) VALUES ({})".format(
        table,
        ", ".join(f"`{c}`" for c in columns),
        ", ".join(["%s"] * len(columns)),
    )
    cursor.execute(sql, tuple(row[c] for c in columns))


def _describe(conn: MySQLConnection) -> str:
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT DATABASE() AS db, VERSION() AS version")
        row = cur.fetchone()
        return f"{row['db']} (MySQL/MariaDB {row['version']})"
    finally:
        cur.close()


def preview_json(value: Any, max_len: int = 140) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."
