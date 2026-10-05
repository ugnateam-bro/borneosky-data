#!/usr/bin/env python3
"""
Copy the history database to a PRIVATE Cloudflare R2 bucket (never the public data bucket).

Usage:
  python scripts/backup_history.py                  one consistent copy, checked, gzipped, uploaded (two names, see below)
  python scripts/backup_history.py --local-only     only make history-backup.db.gz next to the database (no R2)

Uses SQLite's own backup (safe while the recorder runs, unlike copying the file), checks the copy with PRAGMA integrity_check, then
uploads it as history/history-latest.db.gz and history/history-<weekday>.db.gz (seven rotating copies). Needs R2_ACCOUNT_ID,
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_HISTORY_BUCKET (a bucket with no public access and its own key). Set HISTORY_DB.
"""

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("HISTORY_DB", "/var/lib/borneosky/history.db"))
    ap.add_argument("--local-only", action="store_true")
    args = ap.parse_args()

    src = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "copy.db"
        dst = sqlite3.connect(copy_path)
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        dst.close()
        src.close()
        if ok != "ok":
            print(f"integrity check of the copy failed: {ok}", file=sys.stderr)
            return 1
        gz_path = Path(args.db).with_name("history-backup.db.gz") if args.local_only else Path(tmp) / "history.db.gz"
        with open(copy_path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out, 1 << 20)
        size = gz_path.stat().st_size
        if args.local_only:
            print(f"  wrote {gz_path} ({size / 1e6:.1f} MB)")
            return 0
        import boto3
        from botocore.config import Config

        def need(name: str) -> str:
            v = os.environ.get(name, "").strip()
            if not v:
                sys.exit(f"{name} is not set")
            return v

        s3 = boto3.client("s3", endpoint_url=f"https://{need('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
                          aws_access_key_id=need("R2_ACCESS_KEY_ID"), aws_secret_access_key=need("R2_SECRET_ACCESS_KEY"),
                          region_name="auto", config=Config(retries={"max_attempts": 5, "mode": "standard"}))
        bucket = need("R2_HISTORY_BUCKET")
        weekday = datetime.now(timezone.utc).strftime("%a").lower()
        for key in ("history/history-latest.db.gz", f"history/history-{weekday}.db.gz"):
            s3.upload_file(str(gz_path), bucket, key)
        print(f"  uploaded {size / 1e6:.1f} MB to {bucket}: history/history-latest.db.gz and history-{weekday}.db.gz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
