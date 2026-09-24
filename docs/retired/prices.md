# Prices pipeline (retired)

Retired on 24 September 2026, together with the Prices page on the site (the site repo's
`docs/retired/prices.md` says why). Nothing was lost: both sources publish their own full history, so the
first run after a revival rebuilds everything.

## What it did

Every hour the workflow ran `scripts/ingest_prices.py`, which at most every 6 hours wrote three files to R2:

| R2 key | Contents |
| --- | --- |
| `prices/fuel_my.json` | Malaysian retail fuel prices, weekly. `generated_utc`, `effective_date`, `label`, `attribution`, `source_url`, `prices[]` (`key`, `label`, `rm_per_litre`, `change_weekly`) and `history[]` (26 weeks, one column per fuel) |
| `prices/commodities.json` | World prices, monthly. `generated_utc`, `source_updated`, `label`, `attribution`, `source_url`, `series[]` (`key`, `label`, `group`, `unit`, `month`, `value`, `change_1m_pct`, `change_12m_pct`, `history[[month, value]]` for 24 months) |
| `prices/_meta.json` | `{"checked_utc": ...}`, the 6-hour throttle |

- **Sources, both CC BY 4.0 as of September 2026:** the data.gov.my fuel price API
  (`https://api.data.gov.my/data-catalogue/?id=fuelprice&sort=-date&limit=60`, official retail prices from the
  Ministry of Finance) and the World Bank Pink Sheet monthly workbook (`CMO-Historical-Data-Monthly.xlsx`,
  sheet "Monthly Prices", columns "Palm oil", "Palm kernel oil", "Rubber, RSS3", "Rubber, TSR20"). The
  workbook's URL was found by scraping the World Bank commodity-markets page, with a hard-coded fallback that
  goes stale whenever they republish.
- **Behaviour:** each file was independent, a failed fetch kept the last good copy, and the step ended the run
  red only if both files failed. It had its own row in `meta.json` (delayed after 48 hours).
- **Not fetched, on purpose:** the daily MPOB, Pepper Board and Rubber Board prices (no reuse licence).
- **Attribution strings:** "Fuel prices: Ministry of Finance Malaysia, via data.gov.my, CC BY 4.0" and
  "Commodity prices: The World Bank, Commodity Price Data (The Pink Sheet), CC BY 4.0".

## What was removed

- `borneosky/prices.py` and `scripts/ingest_prices.py`.
- The `prices` step, and its place in the "fail the run" step, in `.github/workflows/ingest.yml`.
- The `prices` entries in `STALE_AFTER_MINUTES` and `LABELS` in `borneosky/status.py`.
- `openpyxl` from `requirements.txt` (only the World Bank workbook needed it).
- New: `RETIRED_SOURCES` in `borneosky/status.py`. `meta.json` never forgot a source, so without it the old
  `prices` row would have stayed there for ever and shown as delayed on the site's About page. The next
  hourly run removes it.

## What was left in place

- The three R2 objects, frozen at the last run (2026-09-24 12:24 UTC). They can still be read at
  `data.borneosky.com/prices/…`, but they are no longer updated and nothing links to them. Delete them in the
  Cloudflare dashboard (R2, `borneosky-data`, folder `prices/`) whenever you want them gone.
- `docs/retired/prices/fuel_my.json` and `commodities.json`: a copy of those two files as they were, kept as a
  reference for the format.

## How to find it again

- Annotated tag **`prices-last-live`** is the last commit with the pipeline in place.
- The retirement is one commit titled "Retire the Prices pipeline…": `git log --grep="Retire the Prices"`.

## To revive it

1. `git revert <retirement commit>`. This also takes `RETIRED_SOURCES` and the workflow change with it. (If
   you restore files one by one instead, remove `"prices"` from `RETIRED_SOURCES`, or its row is deleted from
   `meta.json` on every run.)
2. Test without touching R2: `python scripts/ingest_prices.py --dry-run` writes to `./out/`. Check that both
   endpoints still answer and that the World Bank column names above still exist.
3. Re-check both licences and the attribution wording, then bring the site page back (site repo note).
