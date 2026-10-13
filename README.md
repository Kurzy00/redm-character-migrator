# redm-character-migrator

[![CI](https://github.com/Kurzy00/redm-character-migrator/actions/workflows/ci.yml/badge.svg)](https://github.com/Kurzy00/redm-character-migrator/actions/workflows/ci.yml)

CLI tool I use on our RedM (RDR2 multiplayer, RSG framework) server to move player characters
from the creative/staging database to the live one.

The two databases have drifted apart over time (new columns, different defaults, economy fields
that must be reset on live), so plain SQL dumps kept breaking. This tool reads both schemas and
builds every row for the live schema, and it's safe to run against a database with players on it.

## How it works

- INSERT-only: never updates or overwrites. Stops if the `citizenid` already exists on live.
- One transaction per character, so all its tables (players, skin, creator, clothes, outfits)
  are committed together or rolled back together.
- Every row is mapped and checked before the first INSERT.
- `config.json` is validated before connecting, including the JSON stored in columns like
  `money` or `inventory`. A missing comma there once broke money for migrated characters.
- Every decision and insert goes to `logs/migration_YYYYMMDD.log`.

For each live column the first matching rule wins (see [`rows.py`](character_migrator/rows.py)):

1. `skip_columns` or a generated column → not inserted
2. `overrides` → config value
3. non-NULL value in source → copied
4. `defaults` → config value
5. live has DEFAULT / AUTO_INCREMENT / allows NULL → left to the database
6. otherwise → stop (with `strict_required_columns`)

## Usage

```bat
copy .env.example .env
copy config.example.json config.json

run.bat --check-config
run.bat --check
run.bat ABC12345 --dry-run --verbose-preview
run.bat ABC12345
run.bat --file ids.txt --yes
```

Or `pip install -e .` and use the `character-migrator` command.

## Development

```bash
pip install -e ".[dev]"
ruff check . && pytest
```

## Known limitations

- `outfit_id` is copied as-is so clothes and outfits stay linked. If it collides on live, the
  insert fails and rolls back; there's no remapping yet.
- No integration tests against a real MariaDB yet, only unit tests of the mapping and config logic.
