import pytest

from little_explorer_agent.voice import (
    PATIENT,
    ContextStore,
    TurnDecision,
    UserTurn,
    decide,
    is_question,
    llm_gate,
    message_text,
    preset_from_env,
    text_overlap,
)
from little_explorer_agent.voice.models import model_init_args


class Reply:
    def __init__(self, content):
        self.content = content


class FakeModel:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return Reply(self.content)


def test_patient_preset_waits_for_slow_readers():
    handling = PATIENT.turn_handling()
    # The local end-of-turn model stretches the wait up to max_delay when he sounds mid-sentence.
    assert handling["endpointing"] == {"min_delay": 0.5, "max_delay": 4.0}
    assert PATIENT.turn_detection == "model" and PATIENT.turn_detector_version == "v1-mini"
    # Fast local-VAD barge-in that needs no transcript; resume if it was only a noise.
    assert handling["interruption"]["mode"] == "vad"
    assert handling["interruption"]["min_duration"] == 0.3
    assert handling["interruption"]["min_words"] == 0
    assert handling["interruption"]["resume_false_interruption"] is True
    assert handling["preemptive_generation"] == {"enabled": False}
    assert "turn_detection" not in handling
    assert PATIENT.noise_cancellation == "bvc"
    assert PATIENT.with_overrides(turn_detection="stt").turn_handling()["turn_detection"] == "stt"


def test_preset_env_overrides(monkeypatch):
    monkeypatch.setenv("PIP_MIN_ENDPOINTING_DELAY", "2.5")
    monkeypatch.setenv("PIP_NOISE_CANCELLATION", "NONE")
    monkeypatch.setenv("PIP_TTS_VOICE", "abc")
    preset = preset_from_env()
    assert (preset.min_endpointing_delay, preset.noise_cancellation, preset.tts_voice) == (2.5, "none", "abc")
    monkeypatch.setenv("PIP_NOISE_CANCELLATION", "loud")
    with pytest.raises(ValueError):
        preset_from_env()


def test_model_specs():
    assert model_init_args("openai:gpt-5.4-nano@none") == (
        "openai:gpt-5.4-nano",
        {"reasoning_effort": "none", "use_responses_api": True},
    )
    assert model_init_args("openai:gpt-4o-mini") == ("openai:gpt-4o-mini", {"temperature": 0.3})
    for bad in ("", "openai:x@"):
        with pytest.raises(ValueError):
            model_init_args(bad)


def test_message_text():
    assert message_text(Reply([{"type": "text", "text": "Hi "}, "there"])) == "Hi there"
    assert message_text(Reply(None)) == ""


def test_questions_and_overlap():
    assert is_question("um why is it tall")
    assert is_question("the tower is tall?")
    assert not is_question("the tower is tall")
    assert text_overlap("the Eiffel tower", "The Eiffel Tower is very tall.") == 1.0
    assert text_overlap("", "anything") == 0.0


def test_context_store_merges_json_and_deletes():
    store = ContextStore({"book": "paris"})
    assert store.handle_data(b'{"page": "eiffel", "book": "paris"}') == {"page": "eiffel"}
    assert store.update({"book": None}) == {"book": None}
    assert store.handle_data("not json") == {}
    assert store.snapshot() == {"page": "eiffel"}


async def test_decide_accepts_sync_async_and_bool_gates():
    turn = UserTurn("hi")

    async def listen(_):
        return TurnDecision.LISTEN

    assert await decide(None, turn) is TurnDecision.RESPOND
    assert await decide(lambda _: False, turn) is TurnDecision.IGNORE
    assert await decide(listen, turn) is TurnDecision.LISTEN


async def test_llm_gate_parses_and_limits_context():
    model = FakeModel("IGNORE - grown-ups talking")
    gate = llm_gate(model, "a picture book", context_keys=["city"])
    turn = UserTurn("the meeting moved", context={"city": "Paris", "secret": "x"}, history=[("user", "hi")])
    assert await gate(turn) is TurnDecision.IGNORE
    system, human = model.calls[0]
    assert "a picture book" in system.content
    assert "city: Paris" in human.content and "secret" not in human.content
    assert "user: hi" in human.content


async def test_llm_gate_falls_back_on_errors_and_odd_answers():
    assert await llm_gate(FakeModel("maybe?"))(UserTurn("x")) is TurnDecision.RESPOND
    assert await llm_gate(FakeModel(error=RuntimeError()), on_error=TurnDecision.IGNORE)(UserTurn("x")) is TurnDecision.IGNORE


def test_agent_registers_by_name_without_deprecated_argument(monkeypatch):
    from little_explorer_agent import AGENT_NAME
    from little_explorer_agent.agent import create_server, entrypoint

    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "")
    monkeypatch.delenv("LIVEKIT_AGENT_NAME")
    monkeypatch.delenv("LIVEKIT_AGENT_NAME_OVERRIDE", raising=False)
    server = create_server()
    assert server._entrypoint_fnc is entrypoint
    assert (server._agent_name, server._agent_name_source) == (AGENT_NAME, "env")


def test_openai_speech_streams_pcm_in_longer_chunks(monkeypatch):
    from livekit.agents import tts as agents_tts

    from little_explorer_agent.speech import TTS_MIN_CHUNK_CHARS, speech_setup

    monkeypatch.setenv("PIP_SPEECH", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    preset, speech = speech_setup(PATIENT)
    # The local end-of-turn model stays on; transcription streams interim words.
    assert preset.turn_detection == "model"
    assert speech["stt"].model == "gpt-realtime-whisper"
    assert speech["stt"].capabilities.interim_results is True
    assert speech["vad"] is not None
    adapter = speech["tts"]
    assert isinstance(adapter, agents_tts.StreamAdapter)
    assert adapter._wrapped_tts._opts.response_format == "pcm"
    assert adapter._sentence_tokenizer._config.min_sentence_len == TTS_MIN_CHUNK_CHARS
