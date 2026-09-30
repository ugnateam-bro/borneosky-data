# Smoke forecast and wind (CAMS)

Both come from the Copernicus Atmosphere Monitoring Service's global forecast, through the Atmosphere Data Store
(`cams-global-atmospheric-composition-forecasts`, CC-BY licence, commercial use allowed with attribution). The job is
`scripts/ingest_cams.py` (the `cams` step of the hourly workflow); the code is `borneosky/cams.py`.

| File on the data host | What | Written |
| --- | --- | --- |
| `smoke/grid.json` | PM2.5 and aerosol optical depth, every 3 h for 5 days, on the 0.4° grid over Borneo (30 × 27 cells) | when a new 00Z or 12Z run is ready (about 10 h after the run time) |
| `smoke/towns.json` | the same at each town, plus daily mean and maximum | with the grid |
| `smoke/wind.json` | 10 m wind, `u` (east) and `v` (north), in tenths of m/s, same grid and steps | after the smoke files, with its own request |
| `smoke/_meta.json`, `smoke/_wind_meta.json` | which run each holds; the wind one also records a failed try | with each |

## Resolution (checked 30 Sep 2026)

The forecast is about 40 km. The Data Store delivers it already interpolated to 0.4°, and requests cannot ask for a finer
grid (the `grid` keyword is not supported there). Cycle 50r1 (12 May 2026) did not change the resolution. So the map's
smooth look (site: `src/lib/smokeImage.ts`) is a smoother drawing of the same values, not more detail.

## Wind is an extra: it can never break the smoke

- The wind is a separate request (`10m_u_component_of_wind`, `10m_v_component_of_wind`) made after the smoke files are
  written. Any failure there (including an R2 upload error) is caught, recorded and printed; the run still counts as
  successful and the last good `wind.json` stays.
- A failed request for a run is not repeated for 3 hours (`WIND_RETRY_AFTER`), so a broken wind request does not queue
  at the Data Store every hour. If the smoke files are already current but the wind is missing, the next run fetches it,
  using the smoke grid published on the data host.
- The wind must sit on exactly the smoke grid and steps and stay under 120 m/s, or it is refused.
- **Not verified live:** the variable names in the request and the NetCDF short names (`u10`, `v10`) come from the
  dataset's documentation; there was no Data Store key on the development machine. The reader also accepts `10u`/`10v`
  and finds variables by standard or long name, and `scripts/test_cams.py` covers each. After the first hourly run
  with this code, look at the `cams` step's log for `wind: updated` (or the reason it failed) and check that
  `https://data.borneosky.com/smoke/wind.json` exists.
- Size: about 260 KB before compression for 41 steps (the file is stored gzipped).

## Checks

`python scripts/test_cams.py` (8 checks: reading wind by name, refusing a mismatched or impossible file, the file's shape,
first run, a failing wind request, an upload error, the retry wait, no wind when smoke fails).

## Where the site uses them

`private/src/components/MapView.tsx`: the Smoke chip draws `grid.json` as a smooth picture; the Wind chip reads
`wind.json` and shows animated streaks (`src/lib/windFlow.ts`) or still arrows, with the same time slider. Notes for the
site side are in `private/docs/explore.md`'s neighbour, `private/docs/map.md`.
