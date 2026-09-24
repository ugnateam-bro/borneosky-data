#!/usr/bin/env python3
"""Upload registry.json and robots.txt to R2. Cheap and idempotent; runs
every hour. robots.txt keeps crawlers off data.borneosky.com: the processed
files there are for the site only (see borneosky.com/terms)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import geo, r2  # noqa: E402
from borneosky.config import run_main  # noqa: E402


ROBOTS = """# data.borneosky.com serves processed files for borneosky.com only.
# Crawling, scraping and reuse require written permission:
# https://borneosky.com/terms
User-agent: *
Disallow: /
"""


def main() -> None:
    size = r2.put_json("registry.json", geo.registry(), cache_seconds=3600)
    print(f"  uploaded registry.json ({size // 1024} KB)")
    r2.client().put_object(
        Bucket=r2.bucket(), Key="robots.txt", Body=ROBOTS.encode(),
        ContentType="text/plain; charset=utf-8", CacheControl="public, max-age=86400",
    )
    print("  uploaded robots.txt")


if __name__ == "__main__":
    run_main(main)
