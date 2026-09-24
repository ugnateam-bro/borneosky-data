"""Round-trip test against R2: write, read back, delete. Prints no secrets."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import r2  # noqa: E402

KEY = "_healthcheck/r2_check.json"


def main() -> None:
    payload = {"check": "r2", "ts": int(time.time())}
    size = r2.put_json(KEY, payload, cache_seconds=0)
    print(f"write  ok  ({size} bytes)")

    back = r2.get_json(KEY)
    if back != payload:
        raise SystemExit("read   FAILED: content did not match")
    print("read   ok  (content matches)")

    r2.client().delete_object(Bucket=r2.bucket(), Key=KEY)
    if r2.get_json(KEY) is not None:
        raise SystemExit("delete FAILED: object still present")
    print("delete ok")
    print("R2 works.")


if __name__ == "__main__":
    main()
