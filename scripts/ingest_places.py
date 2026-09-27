#!/usr/bin/env python3
"""
Explore: weather forecasts for the places in data/places.json → R2 (places/index.json).

Usage:
  python scripts/ingest_places.py             fetch places whose weather forecast is due (only when EXPLORE_PLACES is "on")
  python scripts/ingest_places.py --dry-run   refetch every place and write to ./out/ only; works whether or not it is on
  python scripts/ingest_places.py --force     ignore MET's Expires times and refetch every place

Does nothing until the repository variable EXPLORE_PLACES is "on" (Settings > Secrets and variables > Actions >
Variables), so the hourly workflow can carry this step while Explore is switched off on the site. Setting it to
anything else stops it at once, with no deploy. The smoke forecast is read from the smoke/grid.json the CAMS step
already publishes (a dry run reads it from R2 when the keys are set, else from ./out/ if a CAMS dry run left one).
Exits non-zero if the place list is wrong or more than a quarter of places fail. Notes: docs/explore.md.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from borneosky import places, status  # noqa: E402
from borneosky.config import run_main, user_agent  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    problems = places.check_list()
    if problems:
        sys.exit("data/places.json: " + "; ".join(problems))

    if args.dry_run:
        def put_json(key, obj):
            path = OUT / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

        def get_json(key):
            if key == places.SMOKE_KEY:
                try:  # the smoke forecast the site is showing now; read only
                    from borneosky import r2
                    return r2.get_json(key)
                except (Exception, SystemExit):
                    print("  (smoke forecast read from ./out/ if there is one: R2 not reachable from here)")
            path = OUT / key
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    else:
        from botocore.exceptions import BotoCoreError, ClientError

        from borneosky import r2

        def put_json(key, obj):
            try:  # the index grows with the list of places, so it is stored compressed (browsers unpack it themselves)
                r2.put_json(key, obj, cache_seconds=900, gzipped=key == places.INDEX_KEY)
            except (ClientError, BotoCoreError) as e:
                sys.exit(f"R2 upload of {key} failed: {type(e).__name__}. Check the R2 keys.")

        get_json = r2.get_json

    session = requests.Session()
    session.headers["User-Agent"] = user_agent()
    res = places.run(put_json, get_json, session, force=args.force or args.dry_run)

    status.detail(places=res["places"], updated=len(res["updated"]), failed=len(res["failed"]))
    print(f"  {res['places']} places in the file: updated {len(res['updated'])}, not modified "
          f"{len(res['not_modified'])}, not yet due {len(res['not_due'])}, failed {len(res['failed'])}")
    for slug, err in res["failed"].items():
        print(f"  FAILED {slug}: {err}")
    if len(res["failed"]) > len(places.places()) / 4:
        sys.exit("Too many places failed.")


if __name__ == "__main__":
    if not places.enabled() and "--dry-run" not in sys.argv:
        print("  EXPLORE_PLACES is not \"on\": Explore is not switched on, skipping.")
    else:
        run_main(main, source=None if "--dry-run" in sys.argv else "places")
