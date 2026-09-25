# Flight boards (built, switched off until the AeroDataBox key exists)

Departures and arrivals for nine airports, from AeroDataBox. The pipeline, the checks and the site's Flights
page are written and tested; until `AERODATABOX_KEY` exists the hourly step does nothing and the site shows
its "in development" message. Nothing needs to be deployed when the key arrives.

## What runs

- `scripts/ingest_flights.py`, from the hourly workflow, makes **one FIDS call per airport** for a 12 hour
  window (2 hours back, 10 ahead) and writes, to R2:
  - `flights/{IATA}.json`: departures and arrivals for one airport;
  - `flights/index.json`: the airports with freshness and counts (the site reads this first);
  - `flights/_usage.json`: calls and API units used this month.
- Airports: KCH, MYY, SBW, BTU, BKI, TWU, SDK, LBU, BWN (list in `borneosky/flights.py`).
- **No calls from 00:01 to 04:00 local time.** That is 20 runs a day.
- **Trust rule:** a flight gets a status only when AeroDataBox marks that movement's data `Live`. Otherwise its
  status is `unknown` and the page says "No live status", never a guess at "On time". A revised time is also
  kept only when live. Cargo flights are left out.
- Failure: each airport is independent, and a failed one keeps its last good file. A refused key or a quota
  error stops the whole run and turns it red. Every airport failing turns it red too.
- `meta.json` gets a `flights` source (delayed after 6 hours, which allows for the quiet hours). It appears
  only once the key is set.

## Cost and limits

- Direct **Starter plan: US$19 a month, 40,000 units, 5 requests a second**, commercial use allowed.
- FIDS is a "tier 2" call = 2 units. 9 airports × 20 runs × 30 days × 2 = **10,800 units a month (27%)**.
- The code stops calling at **90% of the monthly quota** (`AERODATABOX_MONTHLY_UNITS` if the plan differs),
  so a bug cannot run up an overage. It counts in `flights/_usage.json`.

## AeroDataBox terms that matter (read 25 Sep 2026; they were updated on 7 and 19 Sep)

- Commercial use needs a paid plan; free and trial plans forbid it.
- Cached data may be kept 7 days by default. These files are overwritten every run.
- Paid plans need no attribution. The page credits AeroDataBox anyway.
- Section 5.2(b) forbids running an API that works substantially like theirs for third parties, and 5.7 limits
  derived works to end use. Our public JSON is for our own pages: keep `robots.txt` disallowing everything and
  CORS limited to borneosky.com, and keep the Terms of Use ban on reuse.
- **Still to do:** ask AeroDataBox in writing that serving processed boards to our visitors from our own CDN
  is fine. The terms do not say it outright.

## Go-live checklist

1. Subscribe to the direct Starter plan. From its dashboard or docs note the **base URL and the name of the
   key header** (the code defaults to API.market's).
2. Put `AERODATABOX_KEY` in `~/Bs/project/.env` and in the data repo's GitHub secrets. If the base URL or header
   differ from API.market's, also set them in `.env` and as repository **variables**
   `AERODATABOX_BASE_URL` and `AERODATABOX_KEY_HEADER`.
3. `python scripts/flights_coverage.py` (about 18 units): the feed status of each airport and how many flights
   really carry live status. Decide from this whether all nine airports go in.
4. `python scripts/ingest_flights.py --dry-run --only KCH`: look at `out/flights/KCH.json`.
5. Run the workflow once (Actions, Ingest, Run workflow). Check `data.borneosky.com/flights/index.json`. The
   Flights page then switches from "in development" to the boards by itself.
6. **Site text, at the same time** (it must match what is live):
   - About: a row in the sources table (Flight boards, AeroDataBox, commercial licence) and the update rhythm
     ("boards refresh hourly from 04:00 to midnight, local time");
   - Terms: "what the site provides" gains flight boards, and a sentence that flight information can be late,
     incomplete or wrong and must be confirmed with the airline;
   - the footer credit ("Flight data: AeroDataBox");
   - Flights title and description (name the airports; leave Kalimantan out until it is added).
   All in three languages, English prevailing.
7. Watch `meta.json` (the `flights` row) and `flights/_usage.json` for the first days.

## Later

- Kalimantan: BPN, BDJ, PNK, AAP, PKY, TRK, BEJ (KTG, PKN, SMQ to confirm they have jets). Add them to
  `AIRPORTS` with `Asia/Pontianak` or `Asia/Makassar`. Live coverage there is thin, so most rows will read
  "No live status".
- Airports as map markers, and alerts for a followed flight.
- To switch it all off: remove the secret. The step goes quiet again and the page returns to its message.

`python scripts/test_flights.py` runs the checks (no network, no units).
