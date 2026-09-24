"""Pipeline status: one small file, meta.json, with the latest result of every
source. The site's About page shows it, and an uptime monitor can watch it.

  {
    "generated_utc": "...",                 last time any source reported
    "sources": {
      "firms": {
        "ok": true,                         did the latest run finish cleanly
        "last_attempt_utc": "...",
        "last_success_utc": "...",          last clean run (kept when a run fails)
        "stale_after_minutes": 180,         older than this = the site says "delayed"
        "detail": {...},                    small numbers worth showing
        "error": null                       short message when ok is false
      }, ...
    }
  }

Recording is best effort: it must never make an ingest fail.
"""

import sys
from datetime import datetime, timezone

META_KEY = "meta.json"

# How stale a source may get before the site calls it delayed.
STALE_AFTER_MINUTES = {"firms": 180, "met": 180, "cams": 1800, "prices": 2880}
LABELS = {"firms": "NASA FIRMS hotspots", "met": "MET Norway weather forecast",
          "cams": "Copernicus CAMS smoke forecast", "prices": "Fuel and commodity prices"}

_detail: dict = {}
_soft_error: str | None = None


def detail(**kw) -> None:
    """Attach small facts to this run's status (counts, model run time)."""
    _detail.update(kw)


def soft_fail(message: str) -> None:
    """Mark the run as failed even though the script exits normally (it kept
    the last good files)."""
    global _soft_error
    _soft_error = message[:200]


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(source: str, ok: bool, error: str | None = None) -> None:
    if "--dry-run" in sys.argv:
        return
    try:
        from . import r2

        if _soft_error and ok:
            ok, error = False, _soft_error
        now = _iso()
        meta = r2.get_json(META_KEY) or {}
        sources = meta.setdefault("sources", {})
        prev = sources.get(source, {})
        sources[source] = {
            "label": LABELS.get(source, source),
            "ok": ok,
            "last_attempt_utc": now,
            "last_success_utc": now if ok else prev.get("last_success_utc"),
            "stale_after_minutes": STALE_AFTER_MINUTES.get(source, 180),
            "detail": dict(_detail) if ok else prev.get("detail", {}),
            "error": None if ok else (error or "failed")[:200],
        }
        meta["generated_utc"] = now
        r2.put_json(META_KEY, meta, cache_seconds=60)
    except Exception as e:  # noqa: BLE001 - status must never break a run
        print(f"  (status not recorded: {type(e).__name__})")
