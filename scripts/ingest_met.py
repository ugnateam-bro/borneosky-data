#!/usr/bin/env python3
"""
MET Norway weather forecasts for every registry town → R2.

Usage:
  python scripts/ingest_met.py            upload towns whose forecast is due
  python scripts/ingest_met.py --force    ignore Expires and refetch everything
  python scripts/ingest_met.py --dry-run  refetch everything, write to ./out/ only

Each town's file is independent, so a failed town keeps its last good file.
Exits non-zero if more than a quarter of towns fail.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import geo, met  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        def put_json(key, obj):
            path = OUT / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

        def get_json(key):
            return None
    else:
        from botocore.exceptions import BotoCoreError, ClientError

        from borneosky import r2

        def put_json(key, obj):
            try:
                r2.put_json(key, obj, cache_seconds=900)
            except (ClientError, BotoCoreError) as e:
                sys.exit(f"R2 upload of {key} failed: {type(e).__name__}. Check the R2 keys.")

        get_json = r2.get_json

    res = met.run(put_json, get_json, force=args.force or args.dry_run)

    print(f"  updated {len(res['updated'])}, not modified {len(res['not_modified'])}, "
          f"not yet due {len(res['not_due'])}, failed {len(res['failed'])}")
    for slug, err in res["failed"].items():
        print(f"  FAILED {slug}: {err}")

    if len(res["failed"]) > len(geo.towns()) / 4:
        sys.exit("Too many towns failed.")


if __name__ == "__main__":
    from borneosky.config import run_main
    run_main(main)
