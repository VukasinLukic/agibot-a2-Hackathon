from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class AudioTarget(str, Enum):
    ROBOT = "robot"
    HOST = "host"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _parse_device(value: str | None) -> int | str | None:
    if not value:
        return None
    candidate = value.strip()
    if candidate.isdigit():
        return int(candidate)
    return candidate


@dataclass(frozen=True)
class LiveKitBridgeConfig:
    url: str
    api_key: str
    api_secret: str
    room_name: str
    identity: str
    mic_track_name: str
    target_participant: str | None
    ignore_tracks: list[str]


def load_audio_target() -> AudioTarget:
    raw = os.environ.get("AUDIO_TARGET", AudioTarget.ROBOT.value).strip().lower()
    try:
        return AudioTarget(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid AUDIO_TARGET '{raw}', expected 'robot' or 'host'.") from exc


def load_livekit_config(
    *,
    room_name: str | None,
    identity_default: str,
    mic_track_name_default: str,
) -> LiveKitBridgeConfig:
    url = _require_env("LIVEKIT_URL")
    api_key = _require_env("LIVEKIT_API_KEY")
    api_secret = _require_env("LIVEKIT_API_SECRET")
    room = room_name or os.environ.get("LIVEKIT_ROOM")
    if not room:
        raise RuntimeError("Missing required env var: LIVEKIT_ROOM")

    identity = (
        os.environ.get("LIVEKIT_AUDIO_IDENTITY")
        or os.environ.get("LIVEKIT_IDENTITY")
        or identity_default
    )
    mic_track_name = os.environ.get("LIVEKIT_TRACK_NAME") or mic_track_name_default
    target_participant = os.environ.get("AUDIO_TARGET_PARTICIPANT") or None

    ignore_tracks_env = os.environ.get("AUDIO_IGNORE_TRACKS")
    if ignore_tracks_env:
        ignore_tracks = [item.strip() for item in ignore_tracks_env.split(",") if item.strip()]
    else:
        ignore_tracks = [mic_track_name]

    return LiveKitBridgeConfig(
        url=url,
        api_key=api_key,
        api_secret=api_secret,
        room_name=room,
        identity=identity,
        mic_track_name=mic_track_name,
        target_participant=target_participant,
        ignore_tracks=ignore_tracks,
    )


__all__ = [
    "AudioTarget",
    "LiveKitBridgeConfig",
    "load_audio_target",
    "load_livekit_config",
    "_parse_device",
]
