from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable
from contextlib import ExitStack, closing
from datetime import datetime
from pathlib import Path

from .config import ConfigError, load_config
from .db import connect, load_db_settings
from .migrator import CharacterMigrator, CharacterPayload, preview_json

ROOT_DIR = Path(__file__).resolve().parent.parent


def setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("character_migrator")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(
        log_dir / f"migration_{datetime.now():%Y%m%d}.log", encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    return logger


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="character-migrator",
        description="Copy characters from the source (creative) database to the target (live) "
        "database. INSERT-only, one transaction per character.",
    )
    parser.add_argument("charids", nargs="*", help="One or more citizenid values, e.g. ABC12345")
    parser.add_argument("--file", type=Path, help="Text file with one citizenid per line")
    parser.add_argument(
        "--config", type=Path, default=ROOT_DIR / "config.json", help="Path to config.json"
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and preview, no INSERT")
    parser.add_argument("--yes", "-y", action="store_true", help="Do not ask for confirmation")
    parser.add_argument("--check", action="store_true", help="Only test both database connections")
    parser.add_argument(
        "--check-config", action="store_true", help="Only validate config.json (no database needed)"
    )
    parser.add_argument("--verbose-preview", action="store_true", help="Print prepared row values")
    return parser.parse_args(argv)


def read_charids(values: Iterable[str], lines: Iterable[str] = ()) -> list[str]:
    """Merge CLI ids with file lines; skip blanks and # comments, dedupe, keep order."""
    candidates = [*values, *(line for line in lines if not line.strip().startswith("#"))]
    return list(dict.fromkeys(v.strip() for v in candidates if v.strip()))


def print_payload(prepared: CharacterPayload, verbose: bool) -> None:
    print(f"\nCharacter: {prepared.charid}")
    print("-" * 58)
    for item in prepared.tables:
        print(f"{item.table:<24} rows: {len(item.rows)}")
        if verbose:
            for idx, row in enumerate(item.rows, start=1):
                print(f"  [{idx}] {preview_json(row, 500)}")


def confirm(charid: str) -> bool:
    answer = input(f"INSERT {charid} into the target database? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


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

    lines: list[str] = []
    if args.file:
        if not args.file.exists():
            print(f"[ERROR] File not found: {args.file}")
            return 2
        lines = args.file.read_text(encoding="utf-8").splitlines()
    charids = read_charids(args.charids, lines)
    if not charids and not args.check:
        print("[ERROR] Pass at least one citizenid or use --file.")
        return 2

    logger = setup_logger(ROOT_DIR / "logs")

    with ExitStack() as stack:
        try:
            source_settings, target_settings = load_db_settings(ROOT_DIR / ".env")
            source = stack.enter_context(closing(connect(source_settings)))
            target = stack.enter_context(closing(connect(target_settings)))
        except Exception as exc:
            logger.exception("startup failed")
            print(f"[ERROR] Could not start the migrator: {exc}")
            return 2

        migrator = CharacterMigrator(source, target, config, logger)

        if args.check:
            status = migrator.check_connections()
            print("[OK] Source:", status["source"])
            print("[OK] Target:", status["target"])
            return 0

        failures = 0
        for charid in charids:
            try:
                prepared = migrator.prepare(migrator.load_character(charid))
                print_payload(prepared, args.verbose_preview)

                if args.dry_run:
                    migrator.migrate(prepared, dry_run=True)
                    print("[DRY RUN] Validation passed. Target database unchanged.")
                    continue

                if not args.yes and not confirm(charid):
                    print("[SKIP] Skipped.")
                    continue

                migrator.migrate(prepared)
                print(f"[SUCCESS] {charid} copied to target.")
            except Exception as exc:
                failures += 1
                logger.exception("%s | ERROR", charid)
                print(f"[ERROR] {charid}: {exc}")

    if failures:
        print(f"\nFinished with errors: {failures}/{len(charids)} characters failed.")
        return 1
    print(f"\nDone. Processed {len(charids)} characters.")
    return 0
