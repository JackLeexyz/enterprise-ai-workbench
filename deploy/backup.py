import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    source = Path(args.database).resolve()
    if not source.is_file():
        raise SystemExit("Source database does not exist; no empty backup created.")
    directory = Path(args.directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    os.umask(0o077)
    target = directory / ("workbench-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".db")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(target) as output:
        original.backup(output)
        if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("Backup failed integrity check; retained for diagnosis.")
    target.chmod(0o600)
    print(target)


if __name__ == "__main__":
    main()
