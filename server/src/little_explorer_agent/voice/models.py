"""LangChain chat models from ``provider:model[@reasoning_effort]`` specs, and their text."""

from __future__ import annotations

from typing import Any


def model_init_args(raw: str, *, temperature: float | None = 0.3) -> tuple[str, dict[str, Any]]:
    """Model name and ``init_chat_model`` kwargs for a spec such as ``openai:gpt-5.4-mini@none``.

    A reasoning-effort spec omits temperature (OpenAI reasoning models reject it) and uses the
    Responses API for OpenAI models.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("LLM model must not be empty")
    model, sep, effort = value.rpartition("@")
    if not sep:
        model, effort = value, ""
    model, effort = model.strip(), effort.strip()
    if not model or (sep and not effort):
        raise ValueError(f"Invalid LLM model spec {raw!r}; expected provider:model[@effort]")
    kwargs: dict[str, Any] = {}
    if effort:
        kwargs["reasoning_effort"] = effort
        if model.startswith("openai:"):
            kwargs["use_responses_api"] = True
    elif temperature is not None:
        kwargs["temperature"] = temperature
    return model, kwargs


def init_chat_model_from_spec(raw: str, *, temperature: float | None = 0.3, **overrides: Any) -> Any:
    """Build a LangChain chat model from a spec; extra kwargs override the derived ones."""
    from langchain.chat_models import init_chat_model

    model, kwargs = model_init_args(raw, temperature=temperature)
    kwargs.update(overrides)
    return init_chat_model(model, **kwargs)


def message_text(msg: Any) -> str:
    """Text of a LangChain message or chunk (string or content-part list)."""
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return "" if content is None else str(content)
