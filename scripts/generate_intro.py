#!/usr/bin/env python3
"""Record Milo's fixed intro once, in his own realtime voice, so the app can play it on tap.

The app plays ``LittleExplorer/Resources/Narration/milo-intro.m4a`` the moment Milo is turned on,
while it connects in the background, and tells the agent not to say the greeting itself. The clip
is the agent's ``GREETING`` spoken by the same OpenAI Realtime model and voice as Milo
(gpt-realtime, "coral", with his persona), trimmed of leading and trailing silence, and levelled
like the read-aloud clips (-16 LUFS, AAC). whisper-1 checks the words; a take that doesn't say the
greeting is retried. Because the file sits in Resources/Narration it is bundled with the app and
also served from ``/content``.

Run it with the server's environment (it needs the ``openai`` package) and ffmpeg:

    server/.venv/bin/python scripts/generate_intro.py

The key comes from OPENAI_API_KEY or the env files ``generate_narration.py`` reads.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "server" / "src"))

import generate_narration as narration  # noqa: E402
from little_explorer_agent.buddy import GREETING, PERSONA  # noqa: E402

CLIP = narration.OUT / "milo-intro.m4a"
MODEL = "gpt-realtime"
VOICE = "coral"
RATE = 24000
MIN_MATCH = 0.9
TRIES = 3


async def speak(key: str) -> bytes:
    """One take of the greeting as 24 kHz 16-bit mono PCM."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=key)
    audio = bytearray()
    async with client.realtime.connect(model=MODEL) as conn:
        await conn.session.update(
            session={
                "type": "realtime",
                "instructions": PERSONA,
                "output_modalities": ["audio"],
                "audio": {"output": {"voice": VOICE, "format": {"type": "audio/pcm", "rate": RATE}}},
            }
        )
        # The same words the agent used to greet him with (realtime.py start_realtime_session).
        await conn.response.create(response={"instructions": f'Greet him by saying exactly: "{GREETING}"'})
        async for event in conn:
            if event.type == "response.output_audio.delta":
                audio += base64.b64decode(event.delta)
            elif event.type == "error":
                raise RuntimeError(event.error.message)
            elif event.type == "response.done":
                break
    return bytes(audio)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def main() -> None:
    key = narration.api_key()
    with tempfile.TemporaryDirectory() as tmp:
        raw, trimmed, wav = Path(tmp) / "raw.wav", Path(tmp) / "trimmed.wav", Path(tmp) / "clip.wav"
        for attempt in range(1, TRIES + 1):
            pcm = asyncio.run(speak(key))
            with wave.open(str(raw), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(pcm)
            # No silence before the first word, so the intro starts the moment he taps.
            trim = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.02"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), "-af",
                            f"{trim},areverse,{trim},areverse", str(trimmed)], check=True)
            mp3 = Path(tmp) / "take.mp3"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(trimmed), str(mp3)],
                           check=True)
            heard = narration.transcribe(mp3.read_bytes(), "", key).get("text", "")
            match = difflib.SequenceMatcher(None, words(GREETING), words(heard)).ratio()
            print(f"take {attempt}: match {match:.2f}")
            if match >= MIN_MATCH:
                break
        else:
            sys.exit("no take said the greeting; try again")
        narration.level_and_encode(trimmed, wav, CLIP)
        with wave.open(str(wav)) as w:
            duration = w.getnframes() / w.getframerate()
    lufs, peak = narration.measure(CLIP)
    print(f"wrote {CLIP.relative_to(ROOT)}: {duration:.2f}s, {lufs:.1f} LUFS, peak {peak:.1f} dBTP")


if __name__ == "__main__":
    main()
