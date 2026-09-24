#!/usr/bin/env python3
"""
Fuel and commodity prices → R2 (at most every 6 hours).

Usage:
  python scripts/ingest_prices.py            upload if due
  python scripts/ingest_prices.py --force    refetch now
  python scripts/ingest_prices.py --dry-run  fetch and write to ./out/ only

Exits non-zero if both sources fail.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import prices, status  # noqa: E402
from borneosky.config import run_main  # noqa: E402

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
        from borneosky import r2

        def put_json(key, obj):
            r2.put_json(key, obj, cache_seconds=3600)

        get_json = r2.get_json

    res = prices.run(put_json, get_json, force=args.force or args.dry_run)
    status.detail(status=res["status"], updated=len(res.get("updated", [])), failed=len(res.get("failed", {})))
    if res["status"] != "not_due" and res.get("failed"):
        status.soft_fail("; ".join(f"{k}: {v}" for k, v in res["failed"].items()))
    if res["status"] == "not_due":
        print(f"  checked at {res['checked_utc']}, not due yet")
        return
    for key in res["updated"]:
        print(f"  updated {key}")
    for key, err in res["failed"].items():
        print(f"  FAILED {key}: {err}")
    if not res["updated"]:
        sys.exit("Both price sources failed.")


if __name__ == "__main__":
    run_main(main, source="prices")
