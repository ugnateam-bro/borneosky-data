#!/usr/bin/env python3
"""
Build the BorneoSky location registry from towns.csv.

Every page, forecast file and map marker is generated from the output,
so this script validates hard and refuses to write a broken registry.

Usage:  python build_registry.py [towns.csv] [registry.json]
"""

import csv
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Borneo bounding box: west, south, east, north
BBOX = (108.5, -4.5, 119.5, 7.5)

COUNTRIES = {
    "MY": {"name": "Malaysia", "path": "my", "index": "API"},
    "BN": {"name": "Brunei Darussalam", "path": "bn", "index": "PSI"},
    "ID": {"name": "Indonesia", "path": "id", "index": "ISPU"},
}

# Which official portal a visitor is sent to when we have no measured reading.
OFFICIAL_SOURCE = {
    "MY": {
        "name": "Department of Environment Malaysia (APIMS)",
        "url": "https://eqms.doe.gov.my/APIMS/main",
    },
    "BN": {
        "name": "Department of Environment, Parks and Recreation, Brunei",
        "url": "https://www.env.gov.bn",
    },
    "ID": {
        "name": "KLHK / BMKG, Indonesia",
        "url": "https://www.bmkg.go.id",
    },
}


def slugify_region(value):
    return value.lower().replace(" ", "-").replace(".", "")


def build(rows):
    locations = []
    errors = []
    seen_slugs = set()

    for line_no, row in enumerate(rows, start=2):
        slug = row["slug"].strip()
        name = row["name"].strip()
        country = row["country"].strip().upper()
        admin1 = row["admin1"].strip()
        tz = row["timezone"].strip()
        index = row["index"].strip().upper()

        def fail(msg):
            errors.append(f"line {line_no} ({slug or '?'}): {msg}")

        if not slug:
            fail("missing slug")
            continue
        if slug in seen_slugs:
            fail("duplicate slug")
            continue
        seen_slugs.add(slug)

        if slug != slug.lower() or " " in slug:
            fail("slug must be lowercase with no spaces")

        if country not in COUNTRIES:
            fail(f"unknown country code '{country}'")
            continue

        if index != COUNTRIES[country]["index"]:
            fail(f"index '{index}' does not match {country} (expected "
                 f"{COUNTRIES[country]['index']})")

        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except ValueError:
            fail("lat/lon not numeric")
            continue

        west, south, east, north = BBOX
        if not (west <= lon <= east and south <= lat <= north):
            fail(f"coordinates {lat},{lon} fall outside Borneo")

        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            fail(f"unknown timezone '{tz}'")
            continue

        offset = datetime.now(ZoneInfo(tz)).utcoffset()
        utc_offset_hours = offset.total_seconds() / 3600 if offset else None

        locations.append({
            "slug": slug,
            "name": name,
            "country_code": country,
            "country": COUNTRIES[country]["name"],
            "admin1": admin1,
            "point": {"lat": lat, "lon": lon},
            "timezone": tz,
            "utc_offset_hours": utc_offset_hours,
            "index": index,
            "path": f"/{COUNTRIES[country]['path']}/{slugify_region(admin1)}/{slug}",
            "forecast_file": f"forecast/districts/{slug}.json",
            "official_source": OFFICIAL_SOURCE[country],
            "measured_reading": None,
        })

    return locations, errors


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "towns.csv"
    dest = sys.argv[2] if len(sys.argv) > 2 else "registry.json"

    with open(src, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    locations, errors = build(rows)

    if errors:
        print(f"REGISTRY NOT WRITTEN — {len(errors)} problem(s):\n")
        for e in errors:
            print("  " + e)
        sys.exit(1)

    registry = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bbox": list(BBOX),
        "count": len(locations),
        "locations": sorted(locations, key=lambda x: (x["country_code"],
                                                      x["admin1"], x["name"])),
    }

    with open(dest, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

    by_country = {}
    for loc in locations:
        by_country[loc["country"]] = by_country.get(loc["country"], 0) + 1

    print(f"Wrote {dest} — {len(locations)} locations")
    for country, n in sorted(by_country.items()):
        print(f"  {country}: {n}")


if __name__ == "__main__":
    main()
