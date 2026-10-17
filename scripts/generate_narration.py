#!/usr/bin/env python3
"""Pre-generate the read-aloud clips: one natural OpenAI voice clip per page sentence.

For each page the script
1. asks gpt-4o-mini-tts (voice "coral") to read the sentence like a picture book,
2. transcribes the clip with whisper-1 to get word timestamps,
3. aligns those words to the page text (so the app can highlight each word as it's spoken) and
   retries the clip if the transcript doesn't match the sentence,
4. levels it to TARGET_LUFS (-16 LUFS, peaks held under -3 dBFS, with `ffmpeg`) so it plays
   about as loud as Milo, then converts it to a small AAC file (macOS `afconvert`) in
   LittleExplorer/Resources/Narration and records the timings in narration.json.

Needs ffmpeg (`brew install ffmpeg`).

A book can add its own reading note and read phrase by phrase, e.g. the French book, where each phrase
is followed by a sound-it-out hint that a single take sometimes skips:

    "narration": {"instructions": "Say every French phrase in native Parisian French...", "phraseByPhrase": true}

Phrase by phrase, each phrase (split at punctuation and brackets) is its own take, joined with short
pauses, so the word times come from the takes rather than from whisper.

Pages whose text, voice and style haven't changed are skipped, so rerun it after editing a book.
The key comes from OPENAI_API_KEY, else the env file in $LITTLE_EXPLORER_ENV, server/.env or
~/.config/little-explorer/.env (first that exists). It never ends up in the app.

    python3 scripts/generate_narration.py                    # every book, only what changed
    python3 scripts/generate_narration.py --only paris-food  # one book
    python3 scripts/generate_narration.py --only paris/eiffel --force
    python3 scripts/generate_narration.py --loudness         # re-level existing clips, no API calls
"""

from __future__ import annotations

import argparse
import array
import concurrent.futures
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ROOT / "LittleExplorer" / "Resources" / "Books"
OUT = ROOT / "LittleExplorer" / "Resources" / "Narration"
MANIFEST = OUT / "narration.json"

MODEL = "gpt-4o-mini-tts"
VOICE = "coral"
INSTRUCTIONS = (
    "You are reading a picture book aloud to a 6-year-old. "
    "Voice: warm, gentle and friendly, with a little smile. "
    "Pace: a little slow and very clear, so a child can follow each word with a finger. "
    "Pause briefly at commas and a bit longer between sentences. "
    "Read exactly the words given, nothing more. Say French and Spanish names the way locals do."
)
MIN_MATCH = 0.85
ATTEMPTS = 3

TOKEN = re.compile(r"[\w'’]+")


def instructions(extra: str = "") -> str:
    """The shared reading style, plus a book's own `narration` note (e.g. native French phrases)."""
    return f"{INSTRUCTIONS} {extra}".strip() if extra else INSTRUCTIONS


def style_hash(extra: str = "", phrased: bool = False) -> str:
    style = f"{MODEL}|{VOICE}|{instructions(extra)}" + ("|phrase by phrase" if phrased else "")
    return hashlib.sha1(style.encode()).hexdigest()[:10]


def api_key() -> str:
    if key := os.environ.get("OPENAI_API_KEY"):
        return key
    override = os.environ.get("LITTLE_EXPLORER_ENV", "").strip()
    candidates = [Path(override).expanduser()] if override else [
        ROOT / "server" / ".env",
        Path("~/.config/little-explorer/.env").expanduser(),
    ]
    env = next((path for path in candidates if path.is_file()), None)
    if env is not None:
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    sys.exit("Set OPENAI_API_KEY or add it to server/.env or ~/.config/little-explorer/.env")


def request(url: str, key: str, body: bytes, content_type: str) -> bytes:
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503) and attempt < 4:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f"{url}: HTTP {err.code} {err.read()[:300]!r}") from None
        except urllib.error.URLError:
            if attempt < 4:
                time.sleep(2 ** attempt * 2)
                continue
            raise
    raise RuntimeError("unreachable")


def speak(text: str, key: str, extra: str = "") -> bytes:
    body = json.dumps({"model": MODEL, "voice": VOICE, "input": text, "instructions": instructions(extra),
                       "response_format": "mp3"})
    return request("https://api.openai.com/v1/audio/speech", key, body.encode(), "application/json")


def transcribe(audio: bytes, prompt: str, key: str) -> dict:
    boundary = uuid.uuid4().hex
    fields = [("model", "whisper-1"), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word"),
              ("language", "en"), ("prompt", prompt)]
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in fields]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="clip.mp3"\r\n'
                 "Content-Type: audio/mpeg\r\n\r\n".encode() + audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    data = request("https://api.openai.com/v1/audio/transcriptions", key, b"".join(parts), f"multipart/form-data; boundary={boundary}")
    return json.loads(data)


def norm(word: str) -> str:
    word = unicodedata.normalize("NFKD", word.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in word if not unicodedata.combining(c)))


def utf16(text: str, index: int) -> int:
    return len(text[:index].encode("utf-16-le")) // 2


def align(text: str, heard: list[dict], duration: float) -> tuple[list[dict], float]:
    """Give every word of `text` a start/end time from the transcribed words.

    Returns the words (UTF-16 offsets, as NSRange wants) and how well the transcript matched.
    """
    tokens = [m for m in TOKEN.finditer(text) if norm(m.group())]
    spoken: list[tuple[str, float, float]] = []
    for w in heard:
        pieces = [norm(p) for p in TOKEN.findall(w["word"]) if norm(p)]
        step = (w["end"] - w["start"]) / max(len(pieces), 1)
        spoken += [(p, w["start"] + i * step, w["start"] + (i + 1) * step) for i, p in enumerate(pieces)]

    a, b = [norm(t.group()) for t in tokens], [s[0] for s in spoken]
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            times[block.a + k] = spoken[block.b + k][1:]

    # Words the transcript spelled differently (e.g. "7" vs "seven") share the gap between neighbours.
    i = 0
    while i < len(times):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else 0.0
        hi = times[j][0] if j < len(times) else duration
        hi = max(hi, lo)
        step = (hi - lo) / (j - i)
        for k in range(i, j):
            times[k] = (lo + (k - i) * step, lo + (k - i + 1) * step)
        i = j

    words = [{"location": utf16(text, t.start()), "length": utf16(text, t.end()) - utf16(text, t.start()),
              "start": round(s, 3), "end": round(e, 3)} for t, (s, e) in zip(tokens, times)]
    return words, matcher.ratio()


def silent_gaps(wav: Path, min_gap: float = 0.06) -> list[tuple[float, float]]:
    """Quiet stretches in the clip (10 ms frames), used to tighten whisper's word times."""
    with wave.open(str(wav)) as w:
        rate = w.getframerate()
        samples = array.array("h", w.readframes(w.getnframes()))
    hop, win = int(rate * 0.01), int(rate * 0.025)
    rms = [math.sqrt(sum(x * x for x in samples[i:i + win]) / max(1, len(samples[i:i + win])))
           for i in range(0, len(samples), hop)]
    if not rms:
        return []
    loud = sorted(rms)[int(len(rms) * 0.95)]
    quiet = [r < max(120.0, 0.08 * loud) for r in rms]
    gaps, i = [], 0
    while i < len(quiet):
        j = i
        while j < len(quiet) and quiet[j] == quiet[i]:
            j += 1
        if quiet[i] and (j - i) * 0.01 >= min_gap:
            gaps.append((i * 0.01, j * 0.01))
        i = j
    return gaps


def snap_to_pauses(text: str, words: list[dict], gaps: list[tuple[float, float]], duration: float,
                   reach: float = 0.35) -> list[dict]:
    """Whisper tends to stretch a word over the pause next to it. Move word edges onto the real pauses.

    Each pause goes to the nearest word boundary (boundaries at punctuation first), at most one each.
    """
    if not words or not gaps:
        return words
    words = [dict(w) for w in words]
    merged: list[list[float]] = []
    for g0, g1 in gaps:
        if merged and g0 - merged[-1][1] < 0.06:
            merged[-1][1] = g1
        else:
            merged.append([g0, g1])
    if merged[0][0] <= 0.01:
        words[0]["start"] = max(words[0]["start"], merged[0][1])
    if merged[-1][1] >= duration - 0.05 and merged[-1][0] > words[-1]["start"]:
        words[-1]["end"] = min(words[-1]["end"], merged[-1][0])
    inner = [g for g in merged if g[0] > 0.01 and g[1] < duration - 0.05]

    pairs = []
    for gi, (g0, g1) in enumerate(inner):
        for i in range(len(words) - 1):
            edge = words[i + 1]["start"]
            dist = 0.0 if g0 <= edge <= g1 else min(abs(edge - g0), abs(edge - g1))
            between = text[words[i]["location"] + words[i]["length"]:words[i + 1]["location"]]
            if dist <= reach:
                pairs.append((dist - (0.25 if re.search(r"[,.!?;:]", between) else 0.0), gi, i))
    used_gaps, used_edges = set(), set()
    for _, gi, i in sorted(pairs):
        if gi in used_gaps or i in used_edges:
            continue
        used_gaps.add(gi)
        used_edges.add(i)
        words[i]["end"], words[i + 1]["start"] = inner[gi]

    # Keep the order sensible: no word starts before the one before it, and each lasts a moment.
    for i in range(1, len(words)):
        words[i]["start"] = max(words[i]["start"], words[i - 1]["start"] + 0.08)
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else max(w["end"], w["start"] + 0.08)
        w["end"] = min(max(w["end"], w["start"] + 0.08), max(nxt, w["start"] + 0.08))
        w["start"], w["end"] = round(w["start"], 3), round(w["end"], 3)
    return words


def phrases(text: str) -> list[tuple[str, list[re.Match]]]:
    """Split a page into short spoken phrases at punctuation and brackets, e.g.
    "Merci (mair-SEE) means thank you." -> "Merci" / "mair-SEE" / "means thank you."."""
    tokens = [m for m in TOKEN.finditer(text) if norm(m.group())]
    groups, current = [], tokens[:1]
    for prev, tok in zip(tokens, tokens[1:]):
        if re.search(r"[,.!?;:()]", text[prev.end():tok.start()]):
            groups.append(current)
            current = []
        current.append(tok)
    if current:
        groups.append(current)
    out = []
    for k, group in enumerate(groups):
        stop = groups[k + 1][0].start() if k + 1 < len(groups) else len(text)
        spoken = re.sub(r"[()]", "", text[group[0].start():stop]).strip().rstrip(",;:").strip()
        out.append((spoken, group))
    return out


def decode(audio: bytes, wav: Path) -> array.array:
    src = wav.with_suffix(".mp3")
    src.write_bytes(audio)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(RATE),
                    "-c:a", "pcm_s16le", str(wav)], check=True)
    with wave.open(str(wav)) as w:
        return array.array("h", w.readframes(w.getnframes()))


def speak_phrases(text: str, key: str, extra: str, out: Path) -> tuple[list[dict], float]:
    """Read a page one phrase at a time and join the takes with short pauses, so a French phrase and its
    sound-it-out hint are both always spoken (a single take sometimes skips the hint) and every word's
    time is known exactly. Writes 16-bit mono wav to ``out``; returns the word timings and duration."""
    samples, words = array.array("h", [0] * int(RATE * 0.15)), []
    for spoken, group in phrases(text):
        tmp = out.with_name(out.stem + "-phrase.wav")
        take = decode(speak(spoken, key, extra), tmp)
        gaps = silent_gaps(tmp, min_gap=0.03)
        start = next((g[1] for g in gaps if g[0] <= 0.01), 0.0)
        end = next((g[0] for g in gaps if g[1] >= len(take) / RATE - 0.02 and g[0] > start), len(take) / RATE)
        take = take[int(start * RATE):int(end * RATE)]
        t0, length = len(samples) / RATE, len(take) / RATE
        weights = [max(len(norm(t.group())), 2) for t in group]
        for tok, wgt in zip(group, weights):
            t1 = t0 + length * wgt / sum(weights)
            words.append({"location": utf16(text, tok.start()), "length": utf16(text, tok.end()) - utf16(text, tok.start()),
                          "start": round(t0, 3), "end": round(t1, 3)})
            t0 = t1
        pause = 0.5 if re.search(r"[.!?]$", spoken) else 0.35
        samples += take + array.array("h", [0] * int(RATE * pause))
    samples = samples[:len(samples) - int(RATE * 0.2)]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return words, round(len(samples) / RATE, 3)


TARGET_LUFS = -16.0
PEAK_DBFS = -3.0
MAX_TRUE_PEAK = -1.0  # AAC encoding overshoots the limiter by up to ~3 dB; checked after encoding
RATE = 24000


def measure(src: Path) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a clip."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af", "loudnorm=print_format=json",
                          "-f", "null", "-"], check=True, capture_output=True, text=True).stderr
    stats = json.loads(out[out.rindex("{"):out.rindex("}") + 1])
    lufs, peak = float(stats["input_i"]), float(stats["input_tp"])
    return (lufs if math.isfinite(lufs) else TARGET_LUFS), peak


def level(src: Path, wav: Path, trim: float = 0.0) -> None:
    """Write ``src`` as 16-bit mono wav at TARGET_LUFS (less ``trim`` dB): one fixed gain, and a
    limiter (no added delay, so word timings stay put) that only touches the few peaks the gain
    would push over PEAK_DBFS."""
    gain = TARGET_LUFS - measure(src)[0] - trim
    limit = 10 ** (PEAK_DBFS / 20)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-af",
                    f"volume={gain:.2f}dB,alimiter=limit={limit:.4f}:level=0:latency=1", "-ac", "1",
                    "-ar", str(RATE), "-c:a", "pcm_s16le", str(wav)], check=True)


def encode(wav: Path, clip: Path) -> None:
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "48000", str(wav), str(clip)], check=True, capture_output=True)


def level_and_encode(src: Path, wav: Path, clip: Path) -> None:
    """Level ``src`` into ``wav`` and encode ``clip``; if the encoded peak is too hot, turn it down."""
    level(src, wav)
    encode(wav, clip)
    peak = measure(clip)[1]
    if peak > MAX_TRUE_PEAK:
        level(src, wav, trim=peak - MAX_TRUE_PEAK + 0.3)
        encode(wav, clip)


def relevel(clip: Path) -> None:
    """Bring an existing clip to TARGET_LUFS in place (same take, same timings)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.m4a"
        shutil.copyfile(clip, src)
        level_and_encode(src, Path(tmp) / "clip.wav", Path(tmp) / "out.m4a")
        (Path(tmp) / "out.m4a").replace(clip)


def generate_phrased(key_id: str, text: str, key: str, extra: str) -> dict:
    name = "read-" + key_id.replace("/", "-")
    clip = OUT / f"{name}.m4a"
    with tempfile.TemporaryDirectory() as tmp:
        joined, pcm = Path(tmp) / "joined.wav", Path(tmp) / "clip.wav"
        words, duration = speak_phrases(text, key, extra, joined)
        level_and_encode(joined, pcm, clip)
    return {"file": name, "text": text, "duration": duration, "match": 1.0, "heard": "(phrase by phrase)",
            "words": words, "lufs": TARGET_LUFS}


def generate(key_id: str, text: str, key: str, extra: str = "", phrased: bool = False) -> dict:
    if phrased:
        return generate_phrased(key_id, text, key, extra)
    best = None
    for _ in range(ATTEMPTS):
        mp3 = speak(text, key, extra)
        result = transcribe(mp3, text, key)
        words, score = align(text, result.get("words", []), float(result.get("duration", 0)))
        if best is None or score > best[2]:
            best = (mp3, result, score, words)
        if score >= MIN_MATCH:
            break
    mp3, result, score, words = best
    if score < MIN_MATCH:
        print(f"  ! {key_id}: transcript only matched {score:.2f}: {result.get('text')!r}", file=sys.stderr)

    name = "read-" + key_id.replace("/", "-")
    clip = OUT / f"{name}.m4a"
    duration = float(result.get("duration", 0))
    with tempfile.TemporaryDirectory() as tmp:
        src, pcm = Path(tmp) / "clip.mp3", Path(tmp) / "clip.wav"
        src.write_bytes(mp3)
        level_and_encode(src, pcm, clip)
        words = snap_to_pauses(text, words, silent_gaps(pcm), duration)
    return {"file": name, "text": text, "duration": round(float(result.get("duration", 0)), 3),
            "match": round(score, 3), "heard": result.get("text", "").strip(), "words": words,
            "lufs": TARGET_LUFS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", default=[], help="book or book/page (repeatable)")
    parser.add_argument("--force", action="store_true", help="regenerate even if unchanged")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--loudness", action="store_true", help=f"re-level existing clips to {TARGET_LUFS} LUFS and exit")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    clips: dict = manifest.get("clips", {})
    if args.loudness:
        todo = [k for k, c in clips.items() if c.get("lufs") != TARGET_LUFS or args.force]
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            list(pool.map(lambda k: relevel(OUT / f"{clips[k]['file']}.m4a"), todo))
        for k in todo:
            clips[k]["lufs"] = TARGET_LUFS
        MANIFEST.write_text(json.dumps({**manifest, "clips": dict(sorted(clips.items()))}, ensure_ascii=False, indent=1) + "\n")
        print(f"re-levelled {len(todo)} clip(s) to {TARGET_LUFS} LUFS")
        return
    pages, extras = {}, {}
    for path in sorted(BOOKS.glob("*.json")):
        book = json.loads(path.read_text())
        for page in book["pages"]:
            pages[f"{book['id']}/{page['id']}"] = page["text"]
            # Optional per-book narration, e.g. {"instructions": "...", "phraseByPhrase": true}.
            note = book.get("narration", {})
            extras[f"{book['id']}/{page['id']}"] = (note.get("instructions", ""), bool(note.get("phraseByPhrase")))

    def wanted(key_id: str) -> bool:
        if args.only and not any(key_id == o or key_id.startswith(o + "/") for o in args.only):
            return False
        old = clips.get(key_id)
        fresh = old and old["text"] == pages[key_id] and old.get("style") == style_hash(*extras[key_id]) and (OUT / f"{old['file']}.m4a").exists()
        return args.force or not fresh

    todo = [k for k in pages if wanted(k)]
    print(f"{len(todo)} clip(s) to generate")
    key = api_key()
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        futures = {pool.submit(generate, k, pages[k], key, *extras[k]): k for k in todo}
        for future in concurrent.futures.as_completed(futures):
            k = futures[future]
            clips[k] = {**future.result(), "style": style_hash(*extras[k])}
            print(f"  {k}: {clips[k]['duration']:.1f}s match {clips[k]['match']:.2f}")

    # Drop clips for pages that no longer exist.
    for k in [k for k in clips if k not in pages]:
        (OUT / f"{clips.pop(k)['file']}.m4a").unlink(missing_ok=True)

    manifest = {"model": MODEL, "voice": VOICE, "instructions": INSTRUCTIONS,
                "clips": dict(sorted(clips.items()))}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    weak = [k for k, c in clips.items() if c["match"] < MIN_MATCH]
    print(f"narration.json: {len(clips)} clips" + (f"; check these by ear: {', '.join(weak)}" if weak else ""))


if __name__ == "__main__":
    main()
