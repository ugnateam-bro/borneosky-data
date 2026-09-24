"""NASA FIRMS hotspot ingest.

Fetches near-real-time active-fire detections for the Borneo bbox, keeps the
ones that fall on Borneo, tags each to its nearest registry town, and writes:

  current/hotspots.geojson          every detection in the window (the map)
  current/hotspots_summary.json     counts per town and per region (town pages)

These are satellite detections of heat, not confirmed fires. Output carries
that label so the site never has to invent it.
"""

import csv
import io
from datetime import datetime, timedelta, timezone

import requests

from . import geo
from .config import BBOX, require, user_agent

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{days}"

SOURCES = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT",
]

WINDOW_HOURS = 48
DAY_RANGE = 2  # FIRMS counts whole UTC days back from today; 2 covers 48 h

VIIRS_CONFIDENCE = {"l": "low", "n": "nominal", "h": "high"}

# Short property names keep the 48 h file small in fire season (~25k
# detections). The file's "properties_key" documents them for the site.
PROPERTIES_KEY = {
    "t": "acquisition time, UTC (YYYY-MM-DDTHH:MMZ)",
    "sat": "satellite: SNPP, N20, N21 (VIIRS 375 m) or T, A (MODIS Terra/Aqua 1 km)",
    "c": "confidence: l low, n nominal, h high",
    "frp": "fire radiative power, MW",
    "n": "1 if night-time pass",
    "r": "region (state/province/district)",
    "town": "nearest registry town slug",
    "km": "distance to that town, km",
}
SAT_CODE = {"VIIRS_SNPP_NRT": "SNPP", "VIIRS_NOAA20_NRT": "N20",
            "VIIRS_NOAA21_NRT": "N21"}

LABEL = ("Satellite detections of heat. A detection is not a confirmed fire "
         "and can include gas flares, hot industrial sites or other heat sources.")
ATTRIBUTION = ("Fire detections: NASA FIRMS (LANCE), "
               "https://firms.modaps.eosdis.nasa.gov")


class FirmsError(Exception):
    pass


def _fetch_csv(session: requests.Session, key: str, source: str) -> list[dict]:
    url = API.format(key=key, source=source,
                     bbox=",".join(str(v) for v in BBOX), days=DAY_RANGE)
    try:
        resp = session.get(url, timeout=90)
    except requests.RequestException as e:
        # The URL contains the MAP_KEY; report the error type only.
        raise FirmsError(f"{source}: network error ({type(e).__name__})") from None
    if resp.status_code != 200:
        raise FirmsError(f"{source}: HTTP {resp.status_code}")

    text = resp.text.strip()
    # FIRMS reports bad keys and quota problems as 200 with a plain-text body.
    if not text.startswith("latitude,"):
        first = text.splitlines()[0][:120] if text else "(empty body)"
        raise FirmsError(f"{source}: unexpected response: {first.replace(key, '***')}")
    return list(csv.DictReader(io.StringIO(text)))


def _confidence(row: dict) -> str:
    raw = (row.get("confidence") or "").strip().lower()
    if raw in VIIRS_CONFIDENCE:
        return VIIRS_CONFIDENCE[raw]
    try:  # MODIS: 0–100
        pct = int(float(raw))
    except ValueError:
        return "unknown"
    return "low" if pct < 30 else "high" if pct >= 80 else "nominal"


def _acq_time(row: dict) -> datetime:
    hhmm = row["acq_time"].strip().zfill(4)
    return datetime.strptime(f"{row['acq_date']} {hhmm}", "%Y-%m-%d %H%M").replace(
        tzinfo=timezone.utc)


def _float(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def build(now: datetime | None = None) -> tuple[dict, dict]:
    """Fetch every source and return (geojson, summary). Raises FirmsError
    only if *no* source succeeded, so one satellite's outage does not blank
    the map."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    key = require("FIRMS_MAP_KEY")

    session = requests.Session()
    session.headers["User-Agent"] = user_agent()

    source_status, features = {}, []
    dropped_off_island = 0

    for source in SOURCES:
        try:
            rows = _fetch_csv(session, key, source)
        except FirmsError as e:
            source_status[source] = {"ok": False, "error": str(e)}
            continue

        kept = 0
        for row in rows:
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
                acq = _acq_time(row)
            except (KeyError, ValueError):
                continue
            if acq < cutoff:
                continue

            region = geo.borneo_region(lat, lon)
            if region is None:
                dropped_off_island += 1
                continue

            town, km = geo.nearest_town(lat, lon)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [round(lon, 4), round(lat, 4)]},
                "properties": {
                    "t": acq.strftime("%Y-%m-%dT%H:%MZ"),
                    "sat": SAT_CODE.get(source) or row.get("satellite", "")[:1] or "M",
                    "c": _confidence(row)[0],
                    "frp": _float(row.get("frp")),
                    "n": int(row.get("daynight", "").upper() == "N"),
                    "r": region["admin1"],
                    "town": town["slug"],
                    "km": round(km, 1),
                },
            })
            kept += 1
        source_status[source] = {"ok": True, "rows": len(rows), "kept": kept}

    if not any(s["ok"] for s in source_status.values()):
        errors = "; ".join(s["error"] for s in source_status.values())
        raise FirmsError(f"all FIRMS sources failed: {errors}")

    features.sort(key=lambda f: f["properties"]["t"], reverse=True)
    generated = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    meta = {
        "generated_utc": generated,
        "window_hours": WINDOW_HOURS,
        "label": LABEL,
        "attribution": ATTRIBUTION,
        "sources": source_status,
    }
    geojson = {"type": "FeatureCollection", **meta,
               "properties_key": PROPERTIES_KEY, "count": len(features),
               "dropped_off_island": dropped_off_island,
               "features": features}
    return geojson, summarise(features, meta, now)


def summarise(features: list[dict], meta: dict, now: datetime) -> dict:
    last24 = now - timedelta(hours=24)
    by_town = {t["slug"]: {"h24": 0, "h48": 0, "nearest_km": None}
               for t in geo.towns()}
    by_region: dict[str, dict] = {}

    for f in features:
        p = f["properties"]
        recent = datetime.strptime(p["t"], "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=timezone.utc) >= last24

        t = by_town[p["town"]]
        t["h48"] += 1
        t["h24"] += recent
        if t["nearest_km"] is None or p["km"] < t["nearest_km"]:
            t["nearest_km"] = p["km"]

        r = by_region.setdefault(p["r"], {"country_code": geo.region_country(p["r"]),
                                          "admin1": p["r"], "h24": 0, "h48": 0})
        r["h48"] += 1
        r["h24"] += recent

    return {**meta, "total_h24": sum(t["h24"] for t in by_town.values()),
            "total_h48": len(features), "towns": by_town,
            "regions": sorted(by_region.values(), key=lambda r: -r["h48"])}
