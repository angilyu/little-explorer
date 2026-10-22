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

- **Signed out**, the app shows a welcome screen for grown-ups. Tapping **Sign in or sign up** opens the parent
  gate first, then asks for the email and the code. The code works for 10 minutes and 5 tries; a new one can be
  sent after 60 seconds (at most 5 an hour per email, 20 an hour per network, and `PIP_EMAIL_DAILY_CAP`, default
  100, a day in all). The sign-in token is kept in the Keychain on this device only.
- **Signed in**, the shelf shows the account's books. **Sign out** and **Delete account…** are in the Grown-ups
  corner (behind the gate). Signing out keeps everything on the server. Deleting asks for a new code sent to
  the account's email, then removes the email, books list, stars and coins (and any hearts from older builds)
  from the server and signs out every device (Apple requires apps with accounts to offer this).
- Each account's copy of the stars and coins is saved on the device per account, so they show offline,
  and is removed on sign-out. What the device saved before accounts stays under its old keys and is never deleted.
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
| `PIP_DEFAULT_BOOKS` | Comma-separated book ids for new accounts (default: the 12 books above) |
| `PIP_EMAIL_DAILY_CAP` | Most codes sent a day, for all emails together (default 100) |
| `PIP_REVIEW_EMAIL`, `PIP_REVIEW_CODE` | Optional: an email that signs in with a fixed code and no email, for App Store review |

Without an email provider, `POST /auth/start` answers `503 email_off`.

**Endpoints** (all with the app's access key; signed-in ones also with `X-Pip-Session: <token>`, which
`POST /token` takes as well):
`POST /auth/start {email, purpose?}`, `POST /auth/verify {email, code, device}` (returns `token`, `account`,
`claimed_device`), `POST /auth/signout`, `GET /account`, `POST /account/delete {code}`, `GET /account/stars`,
`GET /account/coins`, `POST /account/coins/convert` and `POST /account/import {stars}`. The app used to have
a heart button for pages the child loved; it's gone, but the server still answers `GET`/`POST /account/hearts
{loved, unloved}` and takes `hearts` in `/account/import` so older builds keep working, and keeps the hearts
already saved (deleting the account removes them). Errors: `bad_email`, `bad_code` (with `tries_left`), `expired`,
`too_many` (with `retry_after_s`), `signed_out`.

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
```

Approve your family's account once; every device signed in to it shares its 60 minutes.

The app always uses the deployed server (`PIP_SERVER_URL` in `Config/Milo.xcconfig`). Debug builds can use a
local token server with a launch argument, e.g. `-voice.tokenServerURL http://127.0.0.1:8787` (simulator) or
`http://<your-mac>.local:8787` (a device), in the Xcode scheme's Arguments; release builds ignore it.

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
some earned and some not, from 40 stars and an early-morning star), `-demoCoins 2`,
`-demoGate` (the parent gate) and `-demoParent` (the Grown-ups corner). `-demoShelf grown-ups` scrolls to the
Grown-ups button.

### Install on an iPhone or iPad (TestFlight)

The app is "Little Explorer Kids" in App Store Connect, and every build goes to the internal TestFlight group
"Family". Install **TestFlight** from the App Store on the device, sign in with the developer Apple ID, accept the
invite, and turn on Automatic Updates for the app. New builds then install themselves; no cable is needed.

```sh
scripts/testflight.sh   # archive, sign for App Store distribution, upload, and wait for Apple's processing
```

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
