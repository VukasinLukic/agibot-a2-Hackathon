"""Helpers for sending direct command payloads to a LiveKit room."""

from __future__ import annotations

import json
import os
import uuid

from livekit import rtc
from livekit.api import AccessToken, VideoGrants

from ..models.conversation import AgentCommandRequest, AgentCommandResponse


DEFAULT_AGENT_COMMAND_TOPIC = os.getenv("AGENT_COMMAND_TOPIC", "agent-command")


def build_command_payload(command: AgentCommandRequest) -> tuple[bytes, str]:
    """Build the byte-stream payload to match testing_scripts/send_agent_command.py."""
    if command.plain_text:
        return command.text.encode("utf-8"), "text/plain"

    payload: dict[str, object] = {}
    if command.text.strip():
        payload["text"] = command.text.strip()
    if command.gesture:
        payload["gesture"] = command.gesture.strip()
    if command.force_gesture:
        payload["force_gesture"] = True
    if command.steps:
        payload["steps"] = [
            {
                key: value
                for key, value in {
                    "text": step.text.strip(),
                    "gesture": step.gesture.strip() if step.gesture else None,
                    "pause_after_ms": step.pause_after_ms,
                    "force_gesture": step.force_gesture,
                }.items()
                if value not in ("", None)
            }
            for step in command.steps
            if step.text.strip() or step.gesture or step.pause_after_ms
        ]

    return json.dumps(payload).encode("utf-8"), "application/json"


def generate_room_token(*, room: str, identity: str, name: str) -> str:
    """Create a temporary token for publishing a command into the room."""
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be configured")

    return (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room,
            )
        )
        .to_jwt()
    )


async def send_agent_command(command: AgentCommandRequest, *, room: str) -> AgentCommandResponse:
    """Publish a direct command byte stream into the target LiveKit room."""
    livekit_url = os.getenv("LIVEKIT_URL")
    if not livekit_url:
        raise RuntimeError("LIVEKIT_URL must be configured")

    payload, mime_type = build_command_payload(command)
    topic = (command.topic or DEFAULT_AGENT_COMMAND_TOPIC).strip() or DEFAULT_AGENT_COMMAND_TOPIC
    identity = (command.identity or f"supervisor-command-{uuid.uuid4().hex[:8]}").strip()
    name = (command.name or "Supervisor Command").strip()
    token = generate_room_token(room=room, identity=identity, name=name)

    livekit_room = rtc.Room()
    await livekit_room.connect(livekit_url, token)

    try:
        writer = await livekit_room.local_participant.stream_bytes(
            name="agent-command.txt" if mime_type == "text/plain" else "agent-command.json",
            total_size=len(payload),
            mime_type=mime_type,
            topic=topic,
            attributes={
                "source": "robot-supervisor",
                "identity": identity,
            },
        )
        await writer.write(payload)
        await writer.aclose()

        return AgentCommandResponse(
            status="success",
            room=room,
            topic=topic,
            mime_type=mime_type,
            payload_size=len(payload),
            plain_text=command.plain_text,
            text=command.text.strip(),
            gesture=command.gesture.strip() if command.gesture else None,
        )
    finally:
        await livekit_room.disconnect()
