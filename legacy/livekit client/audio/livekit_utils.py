from __future__ import annotations

import contextlib
import json
from datetime import timedelta
from typing import Optional

from livekit import rtc
from livekit.api import AccessToken, VideoGrants


def build_token(
    *,
    room: str,
    identity: str,
    api_key: str,
    api_secret: str,
    ttl_hours: int = 12,
) -> str:
    token = (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .with_ttl(timedelta(hours=ttl_hours))
        .to_jwt()
    )
    return token


def participant_matches_target(
    participant: rtc.RemoteParticipant,
    target: Optional[str],
) -> bool:
    if not target:
        return True
    target_lower = target.lower()
    candidates: list[str] = []
    if participant.identity:
        candidates.append(participant.identity)
    if participant.name:
        candidates.append(participant.name)
    attrs = getattr(participant, "attributes", {}) or {}
    candidates.extend(attrs.values())
    metadata = participant.metadata
    if metadata:
        candidates.append(metadata)
        with contextlib.suppress(Exception):
            data = json.loads(metadata)
            if isinstance(data, dict):
                for val in data.values():
                    if isinstance(val, str):
                        candidates.append(val)
    return any(val and val.lower() == target_lower for val in candidates)


__all__ = ["build_token", "participant_matches_target"]
