#!/usr/bin/env python3
"""Checks for borneosky/places.py (Explore) with made-up weather forecasts shaped like MET Norway's. No network, no R2:
    python scripts/test_places.py"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import met, places  # noqa: E402

places.time.sleep = lambda s: None
KCH = ZoneInfo("Asia/Kuching")      # UTC+8
PNK = ZoneInfo("Asia/Pontianak")    # UTC+7
NOW = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)      # 00:00 on 27 Sep in Kuching


def step(t: datetime, temp=27.0, wind=3.0, rain1=None, rain6=None, sym1=None, sym6=None, sym12=None):
    data = {"instant": {"details": {"air_temperature": temp, "wind_speed": wind}}}
    if rain1 is not None:
        data["next_1_hours"] = {"summary": {"symbol_code": sym1 or "cloudy"}, "details": {"precipitation_amount": rain1}}
    if rain6 is not None:
        data["next_6_hours"] = {"summary": {"symbol_code": sym6 or "cloudy"}, "details": {"precipitation_amount": rain6}}
    if sym12:
        data["next_12_hours"] = {"summary": {"symbol_code": sym12}}
    return {"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data}


def forecast(hourly_rain=lambda loc: 0.0, six_rain=lambda loc: 0.0, hourly_hours=60, total_days=9, start=NOW, tz=KCH,
             thunder_at=None):
    """Hourly steps for `hourly_hours`, then 6-hourly at 00/06/12/18 UTC, as MET does. Rain comes from the functions,
    given the local start time of each period."""
    ts, t = [], start
    end_hourly = start + timedelta(hours=hourly_hours)
    while t < end_hourly:
        loc = t.astimezone(tz)
        sym = "heavyrainshowersandthunder_day" if thunder_at and loc.date() == thunder_at[0] and loc.hour == thunder_at[1] else None
        ts.append(step(t, temp=24 + (loc.hour % 12) / 2, rain1=hourly_rain(loc), sym1=sym,
                       sym12="partlycloudy_day" if loc.hour == 8 else None))
        t += timedelta(hours=1)
    while t.hour % 6:
        t += timedelta(hours=1)
    while t < start + timedelta(days=total_days):
        loc = t.astimezone(tz)
        ts.append(step(t, temp=26, rain6=six_rain(loc), sym12="cloudy"))
        t += timedelta(hours=6)
    return ts


def check_place_list_is_valid():
    assert places.check_list() == [], places.check_list()
    assert len(places.places()) >= 20


def check_place_list_catches_mistakes():
    good = places.places()[0]
    bad = [
        {**good, "slug": "x1", "kind": "beach"},
        {**good, "slug": "x2", "region": "brunei"},
        {**good, "slug": "x3", "airport": "LDU"},
        {**good, "slug": "x4", "town": "nowhere"},
        {**good, "slug": "x5", "timezone": "Asia/Pontianak"},
        {**good, "slug": "x6", "point": {"lat": 1.55, "lon": 110.36}},        # Kuching: far from Ranau
        {**good, "slug": "x7", "official": {"name": "x", "url": "http://example.com"}},
        good, good,
    ]
    problems = " | ".join(places.check_list(bad))
    for want in ("x1: kind", "x2: region", "x3: airport", "x4: town", "x5: time zone", "x6:", "x7: official",
                 "slug used twice"):
        assert want in problems, (want, problems)


def check_switch():
    os.environ.pop("EXPLORE_PLACES", None)
    assert not places.enabled()
    for v, want in (("on", True), ("ON ", True), ("off", False), ("", False), ("yes", True)):
        os.environ["EXPLORE_PLACES"] = v
        assert places.enabled() is want, v
    os.environ.pop("EXPLORE_PLACES")


def check_sun_times_match_met_norway():
    # Reference values from MET Norway's sunrise service (api.met.no sunrise/3.0), read 26 Sep 2026.
    cases = [((1.717, 110.466, date(2026, 9, 27), KCH), ("06:26", "18:32")),
             ((6.005, 116.542, date(2026, 12, 21), KCH), ("06:18", "18:05")),
             ((0.001, 109.322, date(2027, 6, 21), PNK), ("05:40", "17:48")),
             ((-3.293, 114.661, date(2027, 3, 21), ZoneInfo("Asia/Makassar")), ("06:25", "18:31"))]
    mins = lambda s: int(s[:2]) * 60 + int(s[3:])
    for args, want in cases:
        got = places.sun_times(*args)
        for g, w in zip(got, want):
            assert abs(mins(g) - mins(w)) <= 2, (args, got, want)


def check_windows_split_rain_by_local_hour():
    # 1 mm every hour from 06:00 to 21:59 local on the first two days: 6 mm morning, 6 mm afternoon.
    ts = forecast(hourly_rain=lambda loc: 1.0 if 6 <= loc.hour < 22 else 0.0)
    rows = places.day_rows(ts, KCH, date(2026, 9, 27))
    first = rows[0]
    assert first["date"] == "2026-09-27"
    assert (first["rain_am"], first["rain_pm"], first["am_hours"], first["pm_hours"]) == (6.0, 6.0, 6.0, 6.0), first


def check_six_hour_periods_align_in_utc_plus_8():
    # Kuching: 6-hourly periods start 08:00, 14:00, 20:00, 02:00 local, so each window is one whole period.
    ts = forecast(hourly_hours=0, six_rain=lambda loc: {8: 3.0, 14: 5.0}.get(loc.hour, 0.0))
    rows = places.day_rows(ts, KCH, date(2026, 9, 27))
    r = rows[1]
    assert (r["rain_am"], r["rain_pm"]) == (3.0, 5.0), r


def check_six_hour_periods_are_shared_in_utc_plus_7():
    # Pontianak: periods start 07:00 and 13:00 local. Morning (08-14) gets 5/6 of 07-13 and 1/6 of 13-19.
    start = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)
    ts = forecast(hourly_hours=0, start=start, tz=PNK, six_rain=lambda loc: {7: 6.0, 13: 12.0}.get(loc.hour, 0.0))
    rows = places.day_rows(ts, PNK, date(2026, 9, 27))
    r = rows[1]
    assert (r["rain_am"], r["rain_pm"]) == (7.0, 10.0), r      # 5 + 2, then 10 + 0 (19-20 is in the next period: 0)
    assert r["am_hours"] == 6.0 and r["pm_hours"] == 6.0


def check_thunder_flag_only_in_daytime():
    ts = forecast(thunder_at=(date(2026, 9, 27), 15))
    rows = places.day_rows(ts, KCH, date(2026, 9, 27))
    assert rows[0]["thunder"] is True and rows[1]["thunder"] is False
    ts = forecast(thunder_at=(date(2026, 9, 27), 23))
    assert places.day_rows(ts, KCH, date(2026, 9, 27))[0]["thunder"] is False


def check_best_day_is_least_daytime_rain_after_today():
    today = date(2026, 9, 27)
    rows = [{"date": (today + timedelta(days=i)).isoformat(), "rain_am": ra, "rain_pm": rp, "am_hours": 6.0,
             "pm_hours": 6.0, "thunder": th}
            for i, (ra, rp, th) in enumerate([(0, 0, False), (5, 5, False), (1, 0.5, True), (0.5, 1, False),
                                              (3, 0, False), (9, 9, False), (2, 2, False), (0, 0, False)])]
    # Today (0 mm) is left out; the seventh day after it (0 mm) wins.
    assert places.best_day(rows, today)["date"] == "2026-10-04"
    # Without it, days 2 and 3 tie on 1.5 mm and the one without thunder wins.
    assert places.best_day(rows[:7], today) == {"date": "2026-09-30", "rain": 1.5, "thunder": False}
    # A day the forecast only partly covers does not take part.
    rows[3] = {**rows[3], "pm_hours": 3.0}
    assert places.best_day(rows[:7], today)["date"] == "2026-09-29"
    assert places.best_day(rows[:1], today) is None


def check_smoke_daily_max_at_a_point():
    # A 2 x 2 grid, eight 3-hour steps on one local day (from 00:00 local = 16:00 UTC), rising through the day.
    t0 = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)
    grid = {"lat": [2.0, 1.0], "lon": [110.0, 111.0],
            "times": [(t0 + timedelta(hours=3 * i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(10)],
            "pm25": [[10 * i, 10 * i, 10 * i, 10 * i] for i in range(10)]}
    got = places.smoke_daily(grid, 2.0, 110.0, KCH)
    assert got == {"2026-09-27": 70}, got     # 2 steps on the 28th are too few to count
    assert places.smoke_daily(None, 1, 1, KCH) == {}


class FakeSession:
    def __init__(self, status=200):
        self.status, self.calls = status, 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        sess = self

        class R:
            status_code = sess.status
            headers = {"Expires": "Sat, 26 Sep 2026 17:00:00 GMT", "Last-Modified": "Sat, 26 Sep 2026 15:30:00 GMT"}

            def json(self):
                return {"properties": {"meta": {"updated_at": "2026-09-26T15:00:00Z"},
                                       "timeseries": forecast(six_rain=lambda loc: 1.0)}}
        return R()


def check_run_writes_index_and_respects_expires():
    N = len(places.places())
    store = {}
    put = lambda key, obj: store.__setitem__(key, obj)
    get = lambda key: store.get(key)
    s = FakeSession()
    res = places.run(put, get, s, now=NOW)
    assert s.calls == N and len(res["updated"]) == N and res["places"] == N
    idx = store[places.INDEX_KEY]
    kin = idx["places"]["kinabalu-park"]
    assert kin["days"][0]["date"] == "2026-09-27" and kin["days"][0]["sunrise"] and kin["best"]
    assert idx["attribution"] == met.ATTRIBUTION and "not a measurement" in idx["label"]
    # Before Expires nothing is fetched, but the file is still written.
    s2 = FakeSession()
    res = places.run(put, get, s2, now=NOW + timedelta(minutes=10))
    assert s2.calls == 0 and len(res["not_due"]) == N and res["places"] == N
    # "Not modified" keeps the saved days; a day later, yesterday drops out of the file.
    s3 = FakeSession(status=304)
    res = places.run(put, get, s3, now=NOW + timedelta(days=1, hours=2))
    assert len(res["not_modified"]) == N
    assert store[places.INDEX_KEY]["places"]["bako"]["days"][0]["date"] == "2026-09-28"


def check_failed_place_keeps_its_last_days():
    N = len(places.places())
    store = {}
    put = lambda key, obj: store.__setitem__(key, obj)
    get = lambda key: store.get(key)
    places.run(put, get, FakeSession(), now=NOW)
    res = places.run(put, get, FakeSession(status=500), now=NOW + timedelta(hours=2))
    assert len(res["failed"]) == N and store[places.INDEX_KEY]["places"]["bako"]["days"]


CHECKS = [v for k, v in dict(globals()).items() if k.startswith("check_")]

if __name__ == "__main__":
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok    {fn.__name__[6:]}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__[6:]}: {e}")
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    sys.exit(1 if failed else 0)
