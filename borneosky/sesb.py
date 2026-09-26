"""Sabah outage notices → R2, read from SESB's own outage portal.

https://mysesb.com.my/Outage/ lists planned works and "unplanned" (short-notice) works, and loads two plain
files to do it:

  /Outage/json/PlannedOutage.json       {"Table": [ {...}, ... ]}, every notice since August 2021
  /Outage/json/UnplannedOutage.json     the same shape

Both are rebuilt once a day (about 16:00 UTC, which is midnight in Malaysia). They are notices, not live
faults: read as UTC their timestamps put a notice about 57 hours ahead of the work (median, 2026). This module
keeps the notices that are current, coming, or ended in the last 48 hours, and writes one small file:

  notices/sesb.json     the notices, when SESB's files last changed, and when we last got an answer

The site reads that file. It never reads SESB directly.

How we behave towards the portal: two requests an hour, both conditional (If-None-Match and If-Modified-Since;
the server answers 304 with no body while nothing has changed, so the files are downloaded about once a day),
with an identifying User-Agent. The files are not a documented interface, so their shape is checked on every
download: a change of shape fails the run and keeps the last good file, and so does any error.

Not switched on until the repository variable SESB_NOTICES is "on" (scripts/ingest_sesb.py). Notes on the
rules, the permission letter and the wording: docs/sesb-notices.md.
"""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

from .config import user_agent

PORTAL_URL = "https://mysesb.com.my/Outage/"
BASE_URL = "https://mysesb.com.my/Outage/json/"
FILES = (("planned", "PlannedOutage.json"), ("short_notice", "UnplannedOutage.json"))
OUT_KEY = "notices/sesb.json"
SCHEMA = 1

LOCAL = ZoneInfo("Asia/Kuching")            # Malaysia time, UTC+8, no daylight saving
KEEP_AFTER_END = timedelta(hours=48)        # a finished notice stays listed for two days
SOURCE_STALE_AFTER = timedelta(hours=72)    # SESB rebuilds daily; three days of silence is a fault at their end
MAX_PLACES = 1500                           # characters of "areas affected" kept (the longest in 2026 was 3,342)
TIMEOUT = 30
ATTEMPTS = 3

# What each title is, checked in this order on the upper-cased title. Only the words that decide it are matched, so
# small changes to a title (an extra bracket, "KHAS") do not change its kind. Anything else is "other" and the site
# shows SESB's own words.
KINDS = (
    ("system_strengthening", r"PENGUKUHAN SISTEM"),
    ("new_connection", r"PENYAMBUNGAN"),
    ("hv_upgrade", r"MENAIKTARAF.*VOLTAN TINGGI"),
    ("lv_upgrade", r"MENAIKTARAF.*VOLTAN RENDAH"),
    ("repair", r"PEMBAIKAN"),
    ("line_clearing", r"RENTIS"),
    ("relocation", r"PENGALIHAN"),
    ("substation_maintenance", r"SENGGARAAN PENCAWANG"),
    ("hv_line_maintenance", r"SENGGARAAN SISTEM TALIAN ATAS VOLTAN TINGGI"),
    ("lv_line_maintenance", r"SENGGARAAN SISTEM TALIAN ATAS VOLTAN RENDAH"),
    ("cable_maintenance", r"SENGGARAAN KABEL BAWAH TANAH"),
    ("transmission_maintenance", r"SENGGARAAN PENGHANTARAN"),
    ("streetlight_maintenance", r"LAMPU JALAN"),
    ("protection_setting", r"UFLS"),
)
KIND_CODES = tuple(k for k, _ in KINDS) + ("other",)
# "Critical" or "emergency" in SESB's own words. ("SPECIAL/CRITICAL PROJECT" is the name of a project type, not urgency.)
CRITICAL = re.compile(r"KRITIKAL|KECEMASAN")
NO_AREAS = {"", "NIL", "-", "N/A", "TIADA"}


class SesbError(Exception):
    """The portal could not be read, or its files no longer look the way this module expects."""


def enabled() -> bool:
    """The step runs only when the repository variable SESB_NOTICES is "on" (a kill switch that needs no deploy)."""
    return os.environ.get("SESB_NOTICES", "").strip().lower() in ("on", "1", "true", "yes")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent(), "Accept": "application/json"})
    return s


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Reading the portal ────────────────────────────────────────────────────

def fetch(session, name: str, validators: dict) -> tuple[str, bytes | None, dict]:
    """Conditional GET of one file. Returns ("unchanged", None, validators) on 304, or ("changed", body,
    {"etag": ..., "last_modified": ...}) on 200. Anything else is retried, then raises SesbError."""
    headers = {}
    if validators.get("etag"):
        headers["If-None-Match"] = validators["etag"]
    if validators.get("last_modified"):
        headers["If-Modified-Since"] = validators["last_modified"]
    problem = "no answer"
    for attempt in range(ATTEMPTS):
        try:
            r = session.get(BASE_URL + name, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            problem = type(e).__name__
        else:
            if r.status_code == 304 and headers:
                return "unchanged", None, validators
            if r.status_code == 200:
                return "changed", r.content, {"etag": r.headers.get("ETag"), "last_modified": r.headers.get("Last-Modified")}
            problem = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                break                                   # a client error will not get better by asking again
        if attempt + 1 < ATTEMPTS:
            time.sleep(2 * (attempt + 1))
    raise SesbError(f"{name}: {problem}")


def parse_table(content: bytes, name: str) -> list:
    """The rows of one file. A different top-level shape, or no rows at all, is an error, never "no notices"."""
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except ValueError:
        raise SesbError(f"{name}: not JSON") from None
    table = data.get("Table") if isinstance(data, dict) else None
    if not isinstance(table, list) or not table:
        raise SesbError(f"{name}: no rows under 'Table'")
    return table


# ── One notice ────────────────────────────────────────────────────────────

_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?")


def _stamp(value, tz) -> datetime | None:
    m = _STAMP.match(value) if isinstance(value, str) else None
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x or 0) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=tz)
    except ValueError:
        return None


def clean(value) -> str:
    """One line of plain text: whitespace collapsed, control characters gone."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))).strip()


def classify(title: str) -> tuple[str, bool]:
    upper = re.sub(r"\s+", " ", title.upper())
    for kind, pattern in KINDS:
        if re.search(pattern, upper):
            return kind, bool(CRITICAL.search(upper))
    return "other", bool(CRITICAL.search(upper))


def normalise_area(name: str) -> str:
    """SESB's district as we show it: "W.P.LABUAN" is Labuan; capitals become capitalised words."""
    name = re.sub(r"^(?:W\.\s*P\.\s*|WP\s+)", "", name, flags=re.I).strip()
    return name.title() if name.isupper() else name


def places_text(value) -> tuple[str, bool]:
    """The affected places as SESB wrote them (capitals and all; the site tidies the case), cut at a comma if
    very long. "NIL" and its like mean SESB listed none."""
    text = clean(value)
    if text.upper() in NO_AREAS:
        return "", False
    if len(text) <= MAX_PLACES:
        return text, False
    cut = text[:MAX_PLACES]
    stop = max(cut.rfind(","), cut.rfind(";"))
    return (cut[:stop] if stop > MAX_PLACES // 2 else cut).rstrip(" ,;"), True


def duration_hours(value) -> int | None:
    """SESB's own "Duration (in hours)", when it is a plain whole number of hours. It is shown as SESB gave it (in 2026
    it differs from end minus start only by rounding up). Anything else is left out, and the site works the length
    out from the two times."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 24 * 31 else None


def make_notice(row, kind_of_file: str) -> dict | None:
    """One row of either file as one notice, or None if it cannot be used (counted by the caller)."""
    if not isinstance(row, dict):
        return None
    area = normalise_area(clean(row.get("Area")))
    start, end = _stamp(row.get("StartDateTime"), LOCAL), _stamp(row.get("EndDateTime"), LOCAL)
    title = clean(row.get("ShutdownPurpose"))
    if not area or not start or not end or end <= start or not title:
        return None
    kind, critical = classify(title)
    places, cut = places_text(row.get("AffectedArea"))
    published = _stamp(row.get("CreatedDate"), timezone.utc)      # the server's clock: its daily batch is stamped 16:00
    key = f"{kind_of_file}|{area}|{start.isoformat()}|{end.isoformat()}|{places}"
    notice = {
        "id": hashlib.sha1(key.encode()).hexdigest()[:10],
        "type": kind_of_file,
        "area": area,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "kind": kind,
        "title": title,
        "places": places,
        "done": row.get("Status") is True,
        "published": _iso(published) if published else None,
    }
    duration = duration_hours(row.get("Duration"))
    if duration is not None:
        notice["duration"] = duration
    if critical:
        notice["critical"] = True
    if cut:
        notice["places_cut"] = True
    return notice


def build(tables: dict, now: datetime) -> tuple[list, dict]:
    """All notices worth listing at `now`, oldest start first, and counts for the status line."""
    floor = now - KEEP_AFTER_END
    seen, notices = set(), []
    counts = {"planned": 0, "short_notice": 0, "skipped": 0, "duplicates": 0}
    for kind_of_file, rows in tables.items():
        for row in rows:
            n = make_notice(row, kind_of_file)
            if n is None:
                counts["skipped"] += 1
                continue
            if datetime.fromisoformat(n["end"]) < floor:
                continue                                # long finished: not listed, and not counted as a problem
            if n["id"] in seen:
                counts["duplicates"] += 1               # SESB lists some notices twice
                continue
            seen.add(n["id"])
            counts[kind_of_file] += 1
            notices.append(n)
    notices.sort(key=lambda n: (n["start"], n["area"], n["id"]))
    return notices, counts


def source_changed(validators: dict) -> datetime | None:
    """When SESB's files last changed: the newest Last-Modified of the two."""
    stamps = []
    for v in validators.values():
        try:
            stamps.append(parsedate_to_datetime(v["last_modified"]).astimezone(timezone.utc))
        except (KeyError, TypeError, ValueError):
            pass
    return max(stamps) if stamps else None


def source_is_stale(out: dict, now: datetime) -> bool:
    """SESB's own files have not changed for days: our copy is only as good as theirs."""
    changed = out.get("source_changed_utc")
    return bool(changed) and now - datetime.fromisoformat(changed.replace("Z", "+00:00")) > SOURCE_STALE_AFTER


# ── One run ───────────────────────────────────────────────────────────────

def run(put_json, get_json, session, now: datetime | None = None, force: bool = False) -> dict:
    """Check SESB and, if anything changed, rebuild notices/sesb.json. On any failure nothing is written (R2 keeps
    the last good file) and SesbError is raised. Returns what happened, for the status line."""
    now = now or datetime.now(timezone.utc)
    prev = get_json(OUT_KEY)
    prev_ok = isinstance(prev, dict) and prev.get("schema") == SCHEMA and isinstance(prev.get("notices"), list)
    saved = {} if force or not prev_ok else (prev.get("validators") or {})

    answers = {key: fetch(session, name, saved.get(key) or {}) for key, name in FILES}

    if prev_ok and all(state == "unchanged" for state, _, _ in answers.values()):
        floor = now - KEEP_AFTER_END
        out = dict(prev, generated_utc=_iso(now), checked_utc=_iso(now),
                   notices=[n for n in prev["notices"] if datetime.fromisoformat(n["end"]) >= floor])
        put_json(OUT_KEY, out)
        return {"result": "unchanged", "notices": len(out["notices"]), "source_stale": source_is_stale(out, now), "out": out}

    # Something changed (or this is the first run): both files are needed in full.
    validators, tables = {}, {}
    for key, name in FILES:
        state, body, v = answers[key]
        if state == "unchanged":
            state, body, v = fetch(session, name, {})
        if state != "changed" or body is None:
            raise SesbError(f"{name}: no file")
        tables[key], validators[key] = parse_table(body, name), v

    notices, counts = build(tables, now)
    rows = sum(len(t) for t in tables.values())
    if counts["skipped"] > max(5, rows // 10):
        raise SesbError(f"{counts['skipped']} of {rows} rows could not be read: the file format may have changed")
    published = [n["published"] for n in notices if n["published"]]
    out = {
        "schema": SCHEMA,
        "provider": "sesb",
        "name": "Sabah Electricity Sdn. Bhd. (SESB)",
        "source_url": PORTAL_URL,
        "generated_utc": _iso(now),
        "checked_utc": _iso(now),
        "source_changed_utc": _iso(source_changed(validators)) if source_changed(validators) else None,
        "newest_published_utc": max(published) if published else None,
        "counts": counts,
        "validators": validators,
        "notices": notices,
    }
    put_json(OUT_KEY, out)
    return {"result": "updated", "notices": len(notices), "counts": counts, "source_stale": source_is_stale(out, now), "out": out}
