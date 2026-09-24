"""Check settings and R2 access before the ingests run. Prints names and
lengths only, never values. Exits non-zero if anything is wrong."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky.config import LOCAL_ENV  # noqa: E402  (importing loads .env locally)

REQUIRED = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET", "FIRMS_MAP_KEY", "ADS_API_KEY"]
OPTIONAL = ["CONTACT_EMAIL"]


def main() -> None:
    print(f"  settings from: {'.env' if LOCAL_ENV.is_file() else 'environment (CI)'}")
    bad = False
    for name in REQUIRED + OPTIONAL:
        raw = os.environ.get(name, "")
        val = raw.strip()
        if not val:
            print(f"  {name:22} MISSING or empty" + ("" if name in REQUIRED else " (optional)"))
            bad = bad or name in REQUIRED
        else:
            note = " (has stray whitespace, stripped)" if raw != val else ""
            quoted = " (wrapped in quotes — remove them)" if val[0] in "\"'" else ""
            print(f"  {name:22} set, {len(val)} chars{note}{quoted}")
            bad = bad or bool(quoted)
    if bad:
        sys.exit("Fix the secrets above (repo Settings > Secrets and variables > Actions).")

    from botocore.exceptions import BotoCoreError, ClientError

    from borneosky import r2

    key = "_healthcheck/preflight.json"
    try:
        r2.put_json(key, {"ts": int(time.time())}, cache_seconds=0)
        r2.client().delete_object(Bucket=r2.bucket(), Key=key)
    except ClientError as e:
        err = e.response.get("Error", {})
        sys.exit(f"R2 rejected the request: {err.get('Code')} — {err.get('Message', '')[:120]}")
    except BotoCoreError as e:
        sys.exit(f"R2 connection problem: {type(e).__name__}")
    print("  R2 write/delete ok")


if __name__ == "__main__":
    main()
