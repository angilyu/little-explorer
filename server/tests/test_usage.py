"""Per-session cost logging: token split, pricing, the CSV row, and the report."""

import csv
import importlib.util
import os
import stat
from pathlib import Path

import pytest
from livekit.agents.metrics import RealtimeModelMetrics, STTMetrics
from livekit.agents.metrics.base import Metadata
from livekit.agents.metrics.usage import ModelUsageCollector

from little_explorer_agent import config
from little_explorer_agent.agent import session_model
from little_explorer_agent.usage import COLUMNS, PRICES, Rates, Tokens, UsageMeter, append_row, rates_for, tokens_from_usage

REPORT = Path(__file__).resolve().parents[1] / "tools" / "usage_report.py"


def realtime_metrics(model="gpt-realtime", text_in=1000, audio_in=2000, cached_text=500, cached_audio=100, text_out=50, audio_out=400):
    return RealtimeModelMetrics(
        request_id="r",
        timestamp=0,
        input_tokens=text_in + audio_in,
        output_tokens=text_out + audio_out,
        input_token_details=RealtimeModelMetrics.InputTokenDetails(
            text_tokens=text_in,
            audio_tokens=audio_in,
            cached_tokens=cached_text + cached_audio,
            cached_tokens_details=RealtimeModelMetrics.CachedTokenDetails(text_tokens=cached_text, audio_tokens=cached_audio),
        ),
        output_token_details=RealtimeModelMetrics.OutputTokenDetails(text_tokens=text_out, audio_tokens=audio_out),
        metadata=Metadata(model_name=model, model_provider="openai"),
    )


def transcription_metrics():
    return STTMetrics(
        label="openai.realtime",
        request_id="t",
        timestamp=0,
        duration=0,
        audio_duration=0,
        streamed=True,
        input_tokens=120,
        input_audio_tokens=120,
        output_tokens=15,
        metadata=Metadata(model_name="gpt-4o-mini-transcribe", model_provider="openai"),
    )


def collected(*metrics):
    c = ModelUsageCollector()
    for m in metrics:
        c.collect(m)
    return c.flatten()


def test_realtime_usage_splits_text_audio_and_cached():
    tokens = tokens_from_usage(collected(realtime_metrics(), transcription_metrics()))
    llm = next(t for t in tokens if t.kind == "llm")
    stt = next(t for t in tokens if t.kind == "stt")
    assert (llm.model, llm.input_text, llm.input_audio, llm.cached_text, llm.cached_audio) == ("gpt-realtime", 1000, 2000, 500, 100)
    assert (llm.output_text, llm.output_audio) == (50, 400)
    assert (stt.model, stt.input_audio, stt.output_text) == ("gpt-4o-mini-transcribe", 120, 15)


def test_cost_bills_cached_tokens_at_the_cached_rate():
    t = Tokens("gpt-realtime", input_text=1_000_000, cached_text=400_000, input_audio=1_000_000, cached_audio=0, output_audio=1_000_000)
    r = PRICES["gpt-realtime"]
    assert t.cost(r) == pytest.approx(0.6 * r.text_in + 0.4 * r.text_cached + r.audio_in + r.audio_out)


def test_published_realtime_rates():
    assert PRICES["gpt-realtime"] == Rates(4.00, 0.40, 16.00, 32.00, 0.40, 64.00)
    assert PRICES["gpt-realtime-mini"] == Rates(0.60, 0.06, 2.40, 10.00, 0.30, 20.00)


def test_rates_match_dated_snapshots_and_take_env_overrides():
    assert rates_for("gpt-realtime-2025-08-28", env={}) == PRICES["gpt-realtime"]
    assert rates_for("gpt-realtime-mini-2025-10-06", env={}) == PRICES["gpt-realtime-mini"]
    assert rates_for("mystery-model", env={}) is None
    r = rates_for("gpt-realtime-mini", env={"PIP_PRICE_GPT_REALTIME_MINI_AUDIO_OUT": "25"})
    assert r.audio_out == 25 and r.audio_in == 10.00


def test_meter_counts_responses_and_writes_one_private_row(tmp_path):
    path = tmp_path / "sub" / "usage.csv"
    now = [100.0]
    meter = UsageMeter(mode="realtime", model="gpt-realtime", path=path, clock=lambda: now[0])
    meter.on_context({"book": "paris", "page": "eiffel", "page_text": "secret page words"})
    meter.on_context({"book": "paris", "page": "louvre"})
    meter.on_context({"book": "paris", "page": "eiffel"})
    first = realtime_metrics()
    meter.on_usage(collected(first))
    meter.on_usage(collected(first, transcription_metrics()))  # transcription only: not a response
    meter.on_usage(collected(first, transcription_metrics(), realtime_metrics()))
    now[0] = 190.0
    row = meter.finish()
    assert meter.finish() is None  # the close event and the shutdown callback both call it

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and list(rows[0]) == COLUMNS
    saved = rows[0]
    assert saved["mode"] == "realtime" and saved["model"] == "gpt-realtime" and saved["book"] == "paris"
    assert saved["pages_visited"] == "2" and saved["responses"] == "2" and saved["duration_s"] == "90.0"
    assert saved["input_audio_tokens"] == "4000" and saved["transcribe_model"] == "gpt-4o-mini-transcribe"
    assert float(saved["est_usd"]) > 0 and saved["unpriced_models"] == ""
    assert "secret" not in path.read_text()
    assert row["est_usd"] == saved["est_usd"]


def test_meter_writes_a_row_even_if_the_session_never_started(tmp_path):
    path = tmp_path / "usage.csv"
    UsageMeter(mode="realtime", model="gpt-realtime", path=path).finish()
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["responses"] == "0" and rows[0]["est_usd"] == "0.0000"


def test_rows_append_under_one_header(tmp_path):
    path = tmp_path / "usage.csv"
    append_row({"mode": "realtime"}, path)
    append_row({"mode": "pipeline"}, path)
    lines = path.read_text().splitlines()
    assert lines[0].split(",") == COLUMNS and len(lines) == 3


def test_unknown_models_are_flagged_not_guessed():
    meter = UsageMeter(mode="realtime", model="mystery", path=Path("/dev/null"))
    meter.on_usage(collected(realtime_metrics(model="mystery")))
    row = meter.usage.row(env={})
    assert row["unpriced_models"] == "mystery" and row["est_usd"] == "0.0000"


def test_session_model_per_mode():
    assert session_model("realtime", env={"PIP_REALTIME_MODEL": "gpt-realtime-mini"}) == "gpt-realtime-mini"
    assert session_model("pipeline") == "gpt-5.4-mini"


def test_report_totals_per_day_and_model(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("usage_report", REPORT)
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    rows = [
        {"started_at": "2026-08-01T09:00:00-07:00", "duration_s": "120", "model": "gpt-realtime", "est_usd": "0.20"},
        {"started_at": "2026-08-01T18:00:00-07:00", "duration_s": "60", "model": "gpt-realtime-mini", "est_usd": "0.04"},
        {"started_at": "2026-08-02T09:00:00-07:00", "duration_s": "240", "model": "gpt-realtime", "est_usd": "0.40"},
    ]
    s = report.summarize(rows)
    total = s["total"]["all"]
    assert total["sessions"] == 3 and total["minutes"] == pytest.approx(7)
    assert total["usd"] == pytest.approx(0.64) and total["usd_per_min"] == pytest.approx(0.64 / 7)
    assert s["day"]["2026-08-01"]["sessions"] == 2
    assert s["model"]["gpt-realtime"]["usd_per_min"] == pytest.approx(0.10)
    assert s["model"]["gpt-realtime"]["usd_per_session"] == pytest.approx(0.30)

    path = tmp_path / "usage.csv"
    for r in rows:
        append_row(r, path)
    assert report.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Per model" in out and "gpt-realtime-mini" in out


def test_admin_keys_are_never_loaded(tmp_path, monkeypatch):
    env = tmp_path / "x.env"
    env.write_text("OPENAI_ADMIN_KEY=sk-admin-test\nRENDER_API_KEY=rnd-test\nLE_TEST_VALUE=ok\n")
    monkeypatch.setenv("LITTLE_EXPLORER_ENV", str(env))
    monkeypatch.delenv("LE_TEST_VALUE", raising=False)
    monkeypatch.setenv("RENDER_API_KEY", "from-shell")
    config.load_env()
    assert os.environ["LE_TEST_VALUE"] == "ok"
    assert "OPENAI_ADMIN_KEY" not in os.environ and "RENDER_API_KEY" not in os.environ
    assert "OPENAI_ADMIN_KEY" in env.read_text()  # the file is left alone
