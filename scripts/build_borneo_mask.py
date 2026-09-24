#!/usr/bin/env python3
"""
Build data/borneo_mask.geojson from geoBoundaries (gbOpen).

The FIRMS bounding box is a rectangle, so it also covers the west coast of
Sulawesi and a few southern Philippine islands. This mask keeps only
detections that fall on Borneo itself, and tells us which province/state
each one is in.

Run once and commit the output; the hourly job only reads it.

Usage:  python scripts/build_borneo_mask.py
"""

import json
import sys
from pathlib import Path

import requests
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky.config import DATA_DIR, user_agent  # noqa: E402

API = "https://www.geoboundaries.org/api/current/gbOpen/{iso}/{level}/"

# (ISO3, admin level, our country code, {geoBoundaries name: registry admin1})
# None keeps every feature under its own name.
LAYERS = [
    ("MYS", "ADM1", "MY", {"Sabah": "Sabah", "Sarawak": "Sarawak", "Labuan": "Labuan"}),
    ("IDN", "ADM1", "ID", {
        "West Kalimantan": "Kalimantan Barat",
        "Central Kalimantan": "Kalimantan Tengah",
        "South Kalimantan": "Kalimantan Selatan",
        "East Kalimantan": "Kalimantan Timur",
        "North Kalimantan": "Kalimantan Utara",
    }),
    ("BRN", "ADM1", "BN", None),
]

# Coastal buffer (~5.5 km) applied by borneosky.geo at load time, only for
# points that miss every region, so inland borders never overlap.
BUFFER_DEG = 0.05
SIMPLIFY_DEG = 0.005


def fetch(iso: str, level: str) -> tuple[dict, dict]:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent()
    meta = s.get(API.format(iso=iso, level=level), timeout=60)
    meta.raise_for_status()
    meta = meta.json()
    url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
    gj = s.get(url, timeout=300)
    gj.raise_for_status()
    return meta, gj.json()


def match(name: str, wanted: dict[str, str]) -> str | None:
    """Substring match, since geoBoundaries names vary ('W.P. Labuan')."""
    low = name.lower()
    for src, ours in wanted.items():
        if src.lower() in low:
            return ours
    return None


def main() -> None:
    features, sources, missing = [], [], []

    for iso, level, cc, wanted in LAYERS:
        meta, gj = fetch(iso, level)
        sources.append({
            "iso": iso,
            "level": level,
            "license": meta.get("boundaryLicense"),
            "source": meta.get("boundarySource"),
            "year": meta.get("boundaryYearRepresented"),
        })
        found = set()
        for f in gj["features"]:
            name = f["properties"].get("shapeName", "")
            if wanted is None:
                keep = name
            else:
                keep = match(name, wanted)
                if not keep:
                    continue
            found.add(keep)
            geom = shape(f["geometry"]).buffer(0).simplify(SIMPLIFY_DEG)
            features.append((cc, keep, geom))
        if wanted:
            missing += [f"{iso}:{w}" for w in sorted(set(wanted.values()) - found)]

    if missing:
        sys.exit(f"MASK NOT WRITTEN — regions not found: {', '.join(missing)}")

    # Merge pieces that share a name (e.g. multi-part features).
    merged: dict[tuple[str, str], list] = {}
    for cc, name, geom in features:
        merged.setdefault((cc, name), []).append(geom)

    out = {
        "type": "FeatureCollection",
        "attribution": ("Boundaries: geoBoundaries (gbOpen), www.geoboundaries.org; "
                        "© OpenStreetMap contributors, ODbL 1.0"),
        "license": "ODbL 1.0 (derived from ODbL sources)",
        "sources": sources,
        "buffer_deg": BUFFER_DEG,
        "features": [
            {
                "type": "Feature",
                "properties": {"country_code": cc, "admin1": name},
                "geometry": mapping(unary_union(geoms)),
            }
            for (cc, name), geoms in sorted(merged.items())
        ],
    }

    dest = DATA_DIR / "borneo_mask.geojson"
    dest.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {dest.relative_to(DATA_DIR.parent)} — {len(out['features'])} regions, "
          f"{dest.stat().st_size // 1024} KB")
    for s in sources:
        print(f"  {s['iso']} {s['level']}: {s['license']} ({s['source']})")


if __name__ == "__main__":
    main()
