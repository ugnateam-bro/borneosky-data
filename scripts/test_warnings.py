#!/usr/bin/env python3
"""Checks for borneosky/warnings.py with made-up answers shaped like the agencies' feeds. No network, no R2:
    python scripts/test_warnings.py
The continuous-rain texts follow the wording MetMalaysia's warnings have been reported in (news reports, 2025)."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import warnings as W  # noqa: E402

NOW = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)          # 10:00 in Malaysia
REG = W.towns_registry()
passed = 0


def ok(cond, name):
    global passed
    if not cond:
        print(f"  FAIL  {name}")
        sys.exit(1)
    print(f"  ok  {name}")
    passed += 1


def mm(title, text, frm="2026-11-20T08:00:00", to="2026-11-20T20:00:00", heading=None):
    return {"warning_issue": {"issued": frm, "title_en": title, "title_bm": title + " (ms)"}, "valid_from": frm, "valid_to": to,
            "heading_en": heading or title, "text_en": text, "instruction_en": "Stay away from rivers.", "heading_bm": "x", "text_bm": text + " (ms)", "instruction_bm": "y"}


# ── what a warning is ─────────────────────────────────────────────────────────
ok(W.classify_mm("Thunderstorms Warning", "", "Thunderstorms are expected ...")[:2] == ("thunderstorm", "info"), "a thunderstorm warning is routine (info)")
ok(W.classify_mm("Strong Winds and Rough Seas Warning", "", "SECTION A: FOR MALAYSIAN WATERS ...") == ("wind_sea", "info", True), "strong wind and rough seas is a sea warning")
ok(W.classify_mm("Continuous Rain Warning", "", "Danger-level continuous rain is expected over ...")[:2] == ("heavy_rain", "danger"), "danger-level continuous rain is danger")
ok(W.classify_mm("Continuous Rain Warning", "", "Severe continuous rain is expected over ...")[:2] == ("heavy_rain", "warning"), "severe continuous rain is warning level")
ok(W.classify_mm("Continuous Rain Warning", "", "Alert level continuous rain is expected over ...")[:2] == ("heavy_rain", "alert"), "alert-level continuous rain is alert")
ok(W.classify_mm("Continuous Rain Warning", "", "Continuous rain is expected over ...")[:2] == ("heavy_rain", "warning"), "an unlabelled rain warning is never shown lower than a warning")
ok(W.classify_mm("No Advisory", "", "No Tropical Cyclone system")[0] is None and W.classify_mm("Earthquake Warning", "", "x")[0] is None, "'No Advisory' and earthquakes are left out")
ok(W._urgent("heavy_rain", "danger") and W._urgent("heavy_rain", "warning") and not W._urgent("heavy_rain", "alert") and not W._urgent("thunderstorm", "info"), "only warning and danger levels of rain and cyclones are urgent")

# ── which towns ───────────────────────────────────────────────────────────────
SABAH = ("Thunderstorms, heavy rain and strong winds are expected over the states of Kedah (Langkawi) • Sabah: West Coast (Tuaran and Kota Belud), "
         "Kudat (Kudat), Interior (Pensiangan), Tawau (Kalabakan, Tawau, Semporna and Kunak) until 1:00 PM; Saturday, 3 October 2026.")
towns, states, whole = W.match_towns_mm(SABAH, REG)
ok(towns == ["kudat", "semporna", "tawau"] and states == ["Sabah"] and whole == [], "districts named: Kudat, Semporna and Tawau are covered; Kota Kinabalu (West Coast, Tuaran only) is not; Lahad Datu is not")

RAIN = ("Continuous heavy rain is expected over Sarawak: Kuching, Serian, Samarahan, Sarikei (Meradong), Sibu (Sibu and Selangau), Mukah, "
        "Bintulu (Tatau and Bintulu), Miri (Subis), Limbang (Limbang) until Friday, 21 November 2026.")
towns, _, whole = W.match_towns_mm(RAIN, REG)
ok(sorted(towns) == sorted(["kuching", "serian", "samarahan", "mukah", "sibu", "bintulu", "limbang"]),
   "whole divisions named alone cover their town; districts in brackets cover only those districts")
ok("sarikei" not in towns and "miri" not in towns and "lawas" not in towns, "Sarikei (Meradong) does not cover Sarikei town, Miri (Subis) not Miri town, Limbang (Limbang) not Lawas")

towns, _, whole = W.match_towns_mm("Heavy rain is expected over the states of Sarawak • Sabah until tomorrow.", REG)
ok(len(towns) == 22 and "kuching" in towns and "kota-kinabalu" in towns and "labuan" not in towns and whole == ["Sabah", "Sarawak"], "a whole state covers every town in it and is recorded as named whole")
towns, _, _ = W.match_towns_mm("Thunderstorms are expected over the states of Kedah (Langkawi) • Perak until 1 PM", REG)
ok(towns == [], "a peninsula-only warning covers none of our towns")

# ── the MetMalaysia feed ──────────────────────────────────────────────────────
feed = [
    mm("Thunderstorms Warning", SABAH),
    mm("Continuous Rain Warning", RAIN, heading="Danger Level Continuous Rain"),
    mm("Thunderstorms Warning", "Thunderstorms are expected over the states of Kedah (Langkawi) until 1 PM"),
    mm("Strong Winds and Rough Seas Warning", "SECTION A: FOR MALAYSIAN WATERS 1) THUNDERSTORMS WARNING over the waters of Perak • Sarawak • Sabah until 1:00 PM"),
    mm("Thunderstorms Warning", SABAH, frm="2026-11-19T08:00:00", to="2026-11-19T13:00:00"),
    {"warning_issue": {"issued": "2026-11-20T10:30:00", "title_bm": "x", "title_en": "No Advisory"}, "valid_from": None, "valid_to": None,
     "heading_en": "No Advisory", "text_en": "No Tropical Cyclone system", "instruction_en": "", "heading_bm": "", "text_bm": "", "instruction_bm": ""},
]
got = W.mm_warnings(feed, REG, NOW)
ok(len(got) == 3, "kept: the Sabah thunderstorm, the Sarawak rain warning and the sea warning (peninsula-only, expired and 'No Advisory' are dropped)")
rain = next(w for w in got if w["kind"] == "heavy_rain")
ok(rain["level"] == "danger" and rain["urgent"] and rain["states"] == ["sarawak"] and rain["whole"] == [], "the rain warning is danger, urgent, in Sarawak")
ok(rain["valid_from"] == "2026-11-20T00:00:00Z" and rain["valid_to"] == "2026-11-20T12:00:00Z", "Malaysia times (UTC+8) are written as UTC")
sea = next(w for w in got if w["sea"])
ok(sea["towns"] == [] and sea["states"] == ["sabah", "sarawak"], "a sea warning covers no town but names its states")
ok(rain["title"]["ms"].endswith("(ms)") and rain["text"]["en"] and rain["advice"]["en"], "the agency's English and Malay words are kept")

# ── BMKG ──────────────────────────────────────────────────────────────────────
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Thunderstorm This Morning in Sumatera Utara</title><link>https://www.bmkg.go.id/alerts/nowcast/en/CSU1_alert.xml</link></item>
<item><title>Heavy Rain This Afternoon in Kalimantan Barat</title><link>https://www.bmkg.go.id/alerts/nowcast/en/CKB1_alert.xml</link></item></channel></rss>"""
pont = next(t for t in REG if t["slug"] == "pontianak")["point"]


def cap(event, severity, lang="en", lat=pont["lat"], lon=pont["lon"]):
    d = 0.1
    poly = f"{lat-d},{lon-d} {lat-d},{lon+d} {lat+d},{lon+d} {lat+d},{lon-d} {lat-d},{lon-d}"
    return f"""<?xml version="1.0"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>ID1</identifier><sent>2026-11-20T09:40:00+07:00</sent>
<info><language>{lang}</language><event>{event}</event><urgency>Immediate</urgency><severity>{severity}</severity>
<effective>2026-11-20T10:00:00+07:00</effective><expires>2026-11-20T14:00:00+07:00</expires><headline>{event} in Kalimantan Barat</headline>
<description>Heavy rain may lead to localized flooding.</description><web>https://nowcasting.bmkg.go.id/x.jpg</web>
<area><areaDesc>Kalimantan Barat</areaDesc><polygon>{poly}</polygon></area></info></alert>"""


items = W.parse_rss(RSS)
ok(len(items) == 2 and W.kalimantan_province(items[0][0]) is None and W.kalimantan_province(items[1][0]) == "Kalimantan Barat", "only the Kalimantan provinces are read from the BMKG list")
c = W.parse_cap(cap("Heavy Rain", "Severe"))
ok(c["event"] == "Heavy Rain" and len(c["polygons"]) == 1 and c["severity"] == "Severe", "a CAP file is read: event, severity, polygon")
ok(W.in_polygon(pont["lat"], pont["lon"], c["polygons"][0]) and not W.in_polygon(pont["lat"] + 1, pont["lon"], c["polygons"][0]), "point in polygon")
b = W.bmkg_warning(c, W.parse_cap(cap("Hujan Lebat", "Severe", "id")), "Kalimantan Barat", REG, NOW)
ok(b["towns"] == ["pontianak"] and b["kind"] == "heavy_rain" and b["level"] == "warning" and b["urgent"], "a severe heavy-rain alert over Pontianak is urgent and covers Pontianak only")
ok(b["valid_to"] == "2026-11-20T07:00:00Z" and b["title"]["id"] and b["states"] == ["kalimantan"], "BMKG's offsets are written as UTC; the Indonesian words are kept")
t = W.bmkg_warning(W.parse_cap(cap("Thunderstorm", "Moderate")), None, "Kalimantan Barat", REG, NOW)
ok(t["level"] == "info" and not t["urgent"] and t["title"]["id"] == t["title"]["en"], "a moderate thunderstorm is routine; without an Indonesian file the English words stand in")
ok(W.bmkg_warning(W.parse_cap(cap("Heavy Rain", "Severe")), None, "Kalimantan Barat", REG, datetime(2026, 11, 21, tzinfo=timezone.utc)) is None, "an alert that has ended is dropped")
ok(W.classify_cap("Flood", "Moderate") == ("flood", "alert") and W.classify_cap("Heavy Rain", "Extreme") == ("heavy_rain", "danger"), "flood and extreme classes")


# ── a whole run ───────────────────────────────────────────────────────────────
class Resp:
    def __init__(self, body, code=200):
        self.status_code, self._b = code, body

    def json(self):
        return json.loads(self._b)

    @property
    def text(self):
        return self._b


class Session:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kw):
        r = self.routes.get(url)
        if r is None:
            return Resp("", 404)
        if isinstance(r, Exception):
            raise r
        return Resp(r)


MMOK, BMOK = json.dumps(feed), RSS
routes = {W.MM_URL: MMOK, W.BMKG_RSS.format(lang="en"): BMOK,
          "https://www.bmkg.go.id/alerts/nowcast/en/CKB1_alert.xml": cap("Heavy Rain", "Severe"),
          "https://www.bmkg.go.id/alerts/nowcast/id/CKB1_alert.xml": cap("Hujan Lebat", "Severe", "id")}
store = {}
res = W.run(lambda k, o: store.__setitem__(k, o), store.get, Session(routes), NOW)
out = store[W.OUT_KEY]
ok(res["count"] == 4 and res["urgent"] == 2 and not res["errors"], "a full run: four warnings, two urgent")
ok([w["urgent"] for w in out["warnings"]] == [True, True, False, False], "urgent warnings are listed first")
ok(out["sources"]["metmalaysia"]["ok"] and out["sources"]["bmkg"]["ok"] and out["schema"] == 1, "both sources are recorded as read")

del routes[W.BMKG_RSS.format(lang="en")]            # BMKG down: the fake answers 404
res2 = W.run(lambda k, o: store.__setitem__(k, o), store.get, Session(routes), NOW)
out2 = store[W.OUT_KEY]
ok(res2["errors"].keys() == {"bmkg"} and any(w["source"] == "bmkg" for w in out2["warnings"]), "if BMKG cannot be read its earlier warnings stay until they end, and the run says so")
ok(out2["sources"]["bmkg"]["ok"] is False and out2["sources"]["metmalaysia"]["ok"], "the file records which source failed")

late = datetime(2026, 11, 21, 6, 0, tzinfo=timezone.utc)
res3 = W.run(lambda k, o: store.__setitem__(k, o), store.get, Session(routes), late)
ok(not any(w["source"] == "bmkg" for w in store[W.OUT_KEY]["warnings"]), "kept warnings are dropped once they have ended")

before = json.dumps(store[W.OUT_KEY])
try:
    W.run(lambda k, o: store.__setitem__(k, o), store.get, Session({}), NOW)
    ok(False, "both agencies failing must raise")
except W.WarningsError:
    ok(json.dumps(store[W.OUT_KEY]) == before, "both agencies failing raises and writes nothing (R2 keeps the last good file)")

# Every town in AREAS exists in the registry, in the right state.
admin = {t["slug"]: t["admin1"] for t in REG}
ok(all(s in admin for s in W.AREAS) and all(admin[s] in ("Sarawak", "Sabah", "Labuan") for s in W.AREAS), "every town in the area table is a Malaysian town of ours")
ok({s for s, a in admin.items() if a in ("Sarawak", "Sabah", "Labuan")} == set(W.AREAS), "every Sarawak, Sabah and Labuan town has an entry in the area table")
print(f"{passed} checks passed")
