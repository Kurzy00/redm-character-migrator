from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Iterable
from contextlib import ExitStack, closing
from datetime import datetime
from pathlib import Path

from .config import ConfigError, load_config, shadowed_defaults
from .db import (
    Environment,
    EnvironmentRegistry,
    connect,
    ensure_distinct,
    load_environments,
)
from .migrator import CharacterMigrator, CharacterPayload, preview_json

ROOT_DIR = Path(__file__).resolve().parent.parent


class Abort(Exception):
    """The user cancelled; nothing was written."""


class _RouteFilter(logging.Filter):
    """Stamps every log line with the current `source -> target` route."""

    route = "-"

    def filter(self, record: logging.LogRecord) -> bool:
        record.route = self.route
        return True


_route = _RouteFilter()


def setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("character_migrator")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(
        log_dir / f"migration_{datetime.now():%Y%m%d}.log", encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(route)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    handler.addFilter(_route)
    logger.addHandler(handler)
    return logger


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="character-migrator",
        description="Copy characters between databases defined in .env. INSERT-only, one "
        "transaction per character. Run without arguments for the interactive wizard.",
    )
    parser.add_argument("charids", nargs="*", help="One or more citizenid values, e.g. ABC12345")
    parser.add_argument("--from", dest="source", metavar="ENV", help="Source environment name")
    parser.add_argument("--to", dest="target", metavar="ENV", help="Target environment name")
    parser.add_argument("--list-envs", action="store_true", help="List environments from .env")
    parser.add_argument("--file", type=Path, help="Text file with one citizenid per line")
    parser.add_argument(
        "--config", type=Path, default=ROOT_DIR / "config.json", help="Path to config.json"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the INSERTs inside a transaction and roll back; the target is left unchanged",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Do not ask for confirmation")
    parser.add_argument("--check", action="store_true", help="Only test both database connections")
    parser.add_argument(
        "--check-config", action="store_true", help="Only validate config.json (no database needed)"
    )
    parser.add_argument("--verbose-preview", action="store_true", help="Print prepared row values")
    return parser.parse_args(argv)


# --- input helpers ------------------------------------------------------------------


def is_interactive() -> bool:
    return sys.stdin.isatty()


def read_charids(values: Iterable[str], lines: Iterable[str] = ()) -> list[str]:
    """Merge CLI ids with file lines; skip blanks and # comments, dedupe, keep order."""
    candidates = [*values, *(line for line in lines if not line.strip().startswith("#"))]
    return list(dict.fromkeys(v.strip() for v in candidates if v.strip()))


def ask_charids(first: bool) -> list[str]:
    """Ask for ids (space/comma separated) or a path to a .txt file. Empty input ends a session."""
    prompt = "\nCitizenid(s), separated by space or comma, or a path to a .txt file"
    prompt += ": " if first else " (Enter to finish): "
    while True:
        answer = input(prompt).strip().strip('"')
        if not answer:
            if first:
                print("  Enter at least one citizenid.")
                continue
            return []
        path = Path(answer)
        if answer.lower().endswith(".txt") or path.is_file():
            if not path.is_file():
                print(f"  File not found: {path}")
                continue
            charids = read_charids([], path.read_text(encoding="utf-8").splitlines())
            print(f"  Loaded {len(charids)} citizenid(s) from file.")
            return charids
        return read_charids(re.split(r"[\s,;]+", answer))


def choose_environment(
    registry: EnvironmentRegistry, role: str, default: str | None, exclude: str | None = None
) -> Environment:
    options = [env for env in registry.environments.values() if env.key != exclude]
    if default == exclude:
        default = None
    print(f"\nChoose the {role} environment:")
    for idx, env in enumerate(options, start=1):
        print(f"  [{idx}] {env.describe()}{'  (default)' if env.key == default else ''}")

    while True:
        answer = input("Number or name" + (" [Enter = default]" if default else "") + ": ")
        answer = answer.strip().lower()
        if not answer and default:
            return registry.get(default)
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        for env in options:
            if env.key == answer:
                return env
        print("  Invalid choice.")


def ask_dry_run() -> bool:
    print("\nMode:")
    print("  [1] TEST     - run the INSERTs and roll back, target unchanged")
    print("  [2] MIGRATE  - write to the target")
    while True:
        answer = input("Choose [Enter = 1]: ").strip()
        if answer in ("", "1"):
            return True
        if answer == "2":
            return False
        print("  Invalid choice.")


def resolve_route(
    args: argparse.Namespace, registry: EnvironmentRegistry, interactive: bool
) -> tuple[Environment, Environment]:
    if not interactive and not (args.source and args.target):
        raise Abort("Pass --from and --to (or run in a console to pick them from a menu).")
    source = (
        registry.get(args.source)
        if args.source
        else choose_environment(registry, "SOURCE", registry.default_source)
    )
    target = (
        registry.get(args.target)
        if args.target
        else choose_environment(registry, "TARGET", registry.default_target, exclude=source.key)
    )
    ensure_distinct(source, target)
    return source, target


def confirm_protected(args: argparse.Namespace, target: Environment) -> None:
    """Writing to a protected target needs its name typed once (or an explicit --to with --yes)."""
    if not target.protected or args.dry_run or args.check:
        return
    if args.yes:
        if not args.target:
            raise Abort(f"{target.key} is protected: with --yes pass it explicitly as --to.")
        return
    answer = input(f"Target {target.key} is PROTECTED. Type its name to continue: ")
    if answer.strip().lower() != target.key:
        raise Abort("Name does not match. Nothing was done.")


def confirm(charid: str, target: Environment) -> bool:
    answer = input(f"INSERT {charid} into {target.key}? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def print_payload(prepared: CharacterPayload, verbose: bool) -> None:
    print(f"\nCharacter: {prepared.charid}")
    print("-" * 58)
    for item in prepared.tables:
        print(f"{item.table:<24} rows: {len(item.rows)}")
        if verbose:
            for idx, row in enumerate(item.rows, start=1):
                print(f"  [{idx}] {preview_json(row, 500)}")


def print_route(source: Environment, target: Environment, dry_run: bool) -> None:
    print("\n" + "=" * 58)
    print(f" SOURCE: {source.describe()}")
    print(f" TARGET: {target.describe()}")
    print(f" MODE:   {'TEST (rollback)' if dry_run else 'MIGRATE (write)'}")
    print("=" * 58)


# --- main ---------------------------------------------------------------------------


def process(
    migrator: CharacterMigrator,
    charids: list[str],
    args: argparse.Namespace,
    target: Environment,
    logger: logging.Logger,
) -> int:
    """Migrate a batch of characters; returns the number of failures."""
    failures = 0
    for charid in charids:
        try:
            prepared = migrator.prepare(migrator.load_character(charid))
            print_payload(prepared, args.verbose_preview)

            if args.dry_run:
                migrator.migrate(prepared, dry_run=True)
                print(f"[DRY RUN] INSERTs passed and were rolled back. {target.key} unchanged.")
                continue

            if not args.yes and not confirm(charid, target):
                print("[SKIP] Skipped.")
                continue

            migrator.migrate(prepared)
            print(f"[SUCCESS] {charid}: {migrator.source_name} -> {target.key}.")
        except Exception as exc:
            failures += 1
            logger.exception("%s | ERROR", charid)
            print(f"[ERROR] {charid}: {exc}")
    return failures


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"[ERROR] {exc}")
        return 2
    if args.check_config:
        print(f"[OK] {args.config} is valid.")
        return 0
    for warning in shadowed_defaults(config):
        print(f"[WARN] config: {warning}")

    lines: list[str] = []
    if args.file:
        if not args.file.exists():
            print(f"[ERROR] File not found: {args.file}")
            return 2
        lines = args.file.read_text(encoding="utf-8").splitlines()
    charids = read_charids(args.charids, lines)

    # No ids on the command line: the wizard asks for everything in the console.
    interactive = is_interactive()
    wizard = not charids and not args.check and not args.list_envs
    if wizard and not interactive:
        print("[ERROR] Pass at least one citizenid or use --file (or run in a console).")
        return 2

    logger = setup_logger(ROOT_DIR / "logs")

    try:
        registry = load_environments(ROOT_DIR / ".env")
        if args.list_envs:
            print("Environments in .env:")
            for env in registry.environments.values():
                print(f"  {env.describe()}")
            return 0

        source_env, target_env = resolve_route(args, registry, interactive)
        if wizard and not args.dry_run:
            args.dry_run = ask_dry_run()
        _route.route = f"{source_env.key} -> {target_env.key}"
        print_route(source_env, target_env, args.dry_run)
        confirm_protected(args, target_env)
    except (Abort, KeyboardInterrupt, EOFError) as exc:
        print(f"\n[ABORT] {exc or 'Cancelled.'}")
        return 2
    except Exception as exc:
        logger.exception("startup failed")
        print(f"[ERROR] Could not start the migrator: {exc}")
        return 2

    logger.info("SESSION START | mode=%s", "dry-run" if args.dry_run else "migrate")

    with ExitStack() as stack:
        try:
            source = stack.enter_context(closing(connect(source_env.db)))
            target = stack.enter_context(closing(connect(target_env.db)))
        except Exception as exc:
            logger.exception("connection failed")
            print(f"[ERROR] Could not connect: {exc}")
            return 2

        migrator = CharacterMigrator(source, target, config, logger, source_env.key, target_env.key)

        if args.check:
            status = migrator.check_connections()
            print(f"[OK] {source_env.key}: {status['source']}")
            print(f"[OK] {target_env.key}: {status['target']}")
            return 0

        failures = handled = 0
        try:
            if wizard:
                charids = ask_charids(first=True)
            while charids:
                failures += process(migrator, charids, args, target_env, logger)
                handled += len(charids)
                charids = ask_charids(first=False) if wizard else []
        except (KeyboardInterrupt, EOFError):
            print("\n[ABORT] Cancelled.")
            return 2

    if failures:
        print(f"\nFinished with errors: {failures}/{handled} characters failed.")
        return 1
    print(f"\nDone. Processed {handled} characters.")
    return 0
