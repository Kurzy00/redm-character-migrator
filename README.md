# redm-character-migrator

[![CI](https://github.com/Kurzy00/redm-character-migrator/actions/workflows/ci.yml/badge.svg)](https://github.com/Kurzy00/redm-character-migrator/actions/workflows/ci.yml)

CLI tool I use on our RedM (RDR2 multiplayer, RSG framework) server to move player characters
between databases, usually from the creative/staging database to the live one.

The two databases have drifted apart over time (new columns, different defaults, economy fields
that must be reset on live), so plain SQL dumps kept breaking. This tool reads both schemas and
builds every row for the live schema, and it's safe to run against a database with players on it.

## How it works

- INSERT-only: never updates or overwrites. Stops if the `citizenid` already exists on live.
- One transaction per character, so all its tables (players, skin, creator, clothes, outfits)
  are committed together or rolled back together.
- Every row is mapped and checked before the first INSERT.
- `--dry-run` runs the real INSERTs and rolls back, so NOT NULL / UNIQUE errors show up
  before anything is written. (InnoDB still advances `AUTO_INCREMENT` counters.)
- `config.json` is validated before connecting, including the JSON stored in columns like
  `money` or `inventory`. A missing comma there once broke money for migrated characters.
- Every decision and insert goes to `logs/migration_YYYYMMDD.log`, tagged with the route
  (`creative -> live`).

For each live column the first matching rule wins (see [`rows.py`](character_migrator/rows.py)):

1. `skip_columns` or a generated column → not inserted
2. `overrides` → config value
3. non-NULL value in source → copied
4. `defaults` → config value
5. live has DEFAULT / AUTO_INCREMENT / allows NULL → left to the database
6. otherwise → stop (with `strict_required_columns`)

## Environments

Databases are named profiles in `.env` (see [`.env.example`](.env.example)):

```env
MIGRATOR_ENVS=creative,live,dev
ENV_LIVE_HOST=...
ENV_LIVE_NAME=live
ENV_LIVE_PROTECTED=1
```

- Source and target are picked when you run the tool, never by editing `.env`.
- The tool refuses to run when source and target are the same database.
- A `PROTECTED` target needs its name typed before a real migration
  (with `--yes` it must be passed explicitly as `--to`).
- The old `CREATIVE_DB_*` / `LIVE_DB_*` layout still works as `creative -> live`.

## Usage

```bat
copy .env.example .env
copy config.example.json config.json
run.bat
```

Without arguments `run.bat` starts a wizard that asks for the source, the target, the mode
(test with rollback, or a real migration) and the citizenids (typed, or a path to a `.txt`
file). After a batch you can enter more ids for the same route; an empty line ends.

The same steps as flags, for scripts:

```bat
run.bat --list-envs
run.bat --check-config
run.bat --from creative --to live --check
run.bat --from creative --to live ABC12345 --dry-run --verbose-preview
run.bat --from creative --to live --file ids.txt --yes
```

Or `pip install -e .` and use the `character-migrator` command.

## Development

```bash
pip install -e ".[dev]"
ruff check . && pytest
```

## Known limitations

- `outfit_id` is copied as-is so clothes and outfits stay linked. If it collides on live, the
  insert fails and rolls back with a message naming the table (`--dry-run` catches it first);
  there's no remapping yet.
- No integration tests against a real MariaDB yet, only unit tests of the mapping and config logic.
