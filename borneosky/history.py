"""Daily history of BorneoSky's data → one SQLite file (for analysis on the owner's own computer).

The hourly ingest overwrites the latest file of each dataset in R2, so nothing is kept. This module reads the PUBLISHED files
(https://data.borneosky.com, the same ones the site reads) and appends what is new to a SQLite database. It never calls the
upstream sources, so it adds no load on them, and it does not depend on where the ingest runs.

What is recorded, and what is deliberately NOT (the terms of each source, docs/history.md and the site repository's
docs/growth/data-history-licences.md):

  recorded   MET Norway forecasts as issued (towns: daily rows and hourly rows at chosen lead times; places: daily rows),
             Copernicus CAMS smoke at town level (+ the grid and wind files as compressed blobs), NASA FIRMS detections,
             MetMalaysia warnings, and the FACTS of SESB and Sarawak Energy outage notices (area, kind, times, stage, id).
  NOT        BMKG (its Terms of Use want written permission; the ingest has it off), flights (AeroDataBox allows caching 7 days
             only), and the TEXT of any outage notice (titles, place lists): facts and the post id only.

Everything is idempotent: a primary key per row, INSERT OR IGNORE, so running it twice, or after a gap, never duplicates.
Times are kept as the files give them (UTC, 'YYYY-MM-DDTHH:MM:SSZ'), notice times as local ISO strings with their offset.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable

SCHEMA_VERSION = 1
BASE_URL = "https://data.borneosky.com"

# Hours ahead of the model run at which hourly forecast rows are kept: the first hours act as a "nowcast" series, the later ones let
# the forecast be compared with what came later. Every hour of every run would be about 12,000 rows per run.
KEEP_LEADS = (1, 2, 3, 6, 12, 24, 36, 48)
# MetMalaysia only. BMKG's warnings are never recorded (see the module docstring), whatever a file holds.
WARNING_SOURCES = ("metmalaysia",)

DDL = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, started_utc TEXT NOT NULL, dataset TEXT NOT NULL, source_generated_utc TEXT,
  result TEXT NOT NULL, rows_added INTEGER NOT NULL DEFAULT 0, detail TEXT);
CREATE INDEX IF NOT EXISTS runs_dataset ON runs (dataset, id);
CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT);

CREATE TABLE IF NOT EXISTS forecast_town_daily (
  town TEXT NOT NULL, model_utc TEXT NOT NULL, day TEXT NOT NULL, tmin REAL, tmax REAL, rain_mm REAL, wind_max REAL,
  symbol TEXT, hours REAL, partial INTEGER, resolution TEXT, PRIMARY KEY (town, model_utc, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS forecast_town_hourly (
  town TEXT NOT NULL, model_utc TEXT NOT NULL, valid_utc TEXT NOT NULL, lead_h INTEGER NOT NULL, temp REAL, rh REAL,
  wind REAL, wind_dir REAL, cloud REAL, rain_mm REAL, symbol TEXT, PRIMARY KEY (town, model_utc, valid_utc)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS forecast_place_daily (
  place TEXT NOT NULL, model_utc TEXT NOT NULL, day TEXT NOT NULL, tmin REAL, tmax REAL, rain_am_mm REAL, rain_pm_mm REAL,
  thunder INTEGER, wind_max REAL, symbol TEXT, smoke_max INTEGER, PRIMARY KEY (place, model_utc, day)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS smoke_town (
  town TEXT NOT NULL, run_utc TEXT NOT NULL, valid_utc TEXT NOT NULL, pm25 INTEGER, aod INTEGER,
  PRIMARY KEY (town, run_utc, valid_utc)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS blobs (kind TEXT NOT NULL, run_utc TEXT NOT NULL, gz BLOB NOT NULL, PRIMARY KEY (kind, run_utc)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS hotspots (
  t TEXT NOT NULL, sat TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, conf TEXT, frp REAL, night INTEGER, region TEXT,
  town TEXT, km REAL, first_seen_utc TEXT NOT NULL, PRIMARY KEY (t, sat, lat, lon)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS warnings (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT, level TEXT, urgent INTEGER, sea INTEGER, states TEXT, towns TEXT,
  title_en TEXT, title_ms TEXT, text_en TEXT, issued_utc TEXT, valid_from_utc TEXT, valid_to_utc TEXT,
  first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS notices (
  provider TEXT NOT NULL, id TEXT NOT NULL, type TEXT, area TEXT, kind TEXT, stage TEXT, start_local TEXT, end_local TEXT,
  published_utc TEXT, done INTEGER, restored INTEGER, critical INTEGER, duration_h REAL,
  first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL, PRIMARY KEY (provider, id)) WITHOUT ROWID;
"""


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(DDL)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()
    return conn


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _b(v) -> int | None:
    return None if v is None else int(bool(v))


# ── one handler per dataset: (conn, document, now_iso) -> None; rows are counted from conn.total_changes ──────────────────────

def town_forecast(conn, doc: dict, now: str) -> None:
    town, model = doc["town"], doc.get("model_updated_utc") or doc["generated_utc"]
    conn.executemany(
        "INSERT OR IGNORE INTO forecast_town_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(town, model, d["date"], d.get("tmin"), d.get("tmax"), d.get("rain"), d.get("wind_max"), d.get("symbol"),
          d.get("hours"), _b(d.get("partial")), d.get("resolution")) for d in doc.get("daily", [])])
    base = _parse(model).replace(minute=0, second=0, microsecond=0)
    rows = []
    for h in doc.get("hourly", []):
        lead = round((_parse(h["t"]) - base).total_seconds() / 3600)
        if lead in KEEP_LEADS:
            rows.append((town, model, h["t"], lead, h.get("temp"), h.get("rh"), h.get("wind"), h.get("wind_dir"), h.get("cloud"),
                         h.get("rain"), h.get("symbol")))
    conn.executemany("INSERT OR IGNORE INTO forecast_town_hourly VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)


def place_forecasts(conn, doc: dict, now: str) -> None:
    rows = []
    for slug, p in doc.get("places", {}).items():
        model = p.get("model_updated_utc") or doc["generated_utc"]
        for d in p.get("days", []):
            rows.append((slug, model, d["date"], d.get("tmin"), d.get("tmax"), d.get("rain_am"), d.get("rain_pm"),
                         _b(d.get("thunder")), d.get("wind_max"), d.get("symbol"), d.get("smoke_max")))
    conn.executemany("INSERT OR IGNORE INTO forecast_place_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)


def smoke_towns(conn, doc: dict, now: str) -> None:
    run, times = doc["run_utc"], doc["times"]
    rows = []
    for slug, t in doc.get("towns", {}).items():
        for i, valid in enumerate(times):
            rows.append((slug, run, valid, t["pm25"][i] if i < len(t.get("pm25", [])) else None,
                         t["aod"][i] if i < len(t.get("aod", [])) else None))
    conn.executemany("INSERT OR IGNORE INTO smoke_town VALUES (?,?,?,?,?)", rows)


def blob(kind: str) -> Callable:
    def handler(conn, doc: dict, now: str) -> None:
        body = gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode(), compresslevel=9, mtime=0)
        conn.execute("INSERT OR IGNORE INTO blobs VALUES (?,?,?)", (kind, doc["run_utc"], body))
    return handler


def hotspots(conn, doc: dict, now: str) -> None:
    rows = []
    for f in doc.get("features", []):
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"][:2]
        rows.append((p["t"], p["sat"], lat, lon, p.get("c"), p.get("frp"), p.get("n"), p.get("r"), p.get("town"), p.get("km"), now))
    conn.executemany("INSERT OR IGNORE INTO hotspots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)


def warnings(conn, doc: dict, now: str) -> int:
    """Returns the number of NEW warnings (a warning seen again only has its last-seen time and end moved)."""
    added = 0
    for w in doc.get("warnings", []):
        if w.get("source") not in WARNING_SOURCES:
            continue
        states, towns = json.dumps(w.get("states", [])), json.dumps(w.get("towns", []))
        cur = conn.execute(
            "INSERT OR IGNORE INTO warnings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (w["id"], w["source"], w.get("kind"), w.get("level"), _b(w.get("urgent")), _b(w.get("sea")), states, towns,
             (w.get("title") or {}).get("en"), (w.get("title") or {}).get("ms"), (w.get("text") or {}).get("en"),
             w.get("issued"), w.get("valid_from"), w.get("valid_to"), now, now))
        added += cur.rowcount
        if not cur.rowcount:
            conn.execute("UPDATE warnings SET last_seen_utc=?, level=?, valid_to_utc=?, towns=? WHERE id=?", (now, w.get("level"), w.get("valid_to"), towns, w["id"]))
    return added


def notices(conn, doc: dict, now: str) -> int:
    """FACTS only (area, kind, stage, times, ids). Never the title or the list of places: that is the utility's text, and for
    Sarawak Energy X allows keeping only Post IDs (the id of a Sarawak notice is its post's id). Returns the number of NEW notices."""
    provider, added = doc["provider"], 0
    for n in doc.get("notices", []):
        cur = conn.execute(
            "INSERT OR IGNORE INTO notices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (provider, str(n["id"]), n.get("type"), n.get("area"), n.get("kind"), n.get("stage"), n.get("start"), n.get("end"),
             n.get("published"), _b(n.get("done")), _b(n.get("restored")), _b(n.get("critical")), n.get("duration"), now, now))
        added += cur.rowcount
        if not cur.rowcount:
            conn.execute("UPDATE notices SET last_seen_utc=?, end_local=?, stage=?, done=?, restored=? WHERE provider=? AND id=?",
                         (now, n.get("end"), n.get("stage"), _b(n.get("done")), _b(n.get("restored")), provider, str(n["id"])))
    return added


# (dataset, path, handler). Paths are relative to the data base URL.
SINGLE = [
    ("places", "places/index.json", place_forecasts),
    ("smoke_towns", "smoke/towns.json", smoke_towns),
    ("smoke_grid", "smoke/grid.json", blob("smoke_grid")),
    ("wind", "smoke/wind.json", blob("wind")),
    ("hotspots", "current/hotspots.geojson", hotspots),
    ("warnings", "warnings/current.json", warnings),
    ("notices_sesb", "notices/sesb.json", notices),
    ("notices_sarawak", "notices/sarawak-energy.json", notices),
]


class NotModified(Exception):
    pass


# path -> parsed JSON; raises NotModified when the file has not changed since the last run that recorded it.
# A fetcher may also have .confirm(path): called once the document was recorded, so a failure is retried next time.
Fetch = Callable[[str], dict]


def _log(conn, dataset: str, started: str, generated: str | None, result: str, rows: int, detail: str | None = None) -> None:
    conn.execute("INSERT INTO runs (started_utc, dataset, source_generated_utc, result, rows_added, detail) VALUES (?,?,?,?,?,?)",
                 (started, dataset, generated, result, rows, detail))
    conn.commit()


def _run_one(conn, dataset: str, started: str, fetch: Fetch, path: str, handler: Callable, log: bool = True) -> tuple[str, int]:
    """One dataset in its own transaction: a failure writes nothing for it and is logged; the others carry on."""
    before = conn.total_changes
    try:
        doc = fetch(path)
        ret = handler(conn, doc, started)
        rows = ret if isinstance(ret, int) else conn.total_changes - before
        conn.commit()
        getattr(fetch, "confirm", lambda p: None)(path)
        if log:
            _log(conn, dataset, started, doc.get("generated_utc") or doc.get("run_utc"), "ok", rows)
        return "ok", rows
    except NotModified:
        if log:
            _log(conn, dataset, started, None, "unchanged", 0)
        return "unchanged", 0
    except Exception as e:  # noqa: BLE001 - one broken dataset must not stop the rest
        conn.rollback()
        _log(conn, dataset, started, None, "error", 0, f"{dataset}: {type(e).__name__}: {str(e)[:200]}")
        return "error", 0


def record(conn: sqlite3.Connection, fetch: Fetch, now: datetime | None = None) -> dict[str, tuple[str, int]]:
    """Reads every dataset through `fetch` and appends what is new. Returns {dataset: (result, rows added)}."""
    started = iso(now or datetime.now(timezone.utc))
    out: dict[str, tuple[str, int]] = {}
    for dataset, path, handler in SINGLE:
        out[dataset] = _run_one(conn, dataset, started, fetch, path, handler)
    # The 44 town forecasts: the list of towns comes from the forecast metadata file. One summary line for all of them.
    try:
        slugs = sorted(fetch("forecast/_meta.json")["towns"])
    except NotModified:
        slugs = [r[0] for r in conn.execute("SELECT DISTINCT town FROM forecast_town_daily")]
    except Exception as e:  # noqa: BLE001
        _log(conn, "forecast_towns", started, None, "error", 0, f"forecast/_meta.json: {type(e).__name__}: {str(e)[:200]}")
        out["forecast_towns"] = ("error", 0)
        return out
    results = [_run_one(conn, f"forecast_town:{s}", started, fetch, f"forecast/districts/{s}.json", town_forecast, log=False) for s in slugs]
    failed = sum(1 for r, _ in results if r == "error")
    state = "ok" if not failed else ("error" if failed == len(results) else "partial")
    rows = sum(n for _, n in results)
    _log(conn, "forecast_towns", started, None, state, rows, f"{len(results) - failed} of {len(results)} towns")
    out["forecast_towns"] = (state, rows)
    return out


def http_fetcher(base: str = BASE_URL, conn: sqlite3.Connection | None = None, user_agent: str = "BorneoSky-history/1.0") -> Fetch:
    """Fetch over HTTPS. With a database, the ETag and Last-Modified of each file are kept (after it was recorded), so an unchanged file
    costs a 304 and nothing else."""
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    pending: dict[str, tuple[str | None, str | None]] = {}

    def fetch(path: str) -> dict:
        url = f"{base.rstrip('/')}/{path}"
        headers = {}
        if conn is not None:
            row = conn.execute("SELECT etag, last_modified FROM http_cache WHERE url=?", (url,)).fetchone()
            if row:
                if row[0]:
                    headers["If-None-Match"] = row[0]
                if row[1]:
                    headers["If-Modified-Since"] = row[1]
        r = session.get(url, headers=headers, timeout=30)
        if r.status_code == 304:
            raise NotModified()
        r.raise_for_status()
        pending[path] = (r.headers.get("ETag"), r.headers.get("Last-Modified"))
        return r.json()

    def confirm(path: str) -> None:
        if conn is None or path not in pending:
            return
        etag, lm = pending.pop(path)
        conn.execute("INSERT INTO http_cache VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, last_modified=excluded.last_modified",
                     (f"{base.rstrip('/')}/{path}", etag, lm))
        conn.commit()

    fetch.confirm = confirm  # type: ignore[attr-defined]
    return fetch


def dir_fetcher(root: str) -> Fetch:
    """Read the files from a folder instead (a local `out/` or a download), for tests and for filling the database by hand."""
    import os

    def fetch(path: str) -> dict:
        with open(os.path.join(root, path), "rb") as f:
            body = f.read()
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
        return json.loads(body)

    return fetch
