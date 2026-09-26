# Sabah outage notices (switched on 26 Sep 2026)

Planned and short-notice power work in Sabah and Labuan, read from **SESB's own outage portal**
(https://mysesb.com.my/Outage/). The pipeline, its 23 checks and the site's three places for the notices (the Outages page, a count per town on the front page, a card on each town page) are written and tested.
The repository variable `SESB_NOTICES` was set to `on` on 26 Sep 2026. Set it to anything else and the hourly step stops at
once and writes no file.

The reasoning, the alternatives (Ada Karankah, X) and the wording rules are in the site repository's
`docs/outages-notices.md`. This note is only about the pipeline.

## What runs

- `scripts/ingest_sesb.py`, from the hourly workflow, makes **two requests an hour**, one per file, both
  conditional:
  - `https://mysesb.com.my/Outage/json/PlannedOutage.json` (planned works, since Aug 2021, about 0.5 MB)
  - `https://mysesb.com.my/Outage/json/UnplannedOutage.json` (the portal's "unplanned" tab: mostly
    system-strengthening projects and fault repairs, announced at short notice; about 2.5 MB)
- SESB rebuilds both once a day (about 16:00 UTC, midnight in Malaysia) and its server answers `304 Not Modified`
  with no body while nothing has changed (tested 25 Sep 2026), so the files are downloaded about once a day and
  the other 23 runs cost two empty answers. The ETags and Last-Modified dates are kept in the output file, which is
  the only state the pipeline has between runs.
- Output, to R2: `notices/sesb.json` (about 9 KB): `notices` (current, coming, or ended in the last 48 hours),
  `checked_utc` (when SESB last answered), `source_changed_utc` (when SESB's files last changed) and `counts`.
  The site reads only this file, never SESB.
- These are **notices, not faults**. Read as UTC, their timestamps put a notice a median of about 57 hours ahead of
  the work (2026; 6% are under 24 hours). Faults that happen without warning are not in them.
- **Times** are Malaysia time (UTC+8) in SESB's files; we write them with the offset. `published` is when the
  notice first appeared in SESB's daily file (its server clock, which its own `Last-Modified` header shows is UTC).
- **Duration** is SESB's own "Duration (in hours)", as its portal shows it. In 2026 it equals end minus start except
  when SESB rounds up (7 h 20 min shown as 8). Left out when it is not a plain number; the site then works the hours out.
- **Titles** are 30 stock Malay phrases. Each becomes a `kind` (14 kinds and `other`); SESB's own title is kept
  beside it, so the site can show both. "Critical" is set only when the title says `KRITIKAL` or `KECEMASAN`
  (`SPECIAL/CRITICAL PROJECT` is a project type, not urgency).
- **Districts** are SESB's 23 names; `W.P.LABUAN` becomes `Labuan`. All ten of our Sabah and Labuan towns match a
  district by name, and the site does that matching.
- Repeated rows (SESB lists about one notice in six twice) are collapsed. Very long place lists are cut at a comma
  at 1,500 characters and marked.

## When it goes wrong

- A failed run, or files that no longer have the expected shape (not JSON, no `Table` rows, or more than 10% of
  rows unreadable), **uploads nothing**: R2 keeps the last good file, the step turns the run red, and `checked_utc`
  stops moving, so the site says "we couldn't check SESB for N hours".
- If SESB's own files stop changing for 72 hours, the run records a soft failure ("SESB's own files have not
  changed since…"), so the About page's data status says so. This is the failure that made Ada Karankah untrustworthy:
  a list that is only as fresh as its source must say when the source has gone quiet.
- `meta.json` gets an `sesb` source ("SESB outage notices", delayed after 6 hours). It appears only once the step
  has run.

## Switching on and off

- **On:** repository Settings, Secrets and variables, Actions, **Variables**: add `SESB_NOTICES` = `on`. The next
  hourly run (23 minutes past) writes the file. The owner chose on 26 Sep 2026 not to wait for a reply from SESB
  (option A in the site repository's `docs/outages-notices.md`). Do it before the site's `noticesLive` is set, so the
  file exists on day one; the Actions tab can also run the workflow by hand.
- **Off, at once and with no deploy:** set the variable to anything else (`off`) or delete it. The step then skips.
  R2 keeps the last file; delete `notices/sesb.json` in R2 as well if SESB asks us to stop.
- Local run without R2: `python scripts/ingest_sesb.py --dry-run` writes `out/notices/sesb.json` (it works whether or
  not the variable is set; a second dry run shows the "unchanged" path). `--force` ignores the saved ETags.

## Rules and permission

- We identify ourselves with `BorneoSky/0.1 (+https://borneosky.com; contact)` and never ask more than twice an hour.
- The files are not a documented interface. SESB's portal shows "Copyright 2021 Sabah Electricity Sdn. Bhd." and no
  terms for reuse; `mysesb.com.my` has no `robots.txt`. The notices are SESB's text: the site restates the facts
  and links to the portal. **The owner decided on 26 Sep 2026 that no letter is needed** (the data was obtained
  legally). The switch stays as the way to stop at once if SESB ever asks us to.
- `data.borneosky.com` keeps its `robots.txt` disallowing everything, and the notices are covered by the Terms of Use
  ban on reuse like every other file there.

## Tests

`python scripts/test_sesb.py` (no network): all 30 real titles and their kinds; time zones; district names;
place text; unusable rows; the 48-hour window, order and duplicates; a first run, an unchanged check, one file
changed, `--force`, an older output schema; every failure writes nothing; retries only where asking again can
help; a mostly unreadable file; silence at SESB; the switch; the User-Agent.
