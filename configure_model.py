from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description="Store a replacement DeepSeek key privately, without chat or command-line arguments.")
    parser.add_argument("--key-file", required=True)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("Run in an interactive terminal; keys are never accepted through command-line arguments.")
    path = Path(args.key_file).expanduser().absolute()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise SystemExit("Use a private directory (0700), not a symlink or shared directory.")
    key = getpass.getpass("New project-specific DeepSeek key (hidden): ").strip()
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,200}", key):
        raise SystemExit("Unexpected key format; no file was changed.")
    fd, temporary = tempfile.mkstemp(prefix=".model-key-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(key + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Key saved with mode 0600. No model request was made; activation is configured separately.")


if __name__ == "__main__":
    main()
