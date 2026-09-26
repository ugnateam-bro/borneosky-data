#!/usr/bin/env python3
"""Checks for borneosky/sesb.py with made-up answers shaped like SESB's outage files. No network, no R2:
    python scripts/test_sesb.py"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import sesb  # noqa: E402

sesb.time.sleep = lambda s: None          # retries should not slow the checks

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)      # 23:00 in Malaysia
KEEP = "MELAKUKAN KERJA-KERJA SENGGARAAN PENCAWANG ELEKTRIK"

# Every title SESB has used (25 Sep 2026, both files) and what we make of it: (kind, critical).
TITLES = {
    "PROJEK PENGUKUHAN SISTEM (SPECIAL/CRITICAL PROJECT) - MV": ("system_strengthening", False),
    "PROJEK PENGUKUHAN SISTEM (SPECIAL/CRITICAL PROJECT) - LV": ("system_strengthening", False),
    "MELAKUKAN KERJA-KERJA MENAIKTARAF SISTEM BEKALAN ELEKTRIK VOLTAN TINGGI": ("hv_upgrade", False),
    "MELAKUKAN KERJA-KERJA MENAIKTARAF SISTEM BEKALAN ELEKTRIK VOLTAN RENDAH": ("lv_upgrade", False),
    "KERJA-KERJA PEMBAIKAN KEROSAKAN / RECTIFICATION WORKS": ("repair", False),
    "KERJA-KERJA PEMBAIKAN KEROSAKAN / RECTIFICATION WORKS (KHAS / KRITIKAL)": ("repair", True),
    "KERJA-KERJA PEMBAIKAN KEROSAKAN (KRITIKAL)": ("repair", True),
    "KERJA-KERJA PEMBAIKAN OUTSTANDING FAULT (KRITIKAL)": ("repair", True),
    "KERJA-KERJA PEMBAIKAN OUTSTANDING FAULT (KHAS/KRITIKAL)": ("repair", True),
    "MELAKUKAN KERJA-KERJA SENGGARAAN SISTEM TALIAN ATAS VOLTAN TINGGI": ("hv_line_maintenance", False),
    "MELAKUKAN KERJA-KERJA SENGGARAAN SISTEM TALIAN ATAS VOLTAN RENDAH": ("lv_line_maintenance", False),
    "MELAKUKAN KERJA-KERJA PENYAMBUNGAN BEKALAN ELEKTRIK BARU": ("new_connection", False),
    "MELAKUKAN KERJA-KERJA PENYAMBUNGAN BEKALAN ELEKTRIK BARU (CBE)": ("new_connection", False),
    "MELAKUKAN KERJA-KERJA PENYAMBUNGAN BEKALAN ELEKTRIK BARU (CBE) KHAS/KRITIKAL": ("new_connection", True),
    "PENYAMBUNGAN NEW SERVICE CONNECTION - KATEGORI 1,2": ("new_connection", False),
    "PENYAMBUNGAN NEW SERVICE CONNECTION - KATEGORI 3,4, 5": ("new_connection", False),
    KEEP: ("substation_maintenance", False),
    "MELAKUKAN KERJA-KERJA RENTIS": ("line_clearing", False),
    "MELAKUKAN KERJA-KERJA RENTIS KRITIKAL": ("line_clearing", True),
    "MELAKUKAN KERJA-KERJA RENTIS KRITIKAL (ADUAN/KECEMASAN)": ("line_clearing", True),
    "MELAKUKAN KERJA-KERJA PENGALIHAN PEPASANGAN VOLTAN TINGGI": ("relocation", False),
    "MELAKUKAN KERJA-KERJA PENGALIHAN PEPASANGAN VOLTAN TINGGI (KHAS/KRITIKAL)": ("relocation", True),
    "MELAKUKAN KERJA-KERJA PENGALIHAN PEPASANGAN VOLTAN RENDAH": ("relocation", False),
    "MELAKUKAN KERJA-KERJA PENGALIHAN PEPASANGAN VOLTAN RENDAH (KHAS/KRITIKAL)": ("relocation", True),
    "MELAKUKAN KERJA-KERJA SENGGARAAN PENGHANTARAN": ("transmission_maintenance", False),
    "MELAKUKAN KERJA-KERJA SENGGARAAN KABEL BAWAH TANAH VOLTAN TINGGI": ("cable_maintenance", False),
    "MELAKUKAN KERJA-KERJA SENGGARAAN KABEL BAWAH TANAH VOLTAN RENDAH": ("cable_maintenance", False),
    "MELAKUKAN KERJA-KERJA SENGGARAAN KABEL BAWAH TANAH VOLTAN TINGGI (KECEMASAN)": ("cable_maintenance", True),
    "MELAKUKAN KERJA-KERJA SENGGARAAN LAMPU JALAN KAMPUNG (LJK)": ("streetlight_maintenance", False),
    "UFLS SETTING": ("protection_setting", False),
}


def row(area="Tawau", start="2026-09-26T09:30:00", end="2026-09-26T17:30:00", title=KEEP,
        places="TAMAN A, TAMAN B DAN SEKITAR", status=False, created="2026-09-23T16:00:04.317", **extra):
    """A row as SESB writes it (the fields we do not use are left out)."""
    return {"Outageid": 1, "Area": area, "AreaID": 7, "Circuit": None, "Latitude": 0.0, "Longitude": 0.0,
            "RunningNo": "2026001858", "StartDateTime": start, "EndDateTime": end, "AffectedArea": places,
            "Duration": 8, "ShutdownPurpose": title, "OutageType": True, "CreatedDate": created,
            "Status": status, **extra}


def table(*rows):
    return json.dumps({"Table": list(rows)}).encode()


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self.content, self.headers = status, body, headers or {}


class Session:
    """Answers by file name. An answer may be a Resp, an exception to raise, or a function of the request headers."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, headers=None, timeout=None):
        name = url.rsplit("/", 1)[-1]
        self.calls.append((name, dict(headers or {})))
        a = self.answers[name]
        a = a(headers or {}) if callable(a) else a
        if isinstance(a, Exception):
            raise a
        return a


PLANNED, SHORT = "PlannedOutage.json", "UnplannedOutage.json"
ETAG_P, ETAG_S = '"aaa:0"', '"bbb:0"'
LM = "Thu, 24 Sep 2026 16:00:06 GMT"


def ok(body, etag, lm=LM):
    return Resp(200, body, {"ETag": etag, "Last-Modified": lm})


class Store:
    def __init__(self, start=None):
        self.objs = {} if start is None else {sesb.OUT_KEY: start}
        self.puts = 0

    def put(self, key, obj):
        self.objs[key] = json.loads(json.dumps(obj))
        self.puts += 1

    def get(self, key):
        return self.objs.get(key)


def go(session, store=None, force=False, now=NOW):
    store = store or Store()
    return sesb.run(store.put, store.get, session, now=now, force=force), store


def first_run_session(planned=None, short=None):
    return Session({PLANNED: ok(table(*(planned if planned is not None else [row()])), ETAG_P),
                    SHORT: ok(table(*(short if short is not None else [row(area="Beaufort", title="UFLS SETTING")])), ETAG_S)})


# ── One notice ────────────────────────────────────────────────────────────

def test_every_known_title_has_a_kind():
    for title, (kind, critical) in TITLES.items():
        assert sesb.classify(title) == (kind, critical), title
    assert len(TITLES) == 30
    assert {k for k, _ in TITLES.values()} == set(sesb.KIND_CODES) - {"other"}, "every kind is used by a real title"
    assert sesb.classify("SOMETHING NEW THAT SESB INVENTS") == ("other", False)
    assert sesb.classify("SOMETHING NEW (KECEMASAN)") == ("other", True)
    assert sesb.classify("   melakukan kerja-kerja   senggaraan   pencawang elektrik ") == ("substation_maintenance", False), "case and spacing do not matter"


def test_the_words_that_decide_are_matched_not_the_whole_title():
    assert sesb.classify("KERJA-KERJA PEMBAIKAN KEROSAKAN (KRITIKAL) TAMBAHAN") == ("repair", True)
    assert sesb.classify("SPECIAL/CRITICAL PROJECT") == ("other", False), "'critical' in English is a project type, not urgency"


def test_times_are_malaysia_time_and_publication_is_utc():
    n = sesb.make_notice(row(), "planned")
    assert n["start"] == "2026-09-26T09:30:00+08:00" and n["end"] == "2026-09-26T17:30:00+08:00"
    assert n["published"] == "2026-09-23T16:00:04Z"
    assert sesb.make_notice(row(created="not a date"), "planned")["published"] is None


def test_area_names():
    assert sesb.normalise_area("W.P.LABUAN") == "Labuan" and sesb.normalise_area("W.P. LABUAN") == "Labuan"
    assert sesb.normalise_area("WP LABUAN") == "Labuan" and sesb.normalise_area("Kota Kinabalu") == "Kota Kinabalu"
    assert sesb.normalise_area("TUARAN") == "Tuaran" and sesb.normalise_area("WESTON") == "Weston", "only the W.P. prefix is dropped"
    assert sesb.make_notice(row(area="W.P.LABUAN"), "planned")["area"] == "Labuan"


def test_places_text():
    assert sesb.places_text("NIL") == ("", False) and sesb.places_text("  ") == ("", False) and sesb.places_text(None) == ("", False)
    assert sesb.places_text("KG A,\r\n\r\n KG B\tDAN  SEKITAR ") == ("KG A, KG B DAN SEKITAR", False)
    long = ", ".join(f"KAMPUNG NOMBOR {i}" for i in range(400))
    text, cut = sesb.places_text(long)
    assert cut and len(text) <= sesb.MAX_PLACES and not text.endswith(",") and text.startswith("KAMPUNG NOMBOR 0,")
    assert long.startswith(text), "the cut keeps SESB's own words, nothing added"
    n = sesb.make_notice(row(places=long), "planned")
    assert n["places_cut"] is True and "places_cut" not in sesb.make_notice(row(), "planned")


def test_rows_that_cannot_be_used():
    for bad in (None, "text", 5, row(area=""), row(start="soon"), row(end=None), row(title=" "),
                row(start="2026-09-26T17:30:00", end="2026-09-26T09:30:00"), row(start="2026-09-26T09:30:00", end="2026-09-26T09:30:00"),
                row(start="2026-13-40T09:30:00")):
        assert sesb.make_notice(bad, "planned") is None, bad


def test_the_notice_carries_only_what_the_site_uses():
    n = sesb.make_notice(row(title="KERJA-KERJA PEMBAIKAN KEROSAKAN (KRITIKAL)", status=True), "short_notice")
    assert set(n) == {"id", "type", "area", "start", "end", "kind", "title", "places", "done", "published", "duration", "critical"}, sorted(n)
    assert n["type"] == "short_notice" and n["done"] is True and n["critical"] is True and n["kind"] == "repair"
    assert n["title"] == "KERJA-KERJA PEMBAIKAN KEROSAKAN (KRITIKAL)", "SESB's own title is kept"
    assert set(sesb.make_notice(row(), "planned")) == {"id", "type", "area", "start", "end", "kind", "title", "places", "done", "published", "duration"}
    assert sesb.make_notice(row(), "planned")["id"] == sesb.make_notice(row(RunningNo="other", Outageid=99), "planned")["id"], "the id comes from what the notice says"


def test_duration_is_sesbs_own_number_of_hours():
    assert sesb.make_notice(row(Duration=8), "planned")["duration"] == 8
    assert sesb.make_notice(row(start="2026-09-26T09:40:00", end="2026-09-26T17:00:00", Duration=8), "planned")["duration"] == 8, \
        "SESB rounds 7 h 20 min up to 8; we show its number, not ours"
    assert sesb.make_notice(row(Duration=8.0), "planned")["duration"] == 8
    for bad in (None, "8", "", True, False, 0, -3, 8.5, 24 * 31 + 1, [8], {}):
        assert "duration" not in sesb.make_notice(row(Duration=bad), "planned"), bad
    no_field = row()
    del no_field["Duration"]
    n = sesb.make_notice(no_field, "planned")
    assert n is not None and "duration" not in n, "a row without the field is still a notice; the site works the hours out"
    assert sesb.make_notice(row(Duration=8), "planned")["id"] == sesb.make_notice(row(Duration=9), "planned")["id"], "the id does not depend on it"


# ── Which notices are listed ──────────────────────────────────────────────

def test_window_order_and_duplicates():
    def at(h_from_now, hours=8):        # a notice that ENDS h_from_now hours from `now` (Malaysia time is now + 8)
        end = datetime(2026, 9, 25, 23, 0) + timedelta(hours=h_from_now)
        start = end - timedelta(hours=hours)
        return row(area=f"A{h_from_now}", start=start.isoformat(timespec="seconds"), end=end.isoformat(timespec="seconds"))
    tables = {"planned": [at(30), at(-47), at(-49), at(2), row(area="Zed"), at(-47)],
              "short_notice": [at(-100), row(area="Beaufort", start="2026-09-24T09:00:00", end="2026-09-24T17:00:00")]}
    notices, counts = sesb.build(tables, NOW)
    areas = [n["area"] for n in notices]
    assert "A-49" not in areas and "A-100" not in areas, "a notice that ended more than 48 hours ago is not listed"
    assert "A-47" in areas and areas.count("A-47") == 1, "one that ended 47 hours ago is, once"
    assert counts == {"planned": 4, "short_notice": 1, "skipped": 0, "duplicates": 1}, counts
    assert [n["start"] for n in notices] == sorted(n["start"] for n in notices), "oldest start first"
    assert notices[0]["area"] in ("A-47", "Beaufort")


def test_unreadable_rows_are_counted():
    notices, counts = sesb.build({"planned": [row(), None, row(area="")], "short_notice": []}, NOW)
    assert len(notices) == 1 and counts["skipped"] == 2


# ── Talking to SESB ───────────────────────────────────────────────────────

def test_first_run_downloads_everything_and_saves_what_it_needs_to_ask_politely_next_time():
    session = first_run_session()
    res, store = go(session)
    assert res["result"] == "updated" and res["notices"] == 2 and store.puts == 1
    assert [c[0] for c in session.calls] == [PLANNED, SHORT] and all(not h for _, h in session.calls), "nothing to be conditional about yet"
    out = store.objs[sesb.OUT_KEY]
    assert out["schema"] == 1 and out["provider"] == "sesb" and out["source_url"] == sesb.PORTAL_URL
    assert out["generated_utc"] == out["checked_utc"] == "2026-09-25T15:00:00Z"
    assert out["source_changed_utc"] == "2026-09-24T16:00:06Z" and out["newest_published_utc"] == "2026-09-23T16:00:04Z"
    assert out["validators"] == {"planned": {"etag": ETAG_P, "last_modified": LM}, "short_notice": {"etag": ETAG_S, "last_modified": LM}}
    assert set(out) == {"schema", "provider", "name", "source_url", "generated_utc", "checked_utc", "source_changed_utc",
                        "newest_published_utc", "counts", "validators", "notices"}
    assert res["source_stale"] is False


def test_nothing_changed_costs_two_empty_answers_and_refreshes_only_the_check_time():
    _, store = go(first_run_session())
    later = datetime(2026, 9, 25, 16, 23, tzinfo=timezone.utc)
    session = Session({PLANNED: Resp(304), SHORT: Resp(304)})
    res, store = go(session, store, now=later)
    assert res["result"] == "unchanged" and store.puts == 2
    assert session.calls[0] == (PLANNED, {"If-None-Match": ETAG_P, "If-Modified-Since": LM})
    assert session.calls[1] == (SHORT, {"If-None-Match": ETAG_S, "If-Modified-Since": LM})
    assert len(session.calls) == 2, "no download"
    out = store.objs[sesb.OUT_KEY]
    assert out["checked_utc"] == "2026-09-25T16:23:00Z" and out["source_changed_utc"] == "2026-09-24T16:00:06Z"
    assert len(out["notices"]) == 2


def test_an_unchanged_check_still_lets_old_notices_go():
    old = row(area="Old", start="2026-09-25T09:00:00", end="2026-09-25T17:00:00")
    _, store = go(first_run_session(planned=[row(), old]), now=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc))
    assert {n["area"] for n in store.objs[sesb.OUT_KEY]["notices"]} == {"Tawau", "Old", "Beaufort"}
    res, store = go(Session({PLANNED: Resp(304), SHORT: Resp(304)}), store, now=datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc))
    assert {n["area"] for n in store.objs[sesb.OUT_KEY]["notices"]} == {"Tawau", "Beaufort"} and res["notices"] == 2


def test_if_one_file_changed_both_are_read_in_full():
    _, store = go(first_run_session())
    new_planned = [row(area="Kudat"), row(area="Sandakan")]
    session = Session({PLANNED: ok(table(*new_planned), '"ccc:0"', "Fri, 25 Sep 2026 16:00:07 GMT"),
                       SHORT: lambda h: Resp(304) if h else ok(table(row(area="Beaufort", title="UFLS SETTING")), ETAG_S)})
    res, store = go(session, store)
    assert res["result"] == "updated"
    assert [c[0] for c in session.calls] == [PLANNED, SHORT, SHORT] and session.calls[2][1] == {}, "the unchanged file is asked for again, unconditionally"
    assert {n["area"] for n in store.objs[sesb.OUT_KEY]["notices"]} == {"Kudat", "Sandakan", "Beaufort"}
    assert store.objs[sesb.OUT_KEY]["source_changed_utc"] == "2026-09-25T16:00:07Z", "the newest Last-Modified"
    assert store.objs[sesb.OUT_KEY]["validators"]["planned"]["etag"] == '"ccc:0"'


def test_force_forgets_the_saved_validators():
    _, store = go(first_run_session())
    session = first_run_session()
    res, _ = go(session, store, force=True)
    assert res["result"] == "updated" and all(not h for _, h in session.calls)


def test_a_notice_list_from_an_older_schema_is_not_trusted():
    session = first_run_session()
    res, _ = go(session, Store({"schema": 0, "notices": [], "validators": {"planned": {"etag": ETAG_P}}}))
    assert res["result"] == "updated" and all(not h for _, h in session.calls)


def test_failures_write_nothing():
    _, store = go(first_run_session())
    before = json.dumps(store.objs, sort_keys=True)
    cases = {
        "server error": Resp(503),
        "not found": Resp(404),
        "no connection": requests.ConnectionError("down"),
        "timeout": requests.Timeout("slow"),
        "not JSON": Resp(200, b"<html>maintenance</html>", {}),
        "a list, not a table": Resp(200, b"[1, 2, 3]", {}),
        "no rows": Resp(200, b'{"Table": []}', {}),
        "another shape": Resp(200, b'{"rows": [1]}', {}),
    }
    for name, answer in cases.items():
        session = Session({PLANNED: answer, SHORT: ok(table(row()), ETAG_S)})
        try:
            go(session, store, force=True)
        except sesb.SesbError as e:
            assert PLANNED in str(e), (name, str(e))
        else:
            raise AssertionError(f"{name}: should have failed")
        assert json.dumps(store.objs, sort_keys=True) == before and store.puts == 1, f"{name}: the last good file must stay"


def test_retries_only_what_might_get_better():
    calls = []
    def flaky(headers):
        calls.append(1)
        return Resp(503) if len(calls) < 3 else ok(table(row()), ETAG_P)
    res, _ = go(Session({PLANNED: flaky, SHORT: ok(table(row()), ETAG_S)}))
    assert res["result"] == "updated" and len(calls) == 3
    forbidden = Session({PLANNED: Resp(403), SHORT: ok(table(row()), ETAG_S)})
    try:
        go(forbidden)
    except sesb.SesbError as e:
        assert "HTTP 403" in str(e)
    assert [c[0] for c in forbidden.calls] == [PLANNED], "a refusal is not asked again"
    always = Session({PLANNED: Resp(500), SHORT: Resp(500)})
    try:
        go(always)
    except sesb.SesbError:
        pass
    assert len(always.calls) == sesb.ATTEMPTS


def test_a_file_that_is_mostly_unreadable_is_a_format_change():
    bad = [row(start="soon") for _ in range(30)] + [row()]
    try:
        go(first_run_session(planned=bad))
    except sesb.SesbError as e:
        assert "format may have changed" in str(e)
    else:
        raise AssertionError("30 of 32 rows unreadable should fail")
    res, _ = go(first_run_session(planned=[row(start="soon"), row(), row(area="Kudat")]))
    assert res["result"] == "updated" and res["out"]["counts"]["skipped"] == 1, "a few bad rows are left out, not fatal"


def test_a_byte_order_mark_is_fine():
    body = b"\xef\xbb\xbf" + table(row())
    assert len(sesb.parse_table(body, "x")) == 1


def test_silence_at_sesb_is_reported():
    out = {"source_changed_utc": "2026-09-21T16:00:06Z"}
    assert sesb.source_is_stale(out, NOW) is True
    assert sesb.source_is_stale({"source_changed_utc": "2026-09-23T16:00:06Z"}, NOW) is False
    assert sesb.source_is_stale({"source_changed_utc": None}, NOW) is False
    session = first_run_session()
    session.answers = {PLANNED: ok(table(row()), ETAG_P, "Mon, 21 Sep 2026 16:00:06 GMT"), SHORT: ok(table(row()), ETAG_S, "Mon, 21 Sep 2026 16:00:06 GMT")}
    res, _ = go(session)
    assert res["source_stale"] is True


def test_the_switch():
    old = os.environ.pop("SESB_NOTICES", None)
    try:
        assert sesb.enabled() is False
        for v in ("on", "ON", " on ", "1", "true", "Yes"):
            os.environ["SESB_NOTICES"] = v
            assert sesb.enabled() is True, v
        for v in ("", "off", "no", "0", "false", "maybe"):
            os.environ["SESB_NOTICES"] = v
            assert sesb.enabled() is False, v
    finally:
        os.environ.pop("SESB_NOTICES", None)
        if old is not None:
            os.environ["SESB_NOTICES"] = old


def test_we_say_who_we_are():
    s = sesb.make_session()
    assert "BorneoSky" in s.headers["User-Agent"] and "borneosky.com" in s.headers["User-Agent"]


if __name__ == "__main__":
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    for name in names:
        globals()[name]()
        print(f"  ok  {name}")
    print(f"{len(names)} checks passed")
