"""Keep tests away from the real database, token server and usage files."""

import pytest


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("DATABASE_URL", "PIP_SERVER_URL", "PIP_REPORT_KEY", "PIP_ADMIN_KEY", "PIP_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)
    for name in ("PIP_LIMIT_UNKNOWN_MIN", "PIP_LIMIT_APPROVED_MIN", "PIP_LIMIT_MIN_START_S", "PIP_IDLE_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    for name in ("PIP_COINS", "PIP_STARS_PER_COIN", "PIP_COINS_MIN", "PIP_COINS_MAX"):
        monkeypatch.delenv(name, raising=False)
    for name in ("PIP_EMAIL_PROVIDER", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "RESEND_API_KEY", "PIP_EMAIL_FROM",
                 "PIP_AUTH_SECRET", "PIP_DEFAULT_BOOKS", "PIP_REVIEW_EMAIL", "PIP_REVIEW_CODE", "PIP_EMAIL_DAILY_CAP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PIP_DEVICES_DB", str(tmp_path / "devices.sqlite3"))
    monkeypatch.setenv("PIP_USAGE_FILE", str(tmp_path / "usage.csv"))
