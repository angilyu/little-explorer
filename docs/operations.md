# Running Little Explorer

Accounts, limits, admin tools, deploys and builds: the parts you need to run your own copy. Back to the [README](../README.md).

## Parent gate

The Grown-ups corner is behind the parent gate (the "Grown-ups" button after the last shelf of the library). The gate writes a random number from 101 to 999 in words, like "three hundred forty-eight", and the
grown-up types its digits on the gate's number pad (no multiple choice, no digits in the prompt). Round hundreds
are skipped. Three wrong answers lock it for 30 seconds, and each miss shows a new number.

## Accounts

A grown-up signs in with an email and a 6-digit code sent to it; there are no passwords, and signing up is the
same as signing in. Each account has its own books, stars, coins and daily Milo time, shared by every iPad
and iPhone signed in with that email. The only personal data is the grown-up's email: nothing about the child is asked for. See [PRIVACY.md](../PRIVACY.md).

- **Signed out**, the app shows a welcome screen: a fan of photos from the books, what's inside, and a card for
  grown-ups. Tapping **Sign in or sign up** asks for the email and the code right away: there is no parent gate
  before sign-in, since it only asks for an email (the gate still guards the Grown-ups corner, coin trades and
  Milo's consent when **Talk to Milo** is tapped before Milo is allowed). The code works for 10 minutes and 5
  tries; a new one can be sent after 60 seconds (at most 5 an hour per email, 20 an hour per network, and
  `PIP_EMAIL_DAILY_CAP`, default 100, a day in all). The sign-in token is kept in the Keychain on this device only.
- **Milo consent.** Milo starts only when the account's `accounts.milo_consent` is `yes`. After every sign-in
  where it isn't `yes`, the app shows what Milo sends (the child's voice and the page, to OpenAI through LiveKit)
  and keeps, and asks **Allow Milo** (saves `yes` via `POST /account/milo-consent`) or **Not now** (saves
  nothing). Accounts that said `yes` never see it, on any device. While consent isn't `yes` (NULL, or `no` from the
  Grown-ups corner), **Talk to Milo** stays visible; tapping it shows the parent gate, then the same consent
  screen: **Allow Milo** saves `yes` and starts Milo, **Not now** goes back to the page. This applies to devices
  that were signed in before the consent screen too. The Grown-ups corner has a **Milo, the AI voice buddy**
  switch: turning it on shows the consent text and saves `yes`, turning it off saves `no`.
  The gating is in the app. `/token` answers 403 `milo_off` only for `no`; NULL is still allowed, so builds
  without the consent screen keep working. The tradeoff: an app build that doesn't check consent (anything
  before #38) can still start Milo for an account that never said `yes`.
- **Signed in**, the shelf shows the account's books. **Sign out** and **Delete account…** are in the Grown-ups
  corner (behind the gate). Signing out keeps everything on the server. Deleting asks for a new code sent to
  the account's email, then removes the email, books list, stars, coins, reading activity (and any hearts from
  older builds) from the server and signs out every device (Apple requires apps with accounts to offer this).
- Each account's copy of the stars and coins is saved on the device per account, so they show offline,
  and is removed on sign-out. What the device saved before accounts stays under its old keys and is never deleted.
- **Reading activity** (signed in only, never in demo launches): the app records each page view (book, page,
  seconds on the page up to 10 minutes, whether Milo was on), each read-aloud start, finish or early stop (and
  whether the bundled natural-voice clip or the system voice read it) and one app open a day per device. Ids and
  timings only: no audio, no text. Events wait in a queue on the device (at most 500, oldest dropped) and go to
  `POST /account/reading` in batches of up to 100 when the app comes to the foreground or goes to the
  background, every minute while it's open, and once 50 are waiting. A failed send keeps them for later and
  never affects reading; signing out drops that account's unsent events. See
  [Reading activity](#reading-activity).
- Milo's daily time is the account's too: every device signed in to it shares the minutes (see
  [Milo time limits per account](#milo-time-limits-per-account)).

**Devices from before accounts.** Stars and coins used to belong to a device id. The first account a device
signs in to claims them (`legacy_account_id`), and the app sends that account what it kept only on the device:
any stars the server is missing (`POST /account/import`, safe to repeat). A different account
signing in on that device later starts with its own things. Stars Milo gives while a device is signed in go to
the account: the app sends its sign-in with `/token`, and the agent awards the stars for that account. Older app
builds keep working: `GET /stars?device=`, `GET /coins?device=` and `POST /coins/convert` answer with the
account's stars and coins when the device is linked to one, and their Milo minutes count for that account too.

**Email.** Production sends the codes from the family's Gmail with the **Gmail API** over HTTPS
(`PIP_EMAIL_PROVIDER=gmail`), because Render's free plan blocks outgoing SMTP. SMTP (e.g. Gmail with an App
Password) works on a paid plan or locally, and [Resend](https://resend.com) needs a domain you own.

To set up the Gmail API once:

1. In [Google Cloud console](https://console.cloud.google.com), make a project, enable the **Gmail API**, and
   set up the OAuth consent screen (External, app name "Little Explorer", your email). Add the
   `.../auth/gmail.send` scope, then **Publish app** so it is "In production": while it's in "Testing", Google
   expires the token after 7 days. It doesn't need Google's verification for your own account; the consent page
   just says the app isn't verified (Advanced → Go to Little Explorer).
2. Credentials → Create credentials → OAuth client ID → **Desktop app**.
3. From `server/`, run `uv run python tools/gmail_token.py --test-to you@gmail.com`. It asks for the client ID
   and secret, opens Google's page (sign in as the sender, allow "Send email on your behalf"), saves
   `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` and `GMAIL_REFRESH_TOKEN` to the env file without printing them,
   and sends a test email.
4. On Render set those three and `PIP_EMAIL_PROVIDER=gmail`, and redeploy.

If the codes stop arriving with `gmail token refresh failed: 400 invalid_grant` in the server log, the access was
removed (or the app went back to Testing); run step 3 again and update `GMAIL_REFRESH_TOKEN`.



| Env var | What it is |
|---|---|
| `PIP_AUTH_SECRET` | Long random string; codes and sign-in tokens are stored as HMACs with it. Required. |
| `PIP_EMAIL_PROVIDER` | `gmail`, `smtp`, `resend`, or `console` (local development: logs the code instead of sending it) |
| `PIP_EMAIL_FROM` | Sender, e.g. `Little Explorer <you@gmail.com>` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | e.g. `smtp.gmail.com`, `587` (STARTTLS; `465` uses SSL), the Gmail address and a Gmail App Password |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | For `gmail` (from `tools/gmail_token.py`) |
| `RESEND_API_KEY` | For `resend` |
| `PIP_DEFAULT_BOOKS` | Comma-separated book ids for new accounts (default: the 13 books above) |
| `PIP_EMAIL_DAILY_CAP` | Most codes sent a day, for all emails together (default 100) |
| `PIP_REVIEW_EMAIL`, `PIP_REVIEW_CODE` | Optional: an email that signs in with a fixed code and no email, for App Store review |

Without an email provider, `POST /auth/start` answers `503 email_off`.

**Endpoints** (all with the app's access key; signed-in ones also with `X-Pip-Session: <token>`, which
`POST /token` takes as well):
`POST /auth/start {email, purpose?}`, `POST /auth/verify {email, code, device}` (returns `token`, `account`,
`claimed_device`), `POST /auth/signout`, `GET /account`, `POST /account/delete {code}`, `GET /account/stars`,
`GET /account/coins`, `POST /account/coins/convert`, `POST /account/import {stars}` and
`POST /account/reading {events}` (returns `stored`, `duplicates`, `rejected`). The app used to have
a heart button for pages the child loved; it's gone, but the server still answers `GET`/`POST /account/hearts
{loved, unloved}` and takes `hearts` in `/account/import` so older builds keep working, and keeps the hearts
already saved (deleting the account removes them). Errors: `bad_email`, `bad_code` (with `tries_left`), `expired`,
`too_many` (with `retry_after_s`), `signed_out`, and for reading activity `bad_events` and `too_many_events`
(with `max`).

**Owner tools.** Approve accounts, grant books, link devices and back up the data with `tools/devices.py` (from `server/`):

```sh
uv run python tools/devices.py accounts                               # every account: Milo time, books, devices, stars, coins
uv run python tools/devices.py account mom@example.com                # one account and its devices
uv run python tools/devices.py approve mom@example.com                # Milo time: also block, unblock, limit <email> 30|default
uv run python tools/devices.py create-account mom@example.com --all-books
uv run python tools/devices.py grant mom@example.com hawaii tahoe     # show more books; revoke hides them again
uv run python tools/devices.py all-books mom@example.com on           # every book, including future ones
uv run python tools/devices.py link mom@example.com <device id>       # as if it signed in there; it claims the device
uv run python tools/devices.py link mom@example.com <device id> --no-claim   # ...but its earlier stars and coins count for no one
uv run python tools/devices.py unlink mom@example.com <device id>
uv run python tools/devices.py reading --days 7                      # reading activity: per day, account and book
uv run python tools/devices.py backup                                 # every table to backups/<time>.json (git-ignored)
uv run python tools/devices.py restore backups/<time>.json            # replaces ALL data, asks first
```

The same as HTTP: `GET`/`POST /admin/accounts`, `GET /admin/accounts/<email or id>`,
`POST /admin/accounts/<ref>/link|unlink {device, claim?}`, `POST /admin/accounts/<ref>/books {grant, revoke, all_books}`,
`POST /admin/accounts/<ref>/milo {status?, daily_limit_min?}` (`null` limit = the status's default),
`GET /admin/backup` and `POST /admin/restore {"confirm": "restore", "backup": ...}`, all with
`Authorization: Bearer $PIP_ADMIN_KEY`. The device list shows each device's account.

## Run the server

No audio is stored. The app never holds LiveKit secrets. A small token server mints room tokens that dispatch the agent automatically.

```sh
cd server
cp .env.example .env        # fill in LIVEKIT_* and OPENAI_API_KEY (never commit .env)
uv sync
uv run python -m little_explorer_agent.token_server   # http://127.0.0.1:8787
uv run python -m little_explorer_agent.agent dev      # the Milo worker
uv run pytest -q
uv run python tools/e2e_smoke.py                       # live end-to-end check (no audio saved)
```

Secrets are loaded from the first of: the path in `LITTLE_EXPLORER_ENV`, `server/.env`, or
`~/.config/little-explorer/.env` (a per-user copy, `chmod 600`, that survives deleting the checkout). The token
server, the agent, `tools/e2e_smoke.py` and `scripts/generate_narration.py` all use this order. Variables already
set in the shell win. Admin-only keys in the same file (`OPENAI_ADMIN_KEY`, `RENDER_API_KEY`) are never loaded
by the server, so they can't be logged or passed on.

## Milo time limits per account

**Accounts.** The app sends its sign-in (`X-Pip-Session`) with every token request, and Milo's minutes count
for that account: every iPad and iPhone signed in to it shares them. The token server keeps each account's status:

| Account status | Daily Milo time | When the time is used up |
|---|---|---|
| unknown (any new account) | 10 min (`PIP_LIMIT_UNKNOWN_MIN`) | rests until tomorrow |
| approved | 60 min (`PIP_LIMIT_APPROVED_MIN`) | rests until tomorrow |
| blocked | none | until you unblock it |

A custom limit (`limit <email> 30`) replaces the status's default. Accounts are never blocked automatically. A
token request with a sign-in the server no longer knows gets `401 signed_out`, and the app asks a grown-up to
sign in again. The session's account goes into the agent's dispatch metadata, so the agent reports the minutes
and awards the stars for it.

**Devices.** The app also sends a device id: its vendor id, saved in the Keychain so it survives a reinstall,
plus the device name and model. The server keeps it to list each account's devices and the minutes each used.
Builds from before sign-in send only the device id: on a device linked to an account they use the account's
status, minutes and stars; an unlinked device keeps the old per-device rules (unknown 10 min and then blocked
automatically, approved 60, blocked none).

Days run midnight to midnight in `PIP_TIMEZONE` (default `America/Los_Angeles`). An account that is blocked or
out of time gets no token, and the app shows "Milo needs a rest today. Let's talk again tomorrow!". The token
carries the time left, so a session that reaches the limit ends with a short goodbye from Milo. When a session
ends, the agent reports its length, estimated cost, device and account to the token server (`POST /usage`, with
`PIP_REPORT_KEY`), which stores it. Usage is stored per conversation, never transcripts or audio. Minutes count
when a session ends, so two devices talking at the same moment can each use what was left.

The move from devices to accounts ran once, on the first start after the update
(`migration.milo_per_account` in `settings`): earlier sessions on linked devices count for their account (so
today's minutes carry over), an account with an approved device became approved, and an account takes the largest
custom limit of its devices.

Storage is Postgres when `DATABASE_URL` is set (production uses a free [Neon](https://neon.tech) database:
Render's free disk is wiped on every deploy and restart, and Neon keeps the data, needs no server and costs
nothing at this size). Without it, a local SQLite file is used (`PIP_DEVICES_DB`, default
`~/.config/little-explorer/devices.sqlite3`). If the database can't be reached, tokens are still given out with
the unknown limit.

Check usage and manage accounts and devices with `tools/devices.py`. It calls the server's `/admin` endpoints with
`PIP_ADMIN_KEY` from your env file (`--direct` uses `DATABASE_URL` instead). The admin key is never in the app.

```sh
uv run python tools/devices.py accounts             # accounts: Milo status, today/limit, books, stars, coins
uv run python tools/devices.py approve mom@example.com   # also: block, unblock, limit <email> 30, limit <email> default
uv run python tools/devices.py                      # devices: account, status, today, total minutes and $
uv run python tools/devices.py usage --days 14      # minutes and $ per day, account and device
uv run python tools/devices.py approve <id> --label "Leo's iPad"   # old builds on an unlinked device: by id or name
uv run python tools/devices.py reading --days 14    # reading activity per day, account and book (--json for raw)
```

Approve your family's account once; every device signed in to it shares its 60 minutes.

The app always uses the deployed server (`PIP_SERVER_URL` in `Config/Milo.xcconfig`). Debug builds can use a
local token server with a launch argument, e.g. `-voice.tokenServerURL http://127.0.0.1:8787` (simulator) or
`http://<your-mac>.local:8787` (a device), in the Xcode scheme's Arguments; release builds ignore it. Only Debug
builds use `Config/Info-Debug.plist`, which adds the local-network permission (`NSLocalNetworkUsageDescription`,
`NSAllowsLocalNetworking`) this needs; Release builds use `Config/Info.plist` without it. Keep the two plists in
step otherwise. `LittleExplorer/PrivacyInfo.xcprivacy` is the app's privacy manifest: keep it matching the App
Store privacy label.

## Reading activity

Signed-in devices send what was read to `POST /account/reading` (see [Accounts](#accounts) for what the app
records). The server keeps it in `reading_events`, one row per event with the account and device from the
session: `kind` (`app_open`, `page_view`, `read_aloud`), `at` (the device's time with its UTC offset), `day`
(the device's own date), `book`, `page`, `page_index`, `seconds`, `milo`, `outcome` (`started`, `finished`,
`stopped`) and `mode` (`clip`, `system`). It checks every event: book ids must be in the catalog, a batch holds
at most 100 events, ids and page ids are short and plain, times on a page are capped at 10 minutes, and events
older than 30 days or more than a day ahead are dropped (counted as `rejected`). An event id already stored
counts as a duplicate, so resending a batch is safe. Each account may store 1,000 events an hour and 5,000 a
day (`too_many` with `retry_after_s`; the app keeps them and tries later).

`GET /admin/reading?days=N` (1 to 366, default 7) and `tools/devices.py reading --days N` report, per day, per
account and per book: active accounts and devices, app opens, books opened (distinct account and book pairs),
pages viewed, reading minutes (time on pages) and read-aloud starts, finishes and early stops. Days are the
devices' own dates. The events are in `/admin/backup` and restore, and deleting an account deletes them.

`GET /account/reading` (signed-in session) returns the account's reading progress for the badges, worked out from
its events on every device: `{"progress": {"pages": [...], "milo_pages": [...], "listened": [...], "days": [...]}}`.
`pages` are the `book/page` ids with a page view, `milo_pages` those viewed with Milo on, `listened` those whose
read-aloud finished, and `days` the devices' dates with a page view. The app merges it into its own copy on launch
and when it comes back to the foreground.

## Badges

The Passport badges are server-controlled: `LittleExplorer/Resources/badges.json` is served in the content manifest
like the books (see [Update books without an app update](content.md#update-books-without-an-app-update)). The app
keeps the last copy it downloaded and ships the same file in the bundle, so badges work offline and on first launch.
Names, hints, goals, groups, order, icons and colors can change, and badges can be added or removed, without an app
update. Only a new kind of metric needs one.

To change the badges:

1. Edit `LittleExplorer/Resources/badges.json`.
2. Run `cd server && env -u DATABASE_URL uv run pytest -q tests/test_badges.py tests/test_catalog.py`. The server
   checks the catalog when it builds the manifest, so a bad one stops the content update (the app keeps the
   previous catalog) instead of reaching the app.
3. Merge to `main`. The Deploy workflow rebuilds Render; the next app launch picks the catalog up.

The file has `format` (1), `categories` (the groups, in Passport order: `id`, `title`, `icon`, `tint`) and `badges`
(in order within each group):

| Field | Meaning |
|---|---|
| `id` | Stable id, lowercase with dashes. The app remembers which badges were seen by id, so never reuse an old id for a different badge. A per-city badge's id contains `{city}` (e.g. `expert-{city}` becomes `expert-Paris`). |
| `category` | A category `id`. |
| `title` | The name, at most 20 characters. |
| `hint` | What to do, shown while locked (at most 44 characters). |
| `done` | What the child did, shown once earned (at most 44 characters). |
| `icon` | An SF Symbol name (iOS 17), e.g. `book.fill`. An unknown name shows a star. |
| `tint` | A `#RRGGBB` color (the app uses the group's if it can't read it). |
| `metric` | What it counts (below). |
| `goal` | How many, 1 to 10000, or `"all"` for metrics with a limit from the shelves. |
| `each` | `"city"` for per-city metrics only: the badge repeats for every city with several books. |

`{goal}` in `title`, `hint` and `done` becomes the goal (after it is lowered to what the shelves have) and `{city}` the
city. Keep the words short and simple: the badges are read by 6 and 7 year olds.

| Metric | Counts | Limit from the shelves |
|---|---|---|
| `stars` | all stars earned | |
| `star_answers` | answers (and older questions) that earned a star | |
| `long_answers` | long answers (8+ words) | |
| `because_answers` | answers that explain with "because" | |
| `stars_in_a_day` | most stars on one day | |
| `star_days` | different days with a star | |
| `morning_stars` | stars before 9 am | |
| `bedtime_stars` | stars at 7 pm or later | |
| `books_starred` | different books with a star | books |
| `cities_starred` | Passport stamps (cities with a star, not the Travel shelf) | cities |
| `city_books_starred` | books of one city with a star (`"each": "city"`) | that city's books |
| `pages_read` | different pages read (on screen 1+ second) | |
| `pages_listened` | different pages read aloud to the end | |
| `milo_pages` | different pages read with Milo on | |
| `books_finished` | books with every page read | books |
| `reading_days` | different days with a page read | |
| `reading_streak` | most days in a row with a page read | |
| `cities_read` | cities (not the Travel shelf) with a page read | cities |
| `countries_read` | countries with a page read (not the Travel shelf) | countries |

The reading metrics count the signed-in account's reading progress (see [Reading activity](#reading-activity)), so
they stay locked while signed out. The app skips any badge it can't use (an unknown metric or category, a missing
name, a bad goal) rather than failing, and falls back to the bundled catalog if the downloaded one can't be read or
has a newer `format`. A badge the child has seen earned stays earned on that device even if its goal goes up; on a
device where it wasn't seen yet the new goal applies, so raise goals sparingly. Adding a metric means adding it to
`BadgeMetric` in `Stores/BadgeCatalog.swift` and `Stores/Badges.swift`, and to `METRICS` in
`server/src/little_explorer_agent/badges.py`, then shipping the app before using it in the catalog.

## Coins

A grown-up can turn stars into coins (10 stars = 1 coin by default) in the Grown-ups corner, behind the parent
gate. It shows the stars the child can spend, the coins they make and the stars left over, and asks for confirmation.
The grown-up can convert as many coins as the stars cover (stepper, or quick picks like 5 and "All N"), unless
the server sets a maximum. The kid can't convert. The child's coins (a gold dollar coin) always show next to the stars
on the library badge and in the Passport, even at 0.

The token server owns the rules and the balance. It keeps a ledger per account (per device for a device that
isn't signed in; `coin_conversions`), so coins survive a reinstall and an app can't convert more stars than the server has stored. Spending stars lowers the
number on the badge and in the book, but badges and Passport progress keep counting every star the child ever earned,
so converting never takes a badge away. Older app builds ignore coins and keep showing all the child's stars.

- `GET /coins?device=<id>` (access key) returns the rules (`enabled`, `stars_per_coin`, `min_coins`, `max_coins`)
  and the balance (`stars_earned`, `stars_spent`, `stars` to spend, `coins`, `can_convert`, `leftover_stars`).
- `POST /coins/convert` (access key) with `{"device", "id", "coins"?, "stars_per_coin"?}`. `id` is a
  client-made id; a retry with the same id returns the first result and never converts twice. Leave out
  `coins` to convert as many as possible. If `stars_per_coin` is sent and the rate changed since the screen
  loaded, the server refuses with `409 rate_changed`. Other refusals: `403 disabled`, `409 not_enough_stars`,
  `400 too_few` / `too_many`.

Change the rules without an app release. Admin overrides win over env vars, which win over the defaults:

| Setting | Env var | Admin field | Default |
| --- | --- | --- | --- |
| Conversion on or off | `PIP_COINS` (`0` turns it off) | `enabled` | on |
| Stars per coin | `PIP_STARS_PER_COIN` | `stars_per_coin` | 10 |
| Fewest coins per conversion | `PIP_COINS_MIN` | `min_coins` | 1 |
| Most coins per conversion (0 = no limit) | `PIP_COINS_MAX` | `max_coins` | 0 |

```bash
cd server
uv run python tools/devices.py coins                   # current rules and overrides (the device list has a COINS column)
uv run python tools/devices.py coins rate=20 max=5     # 20 stars per coin, at most 5 coins at a time
uv run python tools/devices.py coins off               # turn conversion off (on turns it back on)
uv run python tools/devices.py coins rate=default      # back to the env var or default for one setting
uv run python tools/devices.py coins reset             # clear every override
curl -H "Authorization: Bearer $PIP_ADMIN_KEY" https://little-explorer-pip.onrender.com/admin/coins
curl -X POST -H "Authorization: Bearer $PIP_ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"stars_per_coin": 20}' https://little-explorer-pip.onrender.com/admin/coins   # null clears a field
```

The app reads the rules each time the Grown-ups corner opens, so changes apply right away.

## Deploy

Two free-tier pieces. The app only talks to the first; LiveKit dispatches the second.

| Piece | Where | What it serves |
|---|---|---|
| Token and book server (`server/Dockerfile.web`) | Render web service `little-explorer-pip` (free plan), https://little-explorer-pip.onrender.com | `POST /token` (needs `Authorization: Bearer $PIP_ACCESS_KEY`), `/content/…` (books, photos, narration), `/healthz` |
| Milo agent (`server/Dockerfile`) | LiveKit Cloud agent, `server/livekit.toml` | joins each room the token server creates |

**Pushing to `main` deploys automatically** (`.github/workflows/deploy.yml`). The workflow works out what
changed, runs the server tests, and deploys only the pieces that need it:

| Change on `main` | Render | LiveKit agent |
|---|---|---|
| `server/src/**` | ✓ | ✓ |
| Books, photos, narration, `library.json` (`LittleExplorer/Resources/**`) | ✓ | – (Milo fetches books from Render) |
| `server/Dockerfile.web`, `.dockerignore` | ✓ | – |
| `server/Dockerfile`, `server/livekit.toml`, `server/pyproject.toml` | – | ✓ |
| `server/requirements*` | ✓ | ✓ |
| iOS app, docs, scripts, tests only | – | – |

- Nothing deploys if the tests fail. One deploy per service runs at a time; a newer push waits for it. Pull
  requests that touch the server or the books run the same tests (`server-tests.yml`, no secrets).
- The Render job calls the service's deploy hook for that exact commit, then waits until `/healthz` reports the
  commit and `/content/manifest.json` matches the commit's books. The agent job runs `lk agent deploy` and waits
  until LiveKit reports that commit as the live, available version. Either job fails if its deploy doesn't go live.
- **Actions → Deploy → Run workflow** (on `main`) force-deploys Render, the agent, or both.
- Render's own auto-deploy is **Off**, so every deploy goes through the tests. (Before, Render watched the
  `angilyu-pip-deploy` branch, so merges to `main` never auto-deployed and were deployed by hand.)
- Secrets are in the repo's `production` environment, which only `main` can use: `RENDER_DEPLOY_HOOK_URL` (from
  the Render service's Settings → Deploy Hook) and `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
  Nothing runs on pull requests with secrets. To rotate one, pipe it in so it isn't echoed or saved in shell history, e.g.
  `grep '^LIVEKIT_API_SECRET=' ~/.config/little-explorer/.env | cut -d= -f2- | tr -d '\n' | gh secret set LIVEKIT_API_SECRET --env production`.
- The agent's runtime secrets (`OPENAI_API_KEY`, `PIP_*`) stay in LiveKit; the workflow keeps them and checks
  they exist. Change them with `scripts/deploy_agent.sh`, which also works for a manual deploy.

- **Render** builds `server/Dockerfile.web` from the repo root. Its env vars are
  `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `PIP_ACCESS_KEY`, `KEEPALIVE_SECONDS`, `DATABASE_URL`,
  `PIP_ADMIN_KEY`, `PIP_REPORT_KEY` and the [account email settings](#accounts) (`PIP_AUTH_SECRET`,
  `PIP_EMAIL_PROVIDER`, `PIP_EMAIL_FROM`, `GMAIL_*`), set in the Render dashboard, not in git. A free service sleeps after 15 idle minutes, so with `KEEPALIVE_SECONDS` set the
  server pings its own `/healthz` to stay awake (one always-on service fits in the free 750 hours a month). If it
  does fall asleep (e.g. after a redeploy fails), the first token takes about a minute.
- **Milo warm-ups** (`POST /milo/warm`, see [Waking Milo up](milo.md#waking-milo-up-cold-starts)) wake the LiveKit
  agent when a book opens, since the free Build plan scales it to zero between sessions (a 10–20 s cold start).
  Optional Render env vars: `PIP_WARM=0` turns them off, `PIP_WARM_DEVICE_GAP_S` (default 600) and `PIP_WARM_FRESH_S`
  (default 300) space them out. Render logs `warming Milo for device …`; `lk agent logs` shows `warm-up job in room
  milo-warm-…` and no usage report. A warm-up never joins its room, so LiveKit meters no agent minutes for it; keep an
  eye on the dashboard's agent session minutes (1,000 a month on the Build plan) anyway.
- **Keep-awake ping** (`.github/workflows/keep-awake.yml`) GETs the public `/healthz` every 10 minutes as a
  backstop, so the app (and App Review) doesn't hit that ~50 s wake-up even if the self-ping stops. It needs no
  secrets and never deploys or runs tests. GitHub may delay scheduled runs (often by several minutes, more at busy
  times), and it disables scheduled workflows after 60 days without repo activity; re-enable it under
  **Actions → Keep server awake**. To turn it off, use **Actions → Keep server awake → ⋯ → Disable workflow**
  (or `gh workflow disable keep-awake.yml`), or delete the file.
- **Agent:** `scripts/deploy_agent.sh` reads the env file, uploads `OPENAI_API_KEY` and the `PIP_*` settings as
  LiveKit secrets, and creates the agent (first run, region `$LK_AGENT_REGION`, default `us-east`) or deploys a
  new version. Set `PIP_CONTENT_URL=https://little-explorer-pip.onrender.com/content` so Milo reads the same
  books as the app. Set `PIP_SERVER_URL=https://little-explorer-pip.onrender.com` and the same `PIP_REPORT_KEY`
  as Render so Milo reports each account's minutes and stars. Stop any local `agent dev` worker afterwards; it would compete for the same rooms.
  `lk agent status .` and `lk agent logs .` (from `server/`, with `LIVEKIT_*` in the environment) show it.
- **Check it:** `TOKEN_URL=https://little-explorer-pip.onrender.com/token uv run python tools/e2e_smoke.py --mode realtime --trials 1`.

## Build and test the app

Requires Xcode 26 (iOS 26 SDK). The project is checked in; no xcodegen needed.

```sh
xcodebuild -project LittleExplorer.xcodeproj -scheme LittleExplorer \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
scripts/screenshots.sh <iphone-udid> <ipad-udid>   # regenerates docs/screenshots
```

DEBUG builds accept launch arguments for screenshots: `-demoBook paris-food -demoPage 4`,
`-demoVoice listening|live|nap|rest|star`, `-demoRead`, `-demoStars 23`, `-demoPassport`, `-demoCalendar`
(the Passport's star calendar, with stars spread over the last few weeks), `-demoBadges` (the Passport's badges,
some earned and some not, from 40 stars, an early-morning star and a few weeks of reading, with the "New badge!"
celebration; `-demoBadges seen` without it, `-demoBadges reading` or another group id to open that group), `-demoCoins 2`,
`-demoGate` (the parent gate on the shelf), `-demoAccount welcome|email|code` (the signed-out welcome screen and
sign-in's email or code step), `-demoParent` (the Grown-ups corner) and `-demoMilo
signin|talk|talk-consent|off|turn-on` (the Milo consent screen after sign-in, the gate and consent when Talk to Milo
is tapped before Milo is allowed, the corner with Milo off, and the consent its switch shows when turned on).
`-demoShelf grown-ups` scrolls to the Grown-ups button.

### Install on an iPhone or iPad (TestFlight)

The app is "Little Explorer: City Books" in App Store Connect, and every build goes to the internal TestFlight group
"Family". Install **TestFlight** from the App Store on the device, sign in with the developer Apple ID, accept the
invite, and turn on Automatic Updates for the app. New builds then install themselves; no cable is needed.

```sh
scripts/testflight.sh               # archive, sign for App Store distribution, upload, and wait for Apple's processing
scripts/testflight.sh --app-store   # the same, but the build can also be submitted for App Review
```

By default the build is for internal testing only, and App Store Connect won't attach it to an App Store version. Use
`--app-store` for a build you plan to submit for review; it still goes to "Family" as usual.

It needs an App Store Connect API key (Admin role): the `.p8` in `~/.appstoreconnect/private_keys/`, and
`ASC_KEY_ID` and `ASC_ISSUER_ID` in the environment or `~/.config/little-explorer/asc.env`. The build number is
the date and time. `PIP_ACCESS_KEY` from `Config/Local.xcconfig` is built in, so keep TestFlight to internal
testers. Book changes don't need a new build (see [Update books without an app update](content.md#update-books-without-an-app-update)).

> **Simulator note:** the iOS 26.2/26.3 simulators draw every emoji as a "?" box. Real devices are fine.
>
> **Voice in the simulator:** the simulator has no Apple echo cancellation, so the app uses WebRTC's
> software echo cancellation there. Milo can still hear itself through loud Mac speakers; headphones
> give the cleanest test.
> The app avoids emoji in its UI: flags are bundled images, and buttons use SF Symbols.
