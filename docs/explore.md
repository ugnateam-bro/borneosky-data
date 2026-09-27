# Explore: weather forecasts for places (off until EXPLORE_PLACES is "on")

`scripts/ingest_places.py` (logic in `borneosky/places.py`) writes one file, `places/index.json`, for the Explore pages on
borneosky.com. The places are in `data/places.json` (20 places, edited by hand; the site keeps a copy in its
`src/data/places.json`, and the site's `npm run test:explore` checks they match).

## What it does each hour

1. For each place whose last weather forecast has expired, ask MET Norway's Locationforecast (same User-Agent,
   If-Modified-Since and Expires handling as the towns in `borneosky/met.py`). About 20 small requests an hour at most.
2. Turn the forecast into one row per local day for eight days: min and max temperature, rain in the morning (08:00 to
   14:00) and afternoon (14:00 to 20:00), a thunder flag, the strongest daytime wind and the daytime symbol. Six-hourly
   periods that straddle a window (Kalimantan Barat and Tengah are UTC+7) count only the hours inside it.
3. Read the smoke forecast grid we already publish (`smoke/grid.json`) and add each day's highest modelled PM2.5 at the
   place. No new request to Copernicus.
4. Calculate sunrise and sunset (NOAA's equations; within 2 minutes of MET Norway's sunrise service).
5. Pick the **best day**: within the next seven days after today, the day with the least daytime rain; a tie goes to a day
   without thunder, then the earlier day. Only days the forecast covers fully take part.
6. Write `places/index.json` and keep each place's rows in `places/_meta.json`, so a "not modified" answer or a failed
   request still leaves a complete file. Days that have passed drop out.

## Switch

The step does nothing until the repository variable `EXPLORE_PLACES` is `on` (Settings > Secrets and variables >
Actions > Variables). Anything else stops it at once. The site has its own switch (`exploreLive`); see the site repo's
`docs/explore.md` for the go-live order.

## Licence and wording

Weather forecast: MET Norway, CC BY 4.0, credited on every place page. Smoke forecast: Copernicus CAMS, credited as for the
towns. Every figure is labelled a forecast, and the best day is labelled a ranking of the weather forecast.

## Checks

`python scripts/test_places.py` (12 checks, no network, no R2): the place list, the switch, sunrise against MET Norway's
values, rain windows in UTC+8 and UTC+7, thunder, best day, smoke at a point, Expires and "not modified" handling, and a
failed place keeping its last rows. `python scripts/ingest_places.py --dry-run` fetches for real and writes to `./out/`.
