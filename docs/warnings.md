# Weather warnings (MetMalaysia; BMKG switched off)

## BMKG: switched off on 5 October 2026

The owner asked for it after BMKG's Terms of Use (www.bmkg.go.id/ketentuan-penggunaan, read 5 Oct 2026) turned out to be stricter than the
feed's own "public domain" note: repackaging, integration into third-party applications or commercial use needs BMKG's written permission
(section 9.3), and machine access must go through the official API (arranged through the Legal, Public Relations and Cooperation Bureau).

- `warnings.run(..., bmkg=False)` is the default: BMKG is not requested and BMKG warnings left in the file are dropped. The ingest script turns it on
  only when the repository variable **`BMKG_WARNINGS` is "on"** (it is not set). The workflow passes the variable.
- The site hides BMKG separately (`bmkgLive` in the site repository, `docs/warnings-flood.md`), and its Terms and Privacy say MetMalaysia only.
- Permission letter (English, unsent): `docs/letters/letter-indonesia-bmkg.md` in the site repository. **Switch it back on only with BMKG's
  written permission and its official channel** (which may mean rewriting `fetch_bmkg` for the API): set `BMKG_WARNINGS` to `on` here, and `bmkgLive` to
  `true` in the site's `features.json`, in the same release.
- Nothing from BMKG is kept: the file only ever held what was in force, and the next run overwrites it.


`borneosky/warnings.py` and `scripts/ingest_warnings.py` write one small file, `warnings/current.json`, from the agencies' own
open feeds. Decided and built 3 Oct 2026; the site side is described in the site repo's `docs/warnings-flood.md`.

## Sources

| Agency | Feed | Covers | Terms |
| --- | --- | --- | --- |
| MetMalaysia | `https://api.data.gov.my/weather/warning/` (JSON; the address without the final slash redirects) | Sarawak, Sabah, Labuan: thunderstorms, continuous rain, strong wind and rough seas, tropical cyclones (earthquakes are left out) | Malaysia's open data, CC BY 4.0, no key; credit MetMalaysia |
| BMKG (**OFF since 5 Oct 2026**, see above) | `https://www.bmkg.go.id/alerts/nowcast/en` (RSS) and one CAP file per alert (`/en/` or `/id/`) | The five Kalimantan provinces | Terms of Use: written permission for commercial use, official API for machine access; credit "Sumber: BMKG" |

## The file

`{schema, generated_utc, sources: {metmalaysia|bmkg: {ok, checked_utc, count, error?}}, warnings: [...]}`. Each warning: `id`, `source`,
`agency`, `kind` (`thunderstorm`, `heavy_rain`, `flood`, `wind_sea`, `cyclone`, `other`), `level` (`info`, `alert`, `warning`, `danger`),
`urgent`, `sea`, `states` (`sarawak`, `sabah`, `labuan`, `kalimantan`), `whole` (the states a MetMalaysia warning names as a whole, not district by district), `towns` (our town slugs), `title`/`text`/`advice` in the
agency's words (`en`+`ms` or `en`+`id`), `issued`, `valid_from`, `valid_to` (UTC), `url`. Only warnings in force or coming, and those that ended
less than 30 minutes ago, are kept. Urgent = a rain, flood or cyclone warning at warning or danger level, or a severe thunderstorm.

## How towns are matched

- **MetMalaysia** names areas as `Sabah: West Coast (Tuaran and Kota Belud), Kudat (Kudat)` or `Kuching` alone. A town is covered when
  its district is named, or its division (zone) is named with no district list, or its whole state is named. The table is `AREAS` in
  `warnings.py`; `scripts/test_warnings.py` checks that every Sarawak, Sabah and Labuan town has an entry. Sea warnings cover no town.
- **BMKG** gives polygons: a town is covered when its point is inside one.

## Levels, honestly

MetMalaysia's continuous-rain levels (Alert, Warning, Danger; Waspada, Amaran, Bahaya) are read from the words of the warning. The feed had
no continuous-rain warning on 3 Oct 2026, so that reading follows news reports of the wording, not a sample of the feed itself: **when the
first real one arrives (the northeast monsoon starts in November), read the run log line for it and check `level`.** An unlabelled rain
warning is shown as a warning, never lower. BMKG's CAP severity maps Extreme to danger, Severe to warning, Moderate to alert (rain and
flood) or info (thunderstorm).

## Switching on

1. Repository variable `WARNINGS` = `on` (Settings > Secrets and variables > Actions > Variables); anything else stops the step at once.
2. Run the Ingest workflow once by hand and check `https://data.borneosky.com/warnings/current.json`.
3. The site has its own switch, `warningsLive` in `src/lib/features.json`.

Run it by hand: `python scripts/ingest_warnings.py --dry-run` (writes ./out/warnings/current.json). Tests: `python scripts/test_warnings.py`.
The hourly schedule is the ingest's; a warning issued just after :23 reaches the site up to an hour later (the site shows when it was checked).
