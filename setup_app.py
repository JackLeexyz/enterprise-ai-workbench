from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets

from store import Store
from acceptance import seed_demo


def main():
    root = Path(__file__).parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=str(root / "data/workbench.db"))
    parser.add_argument("--credentials-out", default=str(root / "private/登录信息.json"))
    parser.add_argument("--acceptance-demo", action="store_true", help="Seed fictional acceptance projects in this new database only.")
    args = parser.parse_args()
    target = Path(args.credentials_out)
    if target.exists():
        raise SystemExit("Credentials output already exists; refusing to overwrite.")
    store = Store(args.database)
    if store.users():
        raise SystemExit("Database already initialized; refusing to overwrite.")
    passwords = {username: secrets.token_urlsafe(24) for username in ["writer", "reviewer", "admin"]}
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump({"notice": "仅此独立虚构演示库的登录信息，不得用于其他系统或公开分享。", "accounts": [{"username": username, "password": password} for username, password in passwords.items()]}, output, ensure_ascii=False, indent=2)
        store.bootstrap(passwords, json.loads((root / "fixtures.json").read_text()))
        if args.acceptance_demo:
            with store.connection(write=True) as db:
                seed_demo(db)
        Path(args.database).chmod(0o600)
    except Exception:
        print("Initialization failed. Existing files were retained for inspection; no secrets printed.")
        raise
    print("Initialized fictional database. Login information is in the private file:")
    print(target)


if __name__ == "__main__":
    main()
