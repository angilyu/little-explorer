"""The page context the app pushes into the room (book, page, page text)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class ContextStore:
    """A small dict of app context. ``update`` merges a mapping; a ``None`` value deletes the key."""

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = {}
        if initial:
            self.update(initial)

    def update(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Merge ``values`` and return the keys that actually changed."""
        changed: dict[str, Any] = {}
        for key, value in values.items():
            if value is None:
                if key in self._data:
                    del self._data[key]
                    changed[key] = None
            elif key not in self._data or self._data[key] != value:
                self._data[key] = value
                changed[key] = value
        return changed

    def handle_data(self, payload: bytes | str) -> dict[str, Any]:
        """Apply a data message: a JSON object is merged; anything else is ignored."""
        text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
        try:
            value = json.loads(text)
        except ValueError:
            return {}
        return self.update(value) if isinstance(value, dict) else {}

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)
