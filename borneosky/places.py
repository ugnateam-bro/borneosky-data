"""Explore: weather forecasts for places people visit → R2.

For every place in data/places.json this writes one small file, places/index.json, that the site's Explore pages
read (the hub, each place page and "This weekend"). For each place and each local day of the next eight:

  tmin, tmax        °C, from MET Norway's weather forecast
  rain_am, rain_pm  mm in the morning (08:00-14:00 local) and afternoon (14:00-20:00 local): boat trips and
                    climbs leave early, and Borneo's storms usually come in the afternoon
  thunder           a thunder symbol in the daytime hours (08:00-20:00)
  wind_max          strongest daytime wind, m/s (matters for boat trips)
  symbol            the daytime weather symbol (MET's 12-hour summary nearest 08:00)
  smoke_max         highest PM2.5 in µg/m³ in the smoke forecast (CAMS), for the days it covers (about five)
  sunrise, sunset   local "HH:MM", worked out here (no service is asked)

plus `best`: the day in the next seven (today left out, it is already under way) with the least daytime rain
in the weather forecast. It is a ranking of a weather forecast, never a promise, and the site says so.

The weather forecast is fetched like the towns' (borneosky/met.py): our own User-Agent, If-Modified-Since, and
nothing before MET's Expires time. Each place's day rows are kept in places/_meta.json so a "not modified"
answer still produces a full file. The smoke forecast is not fetched again: it is read from smoke/grid.json,
which the CAMS step already publishes.

Nothing runs until the repository variable EXPLORE_PLACES is "on" (scripts/ingest_places.py). Notes:
docs/explore.md.
"""

import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from zoneinfo import ZoneInfo

from . import geo, met
from .config import DATA_DIR

INDEX_KEY = "places/index.json"
META_KEY = "places/_meta.json"
SMOKE_KEY = "smoke/grid.json"
DAYS = 8                      # today and the next seven
MORNING = (8, 14)             # local hours [start, end)
AFTERNOON = (14, 20)
LABEL = "Weather forecast from a weather model, not a measurement. The best day is a ranking of that forecast."
SMOKE_LABEL = "Smoke forecast from the CAMS global atmospheric model: modelled values, not measurements."
KINDS = ("sea", "mountain", "forest", "town", "culture")
REGIONS = {"sabah": "MY", "sarawak": "MY", "labuan": "MY", "brunei": "BN", "kalimantan": "ID"}
# Airports on our flight boards (borneosky/flights.py). A place links to a board only for one of these.
BOARD_AIRPORTS = ("KCH", "MYY", "SBW", "BTU", "BKI", "TWU", "SDK", "LBU", "BWN")


def enabled() -> bool:
    """The step runs only when the repository variable EXPLORE_PLACES is "on" (a switch that needs no deploy)."""
    return os.environ.get("EXPLORE_PLACES", "").strip().lower() in ("on", "1", "true", "yes")


def places() -> list[dict]:
    return json.loads((DATA_DIR / "places.json").read_text(encoding="utf-8"))["places"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Sunrise and sunset (NOAA's solar position equations; about a minute's accuracy near the equator) ---------------

def sun_times(lat: float, lon: float, day: date, tz: ZoneInfo) -> tuple[str | None, str | None]:
    """Local sunrise and sunset as "HH:MM" (None if the sun does not rise or set, which never happens in Borneo)."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    zenith = math.radians(90.833)   # refraction and the sun's radius
    phi = math.radians(lat)
    cos_ha = math.cos(zenith) / (math.cos(phi) * math.cos(decl)) - math.tan(phi) * math.tan(decl)
    if not -1 <= cos_ha <= 1:
        return None, None
    ha = math.degrees(math.acos(cos_ha))
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

    def local(minutes_utc: float) -> str:
        t = (midnight + timedelta(minutes=minutes_utc)).astimezone(tz)
        t += timedelta(seconds=30)  # round to the nearest minute
        return t.strftime("%H:%M")

    return local(720 - 4 * (lon + ha) - eqtime), local(720 - 4 * (lon - ha) - eqtime)


# --- Weather forecast → day rows ---------------------------------------------------------------------------------------

def _in_window(local: datetime, window: tuple[int, int]) -> bool:
    return window[0] <= local.hour < window[1]


def day_rows(timeseries: list[dict], tz: ZoneInfo, today: date) -> list[dict]:
    """One row per local day from today, for DAYS days. Rain in each window is shared out in proportion to the
    hours of each forecast period that fall inside it, so 6-hourly periods that straddle a window (UTC+7) count
    only their overlap. `am_hours`/`pm_hours` say how much of each window the forecast covered."""
    days = {today + timedelta(days=i): {"temps": [], "am": 0.0, "pm": 0.0, "am_h": 0.0, "pm_h": 0.0,
                                         "thunder": False, "winds": [], "symbol": None, "gap": None}
            for i in range(DAYS)}

    for step in timeseries:
        t = datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        loc = t.astimezone(tz)
        d = days.get(loc.date())
        if d is None:
            continue
        inst = step["data"]["instant"]["details"]
        if inst.get("air_temperature") is not None:
            d["temps"].append(inst["air_temperature"])
        if inst.get("wind_speed") is not None and _in_window(loc, (MORNING[0], AFTERNOON[1])):
            d["winds"].append(inst["wind_speed"])
        twelve = step["data"].get("next_12_hours", {}).get("summary", {}).get("symbol_code")
        if twelve:
            gap = abs(loc.hour + loc.minute / 60 - 8)
            if d["gap"] is None or gap < d["gap"]:
                d["symbol"], d["gap"] = twelve, gap

    for start, hours, mm, symbol in _periods(timeseries):
        rate = mm / hours
        cursor, end = start, start + timedelta(hours=hours)
        while cursor < end:
            loc = cursor.astimezone(tz)
            nxt = min(end, cursor + timedelta(hours=1) - timedelta(minutes=cursor.minute, seconds=cursor.second))
            span = (nxt - cursor).total_seconds() / 3600
            d = days.get(loc.date())
            if d is not None:
                if _in_window(loc, MORNING):
                    d["am"] += rate * span
                    d["am_h"] += span
                elif _in_window(loc, AFTERNOON):
                    d["pm"] += rate * span
                    d["pm_h"] += span
                if symbol and "thunder" in symbol and _in_window(loc, (MORNING[0], AFTERNOON[1])):
                    d["thunder"] = True
            cursor = nxt

    rows = []
    for day, d in sorted(days.items()):
        if not d["temps"] or d["am_h"] + d["pm_h"] == 0:
            continue
        rows.append({
            "date": day.isoformat(),
            "tmin": round(min(d["temps"])), "tmax": round(max(d["temps"])),
            "rain_am": round(d["am"], 1), "rain_pm": round(d["pm"], 1),
            "am_hours": round(d["am_h"], 1), "pm_hours": round(d["pm_h"], 1),
            "thunder": d["thunder"],
            "wind_max": round(max(d["winds"]), 1) if d["winds"] else None,
            "symbol": d["symbol"],
        })
    return rows


def _periods(timeseries: list[dict]):
    """(start, hours, mm, symbol) covering the forecast once: hourly while MET gives it, then 6-hourly."""
    covered_until = None
    for step in timeseries:
        t = datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        if covered_until and t < covered_until:
            continue
        for key, hours in (("next_1_hours", 1), ("next_6_hours", 6)):
            block = step["data"].get(key)
            mm = (block or {}).get("details", {}).get("precipitation_amount")
            if mm is not None:
                yield t, hours, mm, (block.get("summary") or {}).get("symbol_code")
                covered_until = t + timedelta(hours=hours)
                break


def best_day(rows: list[dict], today: date) -> dict | None:
    """The day after today, within seven, with the least daytime rain; ties go to a day without thunder, then the
    earlier day. Only days whose daytime the forecast fully covers take part."""
    cands = [r for r in rows if r["date"] > today.isoformat()
             and r["date"] <= (today + timedelta(days=DAYS - 1)).isoformat()
             and r["am_hours"] >= MORNING[1] - MORNING[0] - 0.01 and r["pm_hours"] >= AFTERNOON[1] - AFTERNOON[0] - 0.01]
    if not cands:
        return None
    r = min(cands, key=lambda r: (round(r["rain_am"] + r["rain_pm"], 1), r["thunder"], r["date"]))
    return {"date": r["date"], "rain": round(r["rain_am"] + r["rain_pm"], 1), "thunder": r["thunder"]}


# --- Smoke forecast at a place ----------------------------------------------------------------------------------------

def smoke_daily(grid: dict | None, lat: float, lon: float, tz: ZoneInfo) -> dict[str, int]:
    """Highest modelled PM2.5 per local day at a point, from the CAMS step's smoke/grid.json (bilinear, like the towns).
    Days with fewer than four of the eight 3-hour steps are left out: a sliver of a day says little."""
    if not grid:
        return {}
    import numpy as np

    lat_axis, lon_axis = np.array(grid["lat"]), np.array(grid["lon"])
    shape = (len(lat_axis), len(lon_axis))
    out: dict[str, list[float]] = {}
    for t, flat in zip(grid["times"], grid["pm25"]):
        field = np.array([np.nan if v is None else v for v in flat], dtype=float).reshape(shape)
        v = float(_bilinear(lat_axis, lon_axis, field, lat, lon))
        if math.isfinite(v):
            day = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(tz).date().isoformat()
            out.setdefault(day, []).append(v)
    return {d: round(max(vs)) for d, vs in out.items() if len(vs) >= 4}


def _bilinear(lat_axis, lon_axis, field, lat, lon):
    from .cams import _bilinear as bil
    return bil(lat_axis, lon_axis, field, lat, lon)


# --- The run ----------------------------------------------------------------------------------------------------------

def assemble(place_list: list[dict], state: dict, grid: dict | None, now: datetime) -> dict:
    out = {}
    for p in place_list:
        st = state.get(p["slug"])
        if not st or not st.get("days"):
            continue
        tz = ZoneInfo(p["timezone"])
        today = now.astimezone(tz).date()
        lat, lon = p["point"]["lat"], p["point"]["lon"]
        smoke = smoke_daily(grid, lat, lon, tz)
        days = []
        for r in st["days"]:
            if r["date"] < today.isoformat():
                continue  # a file kept from yesterday: drop the days that have passed
            rise, set_ = sun_times(lat, lon, date.fromisoformat(r["date"]), tz)
            days.append({**r, "smoke_max": smoke.get(r["date"]), "sunrise": rise, "sunset": set_})
        if not days:
            continue
        out[p["slug"]] = {"model_updated_utc": st.get("model_updated_utc"), "days": days,
                          "best": best_day(days, today)}
    return {
        "generated_utc": _iso(now),
        "label": LABEL,
        "smoke_label": SMOKE_LABEL,
        "smoke_run_utc": (grid or {}).get("run_utc"),
        "attribution": met.ATTRIBUTION,
        "smoke_attribution": (grid or {}).get("attribution"),
        "windows": {"morning": list(MORNING), "afternoon": list(AFTERNOON)},
        "units": {"temp": "°C", "rain": "mm", "wind": "m/s", "smoke": "µg/m³ PM2.5 (modelled)"},
        "places": out,
    }


def run(put_json, get_json, session, *, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    meta = (None if force else get_json(META_KEY)) or {"places": {}}
    state = meta.setdefault("places", {})
    result = {"updated": [], "not_modified": [], "not_due": [], "failed": {}}
    place_list = places()

    for p in place_list:
        slug, prev = p["slug"], state.get(p["slug"], {})
        expires = met._parse_http_date(prev.get("expires"))
        if not force and expires and now < expires:
            result["not_due"].append(slug)
            continue
        try:
            status, data, headers = met.fetch(session, p, prev.get("last_modified"))
        except met.MetError as e:
            result["failed"][slug] = str(e)
            time.sleep(met.REQUEST_GAP_S)
            continue
        entry = dict(prev)
        if status == 200:
            tz = ZoneInfo(p["timezone"])
            entry["days"] = day_rows(data["properties"]["timeseries"], tz, now.astimezone(tz).date())
            entry["model_updated_utc"] = data["properties"]["meta"].get("updated_at")
            result["updated"].append(slug)
        else:
            result["not_modified"].append(slug)
        entry.update({
            "last_modified": headers.get("Last-Modified") or prev.get("last_modified"),
            "expires": headers.get("Expires") or format_datetime(now + timedelta(minutes=30), usegmt=True),
            "checked_utc": _iso(now),
        })
        state[slug] = entry
        time.sleep(met.REQUEST_GAP_S)

    # Places no longer in the list drop out of the file.
    for gone in set(state) - {p["slug"] for p in place_list}:
        state.pop(gone)
    meta["generated_utc"] = _iso(now)
    put_json(META_KEY, meta)
    index = assemble(place_list, state, get_json(SMOKE_KEY), now)
    put_json(INDEX_KEY, index)
    result["places"] = len(index["places"])
    return result


def check_list(place_list: list[dict] | None = None) -> list[str]:
    """Problems with data/places.json (empty = fine). Used by the tests and before every run."""
    place_list = place_list if place_list is not None else places()
    towns = {t["slug"]: t for t in geo.towns()}
    problems, seen = [], set()
    for p in place_list:
        s = p.get("slug", "?")
        if s in seen:
            problems.append(f"{s}: slug used twice")
        seen.add(s)
        if p.get("kind") not in KINDS:
            problems.append(f"{s}: kind must be one of {KINDS}")
        if REGIONS.get(p.get("region")) != p.get("country_code"):
            problems.append(f"{s}: region and country do not match")
        if p.get("airport") is not None and p["airport"] not in BOARD_AIRPORTS:
            problems.append(f"{s}: airport {p['airport']} is not on our flight boards")
        town = towns.get(p.get("town"))
        if not town:
            problems.append(f"{s}: town {p.get('town')} is not in the registry")
            continue
        if town["timezone"] != p.get("timezone"):
            problems.append(f"{s}: time zone differs from its town's")
        km = geo.haversine_km(p["point"]["lat"], p["point"]["lon"], town["point"]["lat"], town["point"]["lon"])
        if km > 260:  # a few deep-interior parks (Betung Kerihun, Kayan Mentarang) are far from any registry town
            problems.append(f"{s}: {km:.0f} km from its town")
        lat, lon = p["point"]["lat"], p["point"]["lon"]
        if not (-4.5 <= lat <= 7.5 and 108.5 <= lon <= 119.5):
            problems.append(f"{s}: outside the Borneo box")
        off = (p.get("official") or {}).get("url", "https://")
        if not off.startswith("https://"):
            problems.append(f"{s}: official link must be https")
    return problems
