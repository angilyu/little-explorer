"""How good a candidate photo looks, before one is chosen for a made-book page.

Two steps:

1. A free check with Pillow on a 512 px copy (:func:`measure`): sharpness (the Laplacian's variance in the
   sharpest quarter of the picture, so a sharp subject on a soft background still counts), exposure (very dark,
   washed out, or a lot of pure black or white), contrast, colour and pixel width. Clearly blurry or unreadable
   photos are dropped; the rest get a 0-1 score that helps decide which ones the judge sees.
2. A vision judge (:func:`judge`): the best :data:`JUDGE_BATCH` go to ``config.judge_model`` as low-detail
   thumbnails in one call per page. It judges them like a photo editor of a premium picture book or travel
   magazine: the subject must match the page's sentence (a gate), and among the photos that match, the most
   beautiful wins (composition, light, colour, mood, technical polish, a professional rather than snapshot feel).
   Watermarks, text, collages, screenshots, non-photos and people as the subject rule a photo out. A judge that
   fails is skipped (the free scores decide); it never fails a book.
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
# A page's candidate pool: up to POOL are downloaded and checked for free; the best JUDGE_BATCH (both sources) are
# judged in one call.
POOL = 12
JUDGE_BATCH = 8
# On the judge's score less half a point per listed flaw (at most two), plus a point for Commons' own quality,
# featured or valued images: a page takes the best photo at or above GOOD_SCORE, looks further (the other candidates
# and the alternate queries) when none is, and then settles for the best one at or above MIN_SCORE.
GOOD_SCORE = 7.0
MIN_SCORE = 3.0
COMMONS_QUALITY_BOOST = 1.0
PRE_QUALITY_BOOST = 0.3
# A photo with one of these isn't used, whatever its score.
BLOCKING = ("off_topic", "watermark", "text_overlay", "collage", "screenshot", "people_main_subject", "not_a_photo")
FLAWS = ("tourist_snapshot", "cluttered", "cars_signs_wires", "crowd", "tilted_horizon", "flat_light", "harsh_light",
         "dull_colour", "documentary", "subject_small", "blurry", "noisy", "blown_highlights", "dark", "awkward_crop")
PROBLEMS = (*FLAWS, *BLOCKING)
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
    # Hasler and Suesstrunk's colourfulness (about 0 grey, 30 moderate, 60+ vivid).
    colourfulness: float = 0.0
    score: float = 1.0
    problems: list[str] = field(default_factory=list)
    reject: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"sharpness": round(self.sharpness), "mean": round(self.mean), "contrast": round(self.contrast, 1),
                "dark": round(self.dark, 3), "bright": round(self.bright, 3), "saturation": round(self.saturation),
                "colourfulness": round(self.colourfulness),
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
    r, g, b = (ImageStat.Stat(c) for c in _opponent(small))
    quality = Quality(
        sharpness=sharpness, mean=stats.mean[0], contrast=stats.stddev[0], dark=sum(hist[:8]) / total,
        bright=sum(hist[248:]) / total, saturation=ImageStat.Stat(small.convert("HSV")).mean[1], width=width,
        colourfulness=(r.stddev[0] ** 2 + g.stddev[0] ** 2) ** 0.5 + 0.3 * (r.mean[0] ** 2 + g.mean[0] ** 2) ** 0.5,
    )
    _score(quality)
    return quality


def _opponent(image: Any) -> tuple[Any, Any, Any]:
    """The opponent colour channels rg = |R - G| and yb = |(R + G) / 2 - B| (and a placeholder)."""
    from PIL import ImageChops

    red, green, blue = image.split()
    rg = ImageChops.difference(red, green)
    yb = ImageChops.difference(ImageChops.add(red, green, scale=2.0), blue)
    return rg, yb, blue


def appeal(q: Quality) -> float:
    """A small free hint of how striking a photo is (0 to 0.3), from its colour and contrast; the judge decides."""
    return round(0.2 * min(1.0, q.colourfulness / 60) + 0.1 * min(1.0, q.contrast / 60), 3)


def shortlist(picks: list[Any], size: int = JUDGE_BATCH, each: int = 3) -> list[Any]:
    """The ``size`` picks the judge should see: the best by :meth:`Pick.prerank`, with at least ``each`` from every
    source that has that many (so Commons and Openverse are compared, without dropping good ones to even them out)."""
    ranked = sorted((p for p in picks if not p.measured.reject), key=lambda p: p.prerank(), reverse=True)
    chosen: list[Any] = []
    for source in dict.fromkeys(p.candidate.source for p in ranked):
        chosen += [p for p in ranked if p.candidate.source == source][:each]
    chosen = chosen[:size]
    chosen += [p for p in ranked if p not in chosen][:size - len(chosen)]
    return sorted(chosen, key=lambda p: p.prerank(), reverse=True)


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

JUDGE_INSTRUCTIONS = f"""You are the photo editor of a premium children's picture book and travel magazine. Each page shows
one big photo next to one short sentence. You choose the most BEAUTIFUL photo for the page.

1. Gate: does the photo clearly show what the sentence names (or, when the sentence names nothing, like "Goodbye!",
   what the photo was searched for)? If not, set "matches" to false (score it anyway).
2. Score each photo 1-10 on artistic quality alone, comparing the photos with each other:
   - composition: a strong single subject, clean background, rule of thirds, leading lines, a straight horizon
   - light: golden hour, soft or dramatic light; not harsh flat midday glare, not dull overcast
   - colour: vivid but natural, a harmonious palette
   - mood and wow factor
   - technical polish: sharp where it matters, good depth of field, no noise or blown highlights
   - a professional feel, not a snapshot
   Anchors: 9-10 award-winning or postcard quality; 7-8 polished, would look good in a premium book;
   5-6 an ordinary snapshot; 4 or less poor. Be strict: most photos are 5-6. Score tourist snapshots, clutter,
   cars, signs, crowds, wires, a tilted horizon, dull or flat light, low colour, and record or documentary shots
   (inventory, aerial survey, street sign, construction) 5 or less.
3. Write a one-line critique (what makes it beautiful or not), then list its problems from:
   {", ".join(PROBLEMS)}.
   "people_main_subject": a person or crowd is what the photo is about (small people in a scene are fine);
   "off_topic": it doesn't show what the sentence names."""

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["photos"],
    "properties": {"photos": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["photo", "critique", "matches", "score", "problems"],
        "properties": {
            "photo": {"type": "integer"},
            # The critique comes before the score: it makes the scores stricter and steadier.
            "critique": {"type": "string"},
            "matches": {"type": "boolean"},
            "score": {"type": "integer"},
            "problems": {"type": "array", "items": {"type": "string", "enum": list(PROBLEMS)}},
        },
    }}},
}


@dataclass
class Verdict:
    score: int
    problems: list[str]
    critique: str = ""
    matches: bool = True

    @property
    def blocked(self) -> bool:
        return not self.matches or any(p in BLOCKING for p in self.problems)

    @property
    def effective(self) -> float:
        """The aesthetic score less half a point for each flaw the judge listed (clutter, flat light, ...), at most 1."""
        return self.score - 0.5 * min(2, len([p for p in self.problems if p not in BLOCKING]))


def thumbnail(data: bytes, size: int = JUDGE_SIZE) -> str:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        image.thumbnail((size, size))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


async def judge(client: Any, model: str, sentence: str, photos: list[bytes], costs: CostLog,
                searches: list[str] | None = None) -> list[Verdict] | None:
    """One call scoring ``photos`` for the page's ``sentence`` (``searches``: what each was searched for); None if
    it couldn't be done (fail open)."""
    if not photos:
        return []
    try:
        thumbs = await asyncio.to_thread(lambda: [thumbnail(p) for p in photos])
        content: list[dict[str, Any]] = [{"type": "input_text", "text": f"Page sentence: {sentence}"}]
        for index, url in enumerate(thumbs):
            searched = f" (searched for: {searches[index]})" if searches and index < len(searches) else ""
            content += [{"type": "input_text", "text": f"Photo {index}{searched}:"},
                        {"type": "input_image", "image_url": url, "detail": "low"}]
        result = await client.structured(
            model=model, instructions=JUDGE_INSTRUCTIONS, input=[{"role": "user", "content": content}],
            schema_name="photo_judge", schema=JUDGE_SCHEMA, max_output_tokens=300 + 120 * len(photos),
            reasoning="none",
        )
        costs.tokens("photo_judge", model, result.input_tokens, result.output_tokens)
        verdicts: dict[int, Verdict] = {}
        for item in result.data.get("photos") or []:
            index, score = int(item.get("photo", -1)), int(item.get("score", 0))
            if 0 <= index < len(photos) and index not in verdicts:
                verdicts[index] = Verdict(max(1, min(10, score)), [str(p) for p in item.get("problems") or []],
                                          str(item.get("critique") or "")[:200], item.get("matches") is not False)
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

    def aesthetic(self) -> float:
        """The judge's score less its listed flaws, plus the Commons quality-image point (0 if not judged)."""
        if self.verdict is None:
            return 0.0
        return self.verdict.effective + (COMMONS_QUALITY_BOOST if self.candidate.quality else 0.0)

    def rank(self) -> float:
        """The judge's aesthetic score decides (Commons quality images get a point more); before or without the
        judge, the search score plus the free check's."""
        if self.verdict is None:
            return self.prerank()
        return self.aesthetic() + 0.1 * self.prerank()

    def prerank(self) -> float:
        """Before the judge: the search score, the free check and its appeal hint, and a boost for Commons' own
        quality, featured or valued images."""
        return self.candidate.score + self.measured.score + appeal(self.measured) + \
            (PRE_QUALITY_BOOST if self.candidate.quality else 0.0)

    def describe(self) -> str:
        judged = f"judge {self.verdict.score}{' ' + ','.join(self.verdict.problems) if self.verdict.problems else ''}, " \
            if self.verdict else ""
        return f"{judged}free {self.measured.score:.2f}, search {self.candidate.score:.2f}"

    def report(self, round_: int) -> dict[str, Any]:
        return {
            "title": self.photo.title, "source": self.photo.source_name, "license": self.photo.license,
            "url": self.photo.source_url, "round": round_, "search_score": self.candidate.score,
            "quality_image": self.candidate.quality, "free": self.measured.as_json(),
            "judge": {"score": self.verdict.score, "effective": self.verdict.effective, "aesthetic": self.aesthetic(), "matches": self.verdict.matches,
                      "problems": self.verdict.problems, "critique": self.verdict.critique} if self.verdict else None,
        }
