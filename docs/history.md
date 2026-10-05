# The history database

Built 6 October 2026 at the owner's request: keep the daily data (weather, smoke, fires, warnings, notices) so that **analysis can be done
on the owner's own computer** and sold as analysis; the raw data is never resold. The hourly ingest only keeps the latest file of each
dataset in R2, so the history is a separate step.

## How it works

`borneosky/history.py` + `scripts/record_history.py` read the **published** files (https://data.borneosky.com, the ones the site reads) once
an hour and append what is new to one SQLite file (`/var/lib/borneosky/history.db` on the droplet). They never call the upstream sources,
so they add no load on MET Norway, Copernicus, NASA or the utilities, and they work wherever the ingest runs. Conditional requests
(ETag / If-Modified-Since, kept in the `http_cache` table) make an unchanged file a 304. Every row has a primary key and is written with
`INSERT OR IGNORE`, so running twice, or after a gap, never duplicates. Each dataset is its own transaction: one failing file is logged in
`runs` and the others carry on. Installing it on the droplet: `deploy/README.md`. Tests: `python scripts/test_history.py` (25 checks).

## What is in it

| Table | Source | One row is |
|---|---|---|
| `forecast_town_daily` | MET Norway, 44 towns | a town's forecast for one day, as issued by one model run (`model_utc`) |
| `forecast_town_hourly` | MET Norway, 44 towns | one hour of a run, only at lead times 1, 2, 3, 6, 12, 24, 36, 48 h (`lead_h`): the near hours act as a nowcast series, the far ones let a forecast be compared with what came later |
| `forecast_place_daily` | MET Norway, 205 Explore places | a place's day (morning and afternoon rain, thunder, smoke maximum) for one run |
| `smoke_town` | Copernicus CAMS | PM2.5 and aerosol optical depth for a town at one 3-hour step of one model run (twice a day) |
| `blobs` | CAMS | the whole smoke grid and wind files of a run, gzipped JSON (`kind` = `smoke_grid` or `wind`) |
| `hotspots` | NASA FIRMS | one satellite detection (time, satellite, position, confidence, FRP, nearest town), each once |
| `warnings` | MetMalaysia | a warning (words in English and Malay), with first and last time seen |
| `notices` | SESB, Sarawak Energy | the FACTS of an outage notice: area, kind, stage, start, end, published, flags; its id (for Sarawak Energy, the X post id) |
| `runs` | the recorder | what each run did per dataset (ok, unchanged, error, rows added): the health log |

Size: about 20,000 rows an hour at the start (the first run also takes the 48-hour window of detections), then roughly 25,000 to 50,000
rows a day; expect on the order of 1 GB a year. Disk and memory are not a problem on the 2 GB / 50 GB droplet; the service is capped at 400 MB.

## What is deliberately NOT in it, and why

| Left out | Reason (read 5 Oct 2026, `docs/growth/data-history-licences.md` in the site repository) |
|---|---|
| **BMKG alerts** | BMKG's Terms of Use want written permission for commercial use and machine access through its official API; the ingest has BMKG off, and the recorder drops any BMKG warning even if a file holds one (`WARNING_SOURCES`) |
| **Flights** | AeroDataBox lets cached data be kept 7 days and limits derived works to end use |
| **The words of an outage notice** (title, list of places) | They are the utility's text; for Sarawak Energy X allows keeping only post ids. Facts and ids only |
| Observations | None are collected today (all weather is forecast data); the history shows what was forecast and when |

Selling analysis made from the rest is a reading of the published terms (MET Norway CC BY 4.0, Copernicus licence, NASA open data,
Malaysian open data terms), not legal advice: credit each source in every report and have a Malaysian lawyer look before the first sale.

## Backup and analysis on your computer

`scripts/backup_history.py` makes a consistent copy with SQLite's own backup (never copy the live file), checks it, and uploads it gzipped
to a **private** R2 bucket as `history/history-latest.db.gz` and `history/history-<weekday>.db.gz` (seven rotating copies); a systemd
timer runs it daily. On your computer: `python scripts/pull_history.py` (a read-only R2 key in the environment) writes `history.db`. Then,
for example with DuckDB (`pip install duckdb`):

```sql
ATTACH 'history.db' AS h (TYPE sqlite);
-- how far off was the 24-hour forecast? (temperature, per town)
SELECT f.town, round(avg(abs(f.temp - n.temp)), 2) AS mae_24h
FROM h.forecast_town_hourly f JOIN h.forecast_town_hourly n ON n.town = f.town AND n.valid_utc = f.valid_utc AND n.lead_h = 2
WHERE f.lead_h = 24 GROUP BY 1 ORDER BY 2;
-- fire detections per town per day (the first and last day of the data are partial)
SELECT town, substr(t, 1, 10) AS day, count(*) AS detections, round(sum(frp), 0) AS frp_mw FROM h.hotspots GROUP BY 1, 2 ORDER BY 2, 1;
-- outages per area per month
SELECT provider, area, substr(start_local, 1, 7) AS month, count(*) AS notices FROM h.notices GROUP BY 1, 2, 3 ORDER BY 3, 4 DESC;
```

**Restore:** download a copy with `pull_history.py`, gunzip it, put it at `/var/lib/borneosky/history.db` (owner `borneo`) with the timer stopped.

## Later

When the ingest moves to the droplet it can write to this database directly (a tap in `r2.put_json`), but reading the published files stays
the safe default. Adding a dataset: a handler in `history.py`, a row in `SINGLE`, a table in `DDL` (bump `SCHEMA_VERSION`), a test.
