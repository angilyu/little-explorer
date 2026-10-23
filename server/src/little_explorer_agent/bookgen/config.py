"""Book generation settings, all from the environment (see docs/operations.md)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field



def _num(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _flag(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = (env.get(name) or "").strip().lower()
    return default if not raw else raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class R2Config:
    endpoint: str = ""
    bucket: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    region: str = "auto"

    @property
    def ready(self) -> bool:
        return bool(self.endpoint and self.bucket and self.access_key_id and self.secret_access_key)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> R2Config:
        env = os.environ if env is None else env
        endpoint = (env.get("R2_ENDPOINT") or "").strip().rstrip("/")
        account = (env.get("R2_ACCOUNT_ID") or "").strip()
        if not endpoint and account:
            endpoint = f"https://{account}.r2.cloudflarestorage.com"
        return cls(
            endpoint=endpoint,
            bucket=(env.get("R2_BUCKET") or "").strip(),
            access_key_id=(env.get("R2_ACCESS_KEY_ID") or "").strip(),
            secret_access_key=(env.get("R2_SECRET_ACCESS_KEY") or "").strip(),
        )


@dataclass(frozen=True)
class BookgenConfig:
    openai_key: str = ""
    r2: R2Config = field(default_factory=R2Config)
    text_model: str = "gpt-5.4-mini"
    moderation_model: str = "omni-moderation-latest"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "coral"
    stt_model: str = "whisper-1"
    per_day: int = 3
    per_month: int = 10
    # Tries a day, counting ones that failed or were turned down (which don't use up the quota).
    tries_per_day: int = 8
    # "Make this page again" a day; these don't use up a book from the quota.
    page_redos_per_day: int = 5
    daily_usd_cap: float = 2.0
    job_usd_cap: float = 0.75
    max_attempts: int = 4
    lease_s: float = 300.0
    poll_s: float = 30.0
    runner: bool = True
    everyone: bool = False
    asset_url_ttl_s: int = 3600

    @property
    def ready(self) -> bool:
        """Both an OpenAI key and R2 storage are set, so books can be made and served."""
        return bool(self.openai_key) and self.r2.ready

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BookgenConfig:
        env = os.environ if env is None else env
        return cls(
            openai_key=(env.get("BOOKGEN_OPENAI_API_KEY") or "").strip(),
            r2=R2Config.from_env(env),
            text_model=(env.get("BOOKGEN_TEXT_MODEL") or "").strip() or cls.text_model,
            per_day=int(_num(env, "BOOKGEN_PER_DAY", cls.per_day)),
            per_month=int(_num(env, "BOOKGEN_PER_MONTH", cls.per_month)),
            tries_per_day=int(_num(env, "BOOKGEN_TRIES_PER_DAY", cls.tries_per_day)),
            page_redos_per_day=int(_num(env, "BOOKGEN_PAGE_REDOS_PER_DAY", cls.page_redos_per_day)),
            daily_usd_cap=_num(env, "BOOKGEN_DAILY_USD_CAP", cls.daily_usd_cap),
            job_usd_cap=_num(env, "BOOKGEN_JOB_USD_CAP", cls.job_usd_cap),
            poll_s=_num(env, "BOOKGEN_POLL_SECONDS", cls.poll_s),
            runner=_flag(env, "BOOKGEN_RUNNER", True),
            everyone=_flag(env, "BOOKGEN_EVERYONE", False),
        )

    def limits_json(self) -> dict[str, float | int]:
        return {
            "jobs_at_once": 1,
            "per_day": self.per_day,
            "per_month": self.per_month,
            "tries_per_day": self.tries_per_day,
            "page_redos_per_day": self.page_redos_per_day,
            "daily_usd_cap": self.daily_usd_cap,
            "job_usd_cap": self.job_usd_cap,
        }
