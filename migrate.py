"""Entry point kept for run.bat / run.sh; equivalent to the `character-migrator` command."""

from character_migrator.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
