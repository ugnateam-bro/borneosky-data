#!/usr/bin/env python3
"""Checks for borneosky/history.py with made-up files shaped like the published ones. No network, no R2:
    python scripts/test_history.py"""

import copy
import gzip
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from borneosky import history as H  # noqa: E402

NOW = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)
passed = 0


def ok(cond, name):
    global passed
    if not cond:
        print(f"  FAIL  {name}")
        sys.exit(1)
    print(f"  ok  {name}")
    passed += 1


def hours(model="2026-10-05T13:18:08Z", n=50):
    start = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
    return [{"t": (start.replace(hour=14) if i == 0 else datetime.fromtimestamp(start.timestamp() + i * 3600, timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "temp": 25.0 + i / 10, "rh": 90.0, "wind": 1.5, "wind_dir": 200.0, "cloud": 80.0, "rain": 0.0, "symbol": "cloudy"} for i in range(n)]


TOWN = {"town": "kuching", "name": "Kuching", "timezone": "Asia/Kuching", "generated_utc": "2026-10-05T15:24:18Z", "model_updated_utc": "2026-10-05T13:18:08Z",
        "hourly": hours(), "daily": [{"date": "2026-10-05", "tmin": 25.1, "tmax": 30.0, "rain": 0.0, "wind_max": 1.7, "symbol": "cloudy", "hours": 2.0, "partial": True, "resolution": "6-hourly"},
                                     {"date": "2026-10-06", "tmin": 24.0, "tmax": 31.0, "rain": 4.2, "wind_max": 3.0, "symbol": "rain", "hours": 24.0, "partial": False, "resolution": "hourly"}]}
FILES = {
    "forecast/_meta.json": {"towns": {"kuching": {}}, "generated_utc": "x"},
    "forecast/districts/kuching.json": TOWN,
    "places/index.json": {"generated_utc": "2026-10-05T15:26:22Z", "places": {"bako": {"model_updated_utc": "2026-10-05T13:19:04Z", "days": [
        {"date": "2026-10-06", "tmin": 24, "tmax": 31, "rain_am": 1.1, "rain_pm": 0.2, "thunder": False, "wind_max": 4.0, "symbol": "rain", "smoke_max": 27}]}}},
    "smoke/towns.json": {"generated_utc": "2026-10-05T10:25:10Z", "run_utc": "2026-10-05T00:00:00Z", "times": ["2026-10-05T00:00:00Z", "2026-10-05T03:00:00Z"],
                         "towns": {"kuching": {"pm25": [31, 28], "aod": [156, 162], "daily": []}}},
    "smoke/grid.json": {"generated_utc": "g", "run_utc": "2026-10-05T00:00:00Z", "pm25": [[1, 2]]},
    "smoke/wind.json": {"generated_utc": "g", "run_utc": "2026-10-05T00:00:00Z", "u": [[1, 2]]},
    "current/hotspots.geojson": {"type": "FeatureCollection", "generated_utc": "2026-10-05T15:24:08Z", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [111.9584, -2.0451]},
         "properties": {"t": "2026-10-05T12:49Z", "sat": "T", "c": "n", "frp": 22.0, "n": 1, "r": "Kalimantan Tengah", "town": "pangkalan-bun", "km": 80.0}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [110.1, 1.5]},
         "properties": {"t": "2026-10-05T13:10Z", "sat": "N20", "c": "h", "frp": 5.0, "n": 0, "r": "Sarawak", "town": "kuching", "km": 20.0}}]},
    "warnings/current.json": {"schema": 1, "generated_utc": "w", "warnings": [
        {"id": "mm-1", "source": "metmalaysia", "kind": "heavy_rain", "level": "warning", "urgent": True, "sea": False, "states": ["sarawak"], "towns": ["kuching"],
         "title": {"en": "Heavy rain", "ms": "Hujan lebat"}, "text": {"en": "text"}, "issued": "2026-10-05T13:40:00Z", "valid_from": "2026-10-05T13:00:00Z", "valid_to": "2026-10-05T17:00:00Z"},
        {"id": "bk-1", "source": "bmkg", "kind": "heavy_rain", "level": "warning", "urgent": True, "sea": False, "states": ["kalimantan"], "towns": ["pontianak"],
         "title": {"en": "BMKG heavy rain"}, "text": {"en": "BMKG TEXT"}, "issued": "2026-10-05T13:40:00Z", "valid_from": "2026-10-05T13:00:00Z", "valid_to": "2026-10-05T17:00:00Z"}]},
    "notices/sesb.json": {"provider": "sesb", "generated_utc": "s", "notices": [
        {"id": "0949e2cb9f", "type": "short_notice", "area": "Pitas", "start": "2026-10-05T10:00:00+08:00", "end": "2026-10-05T17:00:00+08:00", "kind": "line_clearing",
         "title": "SECRET-TITLE-SESB", "places": "SECRET-PLACES-SESB", "done": False, "published": "2026-10-02T16:00:04Z", "duration": 7, "critical": True}]},
    "notices/sarawak-energy.json": {"provider": "sarawak_energy", "generated_utc": "x", "notices": [
        {"id": "2107017543877877791", "type": "unplanned", "area": "Sarikei", "start": "2026-10-05T15:58:23+08:00", "end": "2026-10-05T20:00:00+08:00", "kind": "other",
         "stage": "repair", "places": "SECRET-PLACES-SWK", "more": False, "update": False, "restored": False, "published": "2026-10-05T07:58:23Z", "readable": True}]},
}


class Fake:
    """Serves FILES, can break some, and records what was confirmed."""

    def __init__(self, files, broken=()):
        self.files, self.broken, self.confirmed, self.asked = files, set(broken), [], []

    def __call__(self, path):
        self.asked.append(path)
        if path in self.broken:
            raise RuntimeError("HTTP 500")
        return copy.deepcopy(self.files[path])

    def confirm(self, path):
        self.confirmed.append(path)


def fresh():
    return H.open_db(":memory:")


def n(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ── the schema ──
c = fresh()
H.open_db(":memory:")
ok(c.execute("PRAGMA user_version").fetchone()[0] == H.SCHEMA_VERSION, "the database carries its schema version")
tmp = tempfile.mkdtemp()
db_path = str(Path(tmp) / "h.db")
H.open_db(db_path).close()
H.open_db(db_path).close()
ok(True, "opening the same file twice is harmless")

# ── a full run ──
c = fresh()
res = H.record(c, Fake(FILES), NOW)
ok(all(r[0] == "ok" for r in res.values()), f"every dataset is read: {res}")
ok(n(c, "forecast_town_daily") == 2, "the daily rows of a town forecast are kept")
leads = [r[0] for r in c.execute("SELECT lead_h FROM forecast_town_hourly ORDER BY lead_h")]
ok(leads == [2, 3, 6, 12, 24, 36, 48] or set(leads) <= set(H.KEEP_LEADS), f"hourly rows only at the chosen lead times: {leads}")
ok(n(c, "forecast_place_daily") == 1 and n(c, "smoke_town") == 2 and n(c, "blobs") == 2 and n(c, "hotspots") == 2, "places, smoke, the two blobs and the detections are kept")
blob = c.execute("SELECT gz FROM blobs WHERE kind='wind'").fetchone()[0]
ok(json.loads(gzip.decompress(blob))["u"] == [[1, 2]], "a blob holds the file, gzipped")
hs = c.execute("SELECT lat, lon, night, town FROM hotspots WHERE sat='T'").fetchone()
ok(hs == (-2.0451, 111.9584, 1, "pangkalan-bun"), "a detection keeps its position (latitude first), night flag and nearest town")

# ── idempotent, and only new rows when something changes ──
before = {t: n(c, t) for t in ("forecast_town_daily", "forecast_town_hourly", "forecast_place_daily", "smoke_town", "blobs", "hotspots", "warnings", "notices")}
res2 = H.record(c, Fake(FILES), NOW)
ok(all(before[t] == n(c, t) for t in before) and sum(r for _, r in res2.values()) == 0, "reading the same files again adds nothing")
files2 = copy.deepcopy(FILES)
files2["forecast/districts/kuching.json"]["model_updated_utc"] = "2026-10-05T14:20:00Z"
files2["current/hotspots.geojson"]["features"].append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [111.0, 0.5]},
                                                        "properties": {"t": "2026-10-05T14:00Z", "sat": "SNPP", "c": "l", "frp": 1.0, "n": 0, "r": "Sarawak", "town": "sibu", "km": 9.0}})
H.record(c, Fake(files2), NOW)
ok(n(c, "forecast_town_daily") == 4 and n(c, "hotspots") == 3 and n(c, "smoke_town") == 2, "a new model run and a new detection add rows; unchanged files add none")

# ── what must never be recorded ──
ok(c.execute("SELECT count(*) FROM warnings WHERE source!='metmalaysia'").fetchone()[0] == 0 and n(c, "warnings") == 1, "BMKG warnings are not recorded, MetMalaysia ones are")
dump = json.dumps([list(r) for t in ("warnings", "notices", "hotspots") for r in c.execute(f"SELECT * FROM {t}")])
ok("BMKG TEXT" not in dump and "SECRET" not in dump, "no BMKG text and no outage-notice text (titles, place lists) anywhere in the database")
ok([r[1] for r in c.execute("PRAGMA table_info(notices)") if r[1] in ("title", "places", "text")] == [], "the notices table has no column for a notice's words")
ok(not any("flight" in p for _, p, _ in H.SINGLE) and "bmkg" not in H.WARNING_SOURCES, "flights are not a dataset and BMKG is not an allowed warning source")
ok(c.execute("SELECT id, provider FROM notices WHERE provider='sarawak_energy'").fetchone() == ("2107017543877877791", "sarawak_energy"), "a Sarawak Energy notice is kept by its post id and facts")

# ── one broken dataset does not stop the others, and writes nothing ──
c = fresh()
res = H.record(c, Fake(FILES, broken={"smoke/towns.json", "warnings/current.json"}), NOW)
ok(res["smoke_towns"][0] == "error" and res["warnings"][0] == "error" and res["hotspots"][0] == "ok" and res["forecast_towns"][0] == "ok", "a failing file is an error for that dataset only")
ok(n(c, "smoke_town") == 0 and n(c, "warnings") == 0 and n(c, "hotspots") == 2, "the failed datasets wrote nothing, the others did")
ok(c.execute("SELECT count(*) FROM runs WHERE result='error'").fetchone()[0] == 2, "each failure is logged in the runs table")
bad = copy.deepcopy(FILES)
del bad["smoke/towns.json"]["run_utc"]            # half-readable: fails inside the handler, after the file was read
f = Fake(bad)
c = fresh()
res = H.record(c, f, NOW)
ok(res["smoke_towns"][0] == "error" and "smoke/towns.json" not in f.confirmed and "places/index.json" in f.confirmed, "a file that failed while being recorded is not confirmed, so it is read again next time")
ok(H.record(fresh(), Fake(FILES, broken={"forecast/_meta.json"}), NOW)["forecast_towns"][0] == "error", "without the list of towns the town forecasts report an error")
res = H.record(fresh(), Fake(FILES, broken={"forecast/districts/kuching.json"}), NOW)
ok(res["forecast_towns"][0] == "error", "all towns failing is an error")

# ── a not-modified file is a quiet skip ──
class Quiet(Fake):
    def __call__(self, path):
        if path == "smoke/grid.json":
            raise H.NotModified()
        return super().__call__(path)


c = fresh()
res = H.record(c, Quiet(FILES), NOW)
ok(res["smoke_grid"] == ("unchanged", 0) and res["wind"][0] == "ok", "a file that has not changed is skipped quietly")

# ── a local folder can be read too ──
d = Path(tempfile.mkdtemp())
for path, doc in FILES.items():
    (d / path).parent.mkdir(parents=True, exist_ok=True)
    (d / path).write_bytes(gzip.compress(json.dumps(doc).encode()) if path.endswith("grid.json") else json.dumps(doc).encode())
c = fresh()
res = H.record(c, H.dir_fetcher(str(d)), NOW)
ok(all(r[0] == "ok" for r in res.values()) and n(c, "blobs") == 2, "the files can be read from a folder, plain or gzipped")

# ── the backup copy ──
live = H.open_db(db_path)
H.record(live, Fake(FILES), NOW)
live.close()
out = subprocess.run([sys.executable, str(HERE / "backup_history.py"), "--db", db_path, "--local-only"], capture_output=True, text=True)
gz = Path(tmp) / "history-backup.db.gz"
ok(out.returncode == 0 and gz.is_file(), f"the backup script writes a gzipped copy: {out.stdout.strip()} {out.stderr.strip()}")
restored = Path(tmp) / "restored.db"
restored.write_bytes(gzip.decompress(gz.read_bytes()))
r = sqlite3.connect(restored)
ok(r.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and r.execute("SELECT count(*) FROM hotspots").fetchone()[0] == 2, "the copy opens, passes the integrity check and holds the data")

print(f"{passed} checks passed")
