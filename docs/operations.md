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

No audio is stored. Every Milo session starts with `AgentSession.start(record=False)` (pipeline and realtime), so
LiveKit Cloud's Agent observability uploads no audio, transcripts, traces or logs for Milo, even with it on for the
project. The one exception is `PIP_RECORD_ACCOUNTS`: a comma-separated list of account ids (shown by
`tools/devices.py account <email>`) whose sessions LiveKit does record, for debugging with our own test accounts. Set
it in the env file and run `scripts/deploy_agent.sh`; leave it empty to record nothing. List only our own accounts:
recording anyone else's child needs their consent and a PRIVACY.md change first. To check after a deploy, open a new
session under Sessions in the LiveKit dashboard: **Agent insights** should have no audio or transcript (or have them,
for a listed account). The worker's runtime logs (`lk agent logs`) still hold the short turn text that
`_log_decision` writes. The app never holds LiveKit secrets. A small token server mints room tokens that dispatch the agent automatically.

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
| `star_answers` | answers and questions that earned a star | |
| `long_answers` | long answers or questions (8+ words) | |
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

## Made books (cloud generation)

The server can write a new book for an account: the text, real Wikimedia Commons photos and the read-aloud
narration. Then it publishes the book into `content/manifest.json`, so the current app shows it on that account's shelf with no
app update. For now only the owner starts a book (through the admin endpoint below); the parent page comes later.
The code is in [`server/src/little_explorer_agent/bookgen/`](../server/src/little_explorer_agent/bookgen).

**Who may make books.** `accounts.can_generate` is the dogfood flag. A one-time migration
(`migration.bookgen_dogfood`) turns it on for `angilyu@gmail.com` and `hfevers@gmail.com`, and new accounts with
those emails get it too. Turn it on for one more account, or off again:

```bash
curl -X POST -H "Authorization: Bearer $PIP_ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"can_generate": true}' https://little-explorer-pip.onrender.com/admin/accounts/someone@example.com/bookgen
```

To **turn it on for everyone**, use `POST /admin/bookgen {"everyone": true}` (a saved setting; `false` turns it back
off), or set `BOOKGEN_EVERYONE=1` on Render. Either one makes every account's `can_generate` true. The account JSON
already carries `can_generate`, so the Phase 2 parent page can use it.

**Make a book** (202 means it is queued):

```bash
curl -X POST -H "Authorization: Bearer $PIP_ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"account": "angilyu@gmail.com", "title": "Owls at Night", "section": "Animals", "level": 1,
       "note": "barn owls, how they hunt in the dark"}' \
  https://little-explorer-pip.onrender.com/admin/books/generate
```

- `title` (the cover title), `section` (the shelf it goes on; an existing city such as `Paris` or a new name) and
  `level` (1, 2 or 3) are required; `note` (what the book should be about) is optional.
- The section is the book's `city`. The app makes a shelf for it, and a new section also adds a Passport stamp.
- The answer is `{"job", "usage"}`. The errors are:
  - 400: the request is invalid
  - 403: the account can't make books
  - 404: no such account
  - 429 `busy`: the account already has a book being made
  - 429 `day_limit` or `month_limit`: the daily or monthly limit is reached
  - 503 `paused`: the global spend cap is reached
  - 503: book generation isn't configured

**Watch and manage it:**

| Endpoint | What it does |
|---|---|
| `GET /admin/bookgen` | ready or not, runner state, limits, spent today, levels, number of made books on the shelf |
| `GET /admin/jobs`, `GET /admin/jobs/<id>` | jobs with their step, attempts, error and the per-call cost log (`costs`, `cost_usd`) |
| `POST /admin/jobs/<id>/cancel` | stops a queued or running job and deletes its unpublished files |
| `GET /admin/books?account=<email or id>`, `GET /admin/books/<id>` | made books; one book in full with its narration timings |
| `POST /admin/books/<id>/unpublish` | **kill switch**: takes it off every shelf right away (the files stay) |
| `POST /admin/books/<id>/publish` | puts an unpublished book back |
| `POST /admin/books/<id>/delete` | removes the book and its R2 files for good |
| `GET /levels` (no key) | the three reading levels and their descriptions |

**How a book is made.** One job at a time, in four steps. After each step the result is saved (R2 for files, the
`books` and `book_assets` tables for the rest), so a retry never pays twice for work already done:

1. **Text:**
   - OpenAI moderation checks the parent's title, section and note.
   - `gpt-5.4-mini` writes the pages at the requested level (8 at level 1, 10 at levels 2 and 3). Each page is checked against the level's sentence
     and word limits and rewritten if needed, up to 2 times.
   - A second pass with web search checks the facts and fixes or drops pages.
   - Every page is moderated again.
2. **Photos:**
   - Commons is searched with the page's query and alternates. Only free licenses are used, checked through the
     Commons API like `scripts/fetch_photos.py`, and no photo is used twice.
   - The first candidate whose image (checked at 512 px) and credit line both pass moderation is resized to a
     JPEG and stored in R2.
   - At least 75% of the pages need a photo, or the job fails.
3. **Narration:** `gpt-4o-mini-tts` (voice `coral`) at the level's pace, then `whisper-1` word timings and an ffmpeg
   level/encode to m4a, the same as `scripts/generate_narration.py`.
4. **Publish:**
   - The book goes into the manifest.
   - The account is granted the book.
   - Milo learns it, with its level, on his next content refresh.

**Shelves.** Every account the app sees is an explicit list, even when its `all_books` flag is on.
`/auth/verify`, `/account` and `/account/milo-consent` send `all_books: false` and `books` = the shelf:
- `all_books` accounts: every hand-made (bundled) book, including ones added later, plus the made books the account
  owns or was granted. Other families' made books are never on it.
- Other accounts: only their grants (their own made books are granted when published).

The server's `may_read` checks follow the same rule. The admin endpoints and `tools/devices.py` still show the real
`all_books` flag and grants. The 1.0 app reads `all_books: true` as "every manifest book", so this keeps made books
off other families' shelves without an app update.

**Moderation fails closed.** If moderation can't be reached, the job is retried later and never publishes unchecked
text. A rejected request or page ends the job as `rejected` and deletes its files. Rejected and failed jobs don't
count against the account's quota.

**The runner is free.** It is one asyncio task inside the Render web service, so it needs no worker or paid plan. The
service is already kept awake (`KEEPALIVE_SECONDS` and the keep-awake workflow).
- A job holds a Postgres row lease (`lease_until`, claimed with `FOR UPDATE SKIP LOCKED`), renewed at every step.
- If the service restarts or redeploys mid-book, the lease runs out and the next start resumes the job from its
  last finished step.
- Other errors retry with backoff (1, 2, 4… minutes, up to an hour) for up to 4 attempts.
- **No database access while idle**, so the free Neon database can still scale to zero (it suspends after 5 idle
  minutes and the plan has 100 compute-hours a month; a query every 30 s would use about 180):
  - The runner checks for work once at startup (resuming an expired lease), then sleeps until woken: a book is
    queued or a backup restored. After a job it checks again until nothing is left.
  - It sets a timer only while a job is waiting: a retry's backoff, a lease held by the other instance during a
    deploy, or the daily spending cap (until midnight). `GET /admin/bookgen` shows it as `runner.next_check_s`
    (`null` = asleep until woken).
  - Made books are loaded at startup and whenever this instance publishes, unpublishes or deletes one. A
    `/content/manifest.json` request reloads them in the background when the last load is over 10 minutes old
    (that's user traffic, which wakes the database anyway). There's no timer.
  - `/healthz` and the keep-awake pings never touch the database.
- Memory stays low (512 MB, 0.1 CPU): one photo is processed at a time and ffmpeg runs as a subprocess.
- The image installs `ffmpeg` and `pillow`.

**Files.** Photos and narration are stored in the private Cloudflare R2 bucket `little-explorer-books` under
`books/<book id>/a<attempt>/` (each attempt writes its own objects, so a worker that lost its lease can't
overwrite the new owner's files). The manifest serves a made book's JSON and the merged `library.json`/`narration.json` from
memory; photos and m4a files are `302` redirects to presigned R2 links that last an hour. The app still checks every
file's SHA-256. The bundled books don't change, and with no made books the manifest is exactly the bundled one.
`/healthz` reports both `content` (the live manifest) and `bundled` (this commit's books); the deploy workflow checks
`bundled`.

**Limits and cost.** About **$0.13 a book** with the "Typical" models:

- `gpt-5.4-mini` for the text and the fact check, plus a few web searches
- narration, about $0.05
- moderation, which is free

The limits are:

- one job at a time across the server
- per account, 3 books a day (`BOOKGEN_PER_DAY`) and 10 a month (`BOOKGEN_PER_MONTH`)
- per book, a $0.75 stop (`BOOKGEN_JOB_USD_CAP`), so a runaway book fails
- for the whole server, a $2 daily spend cap (`BOOKGEN_DAILY_USD_CAP`), after which new requests get 503 and the
  runner starts no new job until the next day

Each job's `costs` log lists every call with its model, tokens or seconds, and dollars. Deleting an account deletes its
books and files, but keeps its jobs' day and cost (with the account and request blanked) so the daily cap still counts
that spend.

**Settings** (Render env vars; they're already set on the service):

- `BOOKGEN_OPENAI_API_KEY`: a separate OpenAI key, so book costs show separately from Milo
- `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`: an object-scoped R2 token;
  it can't list buckets, and the server doesn't need it to
- Optional:
  - `BOOKGEN_TEXT_MODEL` (default `gpt-5.4-mini`)
  - the limits above
  - `BOOKGEN_POLL_SECONDS` (default 30): the first retry after the runner itself hits an error (doubling, up to an
    hour); it doesn't poll when idle
  - `BOOKGEN_RUNNER=0`, which serves made books but doesn't make new ones
  - `BOOKGEN_EVERYONE`

Without the OpenAI key or R2, book generation is off. `/admin/books/generate` answers 503, and everything else works
as before.

**Reading levels.** Every book has a `level` (the bundled books are 2). See [Reading levels](content.md#reading-levels).

## App versions and forced updates

Several app builds are out at once: the App Store version, older App Store versions on devices that haven't
updated, and TestFlight builds. The server and Milo must keep working for all of them, and the owner can make
builds that are too old update.

**Which build is calling.** From 1.1, every request the app makes (token, Milo warm-up, coins, account, stars,
content manifest) sends `X-App-Version` (e.g. `1.1`), `X-App-Build` (e.g. `20261101.0930`) and `X-App-Channel`
(`appstore`, `testflight` or `debug`). A request without `X-App-Version` is a **legacy** client: the 1.0 build.
`/token` passes the build to Milo in the dispatch metadata (`app_version`, `app_build`, `app_channel`; absent for
1.0), Milo logs it, and `tools/devices.py versions` counts the devices on each build.

**The update pop-up (1.1 and later).** At launch and whenever it comes back to the foreground, the app calls
`GET /app/version` (no access key) and gets
`{"status": "ok"|"recommended"|"required", "update_url", "min_version", "recommended_version", "client"}`.

- `required`: a full-screen "Time for an update!" screen the child can't close. Milo and narration stop.
- `recommended`: a "A new version is ready" sheet with "Maybe later", which hides it for 3 days.
- Update opens the parent gate, then the App Store (TestFlight builds open the TestFlight app).
- If the check fails (offline, server down, an unknown answer) the app carries on; it never blocks the child
  because of a failed check. A screen already shown as required stays until a check says otherwise.
- The app also shows the required screen when any API call comes back `426 upgrade_required` (see `enforce`).

**The policy.** Admin overrides win over env vars (Render), which win over the defaults. Overrides apply within
30 seconds, with no deploy. If the database is down the server uses the env vars.

| Setting | Env var | Admin field | CLI key | Default |
| --- | --- | --- | --- | --- |
| Builds below this must update | `PIP_APP_MIN_VERSION` | `min_version` | `min` | none |
| Builds below this see "Maybe later" | `PIP_APP_RECOMMENDED_VERSION` | `recommended_version` | `recommended` | none |
| TestFlight builds below this build number must update | `PIP_APP_TESTFLIGHT_MIN_BUILD` | `testflight_min_build` | `testflight-build` | none |
| App Store link | `PIP_APP_STORE_URL` | `store_url` | `store` | `https://apps.apple.com/app/id6823828609` |
| TestFlight link | `PIP_APP_TESTFLIGHT_URL` | `testflight_url` | `testflight` | `itms-beta://` |
| Refuse API calls from builds that must update | `PIP_APP_ENFORCE` | `enforce` | `enforce` | `off` |

`enforce` decides whether the server also turns too-old builds away, on top of the pop-up:

- `off` (default): the server only tells the app. Every build keeps working.
- `versioned`: app API calls (`/token`, `/milo/warm`, `/coins`, `/account`, `/auth/*`, the app's `/stars`
  restore) from builds that sent a version below the minimum get `426 {"error": "upgrade_required"}`, which shows
  the required screen. 1.0 isn't affected. Content, `/healthz`, `/app/version`, `/admin/*` and Milo's reports are
  never refused.
- `all`: also refuses legacy clients, so it counts 1.0 as below any minimum above 1.0.

> **Warning:** 1.0 has no update pop-up. With `enforce=all` and a minimum above 1.0, the 1.0 app just breaks:
> Milo, sign-in and coins fail with a generic error, and nothing tells the family to update. Use it only after
> almost no device runs 1.0 (check `tools/devices.py versions`), and never while 1.0 is the only App Store version.

**Raise the minimum version** (e.g. after 1.2 is live on the App Store):

```bash
cd server
uv run python tools/devices.py versions                    # the policy, and how many devices run each build
uv run python tools/devices.py versions recommended=1.2    # first, a gentle prompt for older builds
uv run python tools/devices.py versions min=1.2            # later, builds below 1.2 must update (1.1 and later)
uv run python tools/devices.py versions testflight-build=20261101.0930   # older TestFlight builds must update
uv run python tools/devices.py versions enforce=versioned  # also refuse too-old builds' API calls
uv run python tools/devices.py versions store=itms-apps://apps.apple.com/app/id6823828609   # a different link
uv run python tools/devices.py versions min=default        # back to the env var or default; "reset" clears all
curl -H "Authorization: Bearer $PIP_ADMIN_KEY" https://little-explorer-pip.onrender.com/admin/app-versions
curl -X POST -H "Authorization: Bearer $PIP_ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"min_version": "1.2"}' https://little-explorer-pip.onrender.com/admin/app-versions   # null clears a field
curl -H "X-App-Version: 1.1" -H "X-App-Channel: appstore" https://little-explorer-pip.onrender.com/app/version
```

Only raise `min_version` to a version that is **live on the App Store**; otherwise families are told to update to
a version they can't get. TestFlight builds (the beta app, and App Store builds installed through TestFlight) go by
`testflight-build`, the build number, which is the date and time (see [Release the app](#release-the-app-beta-and-app-store)).

**Release checklist.**

1. Bump `MARKETING_VERSION` in the Xcode project for **every release of new code**, beta included: a version
   number always means one commit, and `scripts/release.sh` refuses a version that was released from a different
   commit. Beta-only rounds can use patch versions (1.1.1, 1.1.2); the App Store gets whichever one passes.
2. Keep the server and Milo backward compatible (below). Server changes deploy before the app ships, so the old
   app must keep working with the new server.
3. When a version goes to the App Store, add its contract: copy `server/tests/contracts/app-1.0.json` to
   `app-X.Y.json` with the requests that version sends and the response fields it reads, and replay it in
   `test_client_compat.py`.
4. After it's live, set `recommended`, and later `min`, as above.

**Keeping old builds working.**

- `server/tests/test_client_compat.py` replays what the 1.0 build sends (`contracts/app-1.0.json`) and checks the
  status codes and response fields it reads, plus Milo's wire format (the data-channel topics, the
  participant attributes and the star message). It runs with every server test run (`server-tests.yml` on
  pull requests, and before every deploy). If it fails, the change would break an app build that's already out.
- Changes to requests and responses are **additive**: add optional fields with defaults, never rename or remove a
  field, change its type, or make a new field required. Old builds ignore new fields.
- Gate new behavior that old builds can't handle on the build: `ClientInfo.from_headers(request.headers)` in the
  server, `ClientInfo.from_metadata(metadata)` in Milo, then `client.at_least("1.2")` (false for 1.0).
- Retire an old version by raising the minimum, not by removing what it uses. Remove server code for a version
  only after `min_version` and `enforce` have kept it away for a while, and `tools/devices.py versions` shows no device on it.

`-demoUpgrade required|recommended` in a DEBUG build shows the two screens.

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
  `PIP_EMAIL_PROVIDER`, `PIP_EMAIL_FROM`, `GMAIL_*`) and [book generation](#made-books-cloud-generation) (`BOOKGEN_*`, `R2_*`), set in the Render dashboard, not in git. A free service sleeps after 15 idle minutes, so with `KEEPALIVE_SECONDS` set the
  server pings its own `/healthz` to stay awake (one always-on service fits in the free 750 hours a month). If it
  does fall asleep (e.g. after a redeploy fails), the first token takes about a minute.
- **Milo warm-ups** (`POST /milo/warm`, see [Waking Milo up](milo.md#waking-milo-up-cold-starts)) wake the LiveKit
  agent when the app or a book opens, since the free Build plan scales it to zero between sessions. A cold start is ~16 s from `/token` to the job (Milo listening ~18 s after the tap); warm, Milo joins ~2 s after the tap and the 5.2 s intro is the floor.
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
`-demoShelf grown-ups` scrolls to the Grown-ups button. `-demoUpgrade required|recommended` shows the
[update screens](#app-versions-and-forced-updates).

### Release the app (beta and App Store)

There are two apps, built from the same code by the same target:

| | App Store app | Beta app |
| --- | --- | --- |
| Name on the home screen | Little Explorer | Little Explorer (beta) |
| Bundle ID | `com.angilyu.littleexplorer` | `com.angilyu.littleexplorer.beta` |
| Build configuration | Release | Beta (`Config/Beta.xcconfig`: bundle ID, name, icon) |
| Icon | `AppIcon` | `AppIcon-Beta`, with a purple "BETA" ribbon (`scripts/make_app_icon.py`) |
| Goes to | App Store Connect, then App Review (you submit it) | TestFlight only (internal group "Family") |
| `X-App-Channel` | `appstore` (`testflight` when installed through TestFlight) | always `testflight` |

The beta installs next to the App Store app. It has its own keychain, so it's a separate device to the server, with
its own device ID, stars and sign-in. Both read the same server, books and Milo.

**Same version, same code.** `scripts/release.sh` builds both apps from one commit with the same `MARKETING_VERSION`
and the same build number (the date and time), puts the commit's SHA in Info.plist as `GitCommit`, and tags the
commit `v<version>-<build>` (e.g. `v1.1-20261102.1015`) once the first upload succeeds. Before it builds anything it
refuses to:

- run with uncommitted or untracked changes;
- run on a commit that isn't pushed, or isn't in `origin/main`'s history (`--allow-branch` allows a pushed
  branch). Any earlier main commit works, including a detached HEAD at a release tag;
- release a version that a different commit was already released as (a `v<version>` or `v<version>-*` tag on
  another commit): bump `MARKETING_VERSION`;
- run when the project's `MARKETING_VERSION`s disagree.
- upload a build without `PIP_ACCESS_KEY` in `Config/Local.xcconfig` (gitignored; Milo couldn't connect).

```sh
scripts/release.sh check             # the checks, and the version, build and tag it would release
scripts/release.sh beta --dry-run    # archive both apps and check their Info.plists; no upload, no tag
scripts/release.sh beta              # Little Explorer (beta) -> TestFlight
scripts/release.sh prod              # Little Explorer -> App Store Connect (uploaded, not submitted)
scripts/release.sh both              # both, with the same build number
```

Release `prod` after `beta` from the same commit and it reuses the beta's build number, so "1.2 (20261102.1015)"
is the same code in both apps.

**Beta now, App Store later.** Usually the beta goes to TestFlight first and the App Store build follows once it has
been tried (or once Apple has approved the previous version), by which time main has moved on. Main's tip is then
new code under the same version, so `release.sh prod` there is refused. Build the App Store app from the beta's tag
instead:

```sh
git fetch --tags && git tag -l 'v1.1-*'          # the beta's tag, e.g. v1.1-20261102.1015
git checkout v1.1-20261102.1015                  # a detached HEAD at exactly the beta's commit
scripts/release.sh prod                          # same version, build number and GitCommit as the beta
git checkout main
```

New code on main then needs the next `MARKETING_VERSION` (e.g. 1.2) for its own betas. `--new-build` takes a new build number for the same commit (e.g. when Apple rejected
the upload), and `--build N` sets one. A prod build is only uploaded: in App Store Connect, attach it to the App
Store version and submit it for review yourself. It also shows in the App Store app's TestFlight.
`scripts/testflight.sh` still works: it runs `release.sh beta` (`--app-store`: `release.sh prod`).

To try a beta, install **TestFlight** from the App Store on the device, sign in with the developer Apple ID, accept
the "Family" invite for "Little Explorer (beta)", and turn on Automatic Updates. New builds then install themselves;
no cable is needed.

Setup: the beta needs its own App Store Connect app record for `com.angilyu.littleexplorer.beta` (created by hand
in App Store Connect; the API can't create apps). Uploading needs an App Store Connect API key (Admin role): the
`.p8` in `~/.appstoreconnect/private_keys/`, and `ASC_KEY_ID` and `ASC_ISSUER_ID` in the environment or
`~/.config/little-explorer/asc.env`. `PIP_ACCESS_KEY` from `Config/Local.xcconfig` is built in, so keep TestFlight
to internal testers. Book changes don't need a new build (see [Update books without an app update](content.md#update-books-without-an-app-update)).

The `Release checks` workflow (`.github/workflows/release-checks.yml`, `scripts/tests/test_release.py`) tests
these checks and the Beta configuration on pull requests that touch them.

> **Simulator note:** the iOS 26.2/26.3 simulators draw every emoji as a "?" box. Real devices are fine.
>
> **Voice in the simulator:** the simulator has no Apple echo cancellation, so the app uses WebRTC's
> software echo cancellation there. Milo can still hear itself through loud Mac speakers; headphones
> give the cleanest test.
> The app avoids emoji in its UI: flags are bundled images, and buttons use SF Symbols.
