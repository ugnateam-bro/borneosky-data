#!/usr/bin/env python3
"""
Hourly FIRMS hotspot ingest → R2.

Usage:
  python scripts/ingest_firms.py            fetch and upload to R2
  python scripts/ingest_firms.py --dry-run  fetch and write to ./out/ only

Exits non-zero (and uploads nothing) if every FIRMS source fails, so the last
good file stays live in R2.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import firms  # noqa: E402

# name: (R2 key, content type, pre-gzip)
KEYS = {
    "geojson": ("current/hotspots.geojson", "application/geo+json", True),
    "summary": ("current/hotspots_summary.json", "application/json", False),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="write to ./out/ instead of uploading")
    args = ap.parse_args()

    try:
        geojson, summary = firms.build()
    except firms.FirmsError as e:
        sys.exit(f"FIRMS ingest failed, nothing uploaded: {e}")

    for src, st in geojson["sources"].items():
        detail = f"{st['kept']}/{st['rows']} kept" if st["ok"] else st["error"]
        print(f"  {src:18} {'ok ' if st['ok'] else 'ERR'} {detail}")
    print(f"  {geojson['count']} detections on Borneo in last "
          f"{geojson['window_hours']} h ({geojson['dropped_off_island']} off-island dropped)")

    outputs = {"geojson": geojson, "summary": summary}

    if args.dry_run:
        out = Path(__file__).resolve().parent.parent / "out"
        for name, (key, _, _) in KEYS.items():
            path = out / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(outputs[name], ensure_ascii=False,
                                       separators=(",", ":")),
                            encoding="utf-8")
            print(f"  wrote {path.relative_to(out.parent)}")
        return

    from botocore.exceptions import BotoCoreError, ClientError

    from borneosky import r2

    for name, (key, ctype, gz) in KEYS.items():
        try:
            size = r2.put_json(key, outputs[name], content_type=ctype,
                               cache_seconds=600, gzipped=gz)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "unknown")
            sys.exit(f"R2 upload of {key} failed: {code}. Check the R2 keys "
                     "(.env locally, repository secrets in Actions).")
        except BotoCoreError as e:
            sys.exit(f"R2 upload of {key} failed: {type(e).__name__}")
        print(f"  uploaded {key} ({size // 1024} KB)")


if __name__ == "__main__":
    from borneosky.config import run_main
    run_main(main)
