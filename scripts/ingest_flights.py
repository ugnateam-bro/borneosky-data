#!/usr/bin/env python3
"""
Flight boards (AeroDataBox) → R2, one call per airport.

Usage:
  python scripts/ingest_flights.py                  fetch every airport that is due
  python scripts/ingest_flights.py --force          ignore the 00:01-04:00 quiet hours
  python scripts/ingest_flights.py --dry-run        fetch, write to ./out/ only (uses API units)
  python scripts/ingest_flights.py --only KCH,BWN   just these airports

Does nothing when AERODATABOX_KEY is not set, so the hourly workflow can carry this
step before the plan is bought. Each airport is independent: a failed one keeps its
last good file. Exits non-zero if the key is refused or every airport failed.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import flights, status  # noqa: E402
from borneosky.config import run_main  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    wanted = {c.strip().upper() for c in args.only.split(",") if c.strip()}
    airports = [a for a in flights.AIRPORTS if not wanted or a["iata"] in wanted]
    if not airports:
        sys.exit(f"No airport matches --only {args.only!r}")

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
                r2.put_json(key, obj, cache_seconds=300)
            except (ClientError, BotoCoreError) as e:
                sys.exit(f"R2 upload of {key} failed: {type(e).__name__}. Check the R2 keys.")

        get_json = r2.get_json

    cfg = flights.settings()
    try:
        res = flights.run(put_json, get_json, session=flights.make_session(cfg), cfg=cfg,
                          force=args.force, airports=airports)
    except flights.FlightsAuthError as e:
        sys.exit(str(e))

    used = res["usage"]
    status.detail(updated=len(res["updated"]), failed=len(res["failed"]), quiet=len(res["quiet"]),
                  units_this_month=used["units"])
    print(f"  updated {len(res['updated'])} ({', '.join(res['updated']) or 'none'}), "
          f"failed {len(res['failed'])}, quiet hours {len(res['quiet'])}; "
          f"{used['units']} of {cfg['monthly_units']} units used this month")
    for iata, err in res["failed"].items():
        print(f"  FAILED {iata}: {err}")
    if res["over_budget"]:
        print("  STOPPED: the month's unit budget is nearly used up")
        status.soft_fail("Unit budget for the month is nearly used up: boards not refreshed")
    if res["failed"]:
        status.soft_fail("; ".join(f"{k}: {v}" for k, v in res["failed"].items()))
    if res["failed"] and not res["updated"]:
        sys.exit("Every airport failed.")


if __name__ == "__main__":
    if not os.environ.get("AERODATABOX_KEY", "").strip():
        print("  AERODATABOX_KEY is not set: flight boards are not configured, skipping.")
    else:
        run_main(main, source="flights")
