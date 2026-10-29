"""The Passport badge catalog (``LittleExplorer/Resources/badges.json``).

The app ships a copy and downloads the server's with the books (it is in the content manifest, see
:mod:`.catalog`), so badge names, hints, goals, categories, icons and order change without an app
update. Each badge counts one *metric* the app works out on the device from what it already keeps
(stars, and the signed-in account's reading progress from ``GET /account/reading``); a new kind of
metric needs a new app build, so the app skips badges whose metric it doesn't know.

Format 1::

    {"format": 1,
     "categories": [{"id", "title", "icon", "tint"}, ...],          # shown in this order
     "badges": [{"id", "category", "title", "icon", "tint", "metric", "goal", "hint", "done",
                 "each": "city" (optional)}, ...]}                    # in this order in each category

``icon`` is an SF Symbol name and ``tint`` a ``#RRGGBB`` color. ``goal`` is a whole number, or
``"all"`` for "every one" (every book of a city, say). ``{goal}`` in ``title``, ``hint`` and ``done``
is replaced by the goal. A badge with ``"each": "city"`` (only for per-city metrics) is repeated for
every city with several books on the account's shelf; ``{city}`` in its id and text is the city.

An id is how the app remembers a badge he earned (earned badges stay earned when a goal changes), so
never reuse an old id for a different badge. :func:`validate` refuses a catalog the app couldn't
show well, so a bad edit fails the tests and the manifest instead of reaching the app.
"""

from __future__ import annotations

import re
from typing import Any

FILE = "badges.json"
FORMAT = 1

#: What each metric counts. Keep in step with ``BadgeMetric`` in ``LittleExplorer/Stores/Badges.swift``.
METRICS: dict[str, str] = {
    "stars": "stars earned from Milo",
    "star_answers": "answers and questions that earned a star",
    "long_answers": "long answers or questions (8+ words) that earned a star",
    "because_answers": "answers that explained with \u201cbecause\u201d",
    "stars_in_a_day": "most stars on one day",
    "star_days": "different days with a star",
    "morning_stars": "stars before 9 am",
    "bedtime_stars": "stars at 7 pm or later",
    "books_starred": "different books with a star (goal capped at the books on the shelf)",
    "cities_starred": "Passport stamps: places with a star (capped at the places on the shelf)",
    "city_books_starred": "books of one city with a star (per city, with \"each\": \"city\")",
    "pages_read": "different pages read (shown 1+ second)",
    "pages_listened": "different pages read aloud to the end",
    "milo_pages": "different pages read with Milo on",
    "books_finished": "books with every page read (capped at the books on the shelf)",
    "reading_days": "different days with a page read",
    "reading_streak": "most days in a row with a page read",
    "cities_read": "places (not the Travel shelf) with a page read (capped at the places on the shelf)",
    "countries_read": "countries with a page read (capped at the countries on the shelf)",
}
PER_CITY = frozenset({"city_books_starred"})
# Metrics with a most-there-can-be (from the shelves): only these can have the goal "all".
CAPPED = PER_CITY | frozenset({"books_starred", "cities_starred", "books_finished", "cities_read", "countries_read"})
EACH = ("city",)
MAX_TITLE = 20
MAX_TEXT = 44
ID = re.compile(r"^[a-z0-9-]+(\{city\})?[a-z0-9-]*$")
TINT = re.compile(r"^#[0-9A-Fa-f]{6}$")
ICON = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")


class BadgeError(ValueError):
    pass


def _text(entry: dict[str, Any], key: str, where: str, limit: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BadgeError(f"{where}: '{key}' must be a non-empty string")
    shown = value.replace("{goal}", "000").replace("{city}", "Barcelona")
    if len(shown) > limit:
        raise BadgeError(f"{where}: '{key}' is too long for a young reader ({len(shown)} > {limit} letters)")
    return value


def _look(entry: dict[str, Any], where: str) -> None:
    if not isinstance(entry.get("icon"), str) or not ICON.match(entry["icon"]):
        raise BadgeError(f"{where}: 'icon' must be an SF Symbol name like 'star.fill'")
    if not isinstance(entry.get("tint"), str) or not TINT.match(entry["tint"]):
        raise BadgeError(f"{where}: 'tint' must be a color like '#2E9E44'")


def validate(data: object) -> dict[str, Any]:
    """Checks a parsed ``badges.json``; returns it, or raises :class:`BadgeError` saying what's wrong."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BadgeError(f"{FILE} needs \"format\": {FORMAT}")
    categories, badges = data.get("categories"), data.get("badges")
    if not isinstance(categories, list) or not categories or not isinstance(badges, list) or not badges:
        raise BadgeError(f"{FILE} needs non-empty 'categories' and 'badges' lists")
    category_ids: set[str] = set()
    for i, category in enumerate(categories):
        where = f"category {i}"
        if not isinstance(category, dict) or not isinstance(category.get("id"), str) or not category["id"]:
            raise BadgeError(f"{where}: needs an 'id'")
        where = f"category {category['id']}"
        if category["id"] in category_ids:
            raise BadgeError(f"{where}: duplicate id")
        category_ids.add(category["id"])
        _text(category, "title", where, MAX_TITLE)
        _look(category, where)
    badge_ids: set[str] = set()
    for i, badge in enumerate(badges):
        if not isinstance(badge, dict) or not isinstance(badge.get("id"), str) or not ID.match(badge["id"]):
            raise BadgeError(f"badge {i}: needs an 'id' of lowercase letters, digits and dashes")
        where = f"badge {badge['id']}"
        if badge["id"] in badge_ids:
            raise BadgeError(f"{where}: duplicate id")
        badge_ids.add(badge["id"])
        if badge.get("category") not in category_ids:
            raise BadgeError(f"{where}: unknown category {badge.get('category')!r}")
        metric = badge.get("metric")
        if metric not in METRICS:
            raise BadgeError(f"{where}: unknown metric {metric!r} (the app knows {sorted(METRICS)})")
        goal = badge.get("goal")
        if goal != "all" and (isinstance(goal, bool) or not isinstance(goal, int) or not 1 <= goal <= 10_000):
            raise BadgeError(f"{where}: 'goal' must be a whole number from 1 to 10000, or \"all\"")
        if goal == "all" and metric not in CAPPED:
            raise BadgeError(f"{where}: 'goal' \"all\" only works for {sorted(CAPPED)}")
        each = badge.get("each")
        if each is not None and each not in EACH:
            raise BadgeError(f"{where}: 'each' can only be {EACH}")
        if (each == "city") != (metric in PER_CITY):
            raise BadgeError(f"{where}: per-city metrics {sorted(PER_CITY)} need \"each\": \"city\", others can't have it")
        if (each == "city") != ("{city}" in badge["id"]):
            raise BadgeError(f"{where}: a per-city badge's id needs {{city}} (and only those)")
        _text(badge, "title", where, MAX_TITLE)
        _text(badge, "hint", where, MAX_TEXT)
        _text(badge, "done", where, MAX_TEXT)
        _look(badge, where)
    return data
