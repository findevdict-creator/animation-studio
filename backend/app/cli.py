"""Operator commands.

    python -m app.cli init-storage --access-key dev-admin --secret-key dev-admin-secret
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.core.storage import ensure_buckets, make_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-storage", help="create the library and work buckets if missing")
    init.add_argument("--access-key", required=True, help="an identity allowed to create buckets")
    init.add_argument("--secret-key", required=True)
    args = parser.parse_args(argv)

    if args.command == "init-storage":
        s = get_settings()
        client = make_client(s, access_key=args.access_key, secret_key=args.secret_key)
        ensure_buckets(client, [s.s3_library_bucket, s.s3_work_bucket])
        print(f"buckets ready: {s.s3_library_bucket}, {s.s3_work_bucket}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
