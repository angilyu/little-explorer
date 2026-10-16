"""End-to-end smoke test against a real LiveKit room.

Joins like the iOS app (token from the local token server carrying the opening page, page turns
as JSON on the ``context`` data topic),
then plays synthesized speech into the microphone track and checks how Pip reacts:

1. the greeting is spoken;
2. reading the page aloud slowly, in two pieces, is listened to and then praised once;
3. adults talking to each other get no answer;
4. a question about the page gets an answer;
5. after a page turn, a question about the new page is answered from that page.

Needs the agent worker and token server running, plus OPENAI_API_KEY for the test voices.
Run from server/: ``uv run python tools/e2e_smoke.py``. No audio is saved.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from little_explorer_agent.content import load_books  # noqa: E402

SAMPLE_RATE = 24000
FRAME = SAMPLE_RATE // 100  # 10 ms

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
TOKEN_URL = os.environ.get("TOKEN_URL", "http://127.0.0.1:8787/token")


def page_context(book_id: str, index: int) -> dict[str, str]:
    book = load_books()[book_id]
    page = book.pages[index]
    return {"book": book.id, "page": page.id, "page_number": str(index + 1), "city": book.city, "page_text": page.text}


class Probe:
    def __init__(self) -> None:
        self.room = rtc.Room()
        self.source = rtc.AudioSource(SAMPLE_RATE, 1)
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue()
        self.agent_lines: list[tuple[float, str]] = []
        self.user_lines: list[tuple[float, str]] = []
        self.states: list[tuple[float, str]] = []
        self.tasks: set[asyncio.Task] = set()
        self.t0 = time.monotonic()
        self.openai = AsyncOpenAI()

    def now(self) -> float:
        return time.monotonic() - self.t0

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def connect(self, context: dict[str, str]) -> None:
        body = json.dumps({"context": context}).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
        creds = json.loads(urllib.request.urlopen(req, timeout=10).read())
        print(f"[{self.now():5.1f}] room {creds['roomName']}")

        @self.room.on("participant_attributes_changed")
        def _attrs(changed: dict[str, str], participant: rtc.Participant) -> None:
            state = changed.get("lk.agent.state")
            if state:
                self.states.append((self.now(), state))
                print(f"[{self.now():5.1f}] agent state -> {state}")

        async def _read(reader: rtc.TextStreamReader, identity: str) -> None:
            text = (await reader.read_all()).strip()
            attrs = reader.info.attributes or {}
            if not text or attrs.get("lk.transcription_final") == "false":
                return
            if identity == self.room.local_participant.identity:
                self.user_lines.append((self.now(), text))
                print(f"[{self.now():5.1f}]   heard: {text}")
            else:
                self.agent_lines.append((self.now(), text))
                print(f"[{self.now():5.1f}]   PIP: {text}")

        self.room.register_text_stream_handler("lk.transcription", lambda r, ident: self._spawn(_read(r, ident)))
        await self.room.connect(creds["serverUrl"], creds["participantToken"])
        track = rtc.LocalAudioTrack.create_audio_track("mic", self.source)
        await self.room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        self._spawn(self._pump())

    async def _pump(self) -> None:
        silence = np.zeros(FRAME, dtype=np.int16)
        pending = np.zeros(0, dtype=np.int16)
        while True:
            if not len(pending) and not self.queue.empty():
                pending = self.queue.get_nowait()
            chunk, pending = (pending[:FRAME], pending[FRAME:]) if len(pending) else (silence, pending)
            if len(chunk) < FRAME:
                chunk = np.concatenate([chunk, np.zeros(FRAME - len(chunk), dtype=np.int16)])
            await self.source.capture_frame(rtc.AudioFrame(chunk.tobytes(), SAMPLE_RATE, 1, FRAME))

    async def say(self, text: str, voice: str, style: str) -> None:
        resp = await self.openai.audio.speech.create(
            model="gpt-4o-mini-tts", voice=voice, input=text, instructions=style, response_format="pcm"
        )
        pcm = np.frombuffer(resp.content, dtype=np.int16)
        print(f"[{self.now():5.1f}] >> ({voice}) {text}")
        await self.queue.put(pcm)
        await asyncio.sleep(len(pcm) / SAMPLE_RATE)

    async def set_page(self, ctx: dict[str, str]) -> None:
        await self.room.local_participant.publish_data(json.dumps(ctx), reliable=True, topic="context")
        print(f"[{self.now():5.1f}] page -> {ctx['book']} #{ctx['page_number']}: {ctx['page_text']}")

    async def wait_reply(self, since: float, settle: float = 2.0, timeout: float = 45) -> None:
        """Wait for Pip to speak after ``since`` and go back to listening for ``settle`` seconds."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            spoke = any(when >= since and state == "speaking" for when, state in self.states)
            last = self.states[-1] if self.states else (0.0, "initializing")
            if spoke and last[1] == "listening" and self.now() - last[0] >= settle:
                return
            await asyncio.sleep(0.2)

    def replies_since(self, t: float) -> list[str]:
        return [text for when, text in self.agent_lines if when >= t]


CHILD = "You are a shy 6-year-old boy. Speak softly and a little slowly."
READING = "You are a 6-year-old boy reading aloud slowly and carefully, word by word, a bit quietly."
ADULTS = "An adult chatting casually in a kitchen with another adult."


async def main() -> int:
    probe = Probe()
    await probe.connect(page_context("paris", 1))
    results: dict[str, bool] = {}

    await probe.wait_reply(0, timeout=60)
    results["greeting spoken"] = bool(probe.agent_lines)

    first, _, second = load_books()["paris"].pages[1].text.partition(". ")
    t = probe.now()
    await probe.say(first + ".", "ash", READING)
    await asyncio.sleep(2.5)
    await probe.say(second, "ash", READING)
    await probe.wait_reply(t, settle=4)
    results["slow reading -> listened, then praised once"] = len(probe.replies_since(t)) == 1

    t = probe.now()
    await probe.say("Honey, did you remember to call the plumber about the sink?", "onyx", ADULTS)
    await asyncio.sleep(0.6)
    await probe.say("Not yet, I'll do it after dinner.", "shimmer", ADULTS)
    await asyncio.sleep(8)
    results["adult chatter -> ignored"] = not probe.replies_since(t)

    t = probe.now()
    await probe.say("Pip, why do people in Paris say bonjour?", "ash", CHILD)
    await probe.wait_reply(t)
    results["question -> answered"] = bool(probe.replies_since(t))

    await probe.set_page(page_context("paris", 5))
    await asyncio.sleep(1)
    t = probe.now()
    await probe.say("What color is the paint?", "ash", CHILD)
    await probe.wait_reply(t)
    replies = " ".join(probe.replies_since(t)).lower()
    results["page turn -> answers from the new page's hidden notes"] = "brown" in replies

    await probe.room.disconnect()
    print("\nRESULTS")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"  agent states seen: {sorted({s for _, s in probe.states})}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
