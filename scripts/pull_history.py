#!/usr/bin/env python3
"""
Download the latest history backup to YOUR computer for analysis (DuckDB, pandas, anything that reads SQLite).

Usage:
  python scripts/pull_history.py                   writes ./history.db
  python scripts/pull_history.py --out data/h.db

Needs R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_HISTORY_BUCKET in the environment: use a READ-ONLY R2 key for
this machine. Example with DuckDB afterwards:  ATTACH 'history.db' (TYPE sqlite);  SELECT * FROM history.forecast_town_daily LIMIT 5;
"""

import argparse
import gzip
import os
import shutil
import sys
import tempfile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="history.db")
    args = ap.parse_args()

    def need(name: str) -> str:
        v = os.environ.get(name, "").strip()
        if not v:
            sys.exit(f"{name} is not set")
        return v

    import boto3

    s3 = boto3.client("s3", endpoint_url=f"https://{need('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
                      aws_access_key_id=need("R2_ACCESS_KEY_ID"), aws_secret_access_key=need("R2_SECRET_ACCESS_KEY"), region_name="auto")
    with tempfile.NamedTemporaryFile(suffix=".gz") as tmp:
        s3.download_file(need("R2_HISTORY_BUCKET"), "history/history-latest.db.gz", tmp.name)
        with gzip.open(tmp.name, "rb") as f_in, open(args.out, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out, 1 << 20)
    print(f"  wrote {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
