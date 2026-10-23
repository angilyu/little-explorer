# Books, photos and narration

How the library is built: the book format, where the photos come from, how the read-aloud clips are made, and how books update without an app release. Back to the [README](../README.md).

## Books

The first screen is a shelf of the books on the family's account, grouped by city. Tap a cover to open it.

A new account starts with these 13 books, the best ones for five famous travel cities (`PIP_DEFAULT_BOOKS` on
the server; `Account.defaultBooks` in the app). The rest of the library (more Paris, Barcelona and Beijing
books, Lake Tahoe, Hawaii, Chengdu, Longyan, Israel and the Travel shelf) stays hidden until the owner grants
it to an account (see [Accounts](operations.md#accounts)).

| City | Book (`id`) | Theme |
|---|---|---|
| Paris | Famous Places (`paris`) | The Seine, Eiffel Tower (repainting, sparkles), Louvre and Mona Lisa, Notre-Dame, Arc de Triomphe, toy sailboats |
| Paris | Yummy Paris (`paris-food`) | Baguettes, pain au chocolat, macarons, crêpes, cheese, a market, a café, ice cream, snails, a picnic |
| Paris | Eiffel Tower (`paris-eiffel`) | The Iron Lady, building it in two years, rivets, Gustave Eiffel's name, repainting by hand every seven years, growing in summer heat, 674 steps, the lifts, fireworks, Eiffel's secret apartment, the hourly sparkle |
| Paris | Louvre Museum (`paris-louvre`) | The glass pyramid and the upside-down one, a palace where kings lived, the very long hall, artists copying paintings, the small Mona Lisa, the winged Victory with no head, Venus de Milo, the giant sphinx, painted Egyptian coffins, touch tables for kids |
| London | London (`london`) | The Thames, Big Ben, Tower Bridge, buses, ravens, guards, London Eye, the Tube, the museum whale, fish and chips |
| London | Big Ben (`london-big-ben`) | Big Ben the bell vs the Elizabeth Tower, who it's named after, the giant clock faces and minute hands, the bongs, the radio chimes, pennies on the pendulum, the Ayrton Light, 334 steps, Westminster Bridge |
| Barcelona | Barcelona (`barcelona`) | Beach, Sagrada Família, Casa Batlló, Park Güell's dragon, churros, human towers, football, the magic fountain |
| Barcelona | Sagrada Família (`barcelona-sagrada`) | A church still being built after 140+ years, cranes, Gaudí's 43 years on it, towers like sand castles, fruit on the pinnacles, a turtle and a tortoise holding up columns, a forest of columns with colored light, the magic square that adds up to 33, the wavy-roofed school, the upside-down chain model, Mary's glowing star, the tallest church tower |
| Beijing | Beijing (`beijing`) | China's capital, the sunrise flag raising at Tiananmen, hutong alleys and courtyard homes, pedal rickshaws, crispy Peking duck in pancakes, tanghulu candied fruit, swallow and dragon kites, pandas at Beijing Zoo, the long subway, the Bird's Nest and both Olympics, painted opera faces, the Drum Tower's time-telling drums |
| Beijing | Great Wall (`beijing-great-wall`) | Mutianyu's walkway, about 21,000 km of walls, 2,000 years of builders and old rammed-earth walls, battlements, watchtowers, smoke-by-day fire-by-night beacons, sticky-rice mortar, the Mutianyu cable car and toboggan, steep steps, the seen-from-space myth, snow at Badaling, Old Dragon Head at the sea |
| Beijing | Forbidden City (`beijing-forbidden-city`) | The Meridian Gate, why it was forbidden, the moat and corner towers, the 9,999½ rooms legend, the emperor's yellow roofs, the little roof animals, the lion and cub, the Hall of Supreme Harmony and dragon throne, the Nine Dragon Wall, fire vats kept warm in winter, palace cats, the Imperial Garden |
| Beijing | Temple of Heaven (`beijing-temple-of-heaven`) | The round hall with three blue roofs, built without nails, pillars for seasons, months and hours, round heaven and square earth, the loud center stone, the whispering Echo Wall, the raised stone road, old cypress trees, morning tai chi, kicking a feathered jianzi, painting words with water |
| Shanghai | Shanghai (`shanghai`) | A huge port city, the Huangpu River, the old Bund, the Oriental Pearl Tower's glass floor, twisty Shanghai Tower, the bottle opener, the floating maglev train, xiaolongbao soup dumplings, Yu Garden, the zigzag bridge, the Disneyland castle, the lights at night, a ferry goodbye |

Books are JSON files in [`LittleExplorer/Resources/Books`](../LittleExplorer/Resources/Books) with 10–14 pages each.
Every page has a 4–12 word sentence, hidden `about` notes for Milo, and a photo. The shelf order is `BookLibrary.order`.
A city with several books shows them under one city heading; `theme` is the title on the cover.
Travel books have `"city": "Travel"` and `"kind": "travel"`: they share one Travel shelf with an airplane
badge (`travel-flag.png`), Milo is told they are general travel books rather than about one city, and they
don't give a Passport stamp.

## Photos

All photos come from Wikimedia Commons under free licenses (public domain, CC0, CC BY, CC BY-SA).
The credit (author · license · Commons) is shown under each photo. No photo is used twice.
[`scripts/fetch_photos.py`](../scripts/fetch_photos.py) checks each license through the Commons API and bundles a resized copy:

```sh
python3 scripts/fetch_photos.py --only paris-food            # one book
python3 scripts/fetch_photos.py --only paris-fun/carousel --force   # re-fetch one page
```

Pin a specific file by setting the page's `photo.commonsTitle`.

## Read aloud

The speaker button (bottom-right of every page) reads the sentence in a natural voice, the same OpenAI voice as Milo
(`gpt-4o-mini-tts`, "coral"), styled like a grown-up reading a picture book to a young child: warm, clear and a little slow.
The clips are made ahead of time and bundled (about 17 MB of AAC for all 502 pages), so reading aloud works offline
and the app never holds an API key. Each word is highlighted as it's spoken. Tap again to stop. Speech also stops when
the page turns or the book closes. When voice mode is on, the microphone is muted while the app reads, so Milo doesn't
mistake the app's voice for the child.

[`scripts/generate_narration.py`](../scripts/generate_narration.py) makes the clips. It reads each sentence with
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
Zhuge Liang, Hakka, tulou, Chengqi Lou, hutong, Mutianyu, Cixi, jianzi, xiaolongbao) are said in Mandarin; their pages use plain pinyin and no Chinese characters. The Israel books add a note so Hebrew names (Shalom, Tel Aviv, matkot, Ein Gedi, Masada) are said the Israeli way. Hebrew Words reads phrase by phrase: each page's text is the Hebrew word in Hebrew letters (no vowel marks), a line break, then an English sentence with the transliteration ("שלום\nShalom means hello! It means goodbye too."). The line break splits the phrases, so the Hebrew line is its own take, spoken in Hebrew, and its word is highlighted like any other; the reading check still works because the sentence ends in English. The Barcelona landmark books add a note so Catalan names (Güell, Gaudí, Batlló, Sant Jordi, Tibidabo, Montjuïc, Adéu) are said the Barcelona way. To run it:

```sh
python3 scripts/generate_narration.py                  # new or changed pages only
python3 scripts/generate_narration.py --only paris/eiffel --force
python3 scripts/generate_narration.py --loudness       # re-level existing clips, no API calls
```

If a page has no clip for its current sentence, the app falls back to the best on-device system voice
(`AVSpeechSynthesizer`, slower rate, same word highlighting).

## Update books without an app update

The server publishes `content/manifest.json`, which lists every book, photo and narration clip with its SHA-256.
On launch (and when it comes back to the foreground) the app compares it with its copy, downloads only the changed
files, checks each hash, and switches to the new set in one step. If anything fails it keeps what it has; the
books bundled in the app are the last fallback. Milo re-reads the books from the same server at most once a minute.

1. Edit `LittleExplorer/Resources/Books/*.json` (or `library.json` for the shelf), add photos to
   `Resources/Photos` and run `scripts/generate_narration.py` for new sentences.
2. Merge to `main`. The Deploy workflow rebuilds Render; the next app launch picks the changes up.

A new book must still use the existing page format; a format change needs an app update.

The manifest also carries `badges.json`, the Passport badges, so they can change the same way (see
[Badges](operations.md#badges)).
