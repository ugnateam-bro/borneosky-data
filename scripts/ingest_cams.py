#!/usr/bin/env python3
"""
CAMS smoke forecast → R2. Does nothing if the latest run is already uploaded.

Usage:
  python scripts/ingest_cams.py            fetch the latest run if new, upload
  python scripts/ingest_cams.py --force    refetch the latest run anyway
  python scripts/ingest_cams.py --dry-run  fetch and write to ./out/ only

A failed request keeps the last good files. Exits non-zero only when the
newest run we hold is more than 36 h old.
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import cams  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        def put_json(key, obj, gzipped=False):
            path = OUT / key
            path.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
            path.write_bytes(body)
            gz = f", {len(gzip.compress(body)) // 1024} KB gzipped" if gzipped else ""
            print(f"  wrote {path.relative_to(OUT.parent)} ({len(body) // 1024} KB{gz})")

        def get_json(key):
            return None
    else:
        from botocore.exceptions import BotoCoreError, ClientError

        from borneosky import r2

        def put_json(key, obj, gzipped=False):
            try:
                size = r2.put_json(key, obj, cache_seconds=1800, gzipped=gzipped)
            except (ClientError, BotoCoreError) as e:
                sys.exit(f"R2 upload of {key} failed: {type(e).__name__}. Check the R2 keys.")
            print(f"  uploaded {key} ({size // 1024} KB)")

        get_json = r2.get_json

    res = cams.run(put_json, get_json, force=args.force or args.dry_run)

    if res["status"] == "up_to_date":
        print(f"  already have run {res['run_utc']}, nothing to do")
    elif res["status"] == "updated":
        print(f"  run {res['run_utc']} processed (peak PM2.5 {res['pm25_max']} µg/m³)")
    else:
        print(f"  {res['error']}")
        print(f"  keeping run {res['run_utc'] or '(none)'}")
        if res["stale"]:
            sys.exit("Smoke forecast is stale (older than 36 h).")


if __name__ == "__main__":
    main()
