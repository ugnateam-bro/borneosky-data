#!/usr/bin/env python3
"""
Weather warnings (MetMalaysia for Sarawak, Sabah and Labuan; BMKG for Kalimantan only if BMKG_WARNINGS is "on", which it is not) → R2 (warnings/current.json).

Usage:
  python scripts/ingest_warnings.py            read MetMalaysia (and BMKG only if BMKG_WARNINGS is "on") and upload the file (only when WARNINGS is "on")
  python scripts/ingest_warnings.py --dry-run  the same, but write to ./out/ only; works whether or not it is on

Does nothing until the repository variable WARNINGS is "on" (Settings > Secrets and variables > Actions > Variables), so
the hourly workflow can carry this step before the site shows warnings. Setting it to anything else stops it at once, with
no deploy. If both agencies fail nothing is uploaded and R2 keeps the last good file. Notes: docs/warnings.md.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from borneosky import status, warnings  # noqa: E402
from borneosky.config import run_main, user_agent  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
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
                r2.put_json(key, obj, cache_seconds=120)
            except (ClientError, BotoCoreError) as e:
                sys.exit(f"R2 upload of {key} failed: {type(e).__name__}. Check the R2 keys.")

        get_json = r2.get_json

    session = requests.Session()
    session.headers["User-Agent"] = user_agent()
    try:
        res = warnings.run(put_json, get_json, session, bmkg=bmkg_enabled())
    except warnings.WarningsError as e:
        sys.exit(f"Weather warnings: {e}")

    status.detail(warnings=res["count"], urgent=res["urgent"])
    print(f"  {res['count']} warnings in force or coming ({res['urgent']} urgent)")
    for w in res["out"]["warnings"]:
        print(f"    {w['source']:<11} {w['kind']:<12} {w['level']:<8} {'URGENT ' if w['urgent'] else ''}"
              f"{w['title']['en'][:60]} | towns: {', '.join(w['towns']) or '-'} | until {w['valid_to']}")
    for name, err in res["errors"].items():
        print(f"  {name} could not be read: {err}")
        status.soft_fail(f"{name} could not be read: {err}")


def bmkg_enabled() -> bool:
    """BMKG is read only when the repository variable BMKG_WARNINGS is "on" (off since 5 Oct 2026, see borneosky/warnings.py)."""
    return os.environ.get("BMKG_WARNINGS", "").strip().lower() == "on"


def enabled() -> bool:
    return os.environ.get("WARNINGS", "").strip().lower() == "on"


if __name__ == "__main__":
    if not enabled() and "--dry-run" not in sys.argv:
        print("  WARNINGS is not \"on\": weather warnings are not switched on, skipping.")
    else:
        run_main(main, source=None if "--dry-run" in sys.argv else "warnings")
