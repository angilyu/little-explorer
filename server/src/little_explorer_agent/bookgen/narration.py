"""Read-aloud clips for made books (a port of ``scripts/generate_narration.py``).

Each page is spoken by ``gpt-4o-mini-tts`` ("coral"), transcribed by ``whisper-1`` for word timings
(so the app can light up each word), aligned to the page text, snapped to the real pauses, leveled
to -16 LUFS and encoded as a small AAC ``.m4a`` with ffmpeg. The pace follows the book's level.
"""

from __future__ import annotations

import array
import asyncio
import difflib
import json
import math
import re
import subprocess
import tempfile
import unicodedata
import wave
from pathlib import Path
from typing import Any

from ..levels import level_of
from .config import BookgenConfig
from .cost import CostLog
from .oai import OpenAIClient

# The hand-made books' voice, for "a young child" rather than a fixed age; the level adds a pace.
INSTRUCTIONS = (
    "You are reading a picture book aloud to a young child. Voice: warm, gentle and friendly, with a "
    "little smile. Pace: a little slow and very clear, so a child can follow each word with a finger. "
    "Pause briefly at commas and a bit longer between sentences. Read exactly the words given, nothing "
    "more. Say names of places and foods the way locals do."
)
MIN_MATCH = 0.85
ATTEMPTS = 3
TOKEN = re.compile(r"[\w'’]+")
TARGET_LUFS = -16.0
PEAK_DBFS = -3.0
MAX_TRUE_PEAK = -1.0  # AAC encoding overshoots the limiter by up to ~3 dB; checked after encoding
RATE = 24000
FFMPEG = "ffmpeg"


def instructions(level: object) -> str:
    pace = level_of(level).narration_pace
    return f"{INSTRUCTIONS} {pace}".strip()


def clip_name(book_id: str, page_id: str) -> str:
    return f"read-{book_id}-{page_id}"


def norm(word: str) -> str:
    word = unicodedata.normalize("NFKD", word.lower())
    # Hebrew letters count as word characters too, so a Hebrew word is spoken and highlighted.
    return re.sub(r"[^a-z0-9\u05d0-\u05ea]", "", "".join(c for c in word if not unicodedata.combining(c)))


def utf16(text: str, index: int) -> int:
    return len(text[:index].encode("utf-16-le")) // 2


def align(text: str, heard: list[dict[str, Any]], duration: float) -> tuple[list[dict[str, Any]], float]:
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
        lo = times[i - 1][1] if i > 0 else 0.0  # type: ignore[index]
        hi = times[j][0] if j < len(times) else duration  # type: ignore[index]
        hi = max(hi, lo)
        step = (hi - lo) / (j - i)
        for k in range(i, j):
            times[k] = (lo + (k - i) * step, lo + (k - i + 1) * step)
        i = j

    words = [{"location": utf16(text, t.start()), "length": utf16(text, t.end()) - utf16(text, t.start()),
              "start": round(s, 3), "end": round(e, 3)} for t, (s, e) in zip(tokens, times)]  # type: ignore[misc]
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


def snap_to_pauses(text: str, words: list[dict[str, Any]], gaps: list[tuple[float, float]], duration: float,
                   reach: float = 0.35) -> list[dict[str, Any]]:
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


def measure(src: Path) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a clip."""
    out = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(src), "-af", "loudnorm=print_format=json",
                          "-f", "null", "-"], check=True, capture_output=True, text=True).stderr
    stats = json.loads(out[out.rindex("{"):out.rindex("}") + 1])
    lufs, peak = float(stats["input_i"]), float(stats["input_tp"])
    return (lufs if math.isfinite(lufs) else TARGET_LUFS), peak


def level(src: Path, wav: Path, trim: float = 0.0) -> None:
    """Write ``src`` as 16-bit mono wav at TARGET_LUFS (less ``trim`` dB) with a no-delay limiter."""
    gain = TARGET_LUFS - measure(src)[0] - trim
    limit = 10 ** (PEAK_DBFS / 20)
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-af",
                    f"volume={gain:.2f}dB,alimiter=limit={limit:.4f}:level=0:latency=1", "-ac", "1",
                    "-ar", str(RATE), "-c:a", "pcm_s16le", str(wav)], check=True)


def encode(wav: Path, clip: Path) -> None:
    """AAC at 48 kbps in an .m4a (the local script uses macOS afconvert; ffmpeg runs on the server)."""
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "48k",
                    "-movflags", "+faststart", str(clip)], check=True, capture_output=True)


def level_and_encode(src: Path, wav: Path, clip: Path) -> None:
    level(src, wav)
    encode(wav, clip)
    peak = measure(clip)[1]
    if peak > MAX_TRUE_PEAK:
        level(src, wav, trim=peak - MAX_TRUE_PEAK + 0.3)
        encode(wav, clip)


def finish_clip(mp3: bytes, text: str, words: list[dict[str, Any]], duration: float) -> tuple[bytes, list[dict[str, Any]]]:
    """Level and encode the clip; returns the .m4a bytes and the word times snapped to its pauses."""
    with tempfile.TemporaryDirectory() as tmp:
        src, pcm, clip = Path(tmp) / "clip.mp3", Path(tmp) / "clip.wav", Path(tmp) / "clip.m4a"
        src.write_bytes(mp3)
        level_and_encode(src, pcm, clip)
        words = snap_to_pauses(text, words, silent_gaps(pcm), duration)
        return clip.read_bytes(), words


async def narrate(client: OpenAIClient, config: BookgenConfig, book_id: str, page_id: str, text: str, level_no: int,
                  costs: CostLog) -> tuple[bytes, dict[str, Any]]:
    """Speak one page; returns the .m4a and its clip entry for ``narration.json``."""
    best: tuple[bytes, dict[str, Any], float, list[dict[str, Any]]] | None = None
    for _ in range(ATTEMPTS):
        mp3 = await client.speak(text, model=config.tts_model, voice=config.tts_voice, instructions=instructions(level_no))
        result = await client.transcribe(mp3, text, model=config.stt_model)
        duration = float(result.get("duration") or 0)
        costs.audio("narration", config.tts_model, duration, speech=True)
        costs.audio("narration", config.stt_model, duration, speech=False)
        words, score = align(text, result.get("words") or [], duration)
        if best is None or score > best[2]:
            best = (mp3, result, score, words)
        if score >= MIN_MATCH:
            break
        del mp3
    assert best is not None
    mp3, result, score, words = best
    duration = float(result.get("duration") or 0)
    m4a, words = await asyncio.to_thread(finish_clip, mp3, text, words, duration)
    name = clip_name(book_id, page_id)
    clip = {"file": name, "text": text, "duration": round(duration, 3), "match": round(score, 3),
            "heard": str(result.get("text") or "").strip(), "words": words, "lufs": TARGET_LUFS,
            "style": f"bookgen-l{level_of(level_no).number}"}
    return m4a, clip
