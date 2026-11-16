import logging

import pytest
from mysql.connector import errors

from character_migrator.config import parse_config
from character_migrator.migrator import CharacterMigrator, CharacterPayload, TablePayload
from character_migrator.rows import MigrationError


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.log.append(sql.split()[0])
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise errors.IntegrityError(msg="Duplicate entry '5' for key 'outfit_id'", errno=1062)

    def fetchone(self):
        return None

    def close(self):
        pass


class FakeConnection:
    def __init__(self, fail_on=None):
        self.log = []
        self.fail_on = fail_on

    def cursor(self, **_):
        return FakeCursor(self)

    def commit(self):
        self.log.append("COMMIT")

    def rollback(self):
        self.log.append("ROLLBACK")


PAYLOAD = CharacterPayload(
    "ABC12345",
    [
        TablePayload("players", [{"citizenid": "ABC12345"}]),
        TablePayload("murphy_outfits", [{"charid": "ABC12345", "outfit_id": 5}]),
    ],
)


def migrator(target):
    # stop_if_player_exists off: the fake target has no schema to check against
    config = parse_config({"migration": {"stop_if_player_exists": False}})
    return CharacterMigrator(
        FakeConnection(), target, config, logging.getLogger("test"), "src", "dst"
    )


def test_dry_run_executes_inserts_and_rolls_back():
    target = FakeConnection()
    migrator(target).migrate(PAYLOAD, dry_run=True)
    assert target.log == ["INSERT", "INSERT", "ROLLBACK"]


def test_real_run_commits():
    target = FakeConnection()
    migrator(target).migrate(PAYLOAD)
    assert target.log == ["INSERT", "INSERT", "COMMIT"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_duplicate_key_names_the_table_and_rolls_back(dry_run):
    target = FakeConnection(fail_on="murphy_outfits")
    with pytest.raises(MigrationError, match=r"murphy_outfits: row collides .* in dst"):
        migrator(target).migrate(PAYLOAD, dry_run=dry_run)
    assert target.log == ["INSERT", "INSERT", "ROLLBACK"]
