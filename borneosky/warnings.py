"""Weather warnings → R2 (warnings/current.json), from the agencies' own open feeds.

  BMKG IS OFF (5 Oct 2026): run(bmkg=False) is the default and scripts/ingest_warnings.py turns it on only when the repository
  variable BMKG_WARNINGS is "on". BMKG's Terms of Use want written permission for commercial use and machine access through its
  official API (docs/warnings.md, "BMKG: switched off").

  MetMalaysia  https://api.data.gov.my/weather/warning/     JSON, no key, Malaysia's open data (CC BY 4.0).
               Sarawak, Sabah and Labuan: thunderstorms, continuous rain, strong wind and rough seas, tropical cyclones.
  BMKG         https://www.bmkg.go.id/alerts/nowcast/{en,id} RSS, and one CAP file per alert (alerts with polygons).
               The five Kalimantan provinces only. Credit BMKG; at most 60 requests a minute (we make a handful an hour).

The site never reads the agencies. It reads one small file, warnings/current.json, which holds only what is in force
(or coming) now, in the agencies' own words, and, for each warning, which of our towns it covers:

  MetMalaysia names areas as "Sabah: West Coast (Tuaran and Kota Belud), Kudat (Kudat), Interior (Pensiangan)" or just
  "Kuching" (the whole division). A town is covered when its district is named, or its division is named without a
  district list. Whole states ("Sarawak • Sabah") cover every town in them. Sea warnings cover no town.
  BMKG gives polygons: a town is covered when its point lies inside one.

A warning whose level we cannot read is shown with the agency's words and treated as a warning (never downgraded).
If one agency cannot be read, its earlier warnings stay until they expire and the status says so; if both fail, nothing
is written and R2 keeps the last good file. Switched on with the repository variable WARNINGS (scripts/ingest_warnings.py).
Notes: docs/warnings.md.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DATA_DIR

OUT_KEY = "warnings/current.json"
SCHEMA = 1
MM_URL = "https://api.data.gov.my/weather/warning/"
MM_PAGE = "https://www.met.gov.my/"
BMKG_RSS = "https://www.bmkg.go.id/alerts/nowcast/{lang}"
BMKG_PAGE = "https://nowcasting.bmkg.go.id/"
LOCAL = ZoneInfo("Asia/Kuching")                    # MetMalaysia's times have no zone: Malaysia time, UTC+8
KALIMANTAN = ("Kalimantan Barat", "Kalimantan Tengah", "Kalimantan Selatan", "Kalimantan Timur", "Kalimantan Utara")
KEEP_AFTER_END = timedelta(minutes=30)              # an expired warning is dropped half an hour after it ends
MAX_TEXT = 1500
TIMEOUT = 25
LEVELS = ("info", "alert", "warning", "danger")

# MetMalaysia's zone (division) and district names for each of our Malaysian towns. Sarawak's zones are its divisions
# (named like the town in most cases); Sabah's are West Coast, Interior, Kudat, Sandakan and Tawau.
AREAS: dict[str, tuple[str, tuple[str, ...]]] = {
    "kuching": ("Kuching", ("Kuching",)), "samarahan": ("Samarahan", ("Samarahan",)), "serian": ("Serian", ("Serian",)),
    "sri-aman": ("Sri Aman", ("Sri Aman",)), "betong-sarawak": ("Betong", ("Betong",)), "sarikei": ("Sarikei", ("Sarikei",)),
    "sibu": ("Sibu", ("Sibu",)), "mukah": ("Mukah", ("Mukah",)), "bintulu": ("Bintulu", ("Bintulu",)),
    "kapit": ("Kapit", ("Kapit",)), "miri": ("Miri", ("Miri",)), "limbang": ("Limbang", ("Limbang",)),
    "lawas": ("Limbang", ("Lawas",)),
    "kota-kinabalu": ("West Coast", ("Kota Kinabalu",)), "ranau": ("West Coast", ("Ranau",)),
    "beaufort": ("Interior", ("Beaufort",)), "keningau": ("Interior", ("Keningau",)),
    "kudat": ("Kudat", ("Kudat",)), "sandakan": ("Sandakan", ("Sandakan",)), "tawau": ("Tawau", ("Tawau",)),
    "semporna": ("Tawau", ("Semporna",)), "lahad-datu": ("Tawau", ("Lahad Datu",)),
    "labuan": ("Labuan", ("Labuan",)),
}
STATES = {"Sarawak": "sarawak", "Sabah": "sabah", "Labuan": "labuan"}


class WarningsError(Exception):
    """An agency could not be read, or its answer no longer has the expected shape."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=LOCAL)
    except ValueError:
        return None


def towns_registry() -> list[dict]:
    return json.loads((DATA_DIR / "registry.json").read_text(encoding="utf-8"))["locations"]


# ── MetMalaysia ───────────────────────────────────────────────────────────────

def classify_mm(title: str, heading: str, text: str) -> tuple[str | None, str, bool]:
    """(kind, level, at_sea) from MetMalaysia's English title, heading and text. kind None = not for us (earthquakes, 'No Advisory')."""
    t = f"{title} {heading}".lower()
    body = text.lower()
    if "advisory" in t and "no " in t:
        return None, "info", False
    if "earthquake" in t or "tsunami" in t:
        return None, "info", False
    if "cyclone" in t or "typhoon" in t or "tropical storm" in t:
        kind = "cyclone"
    elif "rain" in t:
        kind = "heavy_rain"
    elif "thunder" in t:
        kind = "thunderstorm"
    elif "wind" in t or "sea" in t:
        kind = "wind_sea"
    else:
        kind = "other"
    sea = kind == "wind_sea" or body.lstrip().startswith("section a") or "waters of" in body[:200]
    both = f"{t} {body[:400]}"
    if kind in ("heavy_rain", "other"):
        if re.search(r"danger|bahaya", both):
            level = "danger"
        elif re.search(r"\bsevere\b|warning level|level:? ?warning|tahap amaran", both):
            level = "warning"
        elif re.search(r"\balert\b|waspada", both):
            level = "alert"
        else:
            level = "warning" if kind == "heavy_rain" else "info"      # an official rain warning is never shown lower than a warning
    elif kind == "cyclone":
        level = "danger" if re.search(r"danger|bahaya", both) else "warning"
    else:
        level = "info"
    return kind, level, sea


def _names(s: str) -> list[str]:
    return [p.strip() for p in re.split(r",|\band\b|&", s) if p.strip()]


def parse_areas(text: str) -> dict:
    """What a MetMalaysia warning names: zones with district lists, whole zones, whole states.
    {'groups': {zone_lower: {district_lower}}, 'whole': {names found on their own, lower}, 'states': {all states mentioned},
     'whole_states': {states named with no zone list}}"""
    groups: dict[str, set[str]] = {}

    def take(m: re.Match) -> str:
        zone = re.sub(r"\s+", " ", m.group(1)).strip().lower()
        groups.setdefault(zone, set()).update(d.lower() for d in _names(m.group(2)))
        return " # "
    rest = re.sub(r"([A-Za-z][A-Za-z' .-]*?)\s*\(([^)]*)\)", take, text)
    rest_l = rest.lower()
    states = {s for s in STATES if re.search(rf"\b{s}\b", text)} | ({"Labuan"} if "wilayah persekutuan labuan" in text.lower() else set())
    whole_states = {s for s in STATES if re.search(rf"\b{s}\b(?!\s*:)", rest)}
    return {"groups": groups, "rest": rest_l, "states": states, "whole_states": whole_states}


def match_towns_mm(text: str, registry: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """(town slugs covered, states mentioned, states named as a whole) for one MetMalaysia warning text."""
    a = parse_areas(text)
    admin = {t["slug"]: t["admin1"] for t in registry}
    out = []
    for slug, (zone, districts) in AREAS.items():
        if slug not in admin:
            continue
        state = admin[slug]
        z, ds = zone.lower(), [d.lower() for d in districts]
        listed = a["groups"]
        named = set().union(*[v for v in listed.values()]) if listed else set()
        zone_listed = any(k.endswith(z) for k in listed)
        if state in a["whole_states"] and state in a["states"]:
            out.append(slug)
        elif any(d in named for d in ds):
            out.append(slug)
        elif not zone_listed and re.search(rf"(?<![a-z]){re.escape(z)}(?![a-z])", a["rest"]):
            out.append(slug)
    return sorted(set(out)), sorted(a["states"]), sorted(s for s in a["whole_states"] if s in a["states"])


def mm_warnings(items: list[dict], registry: list[dict], now: datetime) -> list[dict]:
    out = []
    for x in items:
        issue = x.get("warning_issue") or {}
        title = issue.get("title_en") or x.get("heading_en") or ""
        text = (x.get("text_en") or "").strip()
        start, end = _local(x.get("valid_from")), _local(x.get("valid_to"))
        if not (title and text and start and end) or end + KEEP_AFTER_END < now:
            continue
        kind, level, sea = classify_mm(title, x.get("heading_en") or "", text)
        if kind is None:
            continue
        if sea:
            towns, states = [], sorted({s for s in STATES if re.search(rf"\b{s}\b", text)})
            whole = states
        else:
            towns, states, whole = match_towns_mm(text, registry)
        if not states and not towns:
            continue                                  # a warning for the peninsula only
        issued = _local(issue.get("issued")) or start
        out.append({
            "id": "mm-" + hashlib.sha1(f"{title}|{x.get('valid_from')}|{text[:300]}".encode()).hexdigest()[:10],
            "source": "metmalaysia", "agency": "MetMalaysia", "kind": kind, "level": level, "urgent": _urgent(kind, level),
            "sea": sea, "states": [STATES[s] for s in states], "whole": [STATES[s] for s in whole], "towns": towns,
            "title": {"en": title, "ms": issue.get("title_bm") or title},
            "text": {"en": text[:MAX_TEXT], "ms": (x.get("text_bm") or text)[:MAX_TEXT]},
            "advice": {"en": (x.get("instruction_en") or "")[:400], "ms": (x.get("instruction_bm") or "")[:400]},
            "issued": _iso(issued), "valid_from": _iso(start), "valid_to": _iso(end), "url": MM_PAGE,
        })
    return out


def _urgent(kind: str, level: str) -> bool:
    """The animated, red kind: a rain, flood or cyclone warning at warning or danger level, or a severe thunderstorm."""
    return level in ("warning", "danger") and kind in ("heavy_rain", "flood", "cyclone", "thunderstorm", "other")


# ── BMKG ──────────────────────────────────────────────────────────────────────

CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"


def parse_rss(body: str) -> list[tuple[str, str]]:
    root = ET.fromstring(body)
    return [((i.findtext("title") or "").strip(), (i.findtext("link") or "").strip()) for i in root.iter("item")]


def kalimantan_province(title: str) -> str | None:
    for p in KALIMANTAN:
        if title.strip().lower().endswith(p.lower()):
            return p
    return None


def parse_cap(body: str) -> dict:
    root = ET.fromstring(body)
    info = root.find(f"{CAP}info")
    if info is None:
        raise WarningsError("BMKG CAP file has no info block")
    g = lambda tag: (info.findtext(f"{CAP}{tag}") or "").strip()  # noqa: E731
    polys = []
    for area in info.findall(f"{CAP}area"):
        for p in area.findall(f"{CAP}polygon"):
            pts = [tuple(float(v) for v in pair.split(",")) for pair in (p.text or "").split()]
            if len(pts) >= 3:
                polys.append(pts)                    # (lat, lon) pairs
    return {
        "event": g("event"), "severity": g("severity"), "urgency": g("urgency"), "headline": g("headline"),
        "description": g("description"), "effective": g("effective"), "expires": g("expires"),
        "sent": (root.findtext(f"{CAP}sent") or "").strip(), "web": g("web"),
        "area": (info.find(f"{CAP}area/{CAP}areaDesc").text or "").strip() if info.find(f"{CAP}area/{CAP}areaDesc") is not None else "",
        "polygons": polys, "identifier": (root.findtext(f"{CAP}identifier") or "").strip(),
    }


def in_polygon(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        y1, x1 = poly[i]
        y2, x2 = poly[(i + 1) % n]
        if (x1 > lon) != (x2 > lon) and lat < (y2 - y1) * (lon - x1) / (x2 - x1) + y1:
            inside = not inside
    return inside


def classify_cap(event: str, severity: str) -> tuple[str, str]:
    e = event.lower()
    if "flood" in e or "banjir" in e:
        kind = "flood"
    elif "rain" in e or "hujan" in e:
        kind = "heavy_rain"
    elif "thunder" in e or "petir" in e:
        kind = "thunderstorm"
    elif "wind" in e or "angin" in e:
        kind = "wind_sea"
    else:
        kind = "other"
    sev = severity.lower()
    if sev == "extreme":
        level = "danger"
    elif sev == "severe":
        level = "warning"
    elif kind in ("heavy_rain", "flood") or sev == "moderate" and kind not in ("thunderstorm", "wind_sea"):
        level = "alert"
    else:
        level = "info"
    return kind, level


def _cap_time(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def bmkg_warning(cap_en: dict, cap_id: dict | None, province: str, registry: list[dict], now: datetime) -> dict | None:
    start, end = _cap_time(cap_en["effective"]), _cap_time(cap_en["expires"])
    if not (start and end) or end + KEEP_AFTER_END < now:
        return None
    kind, level = classify_cap(cap_en["event"], cap_en["severity"])
    towns = sorted(t["slug"] for t in registry if t["admin1"] == province
                   and any(in_polygon(t["point"]["lat"], t["point"]["lon"], p) for p in cap_en["polygons"]))
    issued = _cap_time(cap_en["sent"]) or start
    other = cap_id or cap_en
    return {
        "id": "bmkg-" + hashlib.sha1(f"{cap_en['identifier']}|{cap_en['effective']}".encode()).hexdigest()[:10],
        "source": "bmkg", "agency": "BMKG", "kind": kind, "level": level, "urgent": _urgent(kind, level),
        "sea": False, "states": ["kalimantan"], "whole": [], "province": province, "towns": towns,
        "title": {"en": cap_en["headline"], "id": other["headline"] or cap_en["headline"]},
        "text": {"en": cap_en["description"][:MAX_TEXT], "id": other["description"][:MAX_TEXT] or cap_en["description"][:MAX_TEXT]},
        "advice": {"en": "", "id": ""},
        "issued": _iso(issued), "valid_from": _iso(start), "valid_to": _iso(end),
        "url": cap_en["web"] or BMKG_PAGE,
    }


# ── fetching and the whole run ────────────────────────────────────────────────

def _get(session, url: str):
    last = None
    for _ in range(3):
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__
    raise WarningsError(f"{url}: {last}")


def fetch_metmalaysia(session, registry, now) -> list[dict]:
    items = _get(session, MM_URL).json()
    if not isinstance(items, list):
        raise WarningsError("MetMalaysia: the answer is not a list")
    if items and not all(isinstance(i, dict) and "warning_issue" in i and "text_en" in i for i in items):
        raise WarningsError("MetMalaysia: the warnings no longer have the expected fields")
    return mm_warnings(items, registry, now)


def fetch_bmkg(session, registry, now) -> list[dict]:
    rss_en = parse_rss(_get(session, BMKG_RSS.format(lang="en")).text)
    out = []
    for title, link in rss_en:
        prov = kalimantan_province(title)
        if not prov or not link:
            continue
        cap_en = parse_cap(_get(session, link).text)
        cap_id = None
        try:
            cap_id = parse_cap(_get(session, link.replace("/nowcast/en/", "/nowcast/id/")).text)
        except (WarningsError, ET.ParseError):
            pass                                  # the Indonesian text is a nicety; the English one carries the warning
        w = bmkg_warning(cap_en, cap_id, prov, registry, now)
        if w:
            out.append(w)
    return out


def run(put_json, get_json, session, now: datetime | None = None, bmkg: bool = False) -> dict:
    """bmkg=False (the default) means BMKG is not read at all and none of its earlier warnings are carried over: BMKG's Terms of
    Use (checked 5 Oct 2026) ask for written permission for commercial use and for access through its official API."""
    now = now or datetime.now(timezone.utc)
    registry = towns_registry()
    prev = get_json(OUT_KEY)
    prev_ok = isinstance(prev, dict) and prev.get("schema") == SCHEMA and isinstance(prev.get("warnings"), list)
    sources, warnings, errors = {}, [], {}
    agencies = (("metmalaysia", fetch_metmalaysia),) + ((("bmkg", fetch_bmkg),) if bmkg else ())
    for name, fn in agencies:
        try:
            got = fn(session, registry, now)
            sources[name] = {"ok": True, "checked_utc": _iso(now), "count": len(got)}
            warnings += got
        except (WarningsError, ET.ParseError, ValueError) as e:
            errors[name] = str(e)[:160]
            kept = [w for w in (prev["warnings"] if prev_ok else []) if w.get("source") == name
                    and datetime.fromisoformat(w["valid_to"].replace("Z", "+00:00")) + KEEP_AFTER_END >= now]
            prev_src = (prev.get("sources", {}).get(name, {}) if prev_ok else {})
            sources[name] = {"ok": False, "checked_utc": prev_src.get("checked_utc"), "count": len(kept), "error": errors[name]}
            warnings += kept
    if len(errors) == len(agencies):
        raise WarningsError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    warnings.sort(key=lambda w: (not w["urgent"], -LEVELS.index(w["level"]), w["valid_to"], w["id"]))
    out = {"schema": SCHEMA, "generated_utc": _iso(now), "sources": sources, "warnings": warnings}
    put_json(OUT_KEY, out)
    return {"out": out, "errors": errors, "count": len(warnings), "urgent": sum(1 for w in warnings if w["urgent"])}
