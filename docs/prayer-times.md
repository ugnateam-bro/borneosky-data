# Prayer times (not built: waiting for permission)

The owner wants prayer times on the Towns page (as numbers, next to smoke, fires and weather) and on each town
page. BorneoSky is commercial, so every source must allow commercial use. None of the three authorities does
in writing, so this waits for permission. Ramadan 1448 is projected to start on **8 February 2027** (subject to
moon sighting): aim to have Malaysia ready by mid-January.

## Sources checked on 25 Sep 2026

| Country | Official source | What the site says about reuse |
| --- | --- | --- |
| Malaysia | JAKIM e-Solat, `e-solat.gov.my` (zones such as SWK01 to SWK09, SBH01 to SBH09, WLY01) | "All rights reserved" (JAKIM, 2020). Reuse not stated. Its API is undocumented, and republishers say the times are JAKIM's copyright. |
| Brunei | Ministry of Religious Affairs, `mora.gov.bn/SitePages/WaktuSembahyang.aspx` | "All rights reserved" (2023). Reuse not stated. The table loads dynamically. Belait adds 3 minutes and Tutong 1 minute to the national table. |
| Indonesia | Kemenag Bimas Islam, `bimasislam.kemenag.go.id/jadwalshalat` | "All rights reserved" (2020). Reuse not stated. API access is reported to be by email request (`humasbimasislam@kemenag.go.id`; verify on their site before writing). |

Times listed by each: Imsak, Subuh, Syuruk, Dhuha, Zohor, Asar, Maghrib, Isyak.

## Why not calculate them ourselves

The official tables use one reference point per zone, with the authority's own safety minutes. A calculation
for each town would differ from the timetable people follow by a minute or two, and in Ramadan people break
their fast by it. Only the official times are shown, or nothing.

## What to ask (one letter per authority)

Permission to show the official times for our towns, free and ad-supported in future; how (a rolling week,
fetched once a day, credited to the authority with a link); and, for JAKIM, whether the times fall under the
Malaysian Government Open Data Terms of Use 1.0, which already allow commercial reuse with attribution.

## Ready to build the day a yes arrives (about a day for Malaysia)

- Pipeline: `borneosky/prayer.py`, a daily step, `prayer/{country}.json` in R2 with a rolling week per town,
  `prayer` source in `meta.json`. Needs each town's zone: JAKIM zone codes for the 23 Malaysian towns, the four
  Brunei districts with their offsets, and Kemenag's city for the 16 Kalimantan towns (added to the registry).
- Site: a fourth tab "Prayer times" on the Towns page (next prayer and its time for each town), and a card with
  all the day's times on each town page, times shown in the town's own time zone.
- Text: About sources table, Terms ("not a religious authority: follow your local mosque or religious
  department"), the footer credit.
- Each country can go live on its own; a town whose authority has not said yes shows the usual explained empty
  state with a link to the official page.
