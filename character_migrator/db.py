from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import MySQLConnection


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    connect_timeout: int = 10

    def __repr__(self) -> str:
        # keep the password out of tracebacks
        return f"DatabaseSettings({self.user}@{self.host}:{self.port}/{self.database})"


def load_db_settings(env_file: Path) -> tuple[DatabaseSettings, DatabaseSettings]:
    """Return (source, target) settings from the environment / .env file."""
    load_dotenv(env_file)
    return _env_db("CREATIVE"), _env_db("LIVE")


def _env_db(prefix: str) -> DatabaseSettings:
    required = [f"{prefix}_DB_{name}" for name in ("HOST", "USER", "PASSWORD", "NAME")]
    missing = [name for name in required if os.getenv(name) is None]
    if missing:
        raise RuntimeError("Missing variables in .env: " + ", ".join(missing))

    return DatabaseSettings(
        host=os.environ[f"{prefix}_DB_HOST"],
        port=int(os.getenv(f"{prefix}_DB_PORT", "3306")),
        user=os.environ[f"{prefix}_DB_USER"],
        password=os.environ[f"{prefix}_DB_PASSWORD"],
        database=os.environ[f"{prefix}_DB_NAME"],
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
    )


def connect(settings: DatabaseSettings) -> MySQLConnection:
    return mysql.connector.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        charset="utf8mb4",
        use_unicode=True,
        autocommit=False,
        connection_timeout=settings.connect_timeout,
    )
