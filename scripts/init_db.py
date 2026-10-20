#!/usr/bin/env python3
"""Create (or reset) the application database.

    python3 scripts/init_db.py                       # create if missing, seed defaults
    python3 scripts/init_db.py --db /tmp/test.db     # a different file
    python3 scripts/init_db.py --drop --yes          # delete every table first

Seeding is idempotent: existing settings, built-in formulas and dashboard
components are left untouched.
"""

from __future__ import annotations

import argparse
import sys

from _common import add_common_arguments, banner

from database.db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--drop", action="store_true",
                        help="drop all tables before creating them")
    parser.add_argument("--yes", action="store_true",
                        help="required together with --drop")
    args = parser.parse_args(argv)

    if args.drop and not args.yes:
        print("Refusing to drop every table without --yes.", file=sys.stderr)
        return 2

    db = Database(args.db) if args.db else Database()
    banner(f"Database {db.path}")
    existed = db.path.exists()

    if args.drop:
        db.drop_all()
        print("Dropped all tables.")

    db.init_schema()
    print("Schema created." if (args.drop or not existed) else "Schema verified.")

    print(f"Tables        : {', '.join(db.table_names())}")
    summary = db.summary_counts()
    print(f"Symbols       : {summary['stocks']:,}")
    print(f"Session rows  : {summary['records']:,}")
    print(f"Import batches: {summary['batches']:,}")
    print("\nDone. Launch the application with: python3 main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
