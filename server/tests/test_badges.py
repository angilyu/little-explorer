"""The Passport badge catalog (``badges.json``): the bundled one is valid, and edits the app couldn't
show well are refused before they reach it (the manifest won't build)."""

import copy
import json

import pytest

from little_explorer_agent import badges
from little_explorer_agent.catalog import RESOURCES_DIR, CatalogError, build_manifest

BUNDLED = json.loads((RESOURCES_DIR / badges.FILE).read_text(encoding="utf-8"))


def test_bundled_catalog_is_valid_and_uses_known_metrics():
    data = badges.validate(BUNDLED)
    ids = [b["id"] for b in data["badges"]]
    # Ids the app already remembers as earned must stay.
    for old in ("curious-cat", "big-talker", "why-wizard", "star-shower", "come-back-kid", "early-bird",
                "night-owl", "bookworm", "globe-trotter", "super-star", "expert-{city}"):
        assert old in ids
    assert {b["metric"] for b in data["badges"]} <= set(badges.METRICS)
    assert {b["category"] for b in data["badges"]} == {c["id"] for c in data["categories"]}


def edit(change):
    data = copy.deepcopy(BUNDLED)
    change(data)
    return data


def badge(data, badge_id):
    return next(b for b in data["badges"] if b["id"] == badge_id)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d.update(format=2), "format"),
        (lambda d: d.update(badges=[]), "non-empty"),
        (lambda d: d["badges"].append(dict(d["badges"][0])), "duplicate id"),
        (lambda d: d["categories"].append(dict(d["categories"][0])), "duplicate id"),
        (lambda d: badge(d, "first-book").update(metric="hearts_given"), "unknown metric"),
        (lambda d: badge(d, "first-book").update(category="sports"), "unknown category"),
        (lambda d: badge(d, "first-book").update(goal=0), "goal"),
        (lambda d: badge(d, "first-book").update(goal="10"), "goal"),
        (lambda d: badge(d, "first-book").update(goal=True), "goal"),
        (lambda d: badge(d, "page-turner").update(goal="all"), "only works for"),
        (lambda d: badge(d, "first-book").update(id="First Book"), "id"),
        (lambda d: badge(d, "first-book").update(title=""), "title"),
        (lambda d: badge(d, "first-book").update(title="The Most Amazing Reader Ever"), "too long"),
        (lambda d: badge(d, "first-book").update(hint="Read one whole book from the first page to the very end"), "too long"),
        (lambda d: badge(d, "first-book").update(icon="Book Closed!"), "icon"),
        (lambda d: badge(d, "first-book").update(tint="green"), "tint"),
        (lambda d: badge(d, "first-book").update(each="country"), "each"),
        (lambda d: badge(d, "first-book").update(each="city"), "per-city"),
        (lambda d: badge(d, "expert-{city}").pop("each"), "per-city"),
        (lambda d: badge(d, "expert-{city}").update(id="expert"), "per-city"),
    ],
)
def test_rejects_catalogs_the_app_could_not_show(change, message):
    with pytest.raises(badges.BadgeError, match=message):
        badges.validate(edit(change))


def test_new_badges_on_known_metrics_and_new_goals_are_fine():
    def change(d):
        d["categories"].append({"id": "big", "title": "Big Goals", "icon": "trophy.fill", "tint": "#123ABC"})
        d["badges"].append({"id": "mega-reader", "category": "big", "title": "Mega Reader", "icon": "trophy.fill",
                            "tint": "#123ABC", "metric": "pages_read", "goal": 500, "hint": "Read {goal} pages",
                            "done": "{goal} pages!"})
        badge(d, "page-turner")["goal"] = 30

    badges.validate(edit(change))


def test_a_bad_catalog_stops_the_manifest(tmp_path):
    (tmp_path / "Books").mkdir()
    (tmp_path / "library.json").write_text(json.dumps({"order": ["paris"]}))
    (tmp_path / "Books" / "paris.json").write_text(json.dumps({"id": "paris"}))
    (tmp_path / "badges.json").write_text(json.dumps(BUNDLED))
    assert "badges.json" in build_manifest(tmp_path)["files"]
    (tmp_path / "badges.json").write_text(json.dumps(edit(lambda d: badge(d, "first-book").update(metric="x"))))
    with pytest.raises(CatalogError, match="unknown metric"):
        build_manifest(tmp_path)
