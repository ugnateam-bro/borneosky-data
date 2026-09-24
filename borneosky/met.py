"""MET Norway Locationforecast 2.0 → per-town forecast files.

For each registry town writes forecast/districts/{slug}.json with:
  hourly  the next 48 h as MET publishes it (UTC timestamps)
  daily   one row per *local* calendar day, computed in the town's own time
          zone (Asia/Pontianak is UTC+7, the rest of Borneo UTC+8)

MET's terms: identify ourselves in the User-Agent, don't refetch before the
Expires header, and use If-Modified-Since. The Expires/Last-Modified values
for every town are kept in forecast/_meta.json in R2 between runs.

This is forecast data and is labelled as such. It is never shown as a
measured reading.
"""

import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

from . import geo
from .config import user_agent

API = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
META_KEY = "forecast/_meta.json"
SUMMARY_KEY = "forecast/summary.json"
BRIEF_HOURS = 12
HOURLY_HOURS = 48
REQUEST_GAP_S = 0.2  # well under MET's 20 req/s ceiling

LABEL = "Forecast from a weather model, not a measurement."
ATTRIBUTION = ("Weather forecast: MET Norway (Norwegian Meteorological Institute), "
               "CC BY 4.0, https://api.met.no")


class MetError(Exception):
    pass


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(session: requests.Session, town: dict, last_modified: str | None):
    """Return (status, payload, headers). status is 200 or 304."""
    params = {"lat": f"{town['point']['lat']:.4f}", "lon": f"{town['point']['lon']:.4f}"}
    headers = {"If-Modified-Since": last_modified} if last_modified else {}
    try:
        resp = session.get(API, params=params, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise MetError(f"network error ({type(e).__name__})") from None
    if resp.status_code == 304:
        return 304, None, resp.headers
    if resp.status_code == 403:
        raise MetError("HTTP 403 — MET rejected the User-Agent")
    if resp.status_code == 429:
        raise MetError("HTTP 429 — rate limited")
    if resp.status_code != 200:
        raise MetError(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
        data["properties"]["timeseries"]
    except (ValueError, KeyError, TypeError):
        raise MetError("unexpected response body") from None
    return 200, data, resp.headers


def _hourly(timeseries: list[dict], now: datetime) -> list[dict]:
    end = now + timedelta(hours=HOURLY_HOURS)
    rows = []
    for step in timeseries:
        t = datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        nxt = step["data"].get("next_1_hours")
        if t > end or not nxt:
            continue
        d = step["data"]["instant"]["details"]
        rows.append({
            "t": step["time"],
            "temp": d.get("air_temperature"),
            "rh": d.get("relative_humidity"),
            "wind": d.get("wind_speed"),
            "wind_dir": d.get("wind_from_direction"),
            "cloud": d.get("cloud_area_fraction"),
            "rain": nxt.get("details", {}).get("precipitation_amount"),
            "symbol": nxt.get("summary", {}).get("symbol_code"),
        })
    return rows


def _rain_periods(timeseries: list[dict]):
    """Yield (start, hours, mm) covering the forecast once: hourly periods
    while MET provides them, then 6-hourly. Never double-counts."""
    covered_until = None
    for step in timeseries:
        t = datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        if covered_until and t < covered_until:
            continue
        data = step["data"]
        for key, hours in (("next_1_hours", 1), ("next_6_hours", 6)):
            block = data.get(key)
            mm = (block or {}).get("details", {}).get("precipitation_amount")
            if mm is not None:
                yield t, hours, mm
                covered_until = t + timedelta(hours=hours)
                break


def _daily(timeseries: list[dict], tz: ZoneInfo) -> list[dict]:
    days: dict[str, dict] = {}

    def day(d):
        return days.setdefault(d.isoformat(), {
            "temps": [], "winds": [], "rain": 0.0, "hours": 0.0,
            "hourly_steps": 0, "symbol": None, "symbol_gap": None,
        })

    for step in timeseries:
        t = datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        local = t.astimezone(tz)
        d = day(local.date())
        inst = step["data"]["instant"]["details"]
        if inst.get("air_temperature") is not None:
            d["temps"].append(inst["air_temperature"])
        if inst.get("wind_speed") is not None:
            d["winds"].append(inst["wind_speed"])
        if "next_1_hours" in step["data"]:
            d["hourly_steps"] += 1
        # Daytime symbol: the 12-hour summary starting closest to 08:00 local.
        twelve = step["data"].get("next_12_hours", {}).get("summary", {}).get("symbol_code")
        if twelve:
            gap = abs(local.hour + local.minute / 60 - 8)
            if d["symbol_gap"] is None or gap < d["symbol_gap"]:
                d["symbol"], d["symbol_gap"] = twelve, gap

    # Rain and coverage: a period that crosses local midnight is split
    # between the two days in proportion to the hours on each side.
    for start, hours, mm in _rain_periods(timeseries):
        rate = mm / hours
        cursor, end = start, start + timedelta(hours=hours)
        while cursor < end:
            local = cursor.astimezone(tz)
            next_midnight = (local + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            chunk_end = min(end, next_midnight)
            span_h = (chunk_end - cursor).total_seconds() / 3600
            d = day(local.date())
            d["rain"] += rate * span_h
            d["hours"] += span_h
            cursor = chunk_end

    out = []
    for date, d in sorted(days.items()):
        if not d["temps"] or d["hours"] == 0:
            continue
        out.append({
            "date": date,
            "tmin": min(d["temps"]),
            "tmax": max(d["temps"]),
            "rain": round(d["rain"], 1),
            "wind_max": max(d["winds"]) if d["winds"] else None,
            "symbol": d["symbol"],
            "hours": round(d["hours"], 1),
            "partial": d["hours"] < 23.5,
            # 6-hourly days sample temperature 4 times, so min/max are rough.
            "resolution": "hourly" if d["hourly_steps"] >= 20 else "6-hourly",
        })
    return out


def build_town(town: dict, data: dict, headers, now: datetime) -> dict:
    ts = data["properties"]["timeseries"]
    tz = ZoneInfo(town["timezone"])
    return {
        "town": town["slug"],
        "name": town["name"],
        "timezone": town["timezone"],
        "generated_utc": _iso(now),
        "model_updated_utc": data["properties"]["meta"].get("updated_at"),
        "label": LABEL,
        "attribution": ATTRIBUTION,
        "units": {"temp": "°C", "rh": "%", "wind": "m/s", "wind_dir": "degrees from",
                  "cloud": "%", "rain": "mm"},
        "hourly": _hourly(ts, now),
        "daily": _daily(ts, tz),
    }


def brief(doc: dict) -> dict:
    """The small slice of a town's forecast the map needs: the next 12 hours
    (temperature, rain, wind, symbol) and the first two local days. The first
    day is usually partial (only the hours left today); the site says so."""
    hours = [{k: h[k] for k in ("t", "temp", "rain", "wind", "wind_dir", "symbol")}
             for h in doc["hourly"][:BRIEF_HOURS]]
    days = [{k: d[k] for k in ("date", "tmin", "tmax", "rain", "symbol", "partial")}
            for d in doc["daily"][:2]]
    return {"model_updated_utc": doc["model_updated_utc"], "hourly": hours, "days": days}


def run(put_json, get_json, *, force: bool = False, log=print) -> dict:
    """Fetch and upload every town that is due. Returns a status dict."""
    now = datetime.now(timezone.utc)
    meta = (None if force else get_json(META_KEY)) or {"towns": {}}
    state = meta.setdefault("towns", {})

    session = requests.Session()
    session.headers["User-Agent"] = user_agent()
    result = {"updated": [], "not_modified": [], "not_due": [], "failed": {}}

    for town in geo.towns():
        slug = town["slug"]
        prev = state.get(slug, {})
        expires = _parse_http_date(prev.get("expires"))
        if not force and expires and now < expires:
            result["not_due"].append(slug)
            continue

        try:
            status, data, headers = fetch(session, town, prev.get("last_modified"))
            town_brief = prev.get("brief")
            if status == 200:
                doc = build_town(town, data, headers, now)
                put_json(town["forecast_file"], doc)
                town_brief = brief(doc)
                result["updated"].append(slug)
            else:
                result["not_modified"].append(slug)
        except MetError as e:
            result["failed"][slug] = str(e)
            time.sleep(REQUEST_GAP_S)
            continue

        state[slug] = {
            "last_modified": headers.get("Last-Modified") or prev.get("last_modified"),
            "expires": headers.get("Expires")
                       or format_datetime(now + timedelta(minutes=30), usegmt=True),
            "checked_utc": _iso(now),
            "brief": town_brief,
        }
        time.sleep(REQUEST_GAP_S)

    meta["generated_utc"] = _iso(now)
    put_json(META_KEY, meta)
    # One small file with every town's next hours, for the map and overview.
    put_json(SUMMARY_KEY, gzipped=True, obj={
        "generated_utc": _iso(now),
        "label": LABEL,
        "attribution": ATTRIBUTION,
        "towns": {slug: st["brief"] for slug, st in state.items() if st.get("brief")},
    })
    return result
