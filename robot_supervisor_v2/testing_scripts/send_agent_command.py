#!/usr/bin/env python3
"""
Send a direct command payload to agent_main_text.py over a LiveKit byte stream.

Examples:
    python robot_supervisor_v2/testing_scripts/send_agent_command.py --room g1-lab --text "Pozdravljeni, to je test."
    python robot_supervisor_v2/testing_scripts/send_agent_command.py --room g1-lab --text "Pozdrav" --gesture "face wave"
    python robot_supervisor_v2/testing_scripts/send_agent_command.py --room g1-lab --text "Pozdrav" --plain-text

"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ENV = REPO_ROOT / "robot_supervisor_v2" / "app" / ".env"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(APP_ENV)

from livekit_shared.auth import generate_token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a direct text or text+gesture command to agent_main_text.py"
    )
    parser.add_argument("--room", default=os.getenv("LIVEKIT_ROOM", "g1-lab"))
    parser.add_argument("--topic", default=os.getenv("AGENT_COMMAND_TOPIC", "agent-command"))
    parser.add_argument("--text", required=True, help="Text the agent should speak")
    parser.add_argument(
        "--gesture",
        default=None,
        help="Optional gesture value to include in the JSON payload",
    )
    parser.add_argument(
        "--plain-text",
        action="store_true",
        help="Send raw text instead of JSON payload",
    )
    parser.add_argument(
        "--identity",
        default="command-tester",
        help="LiveKit identity used by the sender",
    )
    parser.add_argument(
        "--name",
        default="Command Tester",
        help="Display name used by the sender",
    )
    parser.add_argument(
        "--mime-type",
        default="application/json",
        help="MIME type to use for the byte stream",
    )
    return parser.parse_args()


def build_payload(args: argparse.Namespace) -> tuple[bytes, str]:
    if args.plain_text:
        return args.text.encode("utf-8"), "text/plain"

    payload = {"text": args.text}
    if args.gesture:
        payload["gesture"] = args.gesture
    return json.dumps(payload).encode("utf-8"), args.mime_type


async def main() -> None:
    args = parse_args()

    livekit_url = os.getenv("LIVEKIT_URL")
    if not livekit_url:
        raise SystemExit(f"LIVEKIT_URL is missing. Expected it in {APP_ENV}")

    payload, mime_type = build_payload(args)

    room = rtc.Room()
    token = generate_token(args.room, identity=args.identity, name=args.name)

    print(f"Connecting to room '{args.room}' as '{args.identity}'...")
    await room.connect(livekit_url, token)

    try:
        writer = await room.local_participant.stream_bytes(
            name="agent-command.json" if mime_type == "application/json" else "agent-command.txt",
            total_size=len(payload),
            mime_type=mime_type,
            topic=args.topic,
            attributes={
                "source": "testing-script",
                "identity": args.identity,
            },
        )
        await writer.write(payload)
        await writer.aclose()

        print(f"Sent {len(payload)} bytes to topic '{args.topic}' in room '{args.room}'.")
        print(f"Payload: {payload.decode('utf-8', 'replace')}")

        await asyncio.sleep(1)
    finally:
        await room.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
