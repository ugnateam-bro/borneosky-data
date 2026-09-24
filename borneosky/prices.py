"""Economic data → R2. Only openly licensed sources (CC BY 4.0), and only
prices the public actually checks (fuel, palm oil, rubber):

  prices/fuel_my.json       Malaysian retail fuel prices, weekly
                            (data.gov.my, Ministry of Finance)
  prices/commodities.json   World market prices for Borneo's commodities,
                            monthly (World Bank Pink Sheet)

Daily local prices (MPOB palm oil, Malaysian Pepper Board, Malaysian Rubber
Board) have no reuse licence yet and are deliberately not fetched.

World Bank prices are international benchmarks in US dollars, not what a
local buyer pays a smallholder; the files say so.
"""

import io
import re
from datetime import datetime, timedelta, timezone

import requests

from .config import user_agent

META_KEY = "prices/_meta.json"
MIN_INTERVAL = timedelta(hours=6)   # fuel changes weekly, World Bank monthly

FUEL_API = "https://api.data.gov.my/data-catalogue/"
# Only fields documented on data.gov.my, labelled with the official wording.
FUEL_FIELDS = [
    ("ron95_budi95", "RON95 petrol under BUDI 95 subsidy"),
    ("ron95", "RON95 petrol"),
    ("ron97", "RON97 petrol"),
    ("diesel_eastmsia", "Diesel, East Malaysia"),
    ("ron95_skps", "RON95 petrol under SKPS (Subsidised Petrol Control System)"),
]
FUEL_ATTRIBUTION = ("Fuel prices: Ministry of Finance Malaysia, via data.gov.my, "
                    "CC BY 4.0")

WB_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
WB_FALLBACK = ("https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-"
               "0050012026/related/CMO-Historical-Data-Monthly.xlsx")
# (key, column header in the Pink Sheet, label, group)
WB_SERIES = [
    ("palm_oil", "Palm oil", "Palm oil", "Plantation"),
    ("palm_kernel_oil", "Palm kernel oil", "Palm kernel oil", "Plantation"),
    ("rubber_rss3", "Rubber, RSS3", "Rubber, RSS3", "Plantation"),
    ("rubber_tsr20", "Rubber, TSR20", "Rubber, TSR20", "Plantation"),
]
WB_HISTORY_MONTHS = 24
WB_ATTRIBUTION = ("Commodity prices: The World Bank, Commodity Price Data "
                  "(The Pink Sheet), CC BY 4.0")
WB_LABEL = ("Monthly average world market prices in US dollars. International "
            "benchmarks, not local farm-gate prices.")


class PricesError(Exception):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent()
    return s


# ── Fuel ──────────────────────────────────────────────────────────────────

def fuel(session: requests.Session, now: datetime) -> dict:
    try:
        r = session.get(FUEL_API, params={"id": "fuelprice", "sort": "-date", "limit": 60},
                        timeout=60)
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError) as e:
        raise PricesError(f"fuel: {type(e).__name__}") from None
    if not isinstance(rows, list) or not rows:
        raise PricesError("fuel: empty response")

    levels = [x for x in rows if x.get("series_type") == "level"]
    changes = {x["date"]: x for x in rows if x.get("series_type") == "change_weekly"}
    if not levels:
        raise PricesError("fuel: no price rows")
    latest = levels[0]
    change = changes.get(latest["date"], {})

    prices = []
    for key, label in FUEL_FIELDS:
        v = latest.get(key)
        if v is None:
            continue
        c = change.get(key)
        prices.append({"key": key, "label": label, "rm_per_litre": round(float(v), 2),
                       "change_weekly": None if c is None else round(float(c), 2)})

    history = [{"date": x["date"], **{k: x.get(k) for k, _ in FUEL_FIELDS}}
               for x in reversed(levels[:26])]
    return {
        "generated_utc": _iso(now),
        "effective_date": latest["date"],
        "label": "Official retail prices per litre set weekly by the Government of Malaysia.",
        "attribution": FUEL_ATTRIBUTION,
        "source_url": "https://data.gov.my/data-catalogue/fuelprice",
        "prices": prices,
        "history": history,
    }


# ── World Bank Pink Sheet ─────────────────────────────────────────────────

def _wb_url(session: requests.Session) -> str:
    try:
        html = session.get(WB_PAGE, timeout=60).text
        m = re.search(r"https://thedocs\.worldbank\.org/[^\"']*CMO-Historical-Data-Monthly\.xlsx", html)
        if m:
            return m.group(0)
    except requests.RequestException:
        pass
    return WB_FALLBACK


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    return None  # '…' and blanks mean no value


def commodities(session: requests.Session, now: datetime) -> dict:
    import openpyxl

    url = _wb_url(session)
    try:
        r = session.get(url, timeout=180)
        r.raise_for_status()
        wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
        rows = list(wb["Monthly Prices"].iter_rows(values_only=True))
    except (requests.RequestException, KeyError, OSError, ValueError) as e:
        raise PricesError(f"World Bank: {type(e).__name__}") from None

    updated = next((str(x[0]) for x in rows[:6] if x and x[0] and "Updated" in str(x[0])), None)
    header = next((i for i, x in enumerate(rows) if x and x[1] and "Crude oil" in str(x[1])), None)
    if header is None:
        raise PricesError("World Bank: header row not found")
    names = [re.sub(r"\s*\*+\s*$", "", str(h)).strip() if h else "" for h in rows[header]]
    units = rows[header + 1]
    data = [x for x in rows[header + 2:] if x and isinstance(x[0], str) and re.match(r"\d{4}M\d{2}", x[0])]

    def month(s: str) -> str:  # '2026M08' → '2026-08'
        return f"{s[:4]}-{s[5:7]}"

    series = []
    for key, col_name, label, group in WB_SERIES:
        try:
            j = names.index(col_name)
        except ValueError:
            raise PricesError(f"World Bank: column '{col_name}' missing") from None
        points = [(month(x[0]), _num(x[j])) for x in data]
        points = [(m, v) for m, v in points if v is not None]
        if len(points) < 13:
            raise PricesError(f"World Bank: too few values for {col_name}")
        (last_m, last_v), (_, prev_v), (_, year_v) = points[-1], points[-2], points[-13]
        series.append({
            "key": key, "label": label, "group": group,
            "unit": str(units[j]).strip("() ") if units[j] else "",
            "month": last_m, "value": round(last_v, 2),
            "change_1m_pct": round((last_v / prev_v - 1) * 100, 1) if prev_v else None,
            "change_12m_pct": round((last_v / year_v - 1) * 100, 1) if year_v else None,
            "history": [[m, round(v, 2)] for m, v in points[-WB_HISTORY_MONTHS:]],
        })

    return {
        "generated_utc": _iso(now),
        "source_updated": updated,
        "label": WB_LABEL,
        "attribution": WB_ATTRIBUTION,
        "source_url": "https://www.worldbank.org/en/research/commodity-markets",
        "series": series,
    }


# ── Run ───────────────────────────────────────────────────────────────────

def run(put_json, get_json, *, force: bool = False) -> dict:
    """Refresh both files at most every 6 hours. Each file is independent:
    a failure keeps the last good copy of that file."""
    now = datetime.now(timezone.utc)
    meta = (get_json(META_KEY) or {}) if not force else {}
    last = meta.get("checked_utc")
    if last and not force:
        if now - datetime.fromisoformat(last.replace("Z", "+00:00")) < MIN_INTERVAL:
            return {"status": "not_due", "checked_utc": last}

    session = _session()
    result = {"status": "ran", "updated": [], "failed": {}}
    for key, build in (("prices/fuel_my.json", fuel), ("prices/commodities.json", commodities)):
        try:
            put_json(key, build(session, now))
            result["updated"].append(key)
        except PricesError as e:
            result["failed"][key] = str(e)

    put_json(META_KEY, {"checked_utc": _iso(now)})
    return result
