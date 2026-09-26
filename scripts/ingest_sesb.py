#!/usr/bin/env python3
"""
Sabah outage notices (SESB's own outage portal) → R2.

Usage:
  python scripts/ingest_sesb.py              check SESB and upload notices/sesb.json (only when SESB_NOTICES is "on")
  python scripts/ingest_sesb.py --dry-run    the same, but write to ./out/ only; works whether or not it is on
  python scripts/ingest_sesb.py --force      ignore the saved ETags and download both files again

Does nothing until the repository variable SESB_NOTICES is "on" (Settings > Secrets and variables > Actions >
Variables), so the hourly workflow can carry this step before the permission letter is answered. Setting it to
anything else switches the step off again at once, with no deploy. A failed run uploads nothing: R2 keeps the
last good file. Exits non-zero if SESB cannot be read or its files no longer have the expected shape. What
it does and why: docs/sesb-notices.md.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import sesb, status  # noqa: E402
from borneosky.config import run_main  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        # Local files stand in for R2. The previous output is read back, so a second dry run shows the "unchanged" path.
        def put_json(key, obj):
            path = OUT / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

        def get_json(key):
            path = OUT / key
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    else:
        from botocore.exceptions import BotoCoreError, ClientError

        from borneosky import r2

        def put_json(key, obj):
            try:
                r2.put_json(key, obj, cache_seconds=300)
            except (ClientError, BotoCoreError) as e:
                sys.exit(f"R2 upload of {key} failed: {type(e).__name__}. Check the R2 keys.")

        get_json = r2.get_json

    try:
        res = sesb.run(put_json, get_json, sesb.make_session(), force=args.force)
    except sesb.SesbError as e:
        sys.exit(f"SESB outage portal: {e}")

    out = res["out"]
    counts = out.get("counts", {})
    status.detail(notices=res["notices"], source_changed=out.get("source_changed_utc"))
    if res["result"] == "updated":
        print(f"  updated: {res['notices']} notices listed ({counts.get('planned', 0)} planned, "
              f"{counts.get('short_notice', 0)} short notice; {counts.get('duplicates', 0)} duplicates and "
              f"{counts.get('skipped', 0)} unreadable rows left out)")
    else:
        print(f"  unchanged at SESB: {res['notices']} notices still listed, checked again")
    print(f"  SESB's files last changed: {out.get('source_changed_utc') or 'unknown'}")
    if res["source_stale"]:
        print("  WARNING: SESB's files have not changed for more than 3 days")
        status.soft_fail(f"SESB's own files have not changed since {out.get('source_changed_utc')}: notices may be out of date")


if __name__ == "__main__":
    if not sesb.enabled() and "--dry-run" not in sys.argv:
        print("  SESB_NOTICES is not \"on\": Sabah outage notices are not switched on, skipping.")
    else:
        run_main(main, source=None if "--dry-run" in sys.argv else "sesb")
