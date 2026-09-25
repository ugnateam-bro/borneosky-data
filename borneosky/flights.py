"""Flight boards → R2, from AeroDataBox (a paid plan; commercial use allowed).

One FIDS call per airport per run, for a 12 hour window (2 hours back, 10 ahead):

  flights/{IATA}.json     departures and arrivals for one airport
  flights/index.json      every airport with its freshness, counts and live share
  flights/_usage.json     calls and API units used this month (the plan has a quota)

The site shows a status such as "Delayed" or "Landed" only where the data is
live. A flight with just a timetable gets status "unknown", which the site writes
as "No live status", never a guess at "On time".

No calls are made between 00:01 and 04:00 local time (few flights, and it saves
units). The plan lets data be cached for 7 days; these files are overwritten on
every run, so nothing older is kept.

Not configured (no AERODATABOX_KEY): scripts/ingest_flights.py does nothing.
"""

import os
import re
import time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .config import user_agent

# AeroDataBox through API.market. A direct subscription has its own base URL and key
# header: set AERODATABOX_BASE_URL and AERODATABOX_KEY_HEADER to what its dashboard says.
DEFAULT_BASE_URL = "https://prod.api.market/api/v1/aedbx/aerodatabox"
DEFAULT_KEY_HEADER = "x-api-market-key"

USAGE_KEY = "flights/_usage.json"
INDEX_KEY = "flights/index.json"

WINDOW_BACK = timedelta(hours=2)
WINDOW_AHEAD = timedelta(hours=10)      # the API allows at most 12 hours in one call
RUN_EVERY = timedelta(hours=1)
QUIET_FROM, QUIET_TO = dtime(0, 1), dtime(4, 0)     # local time, no calls

UNITS_PER_CALL = 2                      # the FIDS endpoint is "tier 2"
DEFAULT_MONTHLY_UNITS = 40_000          # direct Starter plan
BUDGET_SHARE = 0.9                      # stop calling at 90% of the quota

# The initial airports. Kalimantan waits: live coverage there is much thinner.
AIRPORTS = [
    {"iata": "KCH", "icao": "WBGG", "name": "Kuching International Airport", "city": "Kuching", "country": "MY", "timezone": "Asia/Kuching"},
    {"iata": "MYY", "icao": "WBGR", "name": "Miri Airport", "city": "Miri", "country": "MY", "timezone": "Asia/Kuching"},
    {"iata": "SBW", "icao": "WBGS", "name": "Sibu Airport", "city": "Sibu", "country": "MY", "timezone": "Asia/Kuching"},
    {"iata": "BTU", "icao": "WBGB", "name": "Bintulu Airport", "city": "Bintulu", "country": "MY", "timezone": "Asia/Kuching"},
    {"iata": "BKI", "icao": "WBKK", "name": "Kota Kinabalu International Airport", "city": "Kota Kinabalu", "country": "MY", "timezone": "Asia/Kuching"},
    {"iata": "TWU", "icao": "WBKW", "name": "Tawau Airport", "city": "Tawau", "country": "MY", "timezone": "Asia/Kuching"},
    {"iata": "SDK", "icao": "WBKS", "name": "Sandakan Airport", "city": "Sandakan", "country": "MY", "timezone": "Asia/Kuching"},
    {"iata": "LBU", "icao": "WBKL", "name": "Labuan Airport", "city": "Labuan", "country": "MY", "timezone": "Asia/Kuching"},
    {"iata": "BWN", "icao": "WBSB", "name": "Brunei International Airport", "city": "Bandar Seri Begawan", "country": "BN", "timezone": "Asia/Brunei"},
]

# AeroDataBox FlightStatus → the words the site knows
STATUS = {
    "Unknown": "unknown", "Expected": "expected", "EnRoute": "enroute", "CheckIn": "checkin",
    "Boarding": "boarding", "GateClosed": "gateclosed", "Departed": "departed", "Delayed": "delayed",
    "Approaching": "approaching", "Arrived": "arrived", "Canceled": "canceled", "Diverted": "diverted",
    "CanceledUncertain": "canceled_uncertain",
}

LABEL = ("Timetables from airlines and airports, with live status only where the data shows it. "
         "Not an official source: confirm with your airline before you travel.")


class FlightsError(Exception):
    pass


class FlightsAuthError(FlightsError):
    """The key was refused: stop the whole run, every airport would fail the same way."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def settings() -> dict:
    return {
        "key": os.environ.get("AERODATABOX_KEY", "").strip(),
        "base": (os.environ.get("AERODATABOX_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/"),
        "header": os.environ.get("AERODATABOX_KEY_HEADER", "").strip() or DEFAULT_KEY_HEADER,
        "monthly_units": int(os.environ.get("AERODATABOX_MONTHLY_UNITS", "").strip() or DEFAULT_MONTHLY_UNITS),
    }


def make_session(cfg: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({cfg["header"]: cfg["key"], "Accept": "application/json", "User-Agent": user_agent()})
    return s


# ── Time ──────────────────────────────────────────────────────────────────

def in_quiet_hours(local: datetime) -> bool:
    t = local.time()
    return QUIET_FROM <= t < QUIET_TO


def next_update(now: datetime, tz: str) -> datetime:
    """When the next run that will fetch this airport is expected."""
    zone = ZoneInfo(tz)
    nxt = now + RUN_EVERY
    while in_quiet_hours(nxt.astimezone(zone)):
        nxt += RUN_EVERY
    return nxt


_DT = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})")


def utc_of(contract) -> str | None:
    """AeroDataBox's {utc, local} pair → '2026-09-25T06:25Z', or None."""
    if not isinstance(contract, dict):
        return None
    m = _DT.match(str(contract.get("utc") or ""))
    return f"{m.group(1)}T{m.group(2)}Z" if m else None


# ── Fetch ─────────────────────────────────────────────────────────────────

def fetch_airport(session: requests.Session, cfg: dict, airport: dict, now: datetime) -> dict:
    """The raw FIDS answer ({departures, arrivals}) for one airport."""
    zone = ZoneInfo(airport["timezone"])
    start = (now - WINDOW_BACK).astimezone(zone)
    end = (now + WINDOW_AHEAD).astimezone(zone)
    fmt = "%Y-%m-%dT%H:%M"
    url = f"{cfg['base']}/flights/airports/iata/{airport['iata']}/{start.strftime(fmt)}/{end.strftime(fmt)}"
    params = {"direction": "Both", "withLeg": "false", "withCancelled": "true", "withCodeshared": "false",
              "withCargo": "false", "withPrivate": "false", "withLocation": "false"}
    last = "no answer"
    for attempt in (1, 2):
        try:
            r = session.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            last = type(e).__name__
        else:
            if r.status_code in (401, 403):
                raise FlightsAuthError(f"AeroDataBox refused the key (HTTP {r.status_code}). Check AERODATABOX_KEY, "
                                       "AERODATABOX_BASE_URL and AERODATABOX_KEY_HEADER.")
            if r.status_code == 429:
                raise FlightsAuthError("AeroDataBox says the rate or quota limit is reached (HTTP 429).")
            if r.status_code == 204:
                return {"departures": [], "arrivals": []}
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    raise FlightsError("answer was not JSON") from None
                if not isinstance(data, dict):
                    raise FlightsError("unexpected answer shape")
                return data
            last = f"HTTP {r.status_code}"
            if r.status_code < 500:
                break                      # a 4xx will not get better by asking again
        if attempt == 1:
            time.sleep(2)
    raise FlightsError(last)


# ── Normalise ─────────────────────────────────────────────────────────────

def _clean(v) -> str | None:
    v = (str(v).strip() if v is not None else "")
    return v or None


def normalize(f: dict, direction: str) -> dict | None:
    """One flight of the raw answer → the record the site reads, or None to leave it out.
    `direction` is 'departure' or 'arrival' as seen from the airport asked about."""
    if not isinstance(f, dict) or f.get("isCargo"):
        return None
    mv = f.get("movement")
    if isinstance(mv, dict):
        peer = mv.get("airport") or {}       # 'movement' describes this airport; its airport is the far end
    else:                                    # a with-leg answer keeps each end on its own side
        mv = f.get("departure" if direction == "departure" else "arrival") or {}
        peer = (f.get("arrival" if direction == "departure" else "departure") or {}).get("airport") or {}
    scheduled = utc_of(mv.get("scheduledTime"))
    number = _clean(f.get("number"))
    if not scheduled or not number:
        return None                          # cannot be placed on the board

    live = "Live" in (mv.get("quality") or [])
    status = STATUS.get(str(f.get("status") or "Unknown"), "unknown") if live else "unknown"
    airline = f.get("airline") or {}
    return {
        "number": number,
        "airline": _clean(airline.get("name")),
        "airline_iata": _clean(airline.get("iata")),
        "other": {
            "iata": _clean(peer.get("iata")),
            "name": _clean(peer.get("shortName")) or _clean(peer.get("name")),
            "city": _clean(peer.get("municipalityName")),
            "country": _clean(peer.get("countryCode")),
        },
        "scheduled_utc": scheduled,
        "revised_utc": utc_of(mv.get("revisedTime")) if live else None,
        "runway_utc": utc_of(mv.get("runwayTime")) if live else None,
        "status": status,
        "live": live,
        "terminal": _clean(mv.get("terminal")),
        "gate": _clean(mv.get("gate")),
        "belt": _clean(mv.get("baggageBelt")) if direction == "arrival" else None,
        "desk": _clean(mv.get("checkInDesk")) if direction == "departure" else None,
        "codeshared": f.get("codeshareStatus") == "IsCodeshared",
    }


def build_board(airport: dict, raw: dict, now: datetime) -> dict:
    def rows(key: str, direction: str) -> list[dict]:
        out = [n for n in (normalize(f, direction) for f in (raw.get(key) or [])) if n]
        out.sort(key=lambda x: (x["scheduled_utc"], x["number"]))
        return out

    departures, arrivals = rows("departures", "departure"), rows("arrivals", "arrival")
    everything = departures + arrivals
    return {
        "airport": {k: airport[k] for k in ("iata", "icao", "name", "city", "country", "timezone")},
        "generated_utc": _iso(now),
        "window_utc": [_iso(now - WINDOW_BACK), _iso(now + WINDOW_AHEAD)],
        "next_update_utc": _iso(next_update(now, airport["timezone"])),
        "counts": {"departures": len(departures), "arrivals": len(arrivals),
                   "live": sum(1 for x in everything if x["live"])},
        "label": LABEL,
        "departures": departures,
        "arrivals": arrivals,
    }


def index_entry(board: dict) -> dict:
    a = board["airport"]
    return {**a, "file": f"flights/{a['iata']}.json", "generated_utc": board["generated_utc"],
            "next_update_utc": board["next_update_utc"], "counts": board["counts"]}


# ── Run ───────────────────────────────────────────────────────────────────

def _month(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m")


def run(put_json, get_json, *, session, cfg: dict, now: datetime | None = None, force: bool = False,
        airports: list[dict] | None = None) -> dict:
    """Refresh every airport that is due. Each airport is independent: one that fails
    keeps its last good file. Raises FlightsAuthError if the key is refused."""
    now = now or datetime.now(timezone.utc)
    airports = airports or AIRPORTS
    usage = get_json(USAGE_KEY) or {}
    if usage.get("month") != _month(now):
        usage = {"month": _month(now), "calls": 0, "units": 0}
    budget = int(cfg["monthly_units"] * BUDGET_SHARE)

    old_index = {a["iata"]: a for a in (get_json(INDEX_KEY) or {}).get("airports", [])}
    entries = dict(old_index)
    result = {"updated": [], "failed": {}, "quiet": [], "over_budget": False}

    try:
        for airport in airports:
            iata = airport["iata"]
            if not force and in_quiet_hours(now.astimezone(ZoneInfo(airport["timezone"]))):
                result["quiet"].append(iata)
                continue
            if usage["units"] + UNITS_PER_CALL > budget:
                result["over_budget"] = True
                break
            try:
                raw = fetch_airport(session, cfg, airport, now)
            except FlightsAuthError:
                raise
            except FlightsError as e:
                result["failed"][iata] = str(e)
                continue
            usage["calls"] += 1
            usage["units"] += UNITS_PER_CALL
            board = build_board(airport, raw, now)
            put_json(f"flights/{iata}.json", board)
            entries[iata] = index_entry(board)
            result["updated"].append(iata)
    finally:
        # keep the count honest even when the run is cut short
        if result["updated"] or result["failed"]:
            usage["updated_utc"] = _iso(now)
            put_json(USAGE_KEY, usage)
            order = {a["iata"]: i for i, a in enumerate(AIRPORTS)}
            put_json(INDEX_KEY, {"generated_utc": _iso(now),
                                 "airports": sorted(entries.values(), key=lambda a: order.get(a["iata"], 99))})
    result["usage"] = usage
    return result
