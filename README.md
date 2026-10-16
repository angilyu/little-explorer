# Little Explorer

A picture-book app for iPhone and iPad that helps a 6-year-old discover cities and get excited about travel.
Each page has a real photo, a short sentence he can read by himself, a heart button for pages he loves,
and a speaker button that reads the sentence aloud. An optional voice buddy, **Pip**, listens patiently
while he reads and answers his questions about the book.

| Shelf | Yummy Paris + heart | Read aloud | Voice mode |
|---|---|---|---|
| ![](docs/screenshots/iphone-shelf.jpg) | ![](docs/screenshots/iphone-yummy-paris-heart.jpg) | ![](docs/screenshots/iphone-read-aloud.jpg) | ![](docs/screenshots/iphone-fun-art-voice.jpg) |

iPad versions are in [`docs/screenshots`](docs/screenshots).

## Books

The first screen is a shelf with every book, grouped by city. Tap a cover to open it.

| City | Book (`id`) | Theme |
|---|---|---|
| Paris | Famous Places (`paris`) | The Seine, Eiffel Tower (repainting, sparkles), Louvre and Mona Lisa, Notre-Dame, Arc de Triomphe, toy sailboats |
| Paris | Yummy Paris (`paris-food`) | Baguettes, pain au chocolat, macarons, crêpes, cheese, a market, a café, ice cream, snails, a picnic |
| Paris | Fun & Art (`paris-fun`) | Carousels, puppet shows, a Seine boat, the Louvre's old castle, Orsay, Monet's water lilies, Montmartre painters and funicular, The Thinker, the river beach |
| London | London (`london`) | The Thames, Big Ben, Tower Bridge, buses, ravens, guards, London Eye, the Tube, the museum whale, fish and chips |
| Barcelona | Barcelona (`barcelona`) | Beach, Sagrada Família, Casa Batlló, Park Güell's dragon, churros, human towers, football, the magic fountain |

Books are JSON files in [`LittleExplorer/Resources/Books`](LittleExplorer/Resources/Books) with 10–14 pages each.
Every page has a 4–12 word sentence, hidden `about` notes for Pip, and a photo. The shelf order is `BookLibrary.order`.
A city with several books shows them under one city heading; `theme` is the title on the cover.

Hearts are stored on the device as `<book id>/<page id>`, so each themed book keeps its own hearts.
Grown-ups can see the loved pages for every book behind the parent gate (lock button).

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

The speaker button (bottom-left of every page) reads the sentence in a natural voice, the same OpenAI voice as Pip
(`gpt-4o-mini-tts`, "coral"), styled like a grown-up reading a picture book to a 6-year-old: warm, clear and a little slow.
The clips are made ahead of time and bundled (about 2 MB of AAC for all 64 pages), so reading aloud works offline
and the app never holds an API key. Each word is highlighted as it's spoken. Tap again to stop. Speech also stops when
the page turns or the book closes. When voice mode is on, the microphone is muted while the app reads, so Pip doesn't
mistake the app's voice for the child.

[`scripts/generate_narration.py`](scripts/generate_narration.py) makes the clips. It reads each sentence with
`gpt-4o-mini-tts` and gets word timings from `whisper-1`. It tightens those timings to the real pauses in the audio,
retries if the transcript doesn't match the sentence, and writes `LittleExplorer/Resources/Narration/`. After editing a
sentence, rerun it (it only regenerates pages that changed). It needs `OPENAI_API_KEY` (or `server/.env`) and macOS `afconvert`:

```sh
python3 scripts/generate_narration.py                  # new or changed pages only
python3 scripts/generate_narration.py --only paris/eiffel --force
```

If a page has no clip for its current sentence, the app falls back to the best on-device system voice
(`AVSpeechSynthesizer`, slower rate, same word highlighting).

## Voice buddy (Pip)

Tap **Talk to Pip** in a book to start voice mode. It stays on across page turns until it's turned off.
On every page turn the app sends the current page (book, page, sentence) to the agent.
The agent lives in [`server/`](server) and uses [voice-kit](https://github.com/angilyu/voice-kit) (LiveKit Agents + LangGraph):

- **Reading:** while he reads the page aloud, Pip stays quiet (`reference_text_gate` → LISTEN), even if he reads slowly or softly, then praises him.
- **Noise:** background voices are cancelled, and an LLM relevance gate ignores noise, other people's conversations and off-topic talk.
- **Patience:** the `patient` preset waits 1.6–6 s for him to finish a turn. Answers are short and warm, and end with an open question that invites a longer reply.

No audio is stored. The app never holds LiveKit secrets. A small token server mints room tokens that dispatch the agent automatically.

```sh
cd server
cp .env.example .env        # fill in LIVEKIT_* and OPENAI_API_KEY (never commit .env)
uv sync
uv run python -m little_explorer_agent.token_server   # http://127.0.0.1:8787
uv run python -m little_explorer_agent.agent dev      # the Pip worker
uv run pytest -q
uv run python tools/e2e_smoke.py                       # live end-to-end check (no audio saved)
```

On a real device, set the token server address in the Grown-ups corner (e.g. `http://<your-mac>.local:8787`).

## Build and test the app

Requires Xcode 26 (iOS 26 SDK). The project is checked in; no xcodegen needed.

```sh
xcodebuild -project LittleExplorer.xcodeproj -scheme LittleExplorer \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
scripts/screenshots.sh <iphone-udid> <ipad-udid>   # regenerates docs/screenshots
```

DEBUG builds accept launch arguments for screenshots: `-demoBook paris-food -demoPage 4 -demoLove`,
`-demoVoice listening|live`, `-demoRead`.

> **Simulator note:** the iOS 26.2/26.3 simulators draw every emoji as a "?" box. Real devices are fine.
> The app avoids emoji in its UI: flags are bundled images, and buttons use SF Symbols.
