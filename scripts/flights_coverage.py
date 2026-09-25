#!/usr/bin/env python3
"""
Check what AeroDataBox really covers before the site relies on it.

For every airport: the feed status (the health endpoint costs no units), then one real
FIDS call (2 units) to count the flights in the current window and how many carry live
status. The whole default run costs about 18 units.

Usage:
  python scripts/flights_coverage.py                   all airports
  python scripts/flights_coverage.py --only KCH,BWN    just these
  python scripts/flights_coverage.py --no-fids         feed status only, no units used

Needs AERODATABOX_KEY (and, for a direct subscription, AERODATABOX_BASE_URL and
AERODATABOX_KEY_HEADER) in .env or the environment. Writes nothing to R2.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import flights  # noqa: E402


def feed_status(health: dict, name: str) -> str:
    f = health.get(name)
    return str((f or {}).get("status") or "-") if isinstance(f, dict) else "-"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-fids", action="store_true")
    args = ap.parse_args()

    cfg = flights.settings()
    if not cfg["key"]:
        sys.exit("AERODATABOX_KEY is not set (put it in ../.env or export it), so there is nothing to check yet.")
    wanted = {c.strip().upper() for c in args.only.split(",") if c.strip()}
    airports = [a for a in flights.AIRPORTS if not wanted or a["iata"] in wanted]
    if not airports:
        sys.exit(f"No airport matches --only {args.only!r}")

    session = flights.make_session(cfg)
    now = datetime.now(timezone.utc)
    units = 0
    print(f"{'airport':8}{'schedules':12}{'live updates':14}{'ADS-B':10}{'flights':>9}{'live':>7}   one example")
    for a in airports:
        health, note = {}, ""
        try:
            r = session.get(f"{cfg['base']}/health/services/airports/{a['icao']}/feeds", timeout=30)
            if r.status_code in (401, 403):
                sys.exit(f"The key was refused (HTTP {r.status_code}). Check AERODATABOX_KEY, AERODATABOX_BASE_URL and AERODATABOX_KEY_HEADER.")
            if r.status_code == 200:
                health = r.json()
            else:
                note = f"feed status: HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            note = f"feed status: {type(e).__name__}"

        total = live = "-"
        example = note
        if not args.no_fids:
            try:
                board = flights.build_board(a, flights.fetch_airport(session, cfg, a, now), now)
            except flights.FlightsAuthError as e:
                sys.exit(str(e))
            except flights.FlightsError as e:
                example = f"flights: {e}"
            else:
                units += flights.UNITS_PER_CALL
                c = board["counts"]
                total, live = c["departures"] + c["arrivals"], c["live"]
                rows = board["departures"] + board["arrivals"]
                pick = next((x for x in rows if x["live"]), rows[0] if rows else None)
                if pick:
                    example = (f"{pick['number']} {pick['other']['iata'] or '?'} {pick['scheduled_utc'][11:16]}Z "
                               f"{pick['status'] if pick['live'] else '(timetable only)'}")
                else:
                    example = "no flights in this window"
        print(f"{a['iata']:8}{feed_status(health, 'flightSchedulesFeed'):12}{feed_status(health, 'liveFlightUpdatesFeed'):14}"
              f"{feed_status(health, 'adsbUpdatesFeed'):10}{total!s:>9}{live!s:>7}   {example}")
    if units:
        print(f"\n{units} API units used by this check.")


if __name__ == "__main__":
    main()
