# Sarawak outage notices (built, switched off until you say)

Power interruptions in Sarawak, read from **Sarawak Energy's own posts on X** (https://x.com/1SarawakEnergy). Its website
has no list and its SEB Cares alerts need an account, so X is the only place these notices are public, and X's paid API
is the sanctioned way to read them. The reader (`borneosky/sarawak.py`), its 29 checks and the site's three places for
the notices are written and tested. Until the repository variable `SARAWAK_NOTICES` is `on`, the hourly step does
nothing, and until the site's `sarawakNoticesLive` is on, visitors see nothing.

The reasoning, the cost estimate, X's rules and the wording are in the site repository's `docs/outages-notices.md`. This
note is only about the pipeline.

## What runs

- `scripts/ingest_sarawak.py`, from the hourly workflow (step `sarawak`), reads the account with X's API, using the
  Bearer Token `X_BEARER_TOKEN` (a GitHub secret, given to that step only; locally a line in `~/Bs/project/.env`).
- **Each hour it asks only for posts newer than the newest one it has seen** (`since_id`), from the user timeline, with
  replies and retweets left out and no fields or expansions asked for. An answer with no new posts costs nothing (tested
  against the real API on 26 Sep 2026). The first run reads the last 48 hours; the account's numeric ID is looked up once.
- **Once a UTC day** it asks X again about the notices it holds that were not read today (`GET /2/tweets?ids=`). X answers
  "not found" for a deleted post, and the notice is removed here too. That is what X's Developer Policy asks (delete
  within 24 hours of a request); any other answer leaves the notice in place.
- Output, to R2: `notices/sarawak-energy.json`: `notices` (kept at most 48 hours after their last news), `checked_utc`
  (when X last answered), `counts`, and `state` (the account's ID, the newest post ID, the day of the last look, and posts
  read per day for the last 35 days). `source_changed_utc` is always null: a quiet account is normal, so we never say
  Sarawak Energy has gone silent. The site reads only this file, never X.
- **What is kept from a post: facts and its ID. Never its text.** The town, the cause (a code), the repair stage (a
  code), the affected places (as written, links and @names removed), the repair estimate, whether it says UPDATE or that
  supply is back. Places are facts; the sentence around them is thrown away. The site builds the link to the post from its ID.
- **Times.** A post carries no timestamp in the answer, so the time is decoded from the post ID (`(id >> 22) + Twitter's
  epoch`), checked against six known posts. Repair estimates (a time after "by", "estimated" or "expected") are turned into the next
  time that hour comes round in Sarawak (UTC+8), or the date the post names ("12 noon, 24 September 2026"). An estimate
  more than 48 hours out, or already past, is not believed.

## What the account posts (looked at on 26 Sep 2026, once, 20 posts, US$0.11)

- **Only fault notices as top-level posts**, about one every 1.2 days (20 in 25 days), in one shape: the town in capitals
  and a colon; the cause and the places affected, often ending "and surrounding areas"; where the repair stands (fault
  finding, repair works, extensive repair needed, or paused until later, usually for site conditions); and an estimated
  completion time. Follow-ups start with the town and the word UPDATE and give a new estimate. No planned interruptions
  and no "supply restored" posts were among them.
- So it is **not** the 2 to 5 a day we assumed. At this rate the reading costs about US$0.12 a month, and about US$0.35 with
  the daily look at deleted posts.
- It names towns and localities (Miri, Serian, Bau, Niah, Igan, Bekenu, Kanowit, Siburan, ...), not districts. Only 13 of
  them are towns on BorneoSky; the others are shown on the Outages page only.
- The reader read all 20 without a miss. Anything it cannot read becomes an unread notice (ID and time only). It will
  meet shapes it has not seen (planned notices, restoration posts); the notes below say how to tune it.
- **Two faults in one town can overlap** (Miri, 10-11 Sep). An update replaces only the latest earlier notice in its
  town, and hands on the places and cause it does not repeat; the other notice stays.
- The account misspells "interruption" as "interuption" at times; the reader allows for it.

## What it costs and how it is held down

X charges US$0.005 per post returned, once per post per UTC day, US$0.01 for the one user lookup, and nothing for an empty
answer (page read 26 Sep 2026; prices "are subject to change"). The file records posts read per day, and the run prints the
month's estimate. **A hard cap stops the reading for the day** (`DAILY_POST_CAP`, 150 posts, US$0.75) whatever the
spending limit at X is; `--force` does not reset it. Set a monthly spending limit at X as well (US$5 is plenty).

## When it goes wrong

- Any refusal or error from X (a bad token, no credits, a 429, a 5xx after three tries) **uploads nothing**: R2 keeps the
  last good file, the step turns the run red, and `checked_utc` stops moving, so the site says "we couldn't check for N
  hours". A message names the HTTP status and X's own title, never the token.
- No token with the switch on is an error, not a silent skip, so a missing secret is seen at once.
- `meta.json` gets a `sarawak` source ("Sarawak Energy outage notices", delayed after 6 hours). It appears only once the
  step has run.
- More new posts than one run reads (300) is reported as a warning; it does not happen at this account's rate.

## Switching on and off

- **On:** the secret `X_BEARER_TOKEN` exists (repository Settings, Secrets and variables, Actions, **Secrets**) and the
  variable `SARAWAK_NOTICES` = `on` (same page, **Variables**). The next run (23 minutes past) writes the file. Do it
  before the site's `sarawakNoticesLive` is set, so the file exists on day one.
- **Off, at once and with no deploy:** set the variable to anything else, or delete it. R2 keeps the last file; delete
  `notices/sarawak-energy.json` there as well if Sarawak Energy or X asks us to stop.
- **A stolen or leaked token:** regenerate the Bearer Token in the X developer console, then update the secret.

## Working on the reader

- `python scripts/test_sarawak.py`: 29 checks with invented posts and a stand-in for X's API. No network, no credits.
  Every post in the tests is invented in the account's shape, with invented places; the account's short vocabulary ("repair
  works", "fault finding", "and surrounding areas") is the parser's own and appears in them. **No real post is quoted
  anywhere in this repository** (the six real post IDs in the tests are IDs, which X allows us to pass on).
- `python scripts/ingest_sarawak.py --sample 20` reads the latest 20 posts (about US$0.11) into `out/_sample/`, which git
  ignores, and prints only counts. Read that file to see the real wording when tuning `borneosky/sarawak.py`. **It is X's
  content: never commit it, paste it into a doc or test, print it to a log, or upload it.** Delete it when done.
- `python scripts/ingest_sarawak.py --dry-run` runs the whole pipeline against the real API and writes `out/` instead of R2
  (costs the posts it reads); a second dry run shows the "nothing new" path.

## Rules and permission

- X's [Developer Policy](https://docs.x.com/developer-terms/policy): only Post IDs may be passed on to third parties, and
  stored content must follow deletions. Hence facts and IDs only, and the daily look. X reviews each developer's stated use.
- Sarawak Energy's own copyright in its posts is not affected by paying X. Restating facts with attribution and a link to the
  post is the usual low-risk practice, but low risk is not permission: the letter to its Corporate Communications is still
  worth sending (see `docs/outages-notices.md` in the site repository).
- We identify ourselves with `BorneoSky/0.1 (+https://borneosky.com; contact)` and never ask more than once an hour.
