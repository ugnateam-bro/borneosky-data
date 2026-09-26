"""Sarawak outage notices → R2, read from Sarawak Energy's own posts on X.

Sarawak Energy (@1SarawakEnergy) announces power interruptions and repair estimates on X. Its website has no list and
its SEB Cares alerts need an account, so X is the only place its notices are public, and X's paid API is the sanctioned
way for a program to read them. This module reads that one account once an hour and writes one small file:

  notices/sarawak-energy.json     what the posts said (facts only), and when we last got an answer from X

The site reads that file. It never reads X.

What we keep, and why. X's Developer Policy lets a service pass on only Post IDs, so what we publish is facts (the
town, the affected places, the times, the kind of cause) and the post's ID, so that every notice can be linked back
to its post. The text of a post is read, turned into those facts and thrown away: it is never stored, logged or
written to R2. A notice lives at most 48 hours, and once a day every stored notice's post is looked up again: one
that has been deleted is removed here too (the Policy asks for that within 24 hours).

What it costs. X charges per post returned (US$0.005 in 2026), once per post per UTC day, and nothing for an answer
with no posts. Each hour we ask only for posts newer than the newest one seen. Replies and retweets are left out (the
account answers customers in replies, and those name individuals), no expansions are asked for (an expanded user is
billed as a user), and a hard cap stops the reading for the day (DAILY_POST_CAP) whatever the spending limit at X is.
The first run reads the last 48 hours; the account's numeric ID is looked up once (US$0.01) and kept.

What we cannot know. The posts are free text. On 26 Sep 2026 the account's latest 20 posts, a month of them, were all
one shape: "TOWN: <cause> caused supply interruption at <places> and surrounding areas. <Fault finding | Repair works>
in progress, estimated to be completed by <time>.", with follow-ups "TOWN UPDATE: ... Completion estimated by <time>."
This reads that shape, planned notices with a date and hours, and a post that says supply is back, and nothing else. A
post that looks like an outage notice but cannot be read is kept as an unread notice (its ID and time only) so the
site can link to it; it is never guessed at. A repair time is an estimate the utility gave and it often slips; the
account seldom posts that supply is back, so a notice usually just outlives its estimate. Nothing here says power is
back unless a post says so.

Not switched on until the repository variable SARAWAK_NOTICES is "on" and the secret X_BEARER_TOKEN exists
(scripts/ingest_sarawak.py). Notes on the rules, the cost and the wording: docs/sarawak-notices.md.
"""

import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .config import user_agent

API = "https://api.x.com/2"
HANDLE = "1SarawakEnergy"
PORTAL_URL = f"https://x.com/{HANDLE}"
OUT_KEY = "notices/sarawak-energy.json"
SCHEMA = 1

LOCAL = ZoneInfo("Asia/Kuching")            # Sarawak time, UTC+8, no daylight saving
KEEP = timedelta(hours=48)                  # the first read, and how long a notice is kept after its last news
PAGE_SIZE = 100                             # the most X returns for one request
MAX_PAGES = 3                               # more than 300 new posts in an hour is not something this account does
DAILY_POST_CAP = 150                        # posts read in one UTC day, whatever the spending limit at X allows (US$0.75)
USAGE_DAYS = 35                             # days of usage kept in the file
TIMEOUT = 30
ATTEMPTS = 3
PRICE_POST = 0.005                          # US$ per post read, X's price page, 2026
PRICE_USER = 0.010                          # US$ per user lookup
MAX_ESTIMATE = timedelta(hours=48)          # a repair estimate further out than this is not believed
MAX_PLACES = 300                            # characters of "areas affected" kept

TWITTER_EPOCH_MS = 1288834974657            # a Post ID carries the moment it was made: (id >> 22) + this, in milliseconds

# Sarawak towns we show (data/registry.json), and other names the posts use for them.
ALIASES = {"samarahan": "Kota Samarahan", "simanggang": "Sri Aman", "kota samarahan": "Kota Samarahan"}

# What caused it, checked in this order on the post's words (the first that matches wins, so the root cause comes before
# the damage it did: a fallen tree that breaks a conductor is a tree). Only what decides it is matched.
# Anything else is "other".
CAUSES = (
    ("tree", r"\bfallen tree|\btrees?\b|\bbranch"),
    ("vehicle", r"\bvehicle|\blorry|\btruck|\bcollision|\baccident|\bcrash|kemalangan|kenderaan"),
    ("fire", r"\bfire\b|\bblaze\b|\bflames?\b|\bburn(?:t|ing)\b|kebakaran"),
    ("cable_fault", r"\b(?:aerial|underground|faulty|damaged|suspected)\s+cable|cable (?:fault|damage|failure)|kabel"),
    ("line_damage", r"conductor|\bOHL\b|overhead line|line (?:fault|damage)|\bpoles?\b|cross ?arm|snapped|broken line|talian (?:atas|putus)|tiang"),
    ("equipment_fault", r"(?:faulty|broken|damaged|defective) equipment|equipment (?:failure|fault|damage)|peralatan"),
    ("transformer", r"transformer|pengubah"),
    ("tripping", r"\btrip(?:ping|ped|s)?\b|feeder"),
    ("weather", r"lightning|thunder|storm|heavy rain|flood|banjir|ribut|petir|angin kencang|strong wind"),
    ("maintenance", r"maintenance|upgrad|penyenggaraan|naik taraf"),
)
# Where the repair is, from the post's second sentence. Checked in this order: work paused until later says more than
# "repair works" does.
STAGES = (
    ("resume", r"\bresume\b|\bcontinue (?:tomorrow|later)\b|akan disambung"),
    ("finding", r"fault[- ]finding|locating the fault|mengesan kerosakan"),
    ("extensive", r"extensive repair|specialised technical|specialized technical|major repair"),
    ("repair", r"repair works?\b|\brepairs?\b|pembaikan"),
)
KIND_CODES = tuple(k for k, _ in CAUSES) + ("other",)
STAGE_CODES = tuple(k for k, _ in STAGES) + ("none",)


class SarawakError(Exception):
    """X could not be read, or answered in a way this module does not expect."""


def enabled() -> bool:
    """The step runs only when the repository variable SARAWAK_NOTICES is "on" (a kill switch that needs no deploy)."""
    return os.environ.get("SARAWAK_NOTICES", "").strip().lower() in ("on", "1", "true", "yes")


def token() -> str:
    """The Bearer Token: a GitHub secret in Actions, a line in ~/Bs/project/.env locally. Never printed."""
    value = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not value:
        raise SarawakError("X_BEARER_TOKEN is not set (a GitHub secret in the data repository, or a line in .env)")
    return value


def make_session(bearer: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {bearer}", "User-Agent": user_agent(), "Accept": "application/json"})
    return s


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Talking to X ──────────────────────────────────────────────────────────

def _describe(r) -> str:
    """"HTTP 401 (Unauthorized)": what X said, in a few words. Never a header, never the token."""
    try:
        body = r.json()
    except (ValueError, AttributeError):
        body = None
    said = ""
    if isinstance(body, dict):
        said = " ".join(str(body[k]) for k in ("title", "detail") if isinstance(body.get(k), str) and body[k])[:100]
    return f"HTTP {r.status_code}" + (f" ({said})" if said else "")


def api_get(session, path: str, params: dict) -> dict:
    """One GET, retried only when trying again might help (a network error, a 5xx). Anything X refuses, including a
    429, is not retried: the job runs hourly, and hammering X costs credits and goodwill."""
    problem = "no answer"
    for attempt in range(ATTEMPTS):
        try:
            r = session.get(API + path, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            problem = type(e).__name__
        else:
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    raise SarawakError(f"{path}: not JSON") from None
                if not isinstance(body, dict):
                    raise SarawakError(f"{path}: unexpected answer")
                return body
            problem = _describe(r)
            if r.status_code < 500:
                break
        if attempt + 1 < ATTEMPTS:
            time.sleep(2 * (attempt + 1))
    raise SarawakError(f"{path}: {problem}")


def lookup_user_id(session) -> str:
    """The account's numeric ID, once (US$0.01)."""
    data = api_get(session, f"/users/by/username/{HANDLE}", {}).get("data")
    uid = data.get("id") if isinstance(data, dict) else None
    if not (isinstance(uid, str) and uid.isdigit()):
        raise SarawakError(f"user {HANDLE}: not found")
    return uid


def _posts(body: dict) -> list:
    """The posts in an answer that have an ID and text. Nothing else is asked for, so nothing else is there."""
    return [p for p in (body.get("data") or []) if isinstance(p, dict) and str(p.get("id", "")).isdigit() and isinstance(p.get("text"), str)]


def fetch_new(session, user_id: str, newest_id: str | None, now: datetime) -> tuple[list, str | None, bool]:
    """Posts newer than `newest_id` (or, the first time, from the last 48 hours), newest first as X sends them, the
    newest ID seen, and whether there were more pages than we read. Empty answers cost nothing."""
    params = {"max_results": PAGE_SIZE, "exclude": "retweets,replies"}
    if newest_id:
        params["since_id"] = newest_id
    else:
        params["start_time"] = _iso(now - KEEP)
    posts, newest, pages, more = [], newest_id, 0, None
    while True:
        body = api_get(session, f"/users/{user_id}/tweets", params)
        posts += _posts(body)
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        top = meta.get("newest_id")
        if isinstance(top, str) and top.isdigit() and (newest is None or int(top) > int(newest)):
            newest = top
        more, pages = meta.get("next_token"), pages + 1
        if not more or pages >= MAX_PAGES:
            break
        params["pagination_token"] = more
    return posts, newest, bool(more)


def still_there(session, ids: list) -> tuple[set, set]:
    """Which of these posts X still has, and which it says are gone ("not found"). Any other answer for a post (or none)
    leaves it in neither set: it is kept and asked about again tomorrow. Costs the posts still there, once a day."""
    have, gone = set(), set()
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        body = api_get(session, "/tweets", {"ids": ",".join(chunk)})
        have |= {str(p["id"]) for p in _posts(body)}
        for e in body.get("errors") or []:
            if isinstance(e, dict) and str(e.get("type", "")).endswith("resource-not-found") and str(e.get("resource_id") or e.get("value") or "") in chunk:
                gone.add(str(e.get("resource_id") or e.get("value")))
    return have, gone - have


# ── What a post is ────────────────────────────────────────────────────────

def post_time(post_id: str, created_at: str | None = None) -> datetime:
    """When the post was made. X's own timestamp if it sent one; otherwise the moment inside the ID."""
    if isinstance(created_at, str):
        try:
            return datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(((int(post_id) >> 22) + TWITTER_EPOCH_MS) / 1000, tz=timezone.utc).replace(microsecond=0)


def clean(text: str) -> str:
    """The post as plain running text: entities decoded, links, @names and #tags removed (they identify people and
    campaigns, not outages), whitespace collapsed."""
    text = html.unescape(text or "")
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"[@#]\w+", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"[ \t\r\f\v]*\n[ \t\r\f\v]*", "\n", text)).strip()


OUTAGE = re.compile(
    r"\b(?:power|supply|electricity|electrical)\b[^.\n]{0,50}\b(?:interr?upt\w*|outage|disrupt\w*|restor\w*|cut|failure|tripp\w*)\b"
    r"|\b(?:interr?uption|outage|blackout|power cut)s?\b"
    r"|gangguan bekalan|bekalan (?:elektrik )?(?:terputus|terganggu|dipulihkan|pulih)|pemulihan bekalan|kerosakan (?:kabel|talian|bekalan)",
    re.I)
RESTORED = re.compile(
    r"(?:supply|power|electricity)[^.\n]{0,40}\b(?:has|have|had|is|was|been)\b[^.\n]{0,20}\brestored\b|\bhas been restored\b|\bfully restored\b"
    r"|\bback to normal\b|\bsupply is back\b|telah (?:dipulihkan|pulih)|bekalan (?:elektrik )?(?:telah )?pulih",
    re.I)
UPDATE = re.compile(r"^[\W_]*(?:update|kemaskini)\b", re.I)
UPDATE_TAIL = re.compile(r"\s+(?:update|kemaskini)(?:\s*\d+)?$", re.I)          # "<TOWN> UPDATE:" as the account writes it
PLANNED = re.compile(r"\b(?:planned|scheduled)\b|\bterancang\b|\bberjadual\b|penyenggaraan|maintenance", re.I)

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "mac": 3, "apr": 4, "may": 5, "mei": 5, "jun": 6, "jul": 7, "aug": 8, "ogo": 8, "sep": 9,
          "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dis": 12}
DATE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|mac|apr|may|mei|jun|jul|aug|ogo|sep|oct|okt|nov|dec|dis)[a-z]*\.?(?:\s+(20\d\d))?\b", re.I)
# "27 and 28 Sep", "27-29 Sep": more than one day. Not read, rather than read as the last day only.
MULTIDAY = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s*(?:and|&|dan|-|–|to|hingga|sehingga)\s*\d{1,2}(?:st|nd|rd|th)?\s+"
                      r"(?:jan|feb|mar|mac|apr|may|mei|jun|jul|aug|ogo|sep|oct|okt|nov|dec|dis)", re.I)
CLOCK = re.compile(
    r"(?<![\d:.])(?:(?P<h>\d{1,2})(?:[:.](?P<m>[0-5]\d))?\s*(?P<ap>[ap])\.?m\b\.?"
    r"|(?P<noon>(?:12\s*)?noon\b)|(?P<mid>(?:12\s*)?midnight\b)"
    r"|(?P<h24>[01]\d|2[0-3])(?P<m24>[0-5]\d)\s*(?:hrs?|hours)\b"
    r"|jam\s+(?P<hj>\d{1,2})(?:[.:](?P<mj>[0-5]\d))?\s*(?P<per>pagi|tengah\s?hari|petang|malam)?)",
    re.I)
CUE = re.compile(r"(?:\bby\b|\bbefore\b|\buntil\b|\btill\b|\baround\b|\bapprox\w*\b|\bestimat\w+|\bexpect\w*|\btarget\w*|\bETR\b|\brestor\w+|\brepair\w*"
                 r"|\bsebelum\b|\bmenjelang\b|\bdijangka\b|\bhingga\b|\bsehingga\b|\bpulih\b)", re.I)
TOMORROW = re.compile(r"\btomorrow\b|\bnext day\b|\besok\b|\bkeesokan\b", re.I)


def _clock(m) -> tuple[int, int]:
    """(hour, minute) from a CLOCK match: 9pm, 9.30 am, 12 noon, 2100hrs, jam 8.00 malam."""
    if m.group("noon"):
        return 12, 0
    if m.group("mid"):
        return 0, 0
    if m.group("ap"):
        h, mi = int(m.group("h")), int(m.group("m") or 0)
        if not 1 <= h <= 12:
            raise ValueError
        return (h % 12) + (12 if m.group("ap").lower() == "p" else 0), mi
    if m.group("h24"):
        return int(m.group("h24")), int(m.group("m24"))
    h, mi, per = int(m.group("hj")), int(m.group("mj") or 0), (m.group("per") or "").lower().replace(" ", "")
    if h > 23:
        raise ValueError
    if per == "pagi":
        h = 0 if h == 12 else h
    elif per == "tengahhari":
        h = 12 if h in (12, 0) else h + 12 if h < 6 else h
    elif per == "petang":
        h = h + 12 if h < 12 else h
    elif per == "malam":
        h = 0 if h == 12 else h + 12 if h < 12 else h
    return h, mi


def _clocks(text: str) -> list:
    out = []
    for m in CLOCK.finditer(text):
        try:
            out.append((m.start(), m.end(), *_clock(m)))
        except ValueError:
            pass
    return out


def estimate(text: str, posted: datetime) -> datetime | None:
    """The repair time the post gives ("repair expected by 9 pm"): the last clock time with a cue word just before it,
    on the next day that time comes round (or tomorrow, if the post says so). None if the post gives none, or gives one
    that is more than two days out (a misread, not an estimate)."""
    local = posted.astimezone(LOCAL)
    best = None
    for start, end, h, mi in _clocks(text):
        if CUE.search(text[max(0, start - 45):start]):
            best = (start, end, h, mi)
    if not best:
        return None
    start, end, h, mi = best
    dm = DATE.search(text[end:end + 30])
    if dm and dm.start() <= 4:                                  # "12 noon, 24 September 2026": the post names the day
        try:
            cand = datetime(int(dm.group(3)) if dm.group(3) else local.year, MONTHS[dm.group(2).lower()[:3]], int(dm.group(1)), h, mi, tzinfo=LOCAL)
        except ValueError:
            return None
        if not dm.group(3) and cand < local - timedelta(days=30):
            cand = cand.replace(year=cand.year + 1)
        return cand if local - timedelta(minutes=15) <= cand and cand - local <= MAX_ESTIMATE else None
    cand = local.replace(hour=h, minute=mi, second=0, microsecond=0)
    if TOMORROW.search(text[max(0, start - 45):end + 25]):
        cand += timedelta(days=1)
    elif cand < local - timedelta(minutes=15):
        cand += timedelta(days=1)
    return cand if cand - local <= MAX_ESTIMATE else None


def planned_window(text: str, posted: datetime) -> tuple[datetime, datetime] | None:
    """A planned interruption's date and hours ("28 September 2026, 9.00am to 5.00pm"): a date and two clock times in
    one sentence. None unless all three are there."""
    local = posted.astimezone(LOCAL)
    dm = DATE.search(text)
    clocks = _clocks(text)
    if not dm or len(clocks) < 2 or MULTIDAY.search(text) or len({m.group(0).lower() for m in DATE.finditer(text)}) > 1:
        return None
    a = next((c for c in clocks if c[0] >= 0), None)
    b = next((c for c in clocks if c[0] > a[1]), None) if a else None
    if not a or not b:
        return None
    month = MONTHS[dm.group(2).lower()[:3]]
    year = int(dm.group(3)) if dm.group(3) else local.year
    try:
        start = datetime(year, month, int(dm.group(1)), a[2], a[3], tzinfo=LOCAL)
        end = datetime(year, month, int(dm.group(1)), b[2], b[3], tzinfo=LOCAL)
    except ValueError:
        return None
    if not dm.group(3) and start < local - timedelta(days=30):
        start, end = start.replace(year=year + 1), end.replace(year=year + 1)
    if end <= start:
        end += timedelta(days=1)
    return (start, end) if end - start <= timedelta(hours=48) and start - local <= timedelta(days=45) else None


# The words in front of a town: "UPDATE -", "PLANNED POWER INTERRUPTION -", "GANGGUAN BEKALAN ELEKTRIK -".
LABEL = re.compile(r"^[\W_]*(?:(?:update|kemaskini|planned|unplanned|scheduled|terancang|berjadual|tidak terancang|urgent|notice|notis|"
                   r"(?:power |supply )?(?:supply )?(?:interruption|outage|disruption)s?|gangguan bekalan(?: elektrik)?|pemberitahuan)"
                   r"[\s\-–—:|/]+)+", re.I)
WORD = r"[A-Z][A-Za-z'’.()/]*"
NAME = rf"{WORD}(?:[ \-]{WORD}|[ \-][a-z]{{1,3}}\b){{0,4}}(?: \d{{1,2}})?"          # "<TOWN> UPDATE 2:" has a number
HEAD = re.compile(rf"^(?P<area>{NAME})\s*[:–—]\s+|^(?P<area2>{NAME})\s+-\s+")
PREP = re.compile(r"^(?:in|at|di|for|untuk)\s+", re.I)


def _town(raw: str) -> str:
    """The name as we show it: one of our Sarawak towns if it is one, otherwise the place as the post wrote it."""
    name = re.sub(r"\s+", " ", raw.strip(" .:-–—()")).strip()
    key = name.lower()
    key = ALIASES.get(key, key)
    for town in SARAWAK_TOWNS:
        if town.lower() == key.lower():
            return town
    return name.title() if name.isupper() or name.islower() else name


def _load_towns() -> tuple:
    try:
        registry = json.loads((Path(__file__).resolve().parent.parent / "data" / "registry.json").read_text(encoding="utf-8"))
        towns = [t["name"] for t in registry["locations"] if t.get("admin1") == "Sarawak" and t.get("country_code") == "MY"]
    except (OSError, ValueError, KeyError, TypeError):
        towns = []
    return tuple(towns or ("Betong", "Bintulu", "Kapit", "Kota Samarahan", "Kuching", "Lawas", "Limbang", "Miri", "Mukah", "Sarikei", "Serian", "Sibu", "Sri Aman"))


SARAWAK_TOWNS = _load_towns()


def area_of(text: str) -> tuple[str | None, bool]:
    """The town or place the post is about, and whether the heading says UPDATE: the words before the colon in
    "BINTULU: ..." or "<TOWN> UPDATE: ...", after any label in front."""
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()][:3]
    for line in lines:
        body = re.sub(r"^[\W_]+", "", PREP.sub("", re.sub(r"^[\W_]+", "", LABEL.sub("", line, count=1))))
        m = HEAD.match(body)
        raw = (m.group("area") or m.group("area2")) if m else None
        if not raw:
            continue
        update = bool(UPDATE_TAIL.search(raw))
        raw = UPDATE_TAIL.sub("", raw)
        if raw.strip() and len(raw.split()) <= 5 and not re.search(r"\b(?:dear|customers?|our|the|we|sarawak energy|sesb|update)\b", raw, re.I):
            return _town(raw), update
    return None, False


PLACES = re.compile(
    r"(?:\binterr?upt\w*|\bdisrupt\w*|\boutages?)\s+(?:at|to|in|affecting|involving|for)\s+(?P<a>[^\n]+)"
    r"|(?:areas? affected|affected areas?|areas? involved|affecting|involving)\s*(?:are|is|include|includes|:|-)?\s*(?P<b>[^\n]+)"
    r"|(?:kawasan (?:yang )?terjejas|menjejaskan|melibatkan|lokasi terjejas)\s*(?:adalah|ialah|:|-)?\s*(?P<c>[^\n]+)", re.I)
# Where the list of places ends: the next sentence (a full stop, then a word that starts the account's status sentence;
# not the dot in "Sg. Dalam"), or a clause about cause or repair.
PLACES_END = re.compile(
    r"(?<=[A-Za-z0-9\)])\.(?=\s+(?:Repairs?|Fault|Restoration|Completion|Specialised|Specialized|Extensive|Supply|Works?|Crews?|Teams?|Technical|"
    r"Please|We|Our|Thank|Estimated|Expected|The|Power|Bekalan|Kerja|Pemulihan|Kami|Sila)\b)|\.\s*$"
    r"|\s*,?\s+(?:with |and )?(?:restoration|repair works?|repairs?|fault finding)\b|\s+(?:due to|caused by|as a result|because|owing to|akibat|disebabkan)\b"
    r"|\s+(?:our (?:team|crew)|kindly|we (?:apolog|are))\b", re.I)
MORE = re.compile(r"(?:,|\band\b|&|\bdan\b)?\s*(?:surrounding|nearby|adjacent|other) areas?\s*$|(?:,|\band\b|\bdan\b)?\s*(?:the )?(?:vicinity|kawasan sekitar|sekitarnya)\s*$", re.I)


def places_of(text: str) -> tuple[str, bool]:
    """The affected places as the post wrote them (spacing fixed, nothing reordered), and whether the post says the list
    goes on ("and surrounding areas")."""
    m = PLACES.search(text)
    if not m:
        return "", False
    tail = next(g for g in m.group("a", "b", "c") if g)
    end = PLACES_END.search(tail)
    p = (tail[:end.start()] if end else tail).strip(" ,;:-")
    more = bool(MORE.search(p))
    p = MORE.sub("", p).strip(" ,;:-")
    p = re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", p)).strip(" ,")
    if len(p) > MAX_PLACES:
        cut = p[:MAX_PLACES]
        p, more = (cut[:cut.rfind(",")] if cut.rfind(",") > MAX_PLACES // 2 else cut).rstrip(" ,"), True
    return p, more


def cause_of(text: str) -> str:
    for kind, pattern in CAUSES:
        if re.search(pattern, text, re.I):
            return kind
    return "other"


def stage_of(text: str) -> str:
    for stage, pattern in STAGES:
        if re.search(pattern, text, re.I):
            return stage
    return "none"


# Words of a fault or repair that a follow-up may use without ever saying "power": "The cable fault has been identified.
# Restoration is expected by 2 am." Enough only if the post also has a town and something to say about it.
FAULT = re.compile(r"\b(?:cable fault|line fault|faults?|fault[- ]finding|restoration|restor\w+|repair\w*|conductor|feeder|transformer|tripp\w+|"
                   r"outage|interr?upt\w*|kerosakan|pemulihan|pulih)\b", re.I)
# Safety advice and campaigns that mention outages without being one.
ADVICE = re.compile(r"\b(?:tips?|how to|what to do|stay safe|prepare|preparedness|guide|safety|reminder)\b", re.I)


def is_outage_post(text: str) -> bool:
    """Whether a post says outright that power is interrupted, restored or cut. The account also posts about events,
    people and campaigns; those are dropped on sight and never stored."""
    return bool(OUTAGE.search(text))


def wanted(text: str, facts: dict) -> bool:
    """Whether to keep a post as a notice. One that says outright that power is interrupted is kept even if we cannot
    read a town from it (it becomes an unread notice, a link), unless it is safety advice. One that only mentions a fault
    or a repair is kept only if it is readable: a town, and something to say about it."""
    if is_outage_post(text):
        return facts["readable"] or not ADVICE.search(text)
    return facts["readable"] and bool(FAULT.search(text))


def parse_post(text: str, posted: datetime) -> dict:
    """The facts in one outage post. Nothing here keeps a word of the post: the results are a town, a cause code, a
    repair stage, places (as written), and times. `readable` means a town and something to say about it."""
    body = clean(text)
    area, update_tail = area_of(body)
    restored = bool(RESTORED.search(body))
    window = planned_window(body, posted) if PLANNED.search(body) else None
    places, more = places_of(body)
    kind = cause_of(body)
    if window:
        start, end = window
    else:
        start, end = posted.astimezone(LOCAL), None if restored else estimate(body, posted)
        if end and end <= start:
            end = None                                            # an estimate that is already past is a misreading
    facts = {
        "type": "planned" if window else "unplanned", "area": area or "", "start": start.isoformat(),
        "end": end.isoformat() if end else None, "kind": kind if kind != "other" or not window else "maintenance",
        "stage": stage_of(body), "places": places, "more": more, "update": bool(UPDATE.match(body)) or update_tail, "restored": restored,
    }
    # It looks like a real notice when it says when, where, why, how the repair stands or that supply is back. Words like
    # "power outage" alone (a safety tip, a campaign) are not enough to keep anything.
    facts["signal"] = bool(end or places or kind != "other" or facts["stage"] != "none" or restored)
    facts["readable"] = bool(area) and facts["signal"]
    return facts


# ── The file ──────────────────────────────────────────────────────────────

def make_notice(post_id: str, posted: datetime, facts: dict) -> dict:
    """One record: facts and the post's ID. Never the text."""
    if not facts["readable"]:
        return {"id": post_id, "type": "unplanned", "area": "", "start": posted.astimezone(LOCAL).isoformat(), "end": None, "kind": "other",
                "stage": "none", "places": "", "more": False, "update": False, "restored": False, "published": _iso(posted), "readable": False}
    n = {k: facts[k] for k in ("type", "area", "start", "end", "kind", "stage", "places", "more", "update", "restored")}
    return {"id": post_id, **n, "published": _iso(posted), "readable": True}


def _last_news(n: dict) -> datetime:
    """The latest moment this notice says something about: its estimate or planned end, or when it was posted."""
    posted = datetime.fromisoformat(n["published"].replace("Z", "+00:00"))
    return max(posted, datetime.fromisoformat(n["end"])) if n.get("end") else posted


def merge_updates(notices: list) -> tuple[list, int]:
    """An "UPDATE", or a post saying supply is back, replaces the latest earlier notice in that town that is not yet
    closed. It replaces one, not all: two faults in one town can overlap, and the update does not say which it is about,
    so the most recent is the fair reading and the other stays as it was. The update carries the news; from the notice it
    replaces it keeps the first report's time and whatever it does not repeat (the places, the cause). Returns the list
    and how many were replaced."""
    at = lambda iso: datetime.fromisoformat(iso.replace("Z", "+00:00"))                    # noqa: E731
    by_time = sorted(notices, key=lambda n: (n["published"], n["id"]))
    dropped = set()
    for i, u in enumerate(by_time):
        if not (u["readable"] and (u["update"] or u["restored"]) and u["area"] and u["type"] == "unplanned"):
            continue
        prior = [o for o in by_time[:i] if o["id"] not in dropped and o["readable"] and o["type"] == "unplanned" and not o["restored"]
                 and o["area"].lower() == u["area"].lower() and at(u["published"]) - at(o["published"]) <= KEEP]
        if not prior:
            continue
        old = prior[-1]
        u["start"] = min(u["start"], old["start"], key=at)
        if not u["places"]:
            u["places"], u["more"] = old["places"], old["more"]
        if u["kind"] == "other":
            u["kind"] = old["kind"]
        if u["stage"] == "none":
            u["stage"] = old["stage"]
        dropped.add(old["id"])
    return [n for n in notices if n["id"] not in dropped], len(dropped)


def _usage(state_usage, today: str) -> dict:
    usage = {d: dict(v) for d, v in (state_usage or {}).items() if isinstance(v, dict)}
    usage.setdefault(today, {"posts": 0, "users": 0})
    for d in sorted(usage)[:-USAGE_DAYS]:
        del usage[d]
    return usage


def valid(prev) -> bool:
    return isinstance(prev, dict) and prev.get("schema") == SCHEMA and isinstance(prev.get("notices"), list) and isinstance(prev.get("state"), dict)


def run(put_json, get_json, session, now: datetime | None = None, force: bool = False) -> dict:
    """Read the account, and rebuild notices/sarawak-energy.json. On any failure nothing is written (R2 keeps the last
    good file) and SarawakError is raised. Returns what happened, for the status line."""
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    prev = get_json(OUT_KEY)
    keep = valid(prev) and not force
    state = dict(prev["state"]) if keep else {}
    notices = [dict(n) for n in prev["notices"]] if keep else []
    usage = _usage(prev["state"].get("usage") if valid(prev) else None, today)      # the daily cap survives --force
    day = usage[today]
    if day["posts"] >= DAILY_POST_CAP:
        raise SarawakError(f"{day['posts']} posts already read today: the daily cap of {DAILY_POST_CAP} stops the reading until tomorrow")

    fresh = not state.get("newest_id")                       # nothing held: every post below is read for the first time today
    user_id = state.get("user_id")
    if not user_id:
        user_id = lookup_user_id(session)
        day["users"] += 1

    posts, newest, truncated = fetch_new(session, user_id, state.get("newest_id"), now)
    day["posts"] += len(posts)
    counts = {"posts_seen": len(posts), "outage_posts": 0, "unread": 0, "dropped": 0, "deleted": 0, "replaced": 0}
    known = {n["id"] for n in notices}
    for p in sorted(posts, key=lambda p: int(p["id"])):
        pid = str(p["id"])
        if pid in known:
            continue
        posted = post_time(pid, p.get("created_at"))
        facts = parse_post(p["text"], posted)
        if not wanted(p["text"], facts):
            counts["dropped"] += 1
            continue
        n = make_notice(pid, posted, facts)
        counts["outage_posts"] += 1
        counts["unread"] += 0 if n["readable"] else 1
        notices.append(n)
        known.add(pid)

    # Once a UTC day, ask X again about the notices we hold that were not read today: a post that has been deleted is
    # removed here too. (A post read today is charged once for the whole day, so asking again would only be a wasted call.)
    if state.get("last_recheck") != today:
        older = [n["id"] for n in notices if not fresh and n["published"][:10] != today]
        if older:
            have, gone = still_there(session, older)
            day["posts"] += len(have)
            counts["deleted"] = len(gone)
            notices = [n for n in notices if n["id"] not in gone]
        state["last_recheck"] = today

    notices, counts["replaced"] = merge_updates(notices)
    floor = now - KEEP
    notices = [n for n in notices if _last_news(n) >= floor]
    notices.sort(key=lambda n: (n["start"], n["area"], n["id"]))

    state.update(user_id=user_id, newest_id=newest, usage=usage)
    published = [n["published"] for n in notices]
    out = {
        "schema": SCHEMA,
        "provider": "sarawak_energy",
        "name": "Sarawak Energy Berhad",
        "source_url": PORTAL_URL,
        "generated_utc": _iso(now),
        "checked_utc": _iso(now),
        "source_changed_utc": None,                 # a quiet account is normal, so we never accuse Sarawak Energy of going silent
        "newest_published_utc": max(published) if published else None,
        "counts": counts,
        "state": state,
        "notices": notices,
    }
    put_json(OUT_KEY, out)
    reads = sum(v.get("posts", 0) for d, v in usage.items() if d[:7] == today[:7])
    users = sum(v.get("users", 0) for d, v in usage.items() if d[:7] == today[:7])
    return {"result": "updated", "notices": len(notices), "counts": counts, "truncated": truncated, "out": out,
            "posts_today": day["posts"], "cost_month_usd": round(reads * PRICE_POST + users * PRICE_USER, 3)}


def sample(session, count: int, now: datetime | None = None) -> dict:
    """For the person tuning the reader: the latest `count` (5 to 100) posts as X sends them, with what this module
    makes of each. Costs `count` posts. The raw posts are for reading on this computer only (the caller writes them
    under out/, which git ignores): they are X's content and must never be committed, printed to a log or uploaded."""
    now = now or datetime.now(timezone.utc)
    uid = lookup_user_id(session)
    body = api_get(session, f"/users/{uid}/tweets", {"max_results": max(5, min(100, count)), "exclude": "retweets,replies"})
    posts = _posts(body)
    rows = []
    for p in posts:
        posted = post_time(str(p["id"]), p.get("created_at"))
        facts = parse_post(p["text"], posted)
        keep = wanted(p["text"], facts)
        rows.append({"id": str(p["id"]), "posted_utc": _iso(posted), "kept": keep, "facts": facts if keep else None})
    return {"user_id": uid, "posts": posts, "rows": rows, "cost_usd": round(PRICE_USER + len(posts) * PRICE_POST, 3)}
