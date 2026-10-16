"""LiveKit access tokens for the app, minted by the token server so the app never holds the secret."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import timedelta


def mint_token(
    *,
    room: str,
    identity: str,
    agent_name: str,
    name: str | None = None,
    attributes: Mapping[str, str] | None = None,
    ttl: timedelta = timedelta(hours=1),
    api_key: str | None = None,
    api_secret: str | None = None,
) -> str:
    """A JWT that lets ``identity`` join ``room`` and dispatches ``agent_name`` into it.

    ``attributes`` become the participant's initial attributes (the opening page, so the agent
    knows it before any data message). The participant may update its own attributes later
    (``can_update_own_metadata``), so the current page is always readable from the room.
    """
    from livekit import api

    key = api_key or os.environ.get("LIVEKIT_API_KEY", "")
    secret = api_secret or os.environ.get("LIVEKIT_API_SECRET", "")
    if not key or not secret:
        raise ValueError("LiveKit API key/secret missing; set LIVEKIT_API_KEY and LIVEKIT_API_SECRET")
    token = (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_ttl(ttl)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                can_update_own_metadata=True,
            )
        )
        .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=agent_name)]))
    )
    if name:
        token = token.with_name(name)
    if attributes:
        token = token.with_attributes(dict(attributes))
    return token.to_jwt()
