#!/usr/bin/env python3
"""
Sarawak outage notices (Sarawak Energy's own posts on X) → R2.

Usage:
  python scripts/ingest_sarawak.py               read the account and upload notices/sarawak-energy.json
                                                 (only when SARAWAK_NOTICES is "on")
  python scripts/ingest_sarawak.py --dry-run     the same, but write to ./out/ only; works whether or not it is on
  python scripts/ingest_sarawak.py --force       forget the saved place and read the last 48 hours again
  python scripts/ingest_sarawak.py --sample 20   read the latest 20 posts (about US$0.11) into out/_sample/ for tuning
                                                 the reader, and print only counts; changes nothing else

Every way of running this except the skip below reads X and spends credits (US$0.005 a post). It needs the Bearer Token
X_BEARER_TOKEN: a GitHub secret in Actions, a line in ~/Bs/project/.env on this computer. It does nothing until the
repository variable SARAWAK_NOTICES is "on" (Settings > Secrets and variables > Actions > Variables), so the hourly
workflow can carry this step before X is paid for. Setting the variable to anything else switches the step off again at
once, with no deploy. A failed run uploads nothing: R2 keeps the last good file. Exits non-zero if X cannot be read or
answers in a way the reader does not expect. What it does, what it costs and why: docs/sarawak-notices.md.

Never printed here: the token, or the text of a post. What is printed is counts and dollars.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import sarawak, status  # noqa: E402
from borneosky.config import run_main  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def sample(session, count: int) -> None:
    res = sarawak.sample(session, count)
    rows = res["rows"]
    kept = [r for r in rows if r["kept"]]
    readable = [r for r in kept if r["facts"]["readable"]]
    dest = OUT / "_sample" / "sarawak-posts.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({
        "note": "X content, for reading on this computer only. Never commit it, print it to a log, or upload it.",
        "user_id": res["user_id"], "posts": res["posts"], "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  read {len(rows)} posts for about US${res['cost_usd']:.3f} (the reader's own count of what it would keep follows)")
    print(f"  kept as notices: {len(kept)} ({len(readable)} readable, {len(kept) - len(readable)} unread); left out: {len(rows) - len(kept)}")
    print(f"  written to {dest.relative_to(OUT.parent)} (git ignores out/)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sample", nargs="?", type=int, const=20, default=None, metavar="N")
    args = ap.parse_args()

    try:
        session = sarawak.make_session(sarawak.token())
        if args.sample is not None:
            sample(session, args.sample)
            return
    except sarawak.SarawakError as e:
        sys.exit(f"Sarawak Energy on X: {e}")

    if args.dry_run:
        # Local files stand in for R2. The previous output is read back, so a second dry run shows the "nothing new" path.
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
        res = sarawak.run(put_json, get_json, session, force=args.force)
    except sarawak.SarawakError as e:
        sys.exit(f"Sarawak Energy on X: {e}")

    c = res["counts"]
    status.detail(notices=res["notices"], posts_today=res["posts_today"], cost_month_usd=res["cost_month_usd"])
    print(f"  {res['notices']} notices listed; {c['posts_seen']} new posts read ({c['outage_posts']} about outages, {c['unread']} of them unread, "
          f"{c['dropped']} left out), {c['deleted']} deleted at X, {c['replaced']} replaced by an update")
    print(f"  {res['posts_today']} posts read today; about US${res['cost_month_usd']:.3f} at X so far this month")
    if res["truncated"]:
        print("  WARNING: there were more new posts than one run reads; some may have been missed")


if __name__ == "__main__":
    local = any(a == "--dry-run" or a.startswith("--sample") for a in sys.argv[1:])       # runs that never touch R2 or meta.json
    if not sarawak.enabled() and not local:
        print("  SARAWAK_NOTICES is not \"on\": Sarawak outage notices are not switched on, skipping.")
    else:
        run_main(main, source=None if local else "sarawak")
