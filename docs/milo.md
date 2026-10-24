# Milo, the voice buddy

How Milo works under the hood: the voice engines, turn-taking, the test harness, what a session costs, and the star game. Back to the [README](../README.md).

## Voice mode

Tap **Talk to Milo** in a book to start voice mode. It stays on across page turns until it's turned off.
On every page turn the app sends the current page (book, page, sentence) to the agent.

**Instant hello:** the app plays Milo's recorded intro ("Hi, I'm Milo. You can read the page to me, or ask me
anything you wonder about.", `Resources/Narration/milo-intro.m4a`, also served from `/content`) the moment the button is
tapped, and connects in the background. The token request sends `"intro": "app"`, so the agent doesn't greet again;
older builds leave it out and the agent still says the greeting. The mic stays off while the 5 s intro plays (so
Milo doesn't hear himself), and the button shows "Waking Milo up…" until the intro is over and Milo is listening. After
the intro, what the child says before the agent has joined (a cold start) is kept with LiveKit pre-connect audio and handed
to Milo when he arrives. Each step is timed from the tap in the device log (category `voice-timing`, e.g.
`Milo timing: tap→listening 6.10s (intro 0.10, token 0.52, connected 1.20, agent joined 2.40, mic on 5.25)`), no words.
`scripts/generate_intro.py` regenerates the clip in Milo's realtime voice (one short Realtime call, levelled like
the narration and checked with Whisper).
The agent lives in [`server/`](../server) (LiveKit Agents + LangGraph; its voice stack is in `server/src/little_explorer_agent/voice/`):

- **Reading:** while the child reads the page aloud, Milo stays quiet (the reading gate → LISTEN), even if they read slowly or softly, then praises them.
- **Noise:** background voices are cancelled, and an LLM relevance gate ignores noise, other people's conversations and off-topic talk.
- **Patience:** Milo waits through the child's pauses and answers briefly and warmly, ending with an open question that invites a longer reply.

Milo's settings start with `PIP_`. Milo has two voice engines. Pick one with `PIP_MODE` in the env file and restart the agent; the app doesn't change.

- `PIP_MODE=pipeline` (default): streaming transcription (`gpt-realtime-whisper`) → reply (LangGraph) → `gpt-4o-mini-tts`.
  A local end-of-turn model decides when the child has finished (0.5 s when their words sound done, up to 4 s mid-sentence).
  Local Silero VAD stops Milo after 0.3 s of the child's speech and resumes if it was only a noise. The reading and noise
  checks are instant; the LLM topic check runs alongside the reply instead of before it.
- `PIP_MODE=realtime`: one OpenAI Realtime speech-to-speech model (`gpt-realtime`, voice `coral`) hears the child and talks
  back. It uses `semantic_vad` with low eagerness, so slow reading isn't cut off. The page is in its instructions
  and is updated on every page turn. The instructions tell it to stay quiet (a `stay_quiet` tool) while the child reads and
  when grown-ups talk. A guard checks each transcript afterwards and cuts off a reply to reading or adult talk (but
  never a short answer to Milo's own question). It never delays a reply. Realtime costs much more per minute than the pipeline.
  Barge-in is "soft": OpenAI's own speech detection no longer cancels Milo. Instead local Silero VAD stops him only
  after 0.6 s of real speech, so claps, knocks, echo and a quick "um" don't cut him off or lose a pending answer.
  Low eagerness sometimes judges a finished question unfinished ("OK, any other food?") and waits for its ~8 s cap.
  So if Silero has heard 2.5 s of silence after the child's speech, OpenAI is still waiting and Milo is idle, the agent
  commits the turn itself and asks for a reply. While the child has been reading aloud in the last 30 s it waits 5 s instead;
  a reading pause that still gets committed is handled by `stay_quiet` and the guard. If OpenAI later commits the
  same turn's leftover silence, that second reply is cancelled.
  Tuning knobs (env): `PIP_REALTIME_EAGERNESS` (low|medium|high|auto), `PIP_REALTIME_BARGE_IN` (vad|model),
  `PIP_REALTIME_MIN_INTERRUPTION_DURATION` (seconds), `PIP_REALTIME_COMMIT_AFTER` (seconds, 0 = off),
  `PIP_REALTIME_READING_COMMIT_AFTER` (seconds), `PIP_REALTIME_TURNS` (server|client, client is experimental).
  Each turn logs its timing (`turn N: wait … answer … total …`, no transcripts) and each session logs a median/p90 summary.
  `wait` is Silero hearing the child stop → their turn committed, `answer` is the commit → Milo's first audio; a turn the
  agent committed itself ends with `forced commit`, after a `forced commit after …s of silence` line.
  An `interrupted by …` line says what stopped a reply (`barge_in`, `guard`, or `programmatic` for the session
  closing) and how long the child had been talking; `he talked over Milo for …` gives the full length once they stop.
  The transcriber sometimes repeats the child's last words on a noise or on Milo's echo, or turns a noise into another
  script (e.g. Chinese characters); the guard skips repeated transcripts and ignores ones with no English letters.
  If the child's app never joins the room (a tap cancelled while Milo was waking up), the agent leaves after 20 s without
  greeting anyone.

The e2e harness can try either engine without restarting the agent: `tools/e2e_smoke.py --mode realtime`.
It reports the reply latency (median/p90), how fast Milo stops when talked over, and checks slow word-by-word
reading, adult chatter, claps while the child is quiet, a clap or "hmm" while they are about to answer, and a clap or
"mm-hmm" while they talk. `--json FILE` saves the numbers. To A/B a change locally without touching production, run the
worker and token server with a different `LIVEKIT_AGENT_NAME` (e.g. `little-explorer-dev`).

## Reading levels

Milo has a neutral child persona: he talks to "a young child (about 4 to 9 years old)". On every page he gets
the open book's reading level and the talk rules for it (from
[`levels.py`](../server/src/little_explorer_agent/levels.py); level 2 when no book is open):

- **Level 1 (ages 4–5):** one or two very short sentences, about six words each, with easy words. He repeats the
  page's key word and asks either/or questions ("Is it big or small?").
- **Level 2 (ages 5–7):** short, simple sentences, two to four a turn. This is how Milo has always talked, and all
  the bundled books are level 2.
- **Level 3 (ages 7–9):** sentences of up to about twelve words. He asks "why" and "how" questions and may use one
  new interesting word a turn, explaining it right away.

Narration for made books follows the level too: slower with pauses at level 1, a lively storytelling pace at
level 3. See [Reading levels](content.md#reading-levels).

## What a session costs

When a Milo session ends (the child closes the book, the app drops off, or the worker stops), the agent appends one row to
`~/.config/little-explorer/usage.csv` (mode 600; `PIP_USAGE_FILE` moves it): start time, length, engine, model,
book, pages visited, number of model responses, input/output tokens split into text and audio, cached tokens,
input-transcription tokens, and an estimated price. It never holds transcripts or audio. Prices are OpenAI's list
prices per 1M tokens, kept in `usage.py` with the source link; override one with e.g.
`PIP_PRICE_GPT_REALTIME_AUDIO_OUT=64`. A worker that is killed outright (`kill -9`) can't write its rows.

```sh
uv run python tools/usage_report.py   # total minutes, $, $/minute, $/session, per day and per model
```

## Naps

When neither the child nor Milo has said anything for 20 seconds (`PIP_IDLE_TIMEOUT_S`, `0` = never), Milo ends
the session. The app switches the mic off and shows "Milo is taking a nap — tap to talk again". Reading aloud
counts as talking. Tapping **Talk to Milo** starts a fresh session.

## Stars and the Passport

The child earns stars for answering Milo, not for talking a lot: up to 5 stars on each page each day. Milo ends its
replies with one easy, open question, and each answer of 5 words or more earns a star until the page has 5
for the day. Asking Milo a question doesn't earn a star (Milo still answers it). The first star on a page starts Milo's reply with
"Great answer, that's a star!"; later ones get a short, varied cheer ("Another star!", "Ooh, star!") and the
rest follows right away. Milo never counts stars or says how many are left, and once a page has all 5 for
the day Milo doesn't mention stars there. When the child answers Milo's question in fewer than 5 words ("the boat"), Milo
(realtime mode) gently asks for more and gives them a sentence start to finish from their own words ("Ooh, the boat! Can
you tell me more? You could say, I would ride the boat because..."), never mentioning words or stars. Milo does this at
most once per question (a second short answer is taken warmly and the talk moves on), even when the page's stars
are all taken, and never for reading the page aloud, small talk, noise or the child's own question. Finishing the
sentence earns the star as usual. The agent (`stars.py`) checks every award before it counts, using the input
transcription and what Milo said last; nothing the model says can hand out a star:

- 1 star per real answer: Milo's last reply asked a question, and the answer has at least 5 words
  (`PIP_STAR_MIN_WORDS`). Reading the page aloud, the child's own question, "hi Milo" or other small talk,
  grown-ups or noise earn nothing. Each star needs a new question from Milo and a new answer.
- The 5 per page count across every session on that account (or unlinked device) that day, so reconnecting doesn't start a page
  over, but favorite books earn again tomorrow. The day is the child's local day: the app sends its time zone
  (`"tz"`) with `/token`, the agent stamps each award's `awarded_at` in it (older apps fall back to
  `PIP_TIMEZONE`), and a star's day is the date in its own `awarded_at`, the same day the star calendar
  shows. When a session starts, the agent loads the account's earlier stars per page and day from
  `GET /stars?account=<id>` (report key; `?device=<id>` for an old build) in the background, off the voice path; if that fails it counts this session only.
- Long (8+ words, `PIP_STAR_LONG_WORDS`) answers and ones that explain with "because" are noted for the badges
  but earn no extra stars. `PIP_STAR_COOLDOWN_S` can add a pause between stars (off by default: turn-taking
  already paces them, and a cooldown could make Milo cheer for a star that is then refused).
- The token server enforces the same limit, so an older agent can't go over it: `POST /stars` refuses an award
  once the account (or unlinked device) already has 5 stars on that book and page that day (across all sessions), and counts each
  award as 1 star. Pages that already had more stars from before the rule keep them.
- There is no model tool, so the game never delays Milo's reply (a tool call made gpt-realtime answer in a
  second response). The star is checked off the voice path once Milo starts replying. `PIP_STARS=0` turns the
  game off on the agent.

Checked awards go to the app on the `milo.star` data topic (a star flies into the counter by the Milo switch
with a soft chime) and to the token server (`POST /stars`, report key), which keeps them in the database for
the session's account (an old build's for its device's account, or the unlinked device). The app saves them
locally and, on launch, merges back the server's copy (`GET /account/stars`; `GET /stars?device=<id>` for old
builds), so stars survive a reinstall. Only counts, kinds, book, page and time are stored, never what the child said.

The library's top corner shows the child's total; tapping it opens the Passport: a stamp for each city where they have
earned a star (not the Travel shelf), the badges, then the star calendar. Stars earned before the answer rule were for
questions (`kind` `question`); new ones are `answer`, and the badges count both.

The badges come from the badge catalog (`badges.json`), which the server can change without an app update (see
[Badges](operations.md#badges)). They are grouped, and each group is one card in the Passport (one column on iPhone,
two on iPad) with its earned medals, how many it has ("3 of 6 badges") and the badge closest to being earned ("Next:
Book Stack, 3/5"). Tapping a card opens the group: "You earned" medals first, then "Still to find" with a hint and a
progress bar each, closest first. The bundled catalog:

| Group | Badge | How to earn it | Counts |
|---|---|---|---|
| Reading | First Book | read every page of a book | reading progress |
| Reading | Page Turner | read 25 different pages | reading progress |
| Reading | Good Listener | listen to 10 pages read aloud to the end | reading progress |
| Reading | Book Stack | read 5 whole books | reading progress |
| Reading | Super Reader | read 100 different pages | reading progress |
| Reading | Story Time | listen to 50 pages read aloud | reading progress |
| Every Day | Book Buddy | read on 5 different days | reading progress |
| Every Day | On a Roll | read 3 days in a row (best run ever) | reading progress |
| Every Day | Super Week | read 7 days in a row | reading progress |
| Every Day | Reading Champ | read on 20 different days | reading progress |
| Every Day | Come-Back Kid | stars on 5 different days (any days, not a streak) | stars |
| Every Day | Early Bird | a star before 9 am | stars |
| Every Day | Night Owl | a star at 7 pm or later | stars |
| Milo Stars | Milo Pal | read 10 pages with Milo on | reading progress |
| Milo Stars | Curious Cat | 10 stars | stars |
| Milo Stars | Big Talker | 3 long answers | stars |
| Milo Stars | Why Wizard | 3 answers that explain with "because" (`why_how`) | stars |
| Milo Stars | Star Shower | 5 stars in one day | stars |
| Milo Stars | Bookworm | stars in 10 different books | stars |
| Milo Stars | Super Star | 50 stars | stars |
| Places | City Hopper | read a page in books about 3 cities (not the Travel shelf) | reading progress, shelves |
| Places | Globe Trotter | stamps from 3 cities | stars, shelves |
| Places | World Traveler | read books from 4 countries | reading progress, shelves |
| Places | *City* Expert | a star in every book of a city with several books, e.g. Paris Expert (the medal shows the city's flag) | stars, shelves |

A page counts as read when it was on screen for a second or more, and as listened to when read-aloud played to the
end. Goals that depend on the shelves are lowered to what the shelves have (with 3 cities, City Hopper needs 3).

**Reading progress.** While signed in, the app keeps a small summary per account on the device
(`account.<id>.readingProgress.v1`): which pages were read, read with Milo on and listened to (`book/page`), and on
which days. It is filled in as pages are read and merged with `GET /account/reading`, which returns the same summary
worked out from the account's reading events on every device, so badges match across the family's devices. Nothing
is ever taken away, so a badge can't be lost; signing out removes the summary from the device. Signed out there is no
reading progress (and none is recorded in demo launches), so the reading badges stay locked; the star badges work as
before.

Badges are worked out in `Stores/Badges.swift` from the saved stars, the reading progress and the shelves, so a new
city or book just works. Days and times are in the device's time zone, as in the calendar. A badge not earned yet is
gray with a short hint ("Earn a star at bedtime") and how close the child is (9/10); an earned one is a colored medal
that says what they did ("Stars on 5 different days!"). Badges earned since the Passport was last opened get a NEW!
ribbon (on the medal and its group's card), a little wiggle (none with Reduce Motion), a "New badge!" celebration with
confetti (no confetti with Reduce Motion) and the star chime; they count as seen when the Passport is closed (kept on
the device only, per account). A badge the child has seen earned stays earned even if its goal is raised later.
VoiceOver reads e.g. "Why Wizard badge, earned. You answered with “because” 3 times!" or "Bookworm badge, not yet.
Earn stars in 10 different books. 9 of 10." The last page of each book shows the
stars from that book, and each book's cover in the library shows a gold star with the number earned in that
book (hidden until it has one; VoiceOver reads e.g. "Famous Places, 7 stars"). `tools/devices.py` lists stars per account (and per device). Pipeline mode gives stars too, but Milo doesn't announce them.

The Passport ends with **My star calendar**: a month grid with a gold star and the count on each day the child earned
stars, today outlined, and the month's total ("33 stars in October"). The arrows or a sideways swipe go back to
earlier months (as far as the first star). Days use each award's `awarded_at` in the device's time zone, so a
reinstall restores them from the server, and turning stars into coins never changes a day. An award without a
readable time is left off the calendar with a short note. VoiceOver reads each day, e.g. "October 18, today, 3 stars".
