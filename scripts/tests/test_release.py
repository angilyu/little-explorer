"""Checks for the release setup: scripts/release.sh's guards (one version, one commit) and the Beta
build configuration. Run with `python -m pytest -q scripts/tests` (needs git and bash; no Xcode)."""

import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PBXPROJ = ROOT / "LittleExplorer.xcodeproj" / "project.pbxproj"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
}


# --- release.sh's checks, in a throwaway repo with its own "origin" ------------------------------

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env={**os.environ, **GIT_ENV}, check=True,
                          capture_output=True, text=True).stdout.strip()


def release(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/release.sh", *args], cwd=repo, env={**os.environ, **GIT_ENV},
                          capture_output=True, text=True)


def write_project(repo: Path, *versions: str) -> None:
    lines = "".join(f"\t\t\t\tMARKETING_VERSION = {v};\n" for v in versions)
    (repo / "LittleExplorer.xcodeproj" / "project.pbxproj").write_text(f"{{\n{lines}}}\n")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "checkout", "-qb", "main")
    (work / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "release.sh", work / "scripts" / "release.sh")
    (work / "LittleExplorer.xcodeproj").mkdir()
    write_project(work, "1.1", "1.1", "1.1", "1.1")
    commit(work, "1.1")
    git(work, "push", "-q", "-u", "origin", "main")
    return work


def tag(repo: Path, name: str, commit: str = "HEAD") -> None:
    git(repo, "tag", "-a", name, "-m", name, commit)
    git(repo, "push", "-q", "origin", f"refs/tags/{name}")


def test_a_clean_pushed_main_commit_can_be_released(repo):
    result = release(repo, "check")
    assert result.returncode == 0, result.stderr
    head = git(repo, "rev-parse", "--short=7", "HEAD")
    assert re.search(rf"Little Explorer 1\.1 \(\d{{8}}\.\d{{4}}\) from {head}, tag v1\.1-\d{{8}}\.\d{{4}}", result.stdout)
    assert "OK to release." in result.stdout


@pytest.mark.parametrize("change", ["edit", "untracked"])
def test_refuses_uncommitted_changes(repo, change):
    if change == "edit":
        write_project(repo, "1.1", "1.1", "1.1", "1.2")
    else:
        (repo / "notes.txt").write_text("hi")
    for mode in ("check", "beta", "prod", "both"):
        result = release(repo, mode)
        assert result.returncode == 1 and "uncommitted or untracked" in result.stderr, mode


def test_refuses_an_unpushed_commit(repo):
    (repo / "notes.txt").write_text("hi")
    head = commit(repo, "local only")
    result = release(repo, "beta")
    assert result.returncode == 1 and f"{head[:7]} isn't pushed" in result.stderr


def test_uploads_only_come_from_main(repo):
    git(repo, "checkout", "-qb", "feature")
    (repo / "notes.txt").write_text("hi")
    commit(repo, "on a branch")
    git(repo, "push", "-q", "origin", "feature")
    result = release(repo, "both")
    assert result.returncode == 1 and "isn't on origin/main" in result.stderr
    result = release(repo, "check")
    assert result.returncode == 0 and "isn't on origin/main" in result.stderr


def test_one_version_is_one_commit(repo):
    tag(repo, "v1.1-20261101.0930")
    (repo / "notes.txt").write_text("new code")
    commit(repo, "new code, same version")
    git(repo, "push", "-q")
    result = release(repo, "prod")
    assert result.returncode == 1
    assert "1.1 was already released from" in result.stderr and "bump MARKETING_VERSION" in result.stderr
    # Bumping the version makes it releasable.
    write_project(repo, "1.2", "1.2", "1.2", "1.2")
    commit(repo, "1.2")
    git(repo, "push", "-q")
    assert release(repo, "check").returncode == 0


def test_another_clones_tag_counts_too(repo, tmp_path):
    """The tag check reads origin's tags, not just this checkout's."""
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    git(other, "checkout", "-qb", "elsewhere")
    (other / "notes.txt").write_text("released from another commit")
    commit(other, "elsewhere")
    git(other, "push", "-q", "origin", "elsewhere")
    tag(other, "v1.1-20261101.0930")
    assert not git(repo, "tag", "-l")
    result = release(repo, "check")
    assert result.returncode == 1 and "already released" in result.stderr


def test_prod_after_beta_reuses_the_build_number(repo):
    tag(repo, "v1.1-20261101.0930")
    tag(repo, "v1.1-20261102.0800")
    result = release(repo, "check")
    assert result.returncode == 0
    assert "1.1 (20261102.0800)" in result.stdout and "released before as" in result.stdout
    assert "1.1 (20261101.0930)" not in release(repo, "check", "--new-build").stdout
    assert "1.1 (7.1)" in release(repo, "check", "--build", "7.1").stdout
    assert release(repo, "check", "--build", "soon").returncode == 2


def test_refuses_mismatched_marketing_versions(repo):
    write_project(repo, "1.1", "1.1", "1.2", "1.1")
    commit(repo, "mismatch")
    git(repo, "push", "-q")
    result = release(repo, "check", "--dry-run")
    assert result.returncode == 1 and "MARKETING_VERSIONs differ: 1.1 1.2" in result.stderr


def test_a_dry_run_only_warns(repo):
    (repo / "notes.txt").write_text("hi")
    result = release(repo, "check", "--dry-run")
    assert result.returncode == 0 and "warning: the working tree" in result.stderr
    assert "OK to release." not in result.stdout


def test_usage(repo):
    for args in ((), ("upload",), ("beta", "--submit")):
        assert release(repo, *args).returncode == 2


# --- the Beta build configuration ----------------------------------------------------------------

def configurations(name: str) -> dict[str, str]:
    """The `name` XCBuildConfiguration blocks: their ids and text."""
    text = PBXPROJ.read_text()
    return dict(re.findall(rf"\t\t(\w{{24}}) /\* {name} \*/ = \{{\n(.*?)\n\t\t\}};", text, re.S))


def test_every_configuration_list_has_a_beta():
    text = PBXPROJ.read_text()
    lists = re.findall(r"buildConfigurations = \((.*?)\);", text, re.S)
    assert len(lists) == 3
    for ids in lists:
        assert "/* Release */" in ids and "/* Beta */" in ids


def test_the_beta_app_is_the_release_app_under_another_name():
    release_app = next(v for v in configurations("Release").values() if "Milo.xcconfig" in v)
    beta_app = next(v for v in configurations("Beta").values() if "baseConfigurationReference" in v)
    assert "Beta.xcconfig" in beta_app
    # The xcconfig decides the name, bundle and icon: settings on the target would override it.
    for key in ("PRODUCT_BUNDLE_IDENTIFIER", "INFOPLIST_KEY_CFBundleDisplayName", "ASSETCATALOG_COMPILER_APPICON_NAME"):
        assert key not in beta_app
        assert key in release_app
    # Otherwise the same settings as Release.
    strip = ("PRODUCT_BUNDLE_IDENTIFIER", "INFOPLIST_KEY_CFBundleDisplayName", "ASSETCATALOG_COMPILER_APPICON_NAME",
             "baseConfigurationReference", "name = ")
    keep = lambda block: sorted(line for line in block.splitlines() if not any(s in line for s in strip))
    assert keep(beta_app) == keep(release_app)
    assert 'PRODUCT_BUNDLE_IDENTIFIER = com.angilyu.littleexplorer;' in release_app


def test_beta_xcconfig():
    text = (ROOT / "Config" / "Beta.xcconfig").read_text()
    settings = dict(re.findall(r"^(\w+) = (.*)$", text, re.M))
    assert settings == {
        "PRODUCT_BUNDLE_IDENTIFIER": "com.angilyu.littleexplorer.beta",
        "INFOPLIST_KEY_CFBundleDisplayName": "Little Explorer (beta)",
        "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon-Beta",
    }
    assert '#include "Milo.xcconfig"' in text


def test_the_beta_icon_exists():
    icon_set = ROOT / "LittleExplorer" / "Assets.xcassets" / "AppIcon-Beta.appiconset"
    assert (icon_set / "AppIcon-Beta.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "AppIcon-Beta.png" in (icon_set / "Contents.json").read_text()


def test_info_plists_carry_the_commit():
    for name in ("Info.plist", "Info-Debug.plist"):
        with open(ROOT / "Config" / name, "rb") as f:
            assert plistlib.load(f)["GitCommit"] == "$(GIT_COMMIT)", name


def test_one_marketing_version():
    assert len(set(re.findall(r"MARKETING_VERSION = ([^;]+);", PBXPROJ.read_text()))) == 1
