#!/usr/bin/env python3
"""Checks for borneosky/sarawak.py with made-up posts and a stand-in for X's API. No network, no R2, no credits:
    python scripts/test_sarawak.py

Every post below is invented for these checks, in the shapes the module reads. No real post of Sarawak Energy's is
quoted here or anywhere in this repository: X's content is not ours to republish."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import sarawak as S  # noqa: E402

S.time.sleep = lambda s: None          # retries should not slow the checks

UTC = timezone.utc
TOKEN = "TEST-TOKEN-must-never-appear-4f9c1e"
NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)            # 11:00 in Sarawak
UID = "778899"

ESTIMATE = "BINTULU: Power supply interruption affecting Taman Ujian and Kampung Contoh due to a broken line conductor. Repair expected by 9 pm."
UPDATE = "UPDATE - BINTULU: Power supply is still interrupted. Repair now expected by 11 pm."
RESTORED = "BINTULU: Power supply has been restored to all affected areas. Thank you for your patience."
SIBU = "SIBU: Power supply interruption at Jalan Ujian 2 due to a suspected cable fault. Repair expected by 1 am."
PROMO = "Join us at the Sarawak Energy Open Day this Saturday! Free health checks for all."
ADVICE = "Tips to stay safe during a power outage: keep a torch handy."
NO_TOWN = "Power supply interruption reported in several areas. Our crews are working on it."

# The shape the account uses, with invented places and wording (none of it is a real post).
SHAPE = {
    "finding": "MIRI: Supply interruption at Kpg Contoh Satu, Kpg Contoh Dua and surrounding areas. Fault finding under way, expected to finish by 8pm.",
    "update": "MIRI UPDATE: Extensive repair works needed. New completion estimate: 7pm.",
    "tree": "KAPIT : A tree felled by a storm has brought down a conductor, causing supply interruption to Kampung Ujian, SK Ujian and surrounding areas. "
            "Work will resume later today because of the rain; supply expected back by 5.00pm.",
    "noon": "SARIKEI: Supply interruption at Kampung Contoh (Klinik and SK Contoh), Kampung Satu and Kampung Dua. Specialised technical support is needed for the repair, "
            "with restoration estimated by 12 noon, 29 September 2026.",
    "resume": "BAU: Faulty equipment is causing supply interruption at Kpg Contoh. Repair will resume tomorrow, weather permitting.",
    "misspelt": "KANOWIT: Faulty equipment led to a supply interuption at Rh Ujian Satu, Rh Ujian Dua and nearby areas. Repairs are in progress and should finish by 12am.",
    "fire": "BINTULU: A blaze at a substation caused supply interruption at Contoh Gardens and Taman Ujian. Repair work is in progress and should be finished by 1AM.",
    "aerial": "SIBU: Suspected aerial cable fault behind supply interruption at Rh Contoh, Jln Ujian, Kpg Satu. Fault finding will resume tomorrow, weather permitting.",
    "vehicle": "SERIAN: A lorry struck a pole, and the cross arm broke, causing supply interruption affecting Kampung Ujian, Kampung Contoh and surrounding areas. "
               "Repair is in progress; supply will be restored by 4PM https://t.co/abc123",
}


def snowflake(when: datetime, seq: int = 0) -> str:
    """A Post ID for `when`, made the way X makes them."""
    return str(((int(when.timestamp() * 1000) - S.TWITTER_EPOCH_MS) << 22) | seq)


class Resp:
    def __init__(self, status=200, body=None, raw=None):
        self.status_code, self._body, self._raw = status, body, raw
        self.headers = {}

    def json(self):
        if self._raw is not None:
            raise ValueError("not json")
        return self._body


class FakeX:
    """Answers like X's API for one account. Keeps every request, so a check can say what was asked."""

    def __init__(self):
        self.posts = []              # {"id", "text"}
        self.deleted = set()         # asked for, "not found"
        self.hidden = set()          # asked for, "not authorized" (not the same as gone)
        self.failures = {}           # path prefix -> Resp, an exception, or a function of the params
        self.calls = []
        self.endless = False         # always more pages

    def add(self, when, text, seq=0):
        pid = snowflake(when, seq)
        self.posts.append({"id": pid, "text": text})
        return pid

    def timeline_calls(self):
        return [c for c in self.calls if c[0].endswith("/tweets") and c[0].startswith("/users/")]

    def get(self, url, params=None, timeout=None):
        path, params = url[len(S.API):], dict(params or {})
        self.calls.append((path, params))
        for prefix, f in self.failures.items():
            if path.startswith(prefix):
                f = f(params) if callable(f) else f
                if isinstance(f, Exception):
                    raise f
                return f
        if path.startswith("/users/by/username/"):
            return Resp(200, {"data": {"id": UID, "name": "Sarawak Energy", "username": S.HANDLE}})
        if path == f"/users/{UID}/tweets":
            rows = sorted((p for p in self.posts if p["id"] not in self.deleted), key=lambda p: int(p["id"]), reverse=True)
            if "since_id" in params:
                rows = [p for p in rows if int(p["id"]) > int(params["since_id"])]
            if "start_time" in params:
                floor = datetime.fromisoformat(params["start_time"].replace("Z", "+00:00"))
                rows = [p for p in rows if S.post_time(p["id"]) >= floor]
            start = int(params.get("pagination_token", 0))
            size = int(params.get("max_results", 10))
            page = rows[start:start + size]
            meta = {"result_count": len(page)}
            if page:
                meta.update(newest_id=page[0]["id"], oldest_id=page[-1]["id"])
            if self.endless or start + size < len(rows):
                meta["next_token"] = str(start + size)
            return Resp(200, {"data": page, "meta": meta} if page else {"meta": meta})
        if path == "/tweets":
            ids = params["ids"].split(",")
            data = [p for p in self.posts if p["id"] in ids and p["id"] not in self.deleted and p["id"] not in self.hidden]
            errors = [{"resource_id": i, "type": "https://api.twitter.com/2/problems/resource-not-found", "title": "Not Found Error"}
                      for i in ids if i in self.deleted]
            errors += [{"resource_id": i, "type": "https://api.twitter.com/2/problems/not-authorized-for-resource", "title": "Authorization Error"}
                       for i in ids if i in self.hidden]
            body = {}
            if data:
                body["data"] = data
            if errors:
                body["errors"] = errors
            return Resp(200, body)
        raise AssertionError(f"unexpected request {path}")


class Store:
    def __init__(self):
        self.objs, self.puts = {}, 0

    def put(self, key, obj):
        self.objs[key] = json.loads(json.dumps(obj, ensure_ascii=False))
        self.puts += 1

    def get(self, key):
        return self.objs.get(key)


def go(x, store=None, now=NOW, force=False):
    store = store or Store()
    return S.run(store.put, store.get, x, now=now, force=force), store


def out(store):
    return store.objs[S.OUT_KEY]


def facts(text, posted=NOW - timedelta(hours=1)):
    return S.parse_post(text, posted)


# ── What a post is ────────────────────────────────────────────────────────

def test_post_ids_carry_their_time():
    """Six real Post IDs, noted in the site repository's docs with the day each was made (no text): the moment inside
    an ID must land on that day, so a post without a timestamp is still dated right."""
    known = {"2009886931610669495": (2026, 1, 10), "2042519841064780166": (2026, 4, 10), "2050546716592722328": (2026, 5, 2),
             "2071528090107510876": (2026, 6, 29), "2076507805675339792": (2026, 7, 13), "2079509024543064079": (2026, 7, 21)}
    for pid, (y, m, d) in known.items():
        assert abs((S.post_time(pid) - datetime(y, m, d, 12, tzinfo=UTC)).total_seconds()) <= 36 * 3600, pid
    assert S.post_time(snowflake(NOW)) == NOW
    assert S.post_time("1", "2026-09-26T02:00:00.000Z") == datetime(2026, 9, 26, 2, 0, tzinfo=UTC), "X's own timestamp wins"
    assert S.post_time("2079509024543064079", "not a time") == S.post_time("2079509024543064079"), "an unreadable timestamp falls back to the ID"


def test_a_post_is_kept_only_if_it_says_something_about_an_outage():
    for text in (ESTIMATE, SIBU, UPDATE, RESTORED, NO_TOWN):
        assert S.wanted(text, facts(text)), text
    assert S.wanted("UPDATE - BAU: The cable fault has been identified. A new cable is being laid. Restoration is expected by 2 am.",
                    facts("UPDATE - BAU: The cable fault has been identified. A new cable is being laid. Restoration is expected by 2 am.")), \
        "a follow-up that never says 'power' is still a notice when it has a town and something to say"
    for text in (PROMO, ADVICE, "Our engineers found the transformer fault and repair is under way.", "Happy Hari Raya from all of us at Sarawak Energy!"):
        assert not S.wanted(text, facts(text)), text


def test_the_parts_of_a_notice():
    f = facts(ESTIMATE, datetime(2026, 9, 20, 2, 5, tzinfo=UTC))
    assert (f["area"], f["type"], f["kind"], f["stage"], f["places"], f["more"]) == ("Bintulu", "unplanned", "line_damage", "repair", "Taman Ujian and Kampung Contoh", False)
    assert f["start"] == "2026-09-20T10:05:00+08:00" and f["end"] == "2026-09-20T21:00:00+08:00" and f["readable"] and not f["update"] and not f["restored"]
    u = facts(UPDATE)
    assert u["update"] is True and u["area"] == "Bintulu"
    r = facts(RESTORED)
    assert r["restored"] is True and r["end"] is None, "a post that says supply is back has no repair estimate"
    assert facts("BINTULU: Repairs are under way, expected to be done by 9pm.", datetime(2026, 9, 20, 13, 5, tzinfo=UTC))["end"] is None, \
        "an estimate that falls a few minutes before the post itself is a misreading, not an estimate"
    assert facts("BINTULU: Repairs are under way, expected to be done by 8pm.", datetime(2026, 9, 20, 13, 30, tzinfo=UTC))["end"] == "2026-09-21T20:00:00+08:00", \
        "posted at 9.30 pm, 'by 8pm' is the next evening"


def test_the_shape_the_account_uses():
    posted = datetime(2026, 9, 28, 2, 0, tzinfo=UTC)                     # 10:00 in Sarawak
    def f(key):
        return facts(SHAPE[key], posted)
    x = f("finding")
    assert (x["area"], x["kind"], x["stage"], x["places"], x["more"], x["end"], x["update"]) == \
        ("Miri", "other", "finding", "Kpg Contoh Satu, Kpg Contoh Dua", True, "2026-09-28T20:00:00+08:00", False)
    x = f("update")
    assert (x["area"], x["update"], x["stage"], x["places"], x["end"]) == ("Miri", True, "extensive", "", "2026-09-28T19:00:00+08:00"), "TOWN UPDATE: is the town, and an update"
    x = f("tree")
    assert (x["area"], x["kind"], x["stage"], x["places"], x["more"], x["end"]) == \
        ("Kapit", "tree", "resume", "Kampung Ujian, SK Ujian", True, "2026-09-28T17:00:00+08:00"), "the tree, not the conductor it brought down; a space before the colon is fine"
    x = f("noon")
    assert (x["area"], x["stage"], x["end"], x["more"]) == ("Sarikei", "extensive", "2026-09-29T12:00:00+08:00", False), "12 noon on the date the post names"
    assert x["places"] == "Kampung Contoh (Klinik and SK Contoh), Kampung Satu and Kampung Dua", "the list is kept as the post wrote it, brackets and 'and' included"
    x = f("resume")
    assert (x["area"], x["kind"], x["stage"], x["places"], x["end"]) == ("Bau", "equipment_fault", "resume", "Kpg Contoh", None), "no estimate: none is made up"
    x = f("misspelt")
    assert (x["area"], x["readable"], x["end"], x["kind"], x["places"], x["more"]) == \
        ("Kanowit", True, "2026-09-29T00:00:00+08:00", "equipment_fault", "Rh Ujian Satu, Rh Ujian Dua", True), "'interuption' is read, 'nearby areas' marks the list as going on, and 12am is the coming midnight"
    assert S.wanted(SHAPE["misspelt"], x)
    x = f("fire")
    assert (x["kind"], x["places"], x["end"]) == ("fire", "Contoh Gardens and Taman Ujian", "2026-09-29T01:00:00+08:00"), "the fire is the cause"
    x = f("aerial")
    assert (x["kind"], x["stage"], x["end"], x["places"]) == ("cable_fault", "resume", None, "Rh Contoh, Jln Ujian, Kpg Satu")
    x = f("vehicle")
    assert (x["kind"], x["stage"], x["places"], x["more"], x["end"], x["restored"]) == \
        ("vehicle", "repair", "Kampung Ujian, Kampung Contoh", True, "2026-09-28T16:00:00+08:00", False), \
        "'supply will be restored by 4PM' is an estimate, not news that supply is back"
    for key in SHAPE:
        assert f(key)["readable"], key


def test_repair_estimates():
    at = lambda h, m=0: datetime(2026, 9, 20, h, m, tzinfo=S.LOCAL).astimezone(UTC)     # noqa: E731
    def end(text, posted):
        e = S.estimate(text, posted)
        return e.isoformat() if e else None
    assert end("Repair expected by 9 pm.", at(10, 5)) == "2026-09-20T21:00:00+08:00"
    assert end("Repair expected by 1am.", at(23, 0)) == "2026-09-21T01:00:00+08:00", "1 am after an 11 pm post is the next night"
    assert end("Repair expected by 9.30 pm.", at(10)) == "2026-09-20T21:30:00+08:00"
    assert end("Repair expected by 2100hrs.", at(10)) == "2026-09-20T21:00:00+08:00"
    assert end("Supply expected to be restored tomorrow by 6am.", at(20)) == "2026-09-21T06:00:00+08:00"
    assert end("Bekalan dijangka pulih sebelum jam 8.00 malam.", at(10)) == "2026-09-20T20:00:00+08:00"
    assert end("Bekalan dijangka pulih sebelum jam 2 pagi.", at(22)) == "2026-09-21T02:00:00+08:00"
    assert end("Supply was cut at 5.30pm. Repair expected by 9pm.", at(18)) == "2026-09-20T21:00:00+08:00", "the time with a cue, not the first time"
    assert end("The fault started at 8pm.", at(21)) is None, "a time without a cue is not an estimate"
    assert end("Repair expected next morning.", at(21)) is None, "no clock time: nothing is guessed"
    assert end("Completion estimated by 12 noon.", at(8)) == "2026-09-20T12:00:00+08:00" and end("Completion estimated by 12 noon.", at(13)) == "2026-09-21T12:00:00+08:00"
    assert end("Repair estimated by midnight.", at(20)) == "2026-09-21T00:00:00+08:00" and end("Repair expected to be finished by 12am.", at(20)) == "2026-09-21T00:00:00+08:00"
    assert end("Restoration estimated by 12 noon, 21 September 2026.", at(8)) == "2026-09-21T12:00:00+08:00", "a date the post names is the date"
    assert end("Restoration estimated by 12 noon, 25 September 2026.", at(8)) is None, "five days out is a misreading"
    assert end("Restoration estimated by 12 noon, 19 September 2026.", at(8)) is None, "a day that has already gone is not an estimate"
    assert end("Repair expected by 9pm tomorrow.", at(10)) == "2026-09-21T21:00:00+08:00"
    assert end("Repair expected by 9pm tomorrow.", at(1)) == "2026-09-21T21:00:00+08:00"
    assert end("Repair expected by 25pm.", at(10)) is None and end("Repair expected by 13:70 pm.", at(10)) is None, "impossible times are not read"


def test_planned_interruptions_have_a_date_and_hours():
    posted = datetime(2026, 9, 26, 2, 0, tzinfo=UTC)
    w = S.planned_window("Maintenance works on 28 September 2026, 9.00am to 5.00pm.", posted)
    assert [x.isoformat() for x in w] == ["2026-09-28T09:00:00+08:00", "2026-09-28T17:00:00+08:00"]
    w = S.planned_window("Kerja penyenggaraan pada 27 Sep dari jam 9.00 pagi hingga jam 5.00 petang.", posted)
    assert [x.isoformat() for x in w] == ["2026-09-27T09:00:00+08:00", "2026-09-27T17:00:00+08:00"]
    w = S.planned_window("Works on 27 Sep, 10pm to 4am.", posted)
    assert w[1] - w[0] == timedelta(hours=6), "hours past midnight run into the next day"
    assert S.planned_window("Works on 27 and 28 Sep, 9am to 5pm.", posted) is None, "two dates are not read"
    assert S.planned_window("Works on 27 Sep.", posted) is None and S.planned_window("Works from 9am to 5pm.", posted) is None
    assert S.planned_window("Works on 27 Nov, 9am to 5pm.", posted) is None, "more than 45 days away is not a notice, it is a misreading"
    planned = facts("PLANNED POWER INTERRUPTION - KUCHING: Maintenance works on 28 September 2026, 9.00am to 5.00pm. Affected areas: Taman Sukma, Jalan Stutong.", posted)
    assert planned["type"] == "planned" and planned["area"] == "Kuching" and planned["kind"] == "maintenance"
    assert planned["start"] == "2026-09-28T09:00:00+08:00" and planned["end"] == "2026-09-28T17:00:00+08:00" and planned["places"] == "Taman Sukma, Jalan Stutong"


def test_the_town():
    for text, want in {"BINTULU: x": "Bintulu", "Sibu: x": "Sibu", "SAMARAHAN: x": "Kota Samarahan", "Simanggang: x": "Sri Aman", "SRI AMAN - x": "Sri Aman",
                       "BAU: x": "Bau", "UPDATE: TUBAU: x": "Tubau", "PLANNED POWER INTERRUPTION - KUCHING: x": "Kuching", "GANGGUAN BEKALAN ELEKTRIK - MIRI: x": "Miri",
                       "🚨 LIMBANG: x": "Limbang", "Power supply interruption in Kapit: x": "Kapit", "Kampung Sungai Niah: x": "Kampung Sungai Niah",
                       "KAPIT : x": "Kapit", "LIMBANG UPDATE: x": "Limbang", "NIAH  UPDATE 2: x": "Niah", "BEKENU: x": "Bekenu"}.items():
        assert S.area_of(S.clean(text))[0] == want, (text, S.area_of(S.clean(text)))
    assert S.area_of("LIMBANG UPDATE: x") == ("Limbang", True) and S.area_of("MIRI: x") == ("Miri", False) and S.area_of("UPDATE - BAU: x") == ("Bau", False), \
        "the town is found whichever way UPDATE is written; the post says separately whether it is one"
    assert S.parse_post("UPDATE - BAU: Repairs are under way, expected by 8pm.", NOW - timedelta(hours=1))["update"] is True
    for text in ("Dear customers: we are sorry.", "Sarawak Energy: notice", "Power supply interruption reported.", "We are working on it: soon", "", "   ", "UPDATE: x"):
        assert S.area_of(text) == (None, False), text
    assert S.area_of("PLANNED POWER INTERRUPTION\nKUCHING: works")[0] == "Kuching", "the town may be on the second line"
    assert set(S.SARAWAK_TOWNS) >= {"Bintulu", "Sibu", "Kuching", "Kota Samarahan", "Sri Aman", "Serian", "Sarikei", "Betong", "Kapit", "Miri"}


def test_places_are_facts_and_nothing_else():
    P = S.places_of
    assert P("BAU: Supply interruption at Kpg A, Kpg B and surrounding areas. Repair works in progress.") == ("Kpg A, Kpg B", True)
    assert P("A broken pole has caused a supply interruption affecting Taman A and Taman B. Repair by 9pm.") == ("Taman A and Taman B", False), "the list is kept as written"
    assert P("Interruption to Menara Ujian. Repair works to resume tomorrow.") == ("Menara Ujian", False)
    assert P("Supply interruption at Sg. Ujian, Jln. Utama and Kpg Baru. Fault finding under way.") == ("Sg. Ujian, Jln. Utama and Kpg Baru", False), "a full stop inside a name is not the end of the list"
    assert P("Supply interruption at Kpg A,Kpg B,  Kpg C and surrounding areas, with repair due by 1am.") == ("Kpg A, Kpg B, Kpg C", True)
    assert P("Supply interruption at Kpg A and nearby areas. Repairs are in progress and should finish by 12am.") == ("Kpg A", True), "'Repairs' starts the next sentence, as 'Repair' does"
    assert P("Supply interruption at Kampung X (Klinik and SK X) and Kampung Y. Extensive repairs are needed.") == ("Kampung X (Klinik and SK X) and Kampung Y", False)
    assert P("Areas affected: Taman A, Taman B and Kampung C due to a fault. Repair by 9pm.") == ("Taman A, Taman B and Kampung C", False)
    assert P("Kawasan terjejas: Taman A dan Taman B. Bekalan dijangka pulih.") == ("Taman A dan Taman B", False)
    assert P("Power is out. Repair expected by 9pm.") == ("", False)
    long = "Supply interruption at " + ", ".join(f"Kampung Nombor {i}" for i in range(80)) + "."
    p, more = P(long)
    assert len(p) <= S.MAX_PLACES and not p.endswith(",") and p.startswith("Kampung Nombor 0,") and more is True, "a very long list is cut at an item, and marked as going on"
    body = S.clean("BINTULU: affecting Taman A @someone #SarawakEnergy https://t.co/abc &amp; Taman B www.example.com")
    assert "@" not in body and "#" not in body and "http" not in body and "www" not in body and "& Taman B" in body


def test_causes_and_the_kinds_the_site_words():
    samples = {"tree": "a fallen tree", "vehicle": "a vehicle hit a pole", "fire": "a blaze at the substation", "cable_fault": "faulty aerial cable",
               "line_damage": "a broken overhead line conductor", "equipment_fault": "faulty equipment", "transformer": "transformer failure",
               "tripping": "tripping of the feeder", "weather": "lightning", "maintenance": "planned maintenance"}
    assert set(samples) == set(S.KIND_CODES) - {"other"}
    for kind, words in samples.items():
        assert S.cause_of(f"Interruption due to {words}.") == kind, kind
    assert S.cause_of("Interruption for unknown reasons.") == "other" and S.cause_of("Extensive repairs are needed.") == "other"
    # The root cause comes before the damage it did.
    assert S.cause_of("A fallen tree broke the overhead conductor and a pole") == "tree"
    assert S.cause_of("A lorry hit a pole and the cross arm broke") == "vehicle"
    assert S.cause_of("A fire damaged the equipment") == "fire"
    assert S.cause_of("A tree came down in the wind and damaged the conductor") == "tree"
    assert S.cause_of("A damaged pole and a broken overhead line conductor") == "line_damage"
    assert S.cause_of("A faulty aerial cable") == "cable_fault"
    assert S.KIND_CODES[-1] == "other" and len(set(S.KIND_CODES)) == len(S.KIND_CODES)
    stages = {"resume": "Repair will resume tomorrow.", "finding": "Fault finding under way.", "extensive": "Extensive repairs are needed.",
              "repair": "Repairs are under way."}
    assert set(stages) == set(S.STAGE_CODES) - {"none"}
    for stage, words in stages.items():
        assert S.stage_of(words) == stage, stage
    assert S.stage_of("Fault finding will resume when the rain stops.") == "resume", "work paused says more than what the work is"
    assert S.stage_of("Specialised technical support required for repair works.") == "extensive"
    assert S.stage_of("Supply interruption at Kpg A.") == "none" and S.STAGE_CODES[-1] == "none"


def test_no_word_of_a_post_is_ever_stored():
    text = ESTIMATE + " Thank you ZQXJ-MARKER for waiting @someone #SarawakEnergy https://example.com/x/ZQXJ"
    x = FakeX()
    x.add(NOW - timedelta(hours=1), text)
    _, store = go(x)
    blob = json.dumps(store.objs, ensure_ascii=False)
    for leak in ("ZQXJ", "someone", "Thank you", "Repair expected", "Power supply interruption"):
        assert leak not in blob, leak
    notices = json.dumps(out(store)["notices"], ensure_ascii=False)
    for leak in ("@", "#", "http", "www"):
        assert leak not in notices, leak                    # (the file's own source_url is a link, the notices are not)
    n = out(store)["notices"][0]
    assert set(n) == {"id", "type", "area", "start", "end", "kind", "stage", "places", "more", "update", "restored", "published", "readable"}, sorted(n)
    assert n["places"] == "Taman Ujian and Kampung Contoh" and n["area"] == "Bintulu"


# ── One run ───────────────────────────────────────────────────────────────

def test_the_first_run_asks_for_the_last_48_hours_and_nothing_else():
    x = FakeX()
    a = x.add(NOW - timedelta(hours=2), ESTIMATE)
    x.add(NOW - timedelta(hours=3), PROMO)
    x.add(NOW - timedelta(hours=60), SIBU)                    # too old for the first read
    d = x.add(NOW - timedelta(hours=30), SIBU)
    res, store = go(x)
    paths = [c[0] for c in x.calls]
    assert paths == [f"/users/by/username/{S.HANDLE}", f"/users/{UID}/tweets"], paths
    params = x.calls[1][1]
    assert params["start_time"] == "2026-09-24T03:00:00Z" and params["exclude"] == "retweets,replies" and params["max_results"] == 100
    assert not set(params) - {"start_time", "exclude", "max_results"}, "no fields, no expansions: an expanded user would be billed as a user"
    o = out(store)
    assert [n["id"] for n in o["notices"]] == [d, a], "oldest first"
    assert o["counts"] == {"posts_seen": 3, "outage_posts": 2, "unread": 0, "dropped": 1, "deleted": 0, "replaced": 0}
    assert o["state"]["user_id"] == UID and o["state"]["newest_id"] == a and o["state"]["last_recheck"] == "2026-09-26"
    assert o["state"]["usage"] == {"2026-09-26": {"posts": 3, "users": 1}} and res["cost_month_usd"] == 0.025
    assert o["source_changed_utc"] is None and o["checked_utc"] == "2026-09-26T03:00:00Z" and o["schema"] == 1 and o["provider"] == "sarawak_energy"
    assert o["newest_published_utc"] == "2026-09-26T01:00:00Z" and store.puts == 1


def test_the_next_run_asks_only_for_newer_posts_and_an_empty_answer_costs_nothing():
    x = FakeX()
    a = x.add(NOW - timedelta(hours=2), ESTIMATE)
    _, store = go(x)
    x.calls.clear()
    res, _ = go(x, store, NOW + timedelta(hours=1))
    assert [c[0] for c in x.calls] == [f"/users/{UID}/tweets"], "no user lookup again, and no re-check on the same day"
    p = x.calls[0][1]
    assert p["since_id"] == a and "start_time" not in p
    o = out(store)
    assert res["counts"]["posts_seen"] == 0 and o["state"]["usage"]["2026-09-26"] == {"posts": 1, "users": 1}, "nothing new: nothing more is counted"
    assert o["checked_utc"] == "2026-09-26T04:00:00Z" and [n["id"] for n in o["notices"]] == [a], "the check time moves, the notices stay"
    assert o["state"]["newest_id"] == a
    b = x.add(NOW + timedelta(hours=1, minutes=30), SIBU)
    res, _ = go(x, store, NOW + timedelta(hours=2))
    assert [n["id"] for n in out(store)["notices"]] == [a, b], "oldest start first"
    assert out(store)["state"]["newest_id"] == b and out(store)["state"]["usage"]["2026-09-26"]["posts"] == 2


def test_a_post_seen_twice_is_one_notice():
    x = FakeX()
    x.add(NOW - timedelta(hours=2), ESTIMATE)
    _, store = go(x)
    o = out(store)
    o["state"]["newest_id"] = None                                    # a lost place: the whole window is read again
    _, _ = go(x, store, NOW + timedelta(minutes=30))
    assert len(out(store)["notices"]) == 1


def test_an_update_replaces_the_first_report_and_a_restored_post_closes_it():
    x = FakeX()
    t0 = NOW - timedelta(hours=1)                                     # 10:00 in Sarawak
    first = x.add(t0, ESTIMATE)
    _, store = go(x)
    second = x.add(t0 + timedelta(hours=3), UPDATE)
    res, _ = go(x, store, t0 + timedelta(hours=4))
    ns = out(store)["notices"]
    assert [n["id"] for n in ns] == [second] and res["counts"]["replaced"] == 1
    assert ns[0]["start"] == "2026-09-26T10:00:00+08:00", "the first report's time is kept"
    assert ns[0]["end"] == "2026-09-26T23:00:00+08:00" and ns[0]["update"] is True and ns[0]["restored"] is False
    third = x.add(t0 + timedelta(hours=6), RESTORED)
    go(x, store, t0 + timedelta(hours=7))
    ns = out(store)["notices"]
    assert [n["id"] for n in ns] == [third] and ns[0]["restored"] is True and ns[0]["end"] is None and ns[0]["start"] == "2026-09-26T10:00:00+08:00"
    other = x.add(t0 + timedelta(hours=7, minutes=30), SIBU)
    go(x, store, t0 + timedelta(hours=8))
    assert [n["area"] for n in out(store)["notices"]] == ["Bintulu", "Sibu"], "another town is another notice"
    x.add(t0 + timedelta(hours=8, minutes=30), "UPDATE - MIRI: Repair now expected by 11 pm.")
    go(x, store, t0 + timedelta(hours=9))
    assert sorted(n["area"] for n in out(store)["notices"]) == ["Bintulu", "Miri", "Sibu"], "an update in a town with no earlier notice stands alone"
    assert first not in [n["id"] for n in out(store)["notices"]] and other in [n["id"] for n in out(store)["notices"]]


def test_an_update_replaces_only_the_latest_notice_in_its_town_and_keeps_what_it_does_not_repeat():
    """Two faults in one town can overlap, and an update does not say which it is about: the most recent is the fair
    reading, and the other stays as it was. The update repeats neither the places nor the cause, so it takes them from
    the notice it replaces."""
    x = FakeX()
    t0 = NOW - timedelta(hours=5)
    first = x.add(t0, "MIRI: Supply interruption at Taman Contoh Satu and surrounding areas. Fault finding continues, expected by 3pm.")
    second = x.add(t0 + timedelta(hours=1), "MIRI: Faulty equipment has caused supply interruption at Kpg Contoh Dua, Kpg Contoh Tiga. Repair works in progress, expected by 6pm.")
    update = x.add(t0 + timedelta(hours=3), "MIRI UPDATE: Extensive repair works needed. New completion estimate: 9pm.")
    res, store = go(x, now=t0 + timedelta(hours=4))
    ns = {n["id"]: n for n in out(store)["notices"]}
    assert set(ns) == {first, update} and res["counts"]["replaced"] == 1, "the first incident stays; the update replaced the second"
    u = ns[update]
    assert (u["places"], u["more"], u["kind"], u["stage"]) == ("Kpg Contoh Dua, Kpg Contoh Tiga", False, "equipment_fault", "extensive"), "places and cause from the notice it replaced, the stage it reports itself"
    assert u["start"] == "2026-09-26T08:00:00+08:00" or u["start"].startswith("2026-09-26T"), u["start"]
    assert u["end"] == "2026-09-26T21:00:00+08:00" and u["update"] is True, "the new estimate is the update's own"
    f = ns[first]
    assert (f["places"], f["more"], f["stage"], f["update"]) == ("Taman Contoh Satu", True, "finding", False)
    # An update that has its own places keeps them.
    y = FakeX()
    y.add(t0, "SIBU: Faulty equipment has caused supply interruption at Rh Contoh. Repair works in progress, expected by 3pm.")
    z = y.add(t0 + timedelta(hours=2), "SIBU UPDATE: Supply interruption at Jln Baru. Repair works in progress, expected by 7pm.")
    _, store = go(y, now=t0 + timedelta(hours=3))
    n = out(store)["notices"]
    assert [m["id"] for m in n] == [z] and n[0]["places"] == "Jln Baru"


def test_a_notice_we_cannot_read_is_kept_as_a_link_and_nothing_more():
    x = FakeX()
    u = x.add(NOW - timedelta(hours=1), NO_TOWN)
    x.add(NOW - timedelta(hours=1, minutes=1), ADVICE)
    res, store = go(x)
    o = out(store)
    assert res["counts"]["unread"] == 1 and res["counts"]["dropped"] == 1
    n = o["notices"][0]
    assert n["id"] == u and n["readable"] is False and n["area"] == "" and n["places"] == "" and n["end"] is None and n["kind"] == "other" and n["restored"] is False
    assert n["stage"] == "none" and n["more"] is False


def test_deleted_posts_are_removed_after_the_daily_look_and_only_once_a_day():
    x = FakeX()
    day1 = datetime(2026, 9, 25, 3, 0, tzinfo=UTC)
    a = x.add(day1 - timedelta(hours=1), ESTIMATE)
    b = x.add(day1 - timedelta(hours=2), SIBU)
    _, store = go(x, now=day1)
    assert not [c for c in x.calls if c[0] == "/tweets"], "the first run has just read everything: no second look"
    x.deleted.add(b)
    x.calls.clear()
    day2 = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
    res, _ = go(x, store, day2)
    looks = [c for c in x.calls if c[0] == "/tweets"]
    assert len(looks) == 1 and set(looks[0][1]["ids"].split(",")) == {a, b} and set(looks[0][1]) == {"ids"}
    assert [n["id"] for n in out(store)["notices"]] == [a] and res["counts"]["deleted"] == 1 and out(store)["state"]["last_recheck"] == "2026-09-26"
    x.calls.clear()
    go(x, store, day2 + timedelta(hours=1))
    assert not [c for c in x.calls if c[0] == "/tweets"], "once a day"
    # A post X will not show us is not the same as a deleted one: it stays until X says "not found".
    x.hidden.add(a)
    go(x, store, day2 + timedelta(days=1))
    assert [n["id"] for n in out(store)["notices"]] == [a], "kept: not being able to see a post is not the same as it being deleted"
    assert S.still_there(x, [a]) == (set(), set())


def test_posts_read_today_are_not_looked_up_again_today():
    x = FakeX()
    a = x.add(datetime(2026, 9, 25, 3, 0, tzinfo=UTC), ESTIMATE)                 # made yesterday
    _, store = go(x, now=datetime(2026, 9, 25, 4, 0, tzinfo=UTC))
    b = x.add(datetime(2026, 9, 26, 1, 0, tzinfo=UTC), SIBU)                     # made today
    x.calls.clear()
    go(x, store, datetime(2026, 9, 26, 2, 0, tzinfo=UTC))
    ids = [c[1]["ids"].split(",") for c in x.calls if c[0] == "/tweets"]
    assert ids == [[a]], "only the post from an earlier day is asked about"


def test_notices_leave_when_they_are_more_than_48_hours_old():
    x = FakeX()
    x.add(NOW - timedelta(hours=2), ESTIMATE)                                    # estimate 21:00 local = 13:00Z
    _, store = go(x)
    go(x, store, NOW + timedelta(hours=40))
    assert len(out(store)["notices"]) == 1, "the estimate passed 24 hours ago: still listed"
    go(x, store, NOW + timedelta(hours=60))
    assert out(store)["notices"] == [] and out(store)["counts"]["posts_seen"] == 0
    # A planned notice for the future is kept until it is over.
    y = FakeX()
    y.add(NOW - timedelta(hours=1), "PLANNED POWER INTERRUPTION - KUCHING: Maintenance works on 30 September 2026, 9.00am to 5.00pm. Affected areas: Taman Sukma.")
    _, store = go(y)
    assert len(out(store)["notices"]) == 1 and out(store)["notices"][0]["type"] == "planned"


def test_failures_write_nothing_and_never_show_the_token():
    def broken(x_failures):
        x = FakeX()
        x.add(NOW - timedelta(hours=1), ESTIMATE)
        x.failures = x_failures
        return x
    cases = {
        "401": {"/users/": Resp(401, {"title": "Unauthorized", "detail": "Unauthorized", "status": 401})},
        "403": {"/users/778899/tweets": Resp(403, {"title": "Forbidden", "detail": "credits exhausted"})},
        "429": {"/users/778899/tweets": Resp(429, {"title": "Too Many Requests"})},
        "500": {"/users/778899/tweets": Resp(500, {})},
        "not json": {"/users/778899/tweets": Resp(200, raw="<html>")},
        "not an object": {"/users/778899/tweets": Resp(200, [1, 2])},
        "no id": {"/users/by/": Resp(200, {"data": {"username": S.HANDLE}})},
        "network": {"/users/778899/tweets": requests.ConnectionError(f"boom {TOKEN}")},
    }
    for name, failures in cases.items():
        x = broken(failures)
        s = S.make_session(TOKEN)
        s.get = x.get                                         # the real session object, with X's answers in place of the network
        store = Store()
        try:
            S.run(store.put, store.get, s, now=NOW)
        except S.SarawakError as e:
            assert TOKEN not in str(e) and "Bearer" not in str(e), name
        else:
            raise AssertionError(f"{name}: should have failed")
        assert store.puts == 0 and store.objs == {}, name
    # A failure after a good run keeps the last good file.
    x = FakeX()
    x.add(NOW - timedelta(hours=1), ESTIMATE)
    _, store = go(x)
    good = json.dumps(store.objs, sort_keys=True)
    x.failures = {"/users/778899/tweets": Resp(500, {})}
    try:
        go(x, store, NOW + timedelta(hours=1))
    except S.SarawakError:
        pass
    assert json.dumps(store.objs, sort_keys=True) == good and store.puts == 1


def test_only_what_might_get_better_is_tried_again():
    x = FakeX()
    x.failures = {"/users/by/": Resp(429, {"title": "Too Many Requests"})}
    try:
        go(x)
    except S.SarawakError:
        pass
    assert len(x.calls) == 1, "a 429 is not hammered"
    x = FakeX()
    x.failures = {"/users/by/": Resp(401, {})}
    try:
        go(x)
    except S.SarawakError:
        pass
    assert len(x.calls) == 1, "a refusal is not retried"
    x = FakeX()
    x.failures = {"/users/by/": Resp(503, {})}
    try:
        go(x)
    except S.SarawakError:
        pass
    assert len(x.calls) == S.ATTEMPTS, "a server error is"
    answers = iter([Resp(502, {}), requests.Timeout("slow"), None])
    y = FakeX()
    y.failures = {"/users/by/": lambda p: next(answers) or Resp(200, {"data": {"id": UID}})}
    go(y)
    assert len(y.calls) >= 3, "and a good answer after two bad ones is used"


def test_the_daily_cap_stops_the_reading_before_any_request():
    x = FakeX()
    x.add(NOW - timedelta(hours=1), ESTIMATE)
    _, store = go(x)
    o = out(store)
    o["state"]["usage"]["2026-09-26"]["posts"] = S.DAILY_POST_CAP
    x.calls.clear()
    try:
        go(x, store, NOW + timedelta(hours=1))
    except S.SarawakError as e:
        assert "daily cap" in str(e)
    else:
        raise AssertionError("should have stopped")
    assert x.calls == []
    try:
        go(x, store, NOW + timedelta(hours=1), force=True)
    except S.SarawakError:
        pass
    else:
        raise AssertionError("--force must not reset the cap")
    res, _ = go(x, store, NOW + timedelta(days=1))
    assert res["result"] == "updated", "a new UTC day starts again"


def test_paging_is_bounded():
    x = FakeX()
    for i in range(5):
        x.add(NOW - timedelta(minutes=10 + i), SIBU, seq=i)
    x.endless = True
    res, _ = go(x)
    assert len(x.timeline_calls()) == S.MAX_PAGES and res["truncated"] is True


def test_usage_and_cost_are_counted_by_the_month():
    x = FakeX()
    a = x.add(datetime(2026, 8, 31, 12, 0, tzinfo=UTC), ESTIMATE)
    _, store = go(x, now=datetime(2026, 8, 31, 13, 0, tzinfo=UTC))
    res, _ = go(x, store, datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
    assert res["cost_month_usd"] == 0.005, "September so far: the re-look at one post, and nothing from August"
    days = {f"2026-07-{d:02d}": {"posts": 1, "users": 0} for d in range(1, 29)}
    store.objs[S.OUT_KEY]["state"]["usage"].update(days)
    go(x, store, datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    assert len(out(store)["state"]["usage"]) <= S.USAGE_DAYS and a


def test_force_starts_again_but_keeps_the_usage():
    x = FakeX()
    x.add(NOW - timedelta(hours=1), ESTIMATE)
    _, store = go(x)
    x.calls.clear()
    go(x, store, NOW + timedelta(minutes=5), force=True)
    assert [c[0] for c in x.calls][0] == f"/users/by/username/{S.HANDLE}" and "start_time" in x.timeline_calls()[0][1]
    assert out(store)["state"]["usage"]["2026-09-26"]["users"] == 2


def test_an_older_or_broken_file_is_not_trusted():
    x = FakeX()
    x.add(NOW - timedelta(hours=1), ESTIMATE)
    for bad in ({"schema": 2, "notices": [], "state": {}}, {"schema": 1, "notices": "no", "state": {}}, {"schema": 1, "notices": []}, "text", [], None):
        store = Store()
        if bad is not None:
            store.objs[S.OUT_KEY] = bad
        x.calls.clear()
        go(x, store)
        assert x.calls[0][0].startswith("/users/by/"), "a file we do not understand starts a fresh read"
        assert len(out(store)["notices"]) == 1


def test_the_switch_and_the_token():
    old = {k: os.environ.pop(k, None) for k in ("SARAWAK_NOTICES", "X_BEARER_TOKEN")}
    try:
        assert S.enabled() is False
        for v in ("on", "ON", " on ", "1", "true", "Yes"):
            os.environ["SARAWAK_NOTICES"] = v
            assert S.enabled() is True, v
        for v in ("", "off", "no", "0", "false", "maybe"):
            os.environ["SARAWAK_NOTICES"] = v
            assert S.enabled() is False, v
        try:
            S.token()
        except S.SarawakError as e:
            assert "X_BEARER_TOKEN" in str(e)
        else:
            raise AssertionError("no token must be an error, not a silent skip")
        os.environ["X_BEARER_TOKEN"] = f"  {TOKEN}\n"
        assert S.token() == TOKEN
        os.environ["X_BEARER_TOKEN"] = "   "
        try:
            S.token()
        except S.SarawakError:
            pass
        else:
            raise AssertionError("a blank token is no token")
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_we_say_who_we_are_and_who_we_are_asking_as():
    s = S.make_session(TOKEN)
    assert "BorneoSky" in s.headers["User-Agent"] and "borneosky.com" in s.headers["User-Agent"]
    assert s.headers["Authorization"] == f"Bearer {TOKEN}"
    assert S.API == "https://api.x.com/2" and S.HANDLE == "1SarawakEnergy" and S.PORTAL_URL == "https://x.com/1SarawakEnergy"


def test_the_sample_costs_what_it_says_and_keeps_nothing_by_itself():
    x = FakeX()
    for i in range(6):
        x.add(NOW - timedelta(hours=i + 1), [ESTIMATE, PROMO, SIBU, ADVICE, NO_TOWN, RESTORED][i], seq=i)
    s = S.sample(x, 20, NOW)
    assert len(s["rows"]) == 6 and s["cost_usd"] == round(0.01 + 6 * 0.005, 3) and s["user_id"] == UID
    assert [r["kept"] for r in s["rows"]] == [True, False, True, False, True, True], "newest first: notice, promo, notice, advice, unread, restored"
    call = x.timeline_calls()[0][1]
    assert call["max_results"] == 20 and call["exclude"] == "retweets,replies"
    assert all("text" not in r for r in s["rows"]), "the rows carry facts; the raw posts are returned apart, for the person tuning"
    assert len(s["posts"]) == 6


if __name__ == "__main__":
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    for name in names:
        globals()[name]()
        print(f"  ok  {name}")
    print(f"{len(names)} checks passed")
