#!/usr/bin/env python3
"""Checks for borneosky/flights.py with made-up answers shaped like AeroDataBox's
FIDS contract. No network, no R2, no API units:  python scripts/test_flights.py"""

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import flights  # noqa: E402

flights.time.sleep = lambda s: None          # retries should not slow the checks

KUL = {"iata": "KUL", "icao": "WMKK", "name": "Kuala Lumpur International", "shortName": "Kuala Lumpur Intl",
       "municipalityName": "Kuala Lumpur", "countryCode": "MY", "timeZone": "Asia/Kuala_Lumpur"}
SIN = {"iata": "SIN", "name": "Singapore Changi", "municipalityName": "Singapore", "countryCode": "SG"}


def t(utc_hm: str, day="2026-09-25"):
    """{utc, local} pair, local = UTC+8, as the API writes them."""
    h, m = map(int, utc_hm.split(":"))
    return {"utc": f"{day} {utc_hm}Z", "local": f"{day} {(h + 8) % 24:02d}:{m:02d}+08:00"}


RAW = {
    "departures": [
        {   # live and delayed, with terminal, gate and desks
            "number": "MH 2601", "status": "Delayed", "codeshareStatus": "IsOperator", "isCargo": False,
            "airline": {"name": "Malaysia Airlines", "iata": "MH", "icao": "MAS"},
            "movement": {"airport": KUL, "scheduledTime": t("06:25"), "revisedTime": t("06:50"),
                         "runwayTime": t("07:02"), "terminal": "1", "gate": "3", "checkInDesk": "A-C",
                         "quality": ["Basic", "Live"]},
        },
        {   # timetable only: the API says Unknown, and a status must not be shown
            "number": "AK 5121", "status": "Unknown", "codeshareStatus": "IsOperator", "isCargo": False,
            "airline": {"name": "AirAsia", "iata": "AK"},
            "movement": {"airport": SIN, "scheduledTime": t("05:10"), "quality": ["Basic"]},
        },
        {   # a cancellation reported without live quality is not shown as a status
            "number": "FY 3103", "status": "Canceled", "isCargo": False, "airline": {"name": "Firefly", "iata": "FY"},
            "movement": {"airport": KUL, "scheduledTime": t("08:00"), "revisedTime": t("08:30"), "quality": ["Basic"]},
        },
        {"number": "5X 100", "status": "Expected", "isCargo": True,          # cargo: left out
         "movement": {"airport": KUL, "scheduledTime": t("07:00"), "quality": ["Live"]}},
        {"number": "XX 1", "status": "Expected", "isCargo": False,            # no scheduled time: left out
         "movement": {"airport": KUL, "quality": ["Live"]}},
        {"status": "Expected", "isCargo": False,                              # no flight number: left out
         "movement": {"airport": KUL, "scheduledTime": t("09:00"), "quality": ["Live"]}},
    ],
    "arrivals": [
        {   # landed, live, with a baggage belt
            "number": "OD 1332", "status": "Arrived", "codeshareStatus": "IsCodeshared", "isCargo": False,
            "airline": {"name": "Batik Air Malaysia", "iata": "OD"},
            "movement": {"airport": KUL, "scheduledTime": t("04:40"), "revisedTime": t("04:35"),
                         "baggageBelt": "2", "checkInDesk": "ignored", "quality": ["Live"]},
        },
    ],
}

AIRPORTS = [a for a in flights.AIRPORTS if a["iata"] in ("KCH", "BWN")]
NOON_UTC = datetime(2026, 9, 25, 6, 23, 40, tzinfo=timezone.utc)      # 14:23 in Kuching
CFG = {"key": "test-key", "base": "https://example.test/adb", "header": "x-test-key", "monthly_units": 40_000}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class Session:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.handler(url, params)


class Store:
    """A stand-in for R2: a dict with the two functions the run needs."""
    def __init__(self, initial=None):
        self.d = copy.deepcopy(initial or {})

    def put(self, key, obj):
        self.d[key] = copy.deepcopy(obj)

    def get(self, key):
        return copy.deepcopy(self.d.get(key))


def run(handler, store=None, now=NOON_UTC, **kw):
    store = store or Store()
    sess = Session(handler)
    res = flights.run(store.put, store.get, session=sess, cfg=kw.pop("cfg", CFG), now=now, airports=kw.pop("airports", AIRPORTS), **kw)
    return res, store, sess


# ── tests ─────────────────────────────────────────────────────────────────

def test_utc_of():
    assert flights.utc_of({"utc": "2026-09-25 06:25Z", "local": "x"}) == "2026-09-25T06:25Z"
    assert flights.utc_of({"utc": "2026-09-25T06:25:00Z"}) == "2026-09-25T06:25Z"
    assert flights.utc_of({}) is None and flights.utc_of(None) is None and flights.utc_of({"utc": "soon"}) is None


def test_live_departure():
    d = flights.normalize(RAW["departures"][0], "departure")
    assert d["number"] == "MH 2601" and d["airline"] == "Malaysia Airlines" and d["airline_iata"] == "MH"
    assert d["other"] == {"iata": "KUL", "name": "Kuala Lumpur Intl", "city": "Kuala Lumpur", "country": "MY"}
    assert d["scheduled_utc"] == "2026-09-25T06:25Z" and d["revised_utc"] == "2026-09-25T06:50Z"
    assert d["runway_utc"] == "2026-09-25T07:02Z"
    assert d["status"] == "delayed" and d["live"] is True
    assert (d["terminal"], d["gate"], d["desk"], d["belt"]) == ("1", "3", "A-C", None)
    assert d["codeshared"] is False


def test_timetable_only_gets_no_status():
    d = flights.normalize(RAW["departures"][1], "departure")
    assert d["status"] == "unknown" and d["live"] is False and d["revised_utc"] is None
    c = flights.normalize(RAW["departures"][2], "departure")     # 'Canceled' without live quality
    assert c["status"] == "unknown" and c["revised_utc"] is None


def test_left_out():
    assert flights.normalize(RAW["departures"][3], "departure") is None      # cargo
    assert flights.normalize(RAW["departures"][4], "departure") is None      # no scheduled time
    assert flights.normalize(RAW["departures"][5], "departure") is None      # no number


def test_arrival_fields():
    a = flights.normalize(RAW["arrivals"][0], "arrival")
    assert a["status"] == "arrived" and a["belt"] == "2" and a["desk"] is None and a["codeshared"] is True


def test_with_leg_shape():
    f = {"number": "MH 1", "status": "Expected", "isCargo": False,
         "departure": {"scheduledTime": t("06:25"), "quality": ["Live"]},
         "arrival": {"airport": KUL, "scheduledTime": t("08:00"), "quality": ["Live"]}}
    d = flights.normalize(f, "departure")
    assert d["scheduled_utc"] == "2026-09-25T06:25Z" and d["other"]["iata"] == "KUL" and d["status"] == "expected"


def test_board():
    b = flights.build_board(AIRPORTS[0], RAW, NOON_UTC)
    assert [x["number"] for x in b["departures"]] == ["AK 5121", "MH 2601", "FY 3103"]       # by scheduled time
    assert b["counts"] == {"departures": 3, "arrivals": 1, "live": 2}
    assert b["generated_utc"] == "2026-09-25T06:23:40Z"
    assert b["window_utc"] == ["2026-09-25T04:23:40Z", "2026-09-25T16:23:40Z"]              # 2 h back, 10 h ahead
    assert b["next_update_utc"] == "2026-09-25T07:23:40Z"
    assert b["airport"]["iata"] == "KCH" and b["airport"]["timezone"] == "Asia/Kuching"


def test_quiet_hours():
    from zoneinfo import ZoneInfo
    z = ZoneInfo("Asia/Kuching")
    q = lambda h, m: flights.in_quiet_hours(datetime(2026, 9, 25, h, m, tzinfo=z))
    assert not q(0, 0) and q(0, 1) and q(2, 30) and q(3, 59) and not q(4, 0) and not q(23, 59)


def test_next_update_skips_the_quiet_night():
    late = datetime(2026, 9, 25, 15, 23, tzinfo=timezone.utc)                 # 23:23 in Kuching
    assert flights.next_update(late, "Asia/Kuching") == datetime(2026, 9, 25, 20, 23, tzinfo=timezone.utc)   # 04:23


def test_run_writes_boards_index_and_usage():
    def ok(url, params):
        return Resp(200, RAW)
    res, store, sess = run(ok)
    assert res["updated"] == ["KCH", "BWN"] and not res["failed"] and not res["quiet"] and not res["over_budget"]
    assert {"flights/KCH.json", "flights/BWN.json", "flights/index.json", "flights/_usage.json"} <= set(store.d)
    idx = store.d["flights/index.json"]["airports"]
    assert [a["iata"] for a in idx] == ["KCH", "BWN"] and idx[0]["counts"]["live"] == 2
    assert store.d["flights/_usage.json"] == {"month": "2026-09", "calls": 2, "units": 4, "updated_utc": "2026-09-25T06:23:40Z"}
    url, params = sess.calls[0]
    # 14:23 in Kuching: 2 hours back to 12:23, 10 ahead to 00:23 the next day (exactly the API's 12 hour limit)
    assert url == "https://example.test/adb/flights/airports/iata/KCH/2026-09-25T12:23/2026-09-26T00:23"
    assert params["direction"] == "Both" and params["withCargo"] == "false" and params["withCodeshared"] == "false"
    assert "test-key" not in repr(store.d)                                     # the key never lands in a file


def test_one_failed_airport_keeps_its_last_file():
    old = flights.build_board(AIRPORTS[0], {"departures": [], "arrivals": []}, datetime(2026, 9, 25, 5, 23, tzinfo=timezone.utc))
    store = Store({"flights/KCH.json": old, "flights/index.json": {"airports": [flights.index_entry(old)]}})

    def handler(url, params):
        return Resp(503) if "/KCH/" in url else Resp(200, RAW)
    res, store, _ = run(handler, store)
    assert res["failed"] == {"KCH": "HTTP 503"} and res["updated"] == ["BWN"]
    assert store.d["flights/KCH.json"] == old                                  # untouched
    idx = {a["iata"]: a for a in store.d["flights/index.json"]["airports"]}
    assert idx["KCH"]["generated_utc"] == "2026-09-25T05:23:00Z" and idx["BWN"]["generated_utc"] == "2026-09-25T06:23:40Z"
    assert store.d["flights/_usage.json"]["calls"] == 1                        # the failed call is not counted


def test_refused_key_stops_the_run():
    try:
        run(lambda url, params: Resp(401))
    except flights.FlightsAuthError as e:
        assert "refused the key" in str(e) and "test-key" not in str(e)
    else:
        raise AssertionError("expected FlightsAuthError")
    try:
        run(lambda url, params: Resp(429))
    except flights.FlightsAuthError as e:
        assert "429" in str(e)
    else:
        raise AssertionError("expected FlightsAuthError")


def test_empty_answer_is_an_empty_board():
    res, store, _ = run(lambda url, params: Resp(204))
    assert res["updated"] == ["KCH", "BWN"]
    assert store.d["flights/KCH.json"]["counts"] == {"departures": 0, "arrivals": 0, "live": 0}


def test_budget_stops_calls():
    store = Store({"flights/_usage.json": {"month": "2026-09", "calls": 17999, "units": 35_999}})
    calls = []
    res, store, _ = run(lambda url, params: calls.append(url) or Resp(200, RAW), store)   # 90% of 40,000 is 36,000
    assert res["over_budget"] and not calls and not res["updated"]


def test_new_month_resets_usage():
    store = Store({"flights/_usage.json": {"month": "2026-08", "calls": 9000, "units": 18_000}})
    res, store, _ = run(lambda url, params: Resp(200, RAW), store)
    assert store.d["flights/_usage.json"]["units"] == 4 and store.d["flights/_usage.json"]["month"] == "2026-09"


def test_quiet_hours_skip_unless_forced():
    night = datetime(2026, 9, 24, 18, 23, tzinfo=timezone.utc)                # 02:23 in Kuching and Brunei
    calls = []
    res, store, _ = run(lambda url, params: calls.append(url) or Resp(200, RAW), now=night)
    assert res["quiet"] == ["KCH", "BWN"] and not calls and not store.d
    res, store, _ = run(lambda url, params: Resp(200, RAW), now=night, force=True)
    assert res["updated"] == ["KCH", "BWN"]


def test_bad_answers_fail_that_airport_only():
    res, _, _ = run(lambda url, params: Resp(200, None))                        # 200 but not JSON
    assert set(res["failed"]) == {"KCH", "BWN"} and not res["updated"]


def test_settings_defaults_and_overrides():
    import os
    for k in ("AERODATABOX_KEY", "AERODATABOX_BASE_URL", "AERODATABOX_KEY_HEADER", "AERODATABOX_MONTHLY_UNITS"):
        os.environ.pop(k, None)
    s = flights.settings()
    assert s["base"] == flights.DEFAULT_BASE_URL and s["header"] == "x-api-market-key" and s["monthly_units"] == 40_000
    os.environ.update(AERODATABOX_BASE_URL="https://direct.example/api/", AERODATABOX_KEY_HEADER="x-api-key",
                      AERODATABOX_MONTHLY_UNITS="400000")
    s = flights.settings()
    assert s["base"] == "https://direct.example/api" and s["header"] == "x-api-key" and s["monthly_units"] == 400_000
    for k in ("AERODATABOX_BASE_URL", "AERODATABOX_KEY_HEADER", "AERODATABOX_MONTHLY_UNITS"):
        os.environ.pop(k)


if __name__ == "__main__":
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    for name in names:
        globals()[name]()
        print(f"  ok  {name}")
    print(f"{len(names)} checks passed")
