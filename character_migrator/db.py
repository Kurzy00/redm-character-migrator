from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import MySQLConnection

_ENV_KEY = re.compile(r"^[a-z0-9_]+$")
_TRUE = {"1", "true", "yes", "y"}


class EnvConfigError(RuntimeError):
    """The .env file does not describe a usable set of environments."""


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

    def identity(self) -> tuple[str, int, str]:
        """The physical database, regardless of which user connects to it."""
        return (self.host.strip().lower(), self.port, self.database.strip().lower())


@dataclass(frozen=True)
class Environment:
    key: str
    label: str
    db: DatabaseSettings
    protected: bool = False

    def describe(self) -> str:
        flag = " [PROTECTED]" if self.protected else ""
        db = self.db
        return f"{self.key} - {self.label} ({db.database}@{db.host}:{db.port}){flag}"


@dataclass(frozen=True)
class EnvironmentRegistry:
    environments: dict[str, Environment]
    default_source: str | None = None
    default_target: str | None = None

    def get(self, key: str) -> Environment:
        normalized = key.strip().lower()
        if normalized not in self.environments:
            available = ", ".join(self.environments)
            raise EnvConfigError(f"Unknown environment: {key}. Available: {available}")
        return self.environments[normalized]


def load_environments(env_file: Path) -> EnvironmentRegistry:
    """Read database environments from the process environment / .env file."""
    load_dotenv(env_file)
    return parse_environments(os.environ)


def parse_environments(env: Mapping[str, str]) -> EnvironmentRegistry:
    """Build the registry from named profiles (MIGRATOR_ENVS + ENV_<NAME>_*).

    The older two-database layout (CREATIVE_DB_* / LIVE_DB_*) is still accepted and
    exposed as the environments `creative` -> `live`, with `live` protected.
    """
    timeout = int(env.get("DB_CONNECT_TIMEOUT", "10"))
    keys_raw = (env.get("MIGRATOR_ENVS") or "").strip()

    if keys_raw:
        environments: dict[str, Environment] = {}
        for raw_key in keys_raw.split(","):
            key = raw_key.strip().lower()
            if not key:
                continue
            if not _ENV_KEY.match(key):
                raise EnvConfigError(
                    f"Invalid environment name in MIGRATOR_ENVS: {raw_key!r} "
                    "(use lowercase letters, digits and _)."
                )
            if key in environments:
                raise EnvConfigError(f"Environment {key} is listed twice in MIGRATOR_ENVS.")
            prefix = f"ENV_{key.upper()}"
            environments[key] = Environment(
                key=key,
                label=env.get(f"{prefix}_LABEL") or key,
                db=_db_settings(env, prefix, timeout),
                protected=(env.get(f"{prefix}_PROTECTED") or "").strip().lower() in _TRUE,
            )
        default_source = env.get("MIGRATOR_DEFAULT_FROM")
        default_target = env.get("MIGRATOR_DEFAULT_TO")
    elif env.get("CREATIVE_DB_HOST") is not None:
        environments = {
            "creative": Environment(
                "creative", "Creative (legacy .env)", _db_settings(env, "CREATIVE_DB", timeout)
            ),
            "live": Environment(
                "live", "Live (legacy .env)", _db_settings(env, "LIVE_DB", timeout), True
            ),
        }
        default_source, default_target = "creative", "live"
    else:
        raise EnvConfigError(
            "No environments in .env. Set MIGRATOR_ENVS and ENV_<NAME>_* (see .env.example)."
        )

    if len(environments) < 2:
        raise EnvConfigError("Define at least two environments in .env.")

    registry = EnvironmentRegistry(
        environments,
        default_source=(default_source or "").strip().lower() or None,
        default_target=(default_target or "").strip().lower() or None,
    )
    for default in (registry.default_source, registry.default_target):
        if default:
            registry.get(default)  # fail fast on a typo in MIGRATOR_DEFAULT_*
    return registry


def ensure_distinct(source: Environment, target: Environment) -> None:
    if source.key == target.key or source.db.identity() == target.db.identity():
        raise EnvConfigError(
            f"Source ({source.key}) and target ({target.key}) point to the same database."
        )


def _db_settings(env: Mapping[str, str], prefix: str, timeout: int) -> DatabaseSettings:
    required = [f"{prefix}_{name}" for name in ("HOST", "USER", "PASSWORD", "NAME")]
    missing = [name for name in required if env.get(name) is None]
    if missing:
        raise EnvConfigError("Missing variables in .env: " + ", ".join(missing))

    return DatabaseSettings(
        host=env[f"{prefix}_HOST"],
        port=int(env.get(f"{prefix}_PORT") or "3306"),
        user=env[f"{prefix}_USER"],
        password=env[f"{prefix}_PASSWORD"],
        database=env[f"{prefix}_NAME"],
        connect_timeout=timeout,
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
