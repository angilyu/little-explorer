import pytest

from little_explorer_agent import config


@pytest.fixture
def paths(tmp_path, monkeypatch):
    server_env = tmp_path / "server.env"
    user_env = tmp_path / "home" / ".config" / "little-explorer" / ".env"
    monkeypatch.setattr(config, "SERVER_ENV", server_env)
    monkeypatch.setattr(config, "USER_ENV", user_env)
    return server_env, user_env


def test_server_env_wins_over_user_copy(paths):
    server_env, user_env = paths
    user_env.parent.mkdir(parents=True)
    user_env.write_text("X=1\n")
    assert config.env_file({}) == user_env
    server_env.write_text("X=2\n")
    assert config.env_file({}) == server_env


def test_override_wins(paths, tmp_path):
    server_env, _ = paths
    server_env.write_text("X=2\n")
    custom = tmp_path / "custom.env"
    assert config.env_file({"LITTLE_EXPLORER_ENV": str(custom)}) == custom


def test_no_env_file(paths):
    assert config.env_file({}) is None


def test_load_env_reads_file_without_overwriting(paths, monkeypatch):
    _, user_env = paths
    user_env.parent.mkdir(parents=True)
    user_env.write_text("LE_TEST_A=from-file\nLE_TEST_B=from-file\n")
    monkeypatch.delenv("LITTLE_EXPLORER_ENV", raising=False)
    monkeypatch.delenv("LE_TEST_A", raising=False)
    monkeypatch.setenv("LE_TEST_B", "already-set")
    assert config.load_env() == user_env
    import os

    assert os.environ["LE_TEST_A"] == "from-file"
    assert os.environ["LE_TEST_B"] == "already-set"
    monkeypatch.delenv("LE_TEST_A")


def test_missing_override_is_an_error(paths, monkeypatch, tmp_path):
    monkeypatch.setenv("LITTLE_EXPLORER_ENV", str(tmp_path / "nope.env"))
    with pytest.raises(FileNotFoundError):
        config.load_env()
