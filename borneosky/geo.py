"""Registry, Borneo land mask and nearest-town lookup."""

import json
import math
from functools import lru_cache

from shapely.geometry import Point, shape
from shapely.prepared import prep

from .config import DATA_DIR

EARTH_RADIUS_KM = 6371.0088


@lru_cache(maxsize=1)
def registry() -> dict:
    return json.loads((DATA_DIR / "registry.json").read_text(encoding="utf-8"))


def towns() -> list[dict]:
    return registry()["locations"]


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest_town(lat: float, lon: float) -> tuple[dict, float]:
    """Brute force over the registry; 44 towns is nothing."""
    best, best_km = None, math.inf
    for t in towns():
        km = haversine_km(lat, lon, t["point"]["lat"], t["point"]["lon"])
        if km < best_km:
            best, best_km = t, km
    return best, best_km


@lru_cache(maxsize=1)
def _mask():
    """Prepared polygons for each Borneo region (built by scripts/build_borneo_mask.py)."""
    fc = json.loads((DATA_DIR / "borneo_mask.geojson").read_text(encoding="utf-8"))
    buf = fc["buffer_deg"]
    exact, coastal = [], []
    for f in fc["features"]:
        geom = shape(f["geometry"])
        exact.append((prep(geom), geom.bounds, f["properties"]))
        wide = geom.buffer(buf)
        coastal.append((prep(wide), wide.bounds, f["properties"]))
    return exact, coastal


def borneo_region(lat: float, lon: float) -> dict | None:
    """Return {country_code, admin1} if the point is on Borneo (plus a small
    coastal buffer), else None. Filters out Sulawesi and the Philippines,
    which the rectangular bbox also covers."""
    pt = Point(lon, lat)
    # Exact boundaries first, so a town on a border (Limbang) lands in the
    # right region; the buffered pass only catches coastal misses.
    for regions in _mask():
        for prepared, (w, s, e, n), props in regions:
            if w <= lon <= e and s <= lat <= n and prepared.contains(pt):
                return props
    return None


def region_country(admin1: str) -> str:
    for _, _, props in _mask()[0]:
        if props["admin1"] == admin1:
            return props["country_code"]
    return ""
