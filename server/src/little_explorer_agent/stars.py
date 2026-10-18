"""Stars for curious questions: the rules that decide when a question earns one.

Milo decides *whether* the child asked a real question for him: the turn guard's relevance check
(the same one that decides whether he answers at all) lets it through and he answers it. Nothing
the model says can hand out a star; :class:`StarKeeper` checks every answered turn against what was
actually heard (the input transcription and the guard's decision) and against the limits:

* the turn was a reply-worthy question (the guard said RESPOND: not reading aloud, not noise or
  grown-ups talking), at least ``min_words`` (3) words, and not small talk like "how are you";
* each heard turn earns at most one award;
* one star per page per session (:data:`STARS_PER_PAGE`): the first star-worthy question on a page
  earns it, later questions on that page earn nothing.

An award is always 1 star. ``long`` (``long_words``, 8+ words) and ``why_how`` are recorded for the
Passport's badges but add nothing.

Awards hold counts and labels only (book, page, kind, flags, word count);
never the transcript. They are sent to the app on the ``milo.star`` data topic and backed up to the
token server (``POST /stars``) so a reinstall can restore them.

Settings: ``PIP_STAR_COOLDOWN_S`` (0: off), ``PIP_STAR_LONG_WORDS``.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .buddy import NAME_WORDS, _words
from .content import BOOK_KEY, CITY_KEY, PAGE_KEY
from .voice import TurnDecision, UserTurn

log = logging.getLogger("little_explorer.stars")

STAR_TOPIC = "milo.star"
# The token server enforces the same limit (devices.py), so no agent build can give more.
STARS_PER_PAGE = 1
STARS_PER_AWARD = 1
MAX_REASON = 80

_QUESTIONISH = frozenset({"why", "what", "how", "where", "who", "when", "which", "wonder", "tell", "explain", "is", "are", "do", "does", "can", "could", "did", "will", "would"})
_WHY_HOW = frozenset({"why", "how"})
_GREETING_WORDS = NAME_WORDS | frozenset({"hi", "hello", "hey", "ok", "okay", "um", "uh", "so"})
_SMALL_TALK = re.compile(
    r"^(how are you( doing| today)?|what('s| is) your name|are you (there|listening|real|a robot)|"
    r"can you hear me|do you hear me|what are you doing|who are you)$"
)


@dataclass(frozen=True)
class StarRules:
    # Off by default: with one star per page, Milo's star line (said on the page's first question)
    # must not be refused because the last star on another page was only seconds ago.
    cooldown_s: float = 0.0
    long_words: int = 8
    min_words: int = 3
    # A transcript older than this cannot earn a star any more.
    fresh_s: float = 20.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StarRules:
        env = os.environ if env is None else env

        def num(name: str, default: float) -> float:
            raw = (env.get(name) or "").strip()
            return float(raw) if raw else default

        return cls(
            cooldown_s=num("PIP_STAR_COOLDOWN_S", cls.cooldown_s),
            long_words=int(num("PIP_STAR_LONG_WORDS", cls.long_words)),
        )


@dataclass
class HeardTurn:
    at: float
    text: str
    decision: TurnDecision
    context: dict[str, Any]
    used: bool = False


@dataclass(frozen=True)
class StarAward:
    id: str
    device_id: str
    room: str
    awarded_at: str
    book: str
    page: str
    city: str
    kind: str
    long: bool
    why_how: bool
    followup: bool
    stars: int
    words: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_small_talk(text: str) -> bool:
    words = [w for w in _words(text) if w not in _GREETING_WORDS]
    return not words or bool(_SMALL_TALK.match(" ".join(words)))


def looks_like_question(text: str) -> bool:
    value = (text or "").strip()
    return value.endswith("?") or not _QUESTIONISH.isdisjoint(_words(value))


@dataclass
class StarKeeper:
    """Validates stars. Feed it every guarded turn (:meth:`heard`) and every time the child starts
    speaking (:meth:`speech_started`); :meth:`judge` returns an award for the newest turn or the
    reason there is none. ``clock`` is monotonic seconds; ``wall`` gives the award's local timestamp."""

    device_id: str = ""
    room: str = ""
    rules: StarRules = field(default_factory=StarRules)
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], datetime] = lambda: datetime.now().astimezone()
    awards: list[StarAward] = field(default_factory=list)
    _turns: list[HeardTurn] = field(default_factory=list)
    _speech_at: float = -1.0
    _last_award_at: float | None = None

    # -- what was heard ---------------------------------------------------------------------------

    def speech_started(self) -> None:
        self._speech_at = self.clock()

    def heard(self, turn: UserTurn, decision: TurnDecision) -> None:
        self._turns.append(HeardTurn(self.clock(), turn.text, decision, dict(turn.context)))
        del self._turns[:-6]

    @property
    def speech_at(self) -> float:
        """When he last started speaking (keeper clock), or -1."""
        return self._speech_at

    def latest(self) -> HeardTurn | None:
        return self._turns[-1] if self._turns else None

    # -- counts ----------------------------------------------------------------------------------

    def page_stars(self, book: str, page: str) -> int:
        return sum(a.stars for a in self.awards if a.book == book and a.page == page)

    def total(self) -> int:
        return sum(a.stars for a in self.awards)

    def stars_left(self, context: Mapping[str, Any]) -> int:
        book, page = str(context.get(BOOK_KEY) or ""), str(context.get(PAGE_KEY) or "")
        return max(STARS_PER_PAGE - self.page_stars(book, page), 0) if book else 0

    # -- claims ----------------------------------------------------------------------------------

    def judge(self, reason: str = "") -> StarAward | str:
        now = self.clock()
        turn = self._turns[-1] if self._turns else None
        if turn is None or turn.used or now - turn.at > self.rules.fresh_s:
            return "no new question heard"
        if turn.decision is not TurnDecision.RESPOND:
            return "that was reading or not meant for Milo"
        words = _words(turn.text)
        if len(words) < self.rules.min_words:
            return "too short to be a question"
        if is_small_talk(turn.text):
            return "small talk"
        if not looks_like_question(turn.text):
            return "not a question"
        book, page = str(turn.context.get(BOOK_KEY) or ""), str(turn.context.get(PAGE_KEY) or "")
        if not book or not page:
            return "no book open"
        if self.page_stars(book, page) >= STARS_PER_PAGE:
            return "this page's star is taken"
        if self._last_award_at is not None and now - self._last_award_at < self.rules.cooldown_s:
            return "too soon after the last star"

        long = len(words) >= self.rules.long_words
        why_how = not _WHY_HOW.isdisjoint(words)
        turn.used = True
        self._last_award_at = now
        award = StarAward(
            id=uuid.uuid4().hex,
            device_id=self.device_id,
            room=self.room,
            awarded_at=self.wall().isoformat(timespec="seconds"),
            book=book,
            page=page,
            city=str(turn.context.get(CITY_KEY) or ""),
            kind="question",
            long=long,
            why_how=why_how,
            followup=False,
            stars=STARS_PER_AWARD,
            words=len(words),
            reason=" ".join((reason or "").split())[:MAX_REASON],
        )
        self.awards.append(award)
        return award

    def status_line(self, context: Mapping[str, Any]) -> str:
        """The stars rule for the model's instructions on the current page."""
        if self.stars_left(context) <= 0:
            return "Stars right now: this page's star is taken. Don't mention stars on this page."
        return "Stars right now: this page's star is still waiting."


async def report_star(award: StarAward, target: tuple[str, str] | None = None) -> bool:
    """Back the award up on the token server (``POST /stars``) so a reinstall can restore it."""
    from .usage import post_report, report_target

    target = report_target(path="stars") if target is None else target
    if target is None:
        return False
    return await post_report(award.to_dict(), target, label=f"star {award.id[:8]}")


def star_payload(award: StarAward, total: int) -> bytes:
    """The ``milo.star`` data message: the award plus the session total (no transcript)."""
    import json

    data = award.to_dict()
    data.pop("room", None)
    data["session_total"] = total
    return json.dumps(data).encode()
