# Little Explorer

A picture-book app for iPhone and iPad that helps a 6-year-old discover cities and get excited about travel.
Each page has a real photo, a short sentence he can read by himself, a heart button for pages he loves,
and a speaker button that reads the sentence aloud. An optional voice buddy, **Milo**, listens patiently
while he reads and answers his questions about the book.

| Shelf | Yummy Paris + heart | Read aloud | Voice mode |
|---|---|---|---|
| ![](docs/screenshots/iphone-shelf.jpg) | ![](docs/screenshots/iphone-yummy-paris-heart.jpg) | ![](docs/screenshots/iphone-read-aloud.jpg) | ![](docs/screenshots/iphone-fun-art-voice.jpg) |

iPad versions are in [`docs/screenshots`](docs/screenshots).

## Books

The first screen is a shelf with every book, grouped by city, with a general Travel shelf last. Tap a cover to open it.

| City | Book (`id`) | Theme |
|---|---|---|
| Paris | Famous Places (`paris`) | The Seine, Eiffel Tower (repainting, sparkles), Louvre and Mona Lisa, Notre-Dame, Arc de Triomphe, toy sailboats |
| Paris | Yummy Paris (`paris-food`) | Baguettes, pain au chocolat, macarons, crêpes, cheese, a market, a café, ice cream, snails, a picnic |
| Paris | Fun & Art (`paris-fun`) | Carousels, puppet shows, a Seine boat, the Louvre's old castle, Orsay, Monet's water lilies, Montmartre painters and funicular, The Thinker, the river beach |
| Paris | Notre-Dame (`paris-notre-dame`) | The island in the Seine, gargoyle rain spouts, chimeras, rose windows, the Emmanuel bell, the kind bell ringer, the 2019 fire and its firefighters, the rebuilders, the golden rooster on the spire, the 2024 reopening, Point Zéro |
| Paris | Eiffel Tower (`paris-eiffel`) | The Iron Lady, building it in two years, rivets, Gustave Eiffel's name, repainting by hand every seven years, growing in summer heat, 674 steps, the lifts, fireworks, Eiffel's secret apartment, the hourly sparkle |
| Paris | Jardin d'Acclimatation (`paris-jardin`) | The little train in, an 1860 park, peacocks and a white peahen, goose families, parrots, Enchanted River boats, the old carousel, puppet theater, the Korean garden |
| Paris | Louvre Museum (`paris-louvre`) | The glass pyramid and the upside-down one, a king's palace, the very long hall, art copyists, the small Mona Lisa, Winged Victory, Venus de Milo, the giant sphinx, Egyptian coffins, too big for one day |
| Paris | Palace of Versailles (`paris-versailles`) | The Sun King, the golden gate, the Hall of Mirrors, the king's getting-up crowd, huge gardens, orange trees in boxes, the Apollo fountain, Grand Canal rowboats, Marie Antoinette's hamlet and its farm |
| Paris | Seine River Cruise (`paris-seine`) | Bateaux-mouches and waving, Pont Neuf, stone faces, Pont Alexandre III's gold, the Zouave flood gauge, houseboats, the bouquinistes' green boxes, the little Statue of Liberty, the 2024 Olympic boat parade, Swan Island |
| Paris | Speak French (`paris-french`) | Travel French, one phrase per page: bonjour in shops, merci at the market, s'il vous plaît for crêpes, a croissant from the baker, oui and non on a carousel, je m'appelle with Guignol, où sont les toilettes, un-deux-trois macarons, j'aime la glace, excusez-moi on the métro, au revoir at the TGV. Milo role-plays the baker or shopkeeper so he says each phrase out loud |
| London | London (`london`) | The Thames, Big Ben, Tower Bridge, buses, ravens, guards, London Eye, the Tube, the museum whale, fish and chips |
| London | Big Ben (`london-big-ben`) | Big Ben the bell vs the Elizabeth Tower, who it's named after, the giant clock faces and minute hands, the bongs, the radio chimes, pennies on the pendulum, the Ayrton Light, 334 steps, Westminster Bridge |
| Barcelona | Barcelona (`barcelona`) | Beach, Sagrada Família, Casa Batlló, Park Güell's dragon, churros, human towers, football, the magic fountain |
| Barcelona | Park Güell (`barcelona-park-guell`) | A garden town where only two houses were built, gingerbread gatehouses, the mosaic dragon (or salamander) on the stairs, trencadís broken-tile mosaics, the hall of 86 columns and its ceiling suns, rainwater saved under the square, the long wavy bench, the leaning laundry portico, Gaudí's pink house, the hill with three crosses |
| Barcelona | Sagrada Família (`barcelona-sagrada`) | A church still being built after 140+ years, cranes, Gaudí's 43 years on it, towers like sand castles, fruit on the pinnacles, a turtle and a tortoise holding up columns, a forest of columns with colored light, the magic square that adds up to 33, the wavy-roofed school, the upside-down chain model, Mary's glowing star, the tallest church tower |
| Barcelona | Casa Batlló (`barcelona-casa-batllo`) | The stepped-roof house next door, bone-like pillars, mask balconies, the big wavy window, a swirly ceiling, the mushroom fireplace, the blue tiled light well, the white arches in the attic, the dragon-scale roof, Sant Jordi's lance, mosaic chimneys, roses for Sant Jordi day |
| Barcelona | Tibidabo (`barcelona-tibidabo`) | A 1901 amusement park on a mountain, a Latin name meaning "I will give you", the Cuca de Llum funicular, the Tramvia Blau, the red airplane flying since 1928, the Ferris wheel, the Talaia, the automaton museum, the temple on top, the view to the sea, wild boars in the forest |
| Barcelona | Montjuïc Cable Car (`barcelona-montjuic`) | A green hill by the sea, cable cars up to the castle, the castle and its cannon, the old port cable car over the water, the Magic Fountain's music and lights, the big palace museum, the 1992 Olympic stadium, the burning-arrow cauldron, the Calatrava tower, cactus gardens, the Poble Espanyol village |
| Lake Tahoe | Lake Tahoe (`tahoe`) | Half California, half Nevada, deeper than the Empire State Building is tall, so clear you see the rocks, Emerald Bay's teahouse island, Vikingsholm's grass roof, Sand Harbor boulders, kayaks, black bears, the Truckee River, a lake that never freezes, the 1960 Olympics |
| Lake Tahoe | Heavenly (`tahoe-heavenly`) | Winter: the village gondola, the lake-view deck, skiing from California into Nevada, chairlifts, ski school, pizza and french fries, the magic carpet, tubing, snow guns, hot cocoa |
| Lake Tahoe | Northstar (`tahoe-northstar`) | Winter: the village ice rink, s'mores by the fire, chairlifts through the pines, a far-off lake view, ski school, corduroy snow, night snowcats, tubing, snowy pines, cocoa |
| Hawaii | Hawaii (`hawaii`) | Volcano islands, the eight-stripe flag, Kīlauea lava, black sand, honu turtles (look, don't touch), leis, hula stories, ancient surfing, humpback whales, shave ice, the humuhumunukunukuāpuaʻa, the nēnē goose, what aloha means |
| Hawaii | Honolulu (`hawaii-honolulu`) | Waikīkī's gentle surf, Diamond Head crater, ʻIolani Palace, King Kamehameha's long leis, Duke's statue, Hanauma Bay fish, outrigger canoes, Aloha Tower, the aquarium, spam musubi |
| Chengdu | Chengdu (`chengdu`) | Ni hao, China's five-star flag, giant pandas munching bamboo, tiny pink panda babies, red pandas' stripy tails, hot pot, tingly Sichuan peppercorns, face-changing opera masks, People's Park teahouses (long-spout kettles, ear cleaners, mahjong), Jinli's lanterns and sugar painting, Zhuge Liang's straw-boat arrow legend, the 2,000-year-old Dujiangyan river system, the Leshan Giant Buddha, the Wide and Narrow Alleys |
| Longyan | Longyan (`longyan`) | A city in green Fujian mountains, round earth tulou houses, Hakka families living together, earth walls with bamboo inside, one big door, a well in the courtyard, Chengqi Lou's four rings and ~400 rooms, Zhencheng Lou's eight pizza slices, kitchens below and beds on top, Hakka stuffed tofu, Guanzhai's red cliffs, South China tigers on Meihua Mountain, the Ting River, Hakka mountain songs |
| Beijing | Beijing (`beijing`) | China's capital, the sunrise flag raising at Tiananmen, hutong alleys and courtyard homes, pedal rickshaws, crispy Peking duck in pancakes, tanghulu candied fruit, swallow and dragon kites, pandas at Beijing Zoo, the long subway, the Bird's Nest and both Olympics, painted opera faces, the Drum Tower's time-telling drums |
| Beijing | Great Wall (`beijing-great-wall`) | Mutianyu's walkway, about 21,000 km of walls, 2,000 years of builders and old rammed-earth walls, battlements, watchtowers, smoke-by-day fire-by-night beacons, sticky-rice mortar, the Mutianyu cable car and toboggan, steep steps, the seen-from-space myth, snow at Badaling, Old Dragon Head at the sea |
| Beijing | Forbidden City (`beijing-forbidden-city`) | The Meridian Gate, why it was forbidden, the moat and corner towers, the 9,999½ rooms legend, the emperor's yellow roofs, the little roof animals, the lion and cub, the Hall of Supreme Harmony and dragon throne, the Nine Dragon Wall, fire vats kept warm in winter, palace cats, the Imperial Garden |
| Beijing | Summer Palace (`beijing-summer-palace`) | A royal summer escape, Kunming Lake, Longevity Hill made from the lake's dirt, the Long Corridor and its 14,000 paintings, the stone Marble Boat, the Seventeen-Arch Bridge and its lions, winter-solstice golden arches, the bronze ox, pretend shopping on Suzhou Street, Cixi's three-floor opera stage, lotus, ice skating |
| Beijing | Temple of Heaven (`beijing-temple-of-heaven`) | Praying for good harvests, the round three-roof blue hall, wood without nails, pillars for seasons, months and hours, round heaven and square earth, the echoing center stone, the whispering Echo Wall, the raised walkway, old cypresses, morning tai chi, jianzi kicking, water calligraphy |
| Shanghai | Shanghai (`shanghai`) | A huge port city, the Huangpu River, the old Bund, the Oriental Pearl Tower's glass floor, twisty Shanghai Tower, the bottle opener, the floating maglev train, xiaolongbao soup dumplings, Yu Garden, the zigzag bridge, the Disneyland castle, the lights at night, a ferry goodbye |
| Travel | Take a Flight (`travel-flight`) | Check-in and the moving belt, the scanner that sees inside bags, the boarding pass, watching planes at the gate, the window seat, the seatbelt light, a snack when ears pop, above the clouds, two pilots, the cabin crew, wheels down |
| Travel | Pack Your Bag (`travel-packing`) | A checklist, rolling clothes, toothbrush, a teddy from home, a backpack with a snack and a book, small bottles in a clear bag, weighing the bag, a name tag, packing for the weather, zipping it shut |
| Travel | Passport (`travel-passport`) | What a passport is, the calm-face photo, cover colors, pretty pages and secret glowing marks, entry stamps, passport control, e-gates, a grown-up keeps it safe, kids' passports renewing sooner |
| Travel | Hotel Stay (`travel-hotel`) | The front desk, the luggage cart, the key card, the lift, how room numbers show the floor, quiet hallways, no bed jumping, the view, Do Not Disturb, towel animals, the breakfast buffet, the pool with a grown-up |
| Travel | Jet Lag (`travel-jetlag`) | Clocks around the world, day here and night there, time zones, the body clock, sleepy days and owl nights, why flying east is harder, sunshine, water, short naps, eating and sleeping at the new time (no medicine advice) |

Books are JSON files in [`LittleExplorer/Resources/Books`](LittleExplorer/Resources/Books) with 10–14 pages each.
Every page has a 4–12 word sentence, hidden `about` notes for Milo, and a photo. The shelf order is `BookLibrary.order`.
A city with several books shows them under one city heading; `theme` is the title on the cover.
Travel books have `"city": "Travel"` and `"kind": "travel"`: they share one Travel shelf with an airplane
badge (`travel-flag.png`), Milo is told they are general travel books rather than about one city, and they
don't give a Passport stamp.

Hearts are stored on the device as `<book id>/<page id>`, so each themed book keeps its own hearts.
Grown-ups can see the loved pages for every book behind the parent gate (the "Grown-ups" button after the last
shelf of the library). The gate writes a random number from 101 to 999 in words, like "three hundred forty-eight", and the
grown-up types its digits on the gate's number pad (no multiple choice, no digits in the prompt). Round hundreds
are skipped. Three wrong answers lock it for 30 seconds, and each miss shows a new number.

### Photos

All photos come from Wikimedia Commons under free licenses (public domain, CC0, CC BY, CC BY-SA).
The credit (author · license · Commons) is shown under each photo. No photo is used twice.
[`scripts/fetch_photos.py`](scripts/fetch_photos.py) checks each license through the Commons API and bundles a resized copy:

```sh
python3 scripts/fetch_photos.py --only paris-food            # one book
python3 scripts/fetch_photos.py --only paris-fun/carousel --force   # re-fetch one page
```

Pin a specific file by setting the page's `photo.commonsTitle`.

## Read aloud

The speaker button (bottom-right of every page) reads the sentence in a natural voice, the same OpenAI voice as Milo
(`gpt-4o-mini-tts`, "coral"), styled like a grown-up reading a picture book to a 6-year-old: warm, clear and a little slow.
The clips are made ahead of time and bundled (about 16 MB of AAC for all 464 pages), so reading aloud works offline
and the app never holds an API key. Each word is highlighted as it's spoken. Tap again to stop. Speech also stops when
the page turns or the book closes. When voice mode is on, the microphone is muted while the app reads, so Milo doesn't
mistake the app's voice for the child.

[`scripts/generate_narration.py`](scripts/generate_narration.py) makes the clips. It reads each sentence with
`gpt-4o-mini-tts` and gets word timings from `whisper-1`. It tightens those timings to the real pauses in the audio,
retries if the transcript doesn't match the sentence, and writes `LittleExplorer/Resources/Narration/`. After editing a
sentence, rerun it (it only regenerates pages that changed). Every clip is levelled to -16 LUFS (normal speech
loudness, the same level as Milo) with peaks kept under -1 dBTP, so the narration isn't quieter than Milo. It needs
`OPENAI_API_KEY` (or an env file, see below), macOS `afconvert` and `ffmpeg` (`brew install ffmpeg`).

A book can add its own reading note. Speak French uses `"narration": {"instructions": "...", "phraseByPhrase": true}`:
the French is spoken natively (not anglicized), and each phrase, e.g. "Ask the baker", "Un croissant" and
"s'il vous plaît!", is its own take joined with short pauses, so the word times come straight from the takes. Pages
don't spell out pronunciations like "Merci (mair-SEE)": the voice says every word, so the hints are only in the
narration note and Milo's notes. The Hawaii books use the same mode so Hawaiian words (honu, nēnē, Waikīkī) are said
the island way; the Tahoe books only add a note for local names
(Nevada as neh-VAD-uh, Truckee). The China books (Chengdu, Longyan, Beijing and Shanghai) add a note so Chinese names (Ni hao, Zaijian, Jinli,
Zhuge Liang, Hakka, tulou, Chengqi Lou, hutong, Mutianyu, Cixi, jianzi, xiaolongbao) are said in Mandarin; their pages use plain pinyin and no Chinese characters. The Barcelona landmark books add a note so Catalan names (Güell, Gaudí, Batlló, Sant Jordi, Tibidabo, Montjuïc, Adéu) are said the Barcelona way. To run it:

```sh
python3 scripts/generate_narration.py                  # new or changed pages only
python3 scripts/generate_narration.py --only paris/eiffel --force
python3 scripts/generate_narration.py --loudness       # re-level existing clips, no API calls
```

If a page has no clip for its current sentence, the app falls back to the best on-device system voice
(`AVSpeechSynthesizer`, slower rate, same word highlighting).

## Voice buddy (Milo)

Tap **Talk to Milo** in a book to start voice mode. It stays on across page turns until it's turned off.
On every page turn the app sends the current page (book, page, sentence) to the agent.

**Instant hello:** the app plays Milo's recorded intro ("Hi, I'm Milo. You can read the page to me, or ask me
anything you wonder about.", `Resources/Narration/milo-intro.m4a`, also served from `/content`) the moment he is
tapped, and connects in the background. The token request sends `"intro": "app"`, so the agent doesn't greet again;
older builds leave it out and the agent still says the greeting. The mic stays off while the 5 s intro plays (so
Milo doesn't hear himself), and the button shows "Waking Milo up…" until the intro is over and Milo is listening. After
the intro, what he says before the agent has joined (a cold start) is kept with LiveKit pre-connect audio and handed
to Milo when he arrives. Each step is timed from the tap in the device log (category `voice-timing`, e.g.
`Milo timing: tap→listening 6.10s (intro 0.10, token 0.52, connected 1.20, agent joined 2.40, mic on 5.25)`), no words.
`scripts/generate_intro.py` regenerates the clip in Milo's realtime voice (one short Realtime call, levelled like
the narration and checked with Whisper).
The agent lives in [`server/`](server) (LiveKit Agents + LangGraph; its voice stack is in `server/src/little_explorer_agent/voice/`):

- **Reading:** while he reads the page aloud, Milo stays quiet (the reading gate → LISTEN), even if he reads slowly or softly, then praises him.
- **Noise:** background voices are cancelled, and an LLM relevance gate ignores noise, other people's conversations and off-topic talk.
- **Patience:** Milo waits through his pauses and answers briefly and warmly, ending with an open question that invites a longer reply.

Milo's settings start with `PIP_`. Milo has two voice engines. Pick one with `PIP_MODE` in the env file and restart the agent; the app doesn't change.

- `PIP_MODE=pipeline` (default): streaming transcription (`gpt-realtime-whisper`) → reply (LangGraph) → `gpt-4o-mini-tts`.
  A local end-of-turn model decides when he has finished (0.5 s when his words sound done, up to 4 s mid-sentence).
  Local Silero VAD stops Milo after 0.3 s of his speech and resumes if it was only a noise. The reading and noise
  checks are instant; the LLM topic check runs alongside the reply instead of before it.
- `PIP_MODE=realtime`: one OpenAI Realtime speech-to-speech model (`gpt-realtime`, voice `coral`) hears him and talks
  back. It uses `semantic_vad` with low eagerness, so slow reading isn't cut off. The page is in its instructions
  and is updated on every page turn. The instructions tell it to stay quiet (a `stay_quiet` tool) while he reads and
  when grown-ups talk. A guard checks each transcript afterwards and cuts off a reply to reading or adult talk (but
  never a short answer to Milo's own question). It never delays a reply. Realtime costs much more per minute than the pipeline.
  Barge-in is "soft": OpenAI's own speech detection no longer cancels Milo. Instead local Silero VAD stops him only
  after 0.6 s of real speech, so claps, knocks, echo and a quick "um" don't cut him off or lose a pending answer.
  Low eagerness sometimes judges a finished question unfinished ("OK, any other food?") and waits for its ~8 s cap.
  So if Silero has heard 2.5 s of silence after his speech, OpenAI is still waiting and Milo is idle, the agent
  commits the turn itself and asks for a reply. While he has been reading aloud in the last 30 s it waits 5 s instead;
  a reading pause that still gets committed is handled by `stay_quiet` and the guard. If OpenAI later commits the
  same turn's leftover silence, that second reply is cancelled.
  Tuning knobs (env): `PIP_REALTIME_EAGERNESS` (low|medium|high|auto), `PIP_REALTIME_BARGE_IN` (vad|model),
  `PIP_REALTIME_MIN_INTERRUPTION_DURATION` (seconds), `PIP_REALTIME_COMMIT_AFTER` (seconds, 0 = off),
  `PIP_REALTIME_READING_COMMIT_AFTER` (seconds), `PIP_REALTIME_TURNS` (server|client, client is experimental).
  Each turn logs its timing (`turn N: wait … answer … total …`, no transcripts) and each session logs a median/p90 summary.
  `wait` is Silero hearing him stop → his turn committed, `answer` is the commit → Milo's first audio; a turn the
  agent committed itself ends with `forced commit`, after a `forced commit after …s of silence` line.
  An `interrupted by …` line says what stopped a reply (`barge_in`, `guard`, or `programmatic` for the session
  closing) and how long he had been talking; `he talked over Milo for …` gives the full length once he stops.
  The transcriber sometimes repeats his last words on a noise or on Milo's echo, or turns a noise into another
  script (e.g. Chinese characters); the guard skips repeated transcripts and ignores ones with no English letters.
  If his app never joins the room (a tap cancelled while Milo was waking up), the agent leaves after 20 s without
  greeting anyone.

The e2e harness can try either engine without restarting the agent: `tools/e2e_smoke.py --mode realtime`.
It reports the reply latency (median/p90), how fast Milo stops when talked over, and checks slow word-by-word
reading, adult chatter, claps while he is quiet, a clap or "hmm" while he is about to answer, and a clap or
"mm-hmm" while he talks. `--json FILE` saves the numbers. To A/B a change locally without touching production, run the
worker and token server with a different `LIVEKIT_AGENT_NAME` (e.g. `little-explorer-dev`).

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

### What a session costs

When a Milo session ends (he closes the book, the app drops off, or the worker stops), the agent appends one row to
`~/.config/little-explorer/usage.csv` (mode 600; `PIP_USAGE_FILE` moves it): start time, length, engine, model,
book, pages visited, number of model responses, input/output tokens split into text and audio, cached tokens,
input-transcription tokens, and an estimated price. It never holds transcripts or audio. Prices are OpenAI's list
prices per 1M tokens, kept in `usage.py` with the source link; override one with e.g.
`PIP_PRICE_GPT_REALTIME_AUDIO_OUT=64`. A worker that is killed outright (`kill -9`) can't write its rows.

```sh
uv run python tools/usage_report.py   # total minutes, $, $/minute, $/session, per day and per model
```

### Naps and daily limits per device

**Naps.** When neither he nor Milo has said anything for 20 seconds (`PIP_IDLE_TIMEOUT_S`, `0` = never), Milo ends
the session. The app switches the mic off and shows "Milo is taking a nap — tap to talk again". Reading aloud
counts as talking. Tapping **Talk to Milo** starts a fresh session.

**Devices.** The app sends a device id with every token request: its vendor id, saved in the Keychain so it
survives a reinstall, plus the device name and model. The Grown-ups corner shows the id. The token server keeps
each device's status and Milo minutes per day:

| Status | Daily Milo time | When the time is used up |
|---|---|---|
| unknown (any new device) | 10 min (`PIP_LIMIT_UNKNOWN_MIN`) | blocked automatically |
| approved | 60 min (`PIP_LIMIT_APPROVED_MIN`) | rests until tomorrow |
| blocked | none | until you unblock it |

Days run midnight to midnight in `PIP_TIMEZONE` (default `America/Los_Angeles`). A device that is blocked or
out of time gets no token, and the app shows "Milo needs a rest today. Let's talk again tomorrow!". The token
carries the time left, so a session that reaches the limit ends with a short goodbye from Milo. When a session
ends, the agent reports its length and estimated cost to the token server (`POST /usage`, with `PIP_REPORT_KEY`),
which stores it. Usage is stored per conversation, never transcripts or audio.

Storage is Postgres when `DATABASE_URL` is set (production uses a free [Neon](https://neon.tech) database:
Render's free disk is wiped on every deploy and restart, and Neon keeps the data, needs no server and costs
nothing at this size). Without it, a local SQLite file is used (`PIP_DEVICES_DB`, default
`~/.config/little-explorer/devices.sqlite3`). If the database can't be reached, tokens are still given out with
the unknown-device limit.

Check usage and manage devices with `tools/devices.py`. It calls the server's `/admin` endpoints with
`PIP_ADMIN_KEY` from your env file (`--direct` uses `DATABASE_URL` instead). The admin key is never in the app.

```sh
uv run python tools/devices.py                      # devices: status, today, last 7 days, total minutes and $
uv run python tools/devices.py usage --days 14      # minutes and $ per device per day
uv run python tools/devices.py approve <id> --label "Leo's iPad"   # by id, or by name/label if unique
uv run python tools/devices.py block <id or label>  # also: unblock, limit <ref> 30, limit <ref> default, rename
```

Approve your own devices after each has used Milo once (it appears in the list as unknown). iOS reports every
iPad as just "iPad", so use the id shown in the Grown-ups corner (it has a copy button) and give it a label.

The app uses the deployed server (`PIP_SERVER_URL` in `Config/Milo.xcconfig`). To try a local token server from a
device, set its address in the Grown-ups corner (e.g. `http://<your-mac>.local:8787`).

### Stars and the Passport

He earns stars for answering Milo, not for talking a lot: up to 5 stars on each page each day. Milo ends his
replies with one easy, open question, and each answer of 5 words or more earns a star until the page has 5
for the day. Asking Milo a question doesn't earn a star (Milo still answers it). The first star on a page starts Milo's reply with
"Great answer, that's a star!"; later ones get a short, varied cheer ("Another star!", "Ooh, star!") and the
rest follows right away. Milo never counts stars or says how many are left, and once a page has all 5 for
the day he doesn't mention stars there. When he answers Milo's question in fewer than 5 words ("the boat"), Milo
(realtime mode) gently asks for more and gives him a sentence start to finish from his own words ("Ooh, the boat! Can
you tell me more? You could say, I would ride the boat because..."), never mentioning words or stars. He does this at
most once per question (a second short answer is taken warmly and the talk moves on), even when the page's stars
are all taken, and never for reading the page aloud, small talk, noise or a question of his own. Finishing the
sentence earns the star as usual. The agent (`stars.py`) checks every award before it counts, using the input
transcription and what Milo said last; nothing the model says can hand out a star:

- 1 star per real answer: Milo's last reply asked a question, and the answer has at least 5 words
  (`PIP_STAR_MIN_WORDS`). Reading the page aloud, a question of his own, "hi Milo" or other small talk,
  grown-ups or noise earn nothing. Each star needs a new question from Milo and a new answer.
- The 5 per page count across every session on that device that day, so reconnecting doesn't start a page
  over, but his favorite books earn again tomorrow. The day is his local day: the app sends its time zone
  (`"tz"`) with `/token`, the agent stamps each award's `awarded_at` in it (older apps fall back to
  `PIP_TIMEZONE`), and a star's day is the date in its own `awarded_at`, the same day the star calendar
  shows. When a session starts, the agent loads the device's earlier stars per page and day from
  `GET /stars` (report key) in the background, off the voice path; if that fails it counts this session only.
- Long (8+ words, `PIP_STAR_LONG_WORDS`) answers and ones that explain with "because" are noted for the badges
  but earn no extra stars. `PIP_STAR_COOLDOWN_S` can add a pause between stars (off by default: turn-taking
  already paces them, and a cooldown could make Milo cheer for a star that is then refused).
- The token server enforces the same limit, so an older agent can't go over it: `POST /stars` refuses an award
  once the device already has 5 stars on that book and page that day (across all sessions), and counts each
  award as 1 star. Pages that already had more stars from before the rule keep them.
- There is no model tool, so the game never delays Milo's reply (a tool call made gpt-realtime answer in a
  second response). The star is checked off the voice path once Milo starts replying. `PIP_STARS=0` turns the
  game off on the agent.

Checked awards go to the app on the `milo.star` data topic (a star flies into the counter by the Milo switch
with a soft chime) and to the token server (`POST /stars`, report key), which keeps them in the database by
device id. The app saves them locally and, on launch, merges back the server's copy (`GET /stars?device=<id>`,
access key, or the report key for the agent), so stars survive a reinstall. Only counts, kinds, book, page and time are stored, never what he said.

The library's top corner shows his total; tapping it opens the Passport: a stamp for each city where he has
earned a star (not the Travel shelf), plus Curious Cat (10 stars), Big Talker (3 long answers) and an Expert badge
for a star in every book of a city (e.g. Paris Expert). Stars earned before the answer rule were for questions
(`kind` `question`); new ones are `answer`, and the badges count both. The last page of each book shows the
stars from that book, and each book's cover in the library shows a gold star with the number earned in that
book (hidden until it has one; VoiceOver reads e.g. "Famous Places, 7 stars, 1 loved page"). `tools/devices.py` lists stars per device. Pipeline mode gives stars too, but Milo doesn't announce them.

The Passport ends with **My star calendar**: a month grid with a gold star and the count on each day he earned
stars, today outlined, and the month's total ("33 stars in October"). The arrows or a sideways swipe go back to
earlier months (as far as his first star). Days use each award's `awarded_at` in the device's time zone, so a
reinstall restores them from the server, and turning stars into coins never changes a day. An award without a
readable time is left off the calendar with a short note. VoiceOver reads each day, e.g. "October 18, today, 3 stars".

### Coins

A grown-up can turn stars into coins (10 stars = 1 coin by default) in the Grown-ups corner, behind the parent
gate. It shows the stars he can spend, the coins they make and the stars left over, and asks for confirmation.
The grown-up can convert as many coins as the stars cover (stepper, or quick picks like 5 and "All N"), unless
the server sets a maximum. The kid can't convert. His coins (a gold dollar coin) always show next to the stars
on the library badge and in the Passport, even at 0.

The token server owns the rules and the balance. It keeps a ledger per device (`coin_conversions`), so coins
survive a reinstall and an app can't convert more stars than the server has stored. Spending stars lowers the
number on the badge and in the book, but badges and Passport progress keep counting every star he ever earned,
so converting never takes a badge away. Older app builds ignore coins and keep showing all his stars.

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

### Deploy

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
  `PIP_ADMIN_KEY` and `PIP_REPORT_KEY`, set in the Render dashboard, not in git. A free service sleeps after 15 idle minutes, so with `KEEPALIVE_SECONDS` set the
  server pings its own `/healthz` to stay awake (one always-on service fits in the free 750 hours a month). If it
  does fall asleep (e.g. after a redeploy fails), the first token takes about a minute.
- **Agent:** `scripts/deploy_agent.sh` reads the env file, uploads `OPENAI_API_KEY` and the `PIP_*` settings as
  LiveKit secrets, and creates the agent (first run, region `$LK_AGENT_REGION`, default `us-east`) or deploys a
  new version. Set `PIP_CONTENT_URL=https://little-explorer-pip.onrender.com/content` so Milo reads the same
  books as the app. Set `PIP_SERVER_URL=https://little-explorer-pip.onrender.com` and the same `PIP_REPORT_KEY`
  as Render so Milo reports each device's minutes. Stop any local `agent dev` worker afterwards; it would compete for the same rooms.
  `lk agent status .` and `lk agent logs .` (from `server/`, with `LIVEKIT_*` in the environment) show it.
- **Check it:** `TOKEN_URL=https://little-explorer-pip.onrender.com/token uv run python tools/e2e_smoke.py --mode realtime --trials 1`.

### Update books without an app update

The server publishes `content/manifest.json`, which lists every book, photo and narration clip with its SHA-256.
On launch (and when it comes back to the foreground) the app compares it with its copy, downloads only the changed
files, checks each hash, and switches to the new set in one step. If anything fails it keeps what it has; the
books bundled in the app are the last fallback. Milo re-reads the books from the same server at most once a minute.

1. Edit `LittleExplorer/Resources/Books/*.json` (or `library.json` for the shelf), add photos to
   `Resources/Photos` and run `scripts/generate_narration.py` for new sentences.
2. Merge to `main`. The Deploy workflow rebuilds Render; the next app launch picks the changes up.

A new book must still use the existing page format; a format change needs an app update.

## Build and test the app

Requires Xcode 26 (iOS 26 SDK). The project is checked in; no xcodegen needed.

```sh
xcodebuild -project LittleExplorer.xcodeproj -scheme LittleExplorer \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
scripts/screenshots.sh <iphone-udid> <ipad-udid>   # regenerates docs/screenshots
```

DEBUG builds accept launch arguments for screenshots: `-demoBook paris-food -demoPage 4 -demoLove`,
`-demoVoice listening|live|nap|rest|star`, `-demoRead`, `-demoStars 23`, `-demoPassport`, `-demoCalendar`
(the Passport's star calendar, with stars spread over the last few weeks), `-demoCoins 2`,
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
testers. Book changes don't need a new build (see "Update books without an app update").

> **Simulator note:** the iOS 26.2/26.3 simulators draw every emoji as a "?" box. Real devices are fine.
>
> **Voice in the simulator:** the simulator has no Apple echo cancellation, so the app uses WebRTC's
> software echo cancellation there. Milo can still hear itself through loud Mac speakers; headphones
> give the cleanest test.
> The app avoids emoji in its UI: flags are bundled images, and buttons use SF Symbols.
