"""End-to-end smoke test against a real LiveKit room.

Joins like the iOS app (token from the local token server carrying the opening page, page turns
as JSON on the ``context`` data topic),
then plays synthesized speech into the microphone track and checks how Milo reacts:

1. the greeting is spoken;
2. reading the page aloud slowly, in two pieces, is listened to and then praised once;
3. adults talking to each other get no answer;
4. a question about the page gets an answer;
5. after a page turn, a question about the new page is answered from that page;
6. talking over Milo makes him stop.

It also measures, from Milo's audio track: reply latency (end of the child's speech -> Milo's first
audible sound) and barge-in stop time (child starts talking over Milo -> Milo goes quiet).

Needs the agent worker and token server running, plus OPENAI_API_KEY for the test voices.
``TOKEN_URL`` points it at another token server; ``PIP_ACCESS_KEY`` is sent if set.
It talks as device ``$E2E_DEVICE_ID`` (``e2e-smoke``); approve it once with
``tools/devices.py approve e2e-smoke`` so the unknown-device limit doesn't block it.
Run from server/: ``uv run python tools/e2e_smoke.py [--mode pipeline|realtime] [--trials N]``.
``--mode`` asks the token server for that engine, so one running agent can be compared in both.
No audio is saved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from livekit import rtc
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from little_explorer_agent.config import load_env  # noqa: E402
from little_explorer_agent.content import load_books  # noqa: E402

SAMPLE_RATE = 24000
FRAME = SAMPLE_RATE // 100  # 10 ms
LOUD_RMS = 300  # int16 RMS above this counts as Milo making sound
QUIET_GAP = 0.7  # Milo has stopped when he stays quiet this long

load_env()
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
        self.loud: list[float] = []  # times of audible 10 ms frames from Milo
        self.last_said: float = 0.0

    def now(self) -> float:
        return time.monotonic() - self.t0

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def connect(self, context: dict[str, str], mode: str | None = None) -> None:
        device_id = os.environ.get("E2E_DEVICE_ID", "e2e-smoke")
        payload: dict[str, object] = {"context": context, "device": {"id": device_id, "name": "e2e smoke test"}}
        if mode:
            payload["pip_mode"] = mode
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if key := os.environ.get("PIP_ACCESS_KEY", "").strip():
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(TOKEN_URL, data=body, headers=headers, method="POST")
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
                print(f"[{self.now():5.1f}]   MILO: {text}")

        self.room.register_text_stream_handler("lk.transcription", lambda r, ident: self._spawn(_read(r, ident)))

        @self.room.on("track_subscribed")
        def _track(track: rtc.Track, _pub: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                self._spawn(self._listen(track))
        await self.room.connect(creds["serverUrl"], creds["participantToken"])
        track = rtc.LocalAudioTrack.create_audio_track("mic", self.source)
        await self.room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        self._spawn(self._pump())

    async def _listen(self, track: rtc.Track) -> None:
        async for event in rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1):
            samples = np.frombuffer(event.frame.data, dtype=np.int16).astype(np.float32)
            if len(samples) and float(np.sqrt(np.mean(samples * samples))) > LOUD_RMS:
                self.loud.append(self.now())

    def first_sound_after(self, t: float) -> float | None:
        return next((when for when in self.loud if when > t), None)

    def quiet_after(self, t: float) -> float | None:
        """When Milo went quiet (for ``QUIET_GAP``) after ``t``; None if he never did."""
        frames = [when for when in self.loud if when >= t]
        previous = t
        for when in frames:
            if when - previous >= QUIET_GAP:
                return previous
            previous = when
        return previous if self.now() - previous >= QUIET_GAP else None

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

    async def synth(self, text: str, voice: str, style: str) -> np.ndarray:
        resp = await self.openai.audio.speech.create(
            model="gpt-4o-mini-tts", voice=voice, input=text, instructions=style, response_format="pcm"
        )
        return np.frombuffer(resp.content, dtype=np.int16)

    async def say(self, text: str, voice: str, style: str, pcm: np.ndarray | None = None) -> None:
        if pcm is None:
            pcm = await self.synth(text, voice, style)
        print(f"[{self.now():5.1f}] >> ({voice}) {text}")
        await self.queue.put(pcm)
        await asyncio.sleep(len(pcm) / SAMPLE_RATE)
        self.last_said = self.now()

    async def set_page(self, ctx: dict[str, str]) -> None:
        await self.room.local_participant.publish_data(json.dumps(ctx), reliable=True, topic="context")
        print(f"[{self.now():5.1f}] page -> {ctx['book']} #{ctx['page_number']}: {ctx['page_text']}")

    async def wait_reply(self, since: float, settle: float = 2.0, timeout: float = 45) -> None:
        """Wait for Milo to speak after ``since`` and go back to listening for ``settle`` seconds."""
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


LATENCY_QUESTIONS = [
    "Milo, why do people in Paris say bonjour?",
    "Milo, what do kids in Paris eat for breakfast?",
    "Why is the Eiffel Tower so tall?",
    "Milo, can you go up to the top of the tower?",
    "What is the river in Paris called?",
]


def median(values: list[float]) -> str:
    return f"{statistics.median(values):.2f}s" if values else "n/a"


async def main(mode: str | None, trials: int) -> int:
    probe = Probe()
    await probe.connect(page_context("paris", 1), mode)
    results: dict[str, bool] = {}
    latencies: list[float] = []
    stops: list[float] = []

    def note_latency(since: float) -> None:
        heard = probe.first_sound_after(since)
        if heard is not None:
            latencies.append(heard - since)
            print(f"[{probe.now():5.1f}]   latency {heard - since:.2f}s")

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
    await probe.say(LATENCY_QUESTIONS[0], "ash", CHILD)
    said = probe.last_said
    await probe.wait_reply(t)
    note_latency(said)
    results["question -> answered"] = bool(probe.replies_since(t))
    for question in LATENCY_QUESTIONS[1:trials]:
        t = probe.now()
        await probe.say(question, "ash", CHILD)
        said = probe.last_said
        await probe.wait_reply(t)
        note_latency(said)

    await probe.set_page(page_context("paris", 5))
    await asyncio.sleep(1)
    t = probe.now()
    await probe.say("What color is the paint?", "ash", CHILD)
    await probe.wait_reply(t)
    replies = " ".join(probe.replies_since(t)).lower()
    results["page turn -> answers from the new page's hidden notes"] = "brown" in replies

    barge_text = "Wait Milo! Wait! Does the tower have an elevator?"
    barge_pcm = await probe.synth(barge_text, "ash", CHILD)
    for _ in range(max(1, trials // 2)):
        t = probe.now()
        await probe.say("Milo, can you tell me a long story about the Eiffel Tower?", "ash", CHILD)
        # Let Milo talk for about two seconds, then talk over him.
        first = None
        for _ in range(150):
            first = probe.first_sound_after(probe.last_said)
            if first is not None and probe.now() - first >= 2.0:
                break
            await asyncio.sleep(0.1)
        if first is None:
            print("   Milo never started; skipping barge-in trial")
            continue
        barge = probe.now()
        await probe.say(barge_text, "ash", CHILD, barge_pcm)
        quiet = probe.quiet_after(barge)
        if quiet is not None and quiet < probe.last_said:
            stops.append(quiet - barge)
            print(f"[{probe.now():5.1f}]   barge-in stop {quiet - barge:.2f}s")
        else:
            print(f"[{probe.now():5.1f}]   Milo kept talking over the child")
        await probe.wait_reply(t, settle=3)
    results["barge-in -> Milo stops"] = bool(stops)

    await probe.room.disconnect()
    print(f"\nRESULTS ({mode or 'agent default'} mode)")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"  agent states seen: {sorted({s for _, s in probe.states})}")
    print(f"  reply latency (end of speech -> first audio): median {median(latencies)} {[round(x, 2) for x in latencies]}")
    print(f"  barge-in stop time: median {median(stops)} {[round(x, 2) for x in stops]}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["pipeline", "realtime"], help="ask the token server for this engine")
    parser.add_argument("--trials", type=int, default=3, help="latency questions (and half as many barge-ins)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.mode, args.trials)))
