#!/usr/bin/env python3
"""
Append what is new in BorneoSky's published data files to the history database (docs/history.md).

Usage:
  python scripts/record_history.py                      read https://data.borneosky.com, write $HISTORY_DB
  python scripts/record_history.py --db history.db      another database file
  python scripts/record_history.py --from-dir out       read a local folder (the ingest's out/, or a download) instead of the web

Meant to run once an hour on the droplet (deploy/borneosky-history.timer). It only reads the files the site itself reads, never the
upstream sources. A dataset that fails is logged in the `runs` table and the others carry on; the exit code is 1 only when nothing
could be read at all. Set HISTORY_DB (default /var/lib/borneosky/history.db) and optionally DATA_BASE_URL.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import history  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("HISTORY_DB", "/var/lib/borneosky/history.db"))
    ap.add_argument("--from-dir", help="read the files from this folder instead of the web")
    args = ap.parse_args()

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = history.open_db(args.db)
    if args.from_dir:
        fetch = history.dir_fetcher(args.from_dir)
    else:
        fetch = history.http_fetcher(os.environ.get("DATA_BASE_URL", history.BASE_URL), conn)
    res = history.record(conn, fetch)

    width = max(len(k) for k in res)
    for name, (state, rows) in res.items():
        print(f"  {name:<{width}}  {state:<9} {rows:>7} rows")
    bad = [k for k, (s, _) in res.items() if s == "error"]
    for name in bad:
        row = conn.execute("SELECT detail FROM runs WHERE dataset=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        print(f"  {name} FAILED: {row[0] if row else ''}", file=sys.stderr)
    return 1 if len(bad) == len(res) else 0


if __name__ == "__main__":
    sys.exit(main())
