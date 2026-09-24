#!/usr/bin/env python3
"""Upload registry.json to R2 so the site can build from the same list of
towns the pipeline uses. Cheap and idempotent; runs every hour."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import geo, r2  # noqa: E402
from borneosky.config import run_main  # noqa: E402


def main() -> None:
    size = r2.put_json("registry.json", geo.registry(), cache_seconds=3600)
    print(f"  uploaded registry.json ({size // 1024} KB)")


if __name__ == "__main__":
    run_main(main)
