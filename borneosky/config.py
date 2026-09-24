"""Settings and secrets.

In GitHub Actions secrets arrive as environment variables. Locally they are
read from ~/Bs/project/.env, which lives outside both repos, and override any
stale values already exported in the shell. Values are never
logged: errors name the missing variable, not its contents.
"""

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
LOCAL_ENV = REPO_ROOT.parent / ".env"

# Borneo bounding box: west, south, east, north
BBOX = (108.5, -4.5, 119.5, 7.5)


_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _load_local_env() -> None:
    if not LOCAL_ENV.is_file():
        return
    for raw in LOCAL_ENV.read_text().splitlines():
        m = _LINE.match(raw)
        if not m:
            continue  # comments, blanks, malformed lines: skip silently
        key, val = m.groups()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ[key] = val


_load_local_env()


def require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"Missing setting: {name} (set it in .env or as a GitHub secret)")
    return val


def user_agent() -> str:
    """Identifying User-Agent (MET Norway requires a contact). The email is a
    setting, not code, so it stays out of the public repo."""
    contact = os.environ.get("CONTACT_EMAIL", "").strip()
    return f"BorneoSky/0.1 (+https://borneosky.com{'; ' + contact if contact else ''})"
