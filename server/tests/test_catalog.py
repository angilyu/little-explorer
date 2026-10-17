import json

import pytest

from little_explorer_agent.catalog import RESOURCES_DIR, CatalogError, build_manifest


def make_root(tmp_path, order=("paris",), books=("paris",)):
    (tmp_path / "Books").mkdir()
    (tmp_path / "Photos").mkdir()
    (tmp_path / "library.json").write_text(json.dumps({"order": list(order)}))
    for b in books:
        (tmp_path / "Books" / f"{b}.json").write_text(json.dumps({"id": b}))
    (tmp_path / "Photos" / "a.jpg").write_bytes(b"jpg")
    return tmp_path


def test_bundled_content_makes_a_valid_manifest():
    manifest = build_manifest(RESOURCES_DIR)
    files = manifest["files"]
    assert manifest["order"] == ["paris", "paris-food", "paris-fun", "paris-notre-dame", "london", "barcelona"]
    assert "library.json" in files and "Narration/narration.json" in files
    assert any(p.startswith("Photos/") for p in files) and any(p.endswith(".m4a") for p in files)
    assert all(len(e["sha256"]) == 64 and e["size"] > 0 for e in files.values())


def test_version_changes_with_any_file(tmp_path):
    root = make_root(tmp_path)
    before = build_manifest(root)["version"]
    assert build_manifest(root)["version"] == before
    (root / "Photos" / "a.jpg").write_bytes(b"new photo")
    assert build_manifest(root)["version"] != before


def test_ignores_hidden_and_unknown_files(tmp_path):
    root = make_root(tmp_path)
    (root / "Photos" / ".DS_Store").write_bytes(b"x")
    (root / "Photos" / "notes.txt").write_text("x")
    assert set(build_manifest(root)["files"]) == {"library.json", "Books/paris.json", "Photos/a.jpg"}


@pytest.mark.parametrize(
    "setup, message",
    [
        (lambda r: (r / "library.json").unlink(), "missing"),
        (lambda r: (r / "library.json").write_text('{"order": []}'), "non-empty"),
        (lambda r: (r / "library.json").write_text('{"order": ["paris", "rome"]}'), "rome"),
        (lambda r: (r / "Photos" / "paris.json").write_text("{}"), "unique"),
    ],
)
def test_rejects_content_the_app_could_not_use(tmp_path, setup, message):
    root = make_root(tmp_path)
    setup(root)
    with pytest.raises(CatalogError, match=message):
        build_manifest(root)
