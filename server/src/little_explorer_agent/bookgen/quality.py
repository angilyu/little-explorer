"""How good a candidate photo looks, before one is chosen for a made-book page.

Two steps:

1. A free check with Pillow on a 512 px copy (:func:`measure`): sharpness (the Laplacian's variance in the
   sharpest quarter of the picture, so a sharp subject on a soft background still counts), exposure (very dark,
   washed out, or a lot of pure black or white), contrast, colour and pixel width. Clearly blurry or unreadable
   photos are dropped; the rest get a 0-1 score that helps decide which ones the judge sees.
2. A vision judge (:func:`judge`): the best few go to ``config.judge_model`` as low-detail thumbnails in one
   call per page, which scores each 1-10 for a children's picture book and flags clutter, watermarks, text,
   collages, screenshots and people as the subject. A judge that fails is skipped (the free scores decide);
   it never fails a book.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from .cost import CostLog

log = logging.getLogger(__name__)

SIZE = 512
JUDGE_SIZE = 512
JUDGE_BATCH = 4
# On the judge's score less half a point per listed problem (at most two): a page takes the first photo at or above
# GOOD_SCORE, looks further (the other candidates and the alternate queries) when none is, and then settles for the
# best one at or above MIN_SCORE.
GOOD_SCORE = 7.0
MIN_SCORE = 3.0
# A photo with one of these as its main trouble isn't used, whatever its score.
BLOCKING = ("watermark", "text_overlay", "collage", "screenshot", "people_main_subject", "not_a_photo")
PROBLEMS = ("blurry", "dark", "washed_out", "cluttered", "subject_small", "off_topic", *BLOCKING)
SHARP_REJECT = 150.0
SHARP_GOOD = 450.0


@dataclass
class Quality:
    sharpness: float
    mean: float
    contrast: float
    dark: float
    bright: float
    saturation: float
    width: int
    score: float = 1.0
    problems: list[str] = field(default_factory=list)
    reject: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"sharpness": round(self.sharpness), "mean": round(self.mean), "contrast": round(self.contrast, 1),
                "dark": round(self.dark, 3), "bright": round(self.bright, 3), "saturation": round(self.saturation),
                "width": self.width, "score": round(self.score, 3), "problems": self.problems, "reject": self.reject}


_LAPLACE = None


def _laplace() -> Any:
    global _LAPLACE
    if _LAPLACE is None:
        from PIL import ImageFilter

        _LAPLACE = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], 1, 128)
    return _LAPLACE


def measure(data: bytes) -> Quality:
    """The free check (see the module's doc). ``data`` is the photo as stored (a JPEG up to 1400 px wide)."""
    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(data)) as image:
        width = image.width
        rgb = image.convert("RGB")
    small = rgb.resize((SIZE, max(1, round(rgb.height * SIZE / rgb.width))))
    gray = small.convert("L")
    edges = gray.filter(_laplace())
    w, h = edges.size
    tiles = sorted(ImageStat.Stat(edges.crop((i * w // 4, j * h // 4, (i + 1) * w // 4, (j + 1) * h // 4))).var[0]
                   for i in range(4) for j in range(4))
    sharpness = tiles[-4]  # the sharpest quarter of the picture
    stats = ImageStat.Stat(gray)
    hist = gray.histogram()
    total = sum(hist) or 1
    quality = Quality(
        sharpness=sharpness, mean=stats.mean[0], contrast=stats.stddev[0], dark=sum(hist[:8]) / total,
        bright=sum(hist[248:]) / total, saturation=ImageStat.Stat(small.convert("HSV")).mean[1], width=width,
    )
    _score(quality)
    return quality


def _score(q: Quality) -> None:
    def penalty(amount: float, problem: str) -> None:
        q.score -= amount
        q.problems.append(problem)

    if q.sharpness < SHARP_REJECT:
        q.reject = f"blurry (sharpness {q.sharpness:.0f})"
    elif q.mean < 25 or q.mean > 235 or q.contrast < 15:
        q.reject = f"unreadable exposure (mean {q.mean:.0f}, contrast {q.contrast:.0f})"
    if q.sharpness < SHARP_GOOD:
        penalty(0.35 * (SHARP_GOOD - max(q.sharpness, SHARP_REJECT)) / (SHARP_GOOD - SHARP_REJECT), "soft")
    if q.mean < 60 or q.dark > 0.3:
        penalty(0.2, "dark")
    if q.mean > 200 or q.bright > 0.2:
        penalty(0.2, "washed_out")
    if q.contrast < 32:
        penalty(0.15, "flat")
    if q.saturation < 35:
        penalty(0.1, "dull")
    if q.width < 1200:
        penalty(0.05, "small")
    q.score = max(0.0, round(q.score, 3))


# -- the vision judge --------------------------------------------------------------------------------

JUDGE_INSTRUCTIONS = f"""You are a demanding photo editor choosing photos for a children's picture book (ages 4-9). Each page
shows one big photo next to one short sentence. Compare the photos with each other, then score each from 1 to 10:
- 9-10: excellent: sharp, well lit, vivid; the thing the sentence names is the clear hero of the picture, large
  and instantly obvious to a small child, with a simple background. Looks like a page from a beautiful book.
- 7-8: good, with one small flaw.
- 5-6: usable but ordinary: dull or flat light, the subject small, partly hidden or sharing the frame with
  buildings, cars, signs or crowds, or an awkward crop.
- 3-4: poor: the subject is faint, tiny or hard to find, the scene is cluttered, or the photo looks like a snapshot.
- 1-2: wrong subject or unusable.
Most ordinary snapshots are 5-6; give 9-10 only to photos you would proudly print. For each photo first name its
main flaws in a few words (or "none"), then score it, then list its problems
from: {", ".join(PROBLEMS)}. "people_main_subject" means a person or crowd is what the photo is about (people small
in a scene are fine)."""

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["photos"],
    "properties": {"photos": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["photo", "flaws", "score", "problems"],
        "properties": {
            "photo": {"type": "integer"},
            # Naming the flaws before scoring makes the small model's scores stricter and steadier.
            "flaws": {"type": "string"},
            "score": {"type": "integer"},
            "problems": {"type": "array", "items": {"type": "string", "enum": list(PROBLEMS)}},
        },
    }}},
}


@dataclass
class Verdict:
    score: int
    problems: list[str]
    flaws: str = ""

    @property
    def blocked(self) -> bool:
        return any(p in BLOCKING for p in self.problems)

    @property
    def effective(self) -> float:
        """The score less half a point for each problem the judge listed (clutter, a small subject, ...), at most 1."""
        return self.score - 0.5 * min(2, len([p for p in self.problems if p not in BLOCKING]))


def thumbnail(data: bytes, size: int = JUDGE_SIZE) -> str:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        image.thumbnail((size, size))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


async def judge(client: Any, model: str, sentence: str, photos: list[bytes], costs: CostLog) -> list[Verdict] | None:
    """One call scoring ``photos`` for the page's ``sentence``; None if it couldn't be done (fail open)."""
    if not photos:
        return []
    try:
        thumbs = await asyncio.to_thread(lambda: [thumbnail(p) for p in photos])
        content: list[dict[str, Any]] = [{"type": "input_text", "text": f"Page sentence: {sentence}"}]
        for index, url in enumerate(thumbs):
            content += [{"type": "input_text", "text": f"Photo {index}:"},
                        {"type": "input_image", "image_url": url, "detail": "low"}]
        result = await client.structured(
            model=model, instructions=JUDGE_INSTRUCTIONS, input=[{"role": "user", "content": content}],
            schema_name="photo_judge", schema=JUDGE_SCHEMA, max_output_tokens=800, reasoning="none",
        )
        costs.tokens("photo_judge", model, result.input_tokens, result.output_tokens)
        verdicts: dict[int, Verdict] = {}
        for item in result.data.get("photos") or []:
            index, score = int(item.get("photo", -1)), int(item.get("score", 0))
            if 0 <= index < len(photos) and index not in verdicts:
                verdicts[index] = Verdict(max(1, min(10, score)), [str(p) for p in item.get("problems") or []],
                                          str(item.get("flaws") or "")[:200])
        if len(verdicts) != len(photos):
            raise ValueError(f"judged {len(verdicts)} of {len(photos)} photos")
        return [verdicts[i] for i in range(len(photos))]
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the free scores decide instead
        log.warning("photo judge failed (%s: %s); choosing without it", type(exc).__name__, str(exc)[:200])
        return None


@dataclass
class Pick:
    """A downloaded candidate with its free check and (once judged) the judge's verdict."""

    candidate: Any
    photo: Any
    measured: Quality
    verdict: Verdict | None = None
    # Its line in the page's choice log (see Runner._find_photo).
    entry: dict[str, Any] = field(default_factory=dict)

    def rank(self) -> float:
        """The judge's score decides (Commons quality images get half a point); before or without the judge, the
        search score plus the free check's."""
        base = self.candidate.score + self.measured.score
        if self.verdict is None:
            return base
        return self.verdict.effective + (0.5 if self.candidate.quality else 0.0) + 0.1 * base

    def describe(self) -> str:
        judged = f"judge {self.verdict.score}{' ' + ','.join(self.verdict.problems) if self.verdict.problems else ''}, " \
            if self.verdict else ""
        return f"{judged}free {self.measured.score:.2f}, search {self.candidate.score:.2f}"

    def report(self, round_: int) -> dict[str, Any]:
        return {
            "title": self.photo.title, "source": self.photo.source_name, "license": self.photo.license,
            "url": self.photo.source_url, "round": round_, "search_score": self.candidate.score,
            "quality_image": self.candidate.quality, "free": self.measured.as_json(),
            "judge": {"score": self.verdict.score, "effective": self.verdict.effective, "problems": self.verdict.problems,
                      "flaws": self.verdict.flaws} if self.verdict else None,
        }
