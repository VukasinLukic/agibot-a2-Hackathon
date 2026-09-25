#!/usr/bin/env python3
"""
Minimal LiveKit transcription listener for debugging.

Usage:
    python test_transcript_listener.py

This script connects to a LiveKit room and listens for transcriptions.
It will print any transcription events or text streams it receives.
"""

import asyncio
import os
from dotenv import load_dotenv
from livekit import rtc
from livekit.api import AccessToken, VideoGrants
from livekit.agents.types import (
    ATTRIBUTE_TRANSCRIPTION_FINAL,
    ATTRIBUTE_TRANSCRIPTION_SEGMENT_ID,
    ATTRIBUTE_TRANSCRIPTION_TRACK_ID,
    TOPIC_TRANSCRIPTION,
)

load_dotenv()

# Configuration from environment
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://127.0.0.1:7880")
LIVEKIT_ROOM = os.getenv("LIVEKIT_ROOM", "g1-lab")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
IDENTITY = "test-transcript-listener"


def on_text_stream(reader: rtc.TextStreamReader, participant_identity: str):
    """Handle text stream."""
    print(f"\n🎤 TEXT STREAM from {participant_identity}")
    print(f"   Topic: {reader.info.topic}")
    print(f"   Stream ID: {reader.info.stream_id}")
    print(f"   Attributes: {reader.info.attributes}")

    # Create task to consume stream
    asyncio.create_task(consume_stream(reader, participant_identity))


async def consume_stream(reader: rtc.TextStreamReader, participant_identity: str):
    """Consume text stream chunks."""
    try:
        attributes = reader.info.attributes
        segment_id = attributes.get(ATTRIBUTE_TRANSCRIPTION_SEGMENT_ID, "unknown")

        print(f"\n📝 CONSUMING STREAM")
        print(f"   Segment ID: {segment_id}")

        chunks = []
        async for chunk in reader:
            chunks.append(chunk)
            print(f"   Chunk: '{chunk}'")

        full_text = "".join(chunks)
        final_attr = attributes.get(ATTRIBUTE_TRANSCRIPTION_FINAL, "false")
        is_final = str(final_attr).lower() == "true"

        print(f"\n✅ STREAM COMPLETE")
        print(f"   Full text: '{full_text}'")
        print(f"   Final: {is_final}")
        print(f"   Participant: {participant_identity}")

    except Exception as e:
        print(f"\n❌ ERROR consuming stream: {e}")
        import traceback
        traceback.print_exc()


def on_transcription_received(segments, participant, publication):
    """Handle transcription_received event."""
    participant_identity = getattr(participant, "identity", None) if participant else None

    print(f"\n🎯 TRANSCRIPTION EVENT")
    print(f"   Participant: {participant_identity or 'None'}")
    print(f"   Segments: {len(segments)}")

    for i, segment in enumerate(segments):
        text = getattr(segment, "text", "")
        final = getattr(segment, "final", False)
        seg_id = getattr(segment, "id", "unknown")

        print(f"   Segment {i+1}:")
        print(f"     ID: {seg_id}")
        print(f"     Text: '{text}'")
        print(f"     Final: {final}")


def on_participant_connected(participant):
    """Handle participant connection."""
    print(f"\n👤 PARTICIPANT CONNECTED: {participant.identity} (sid: {participant.sid})")


def on_track_subscribed(track, publication, participant):
    """Handle track subscription."""
    print(f"\n🎵 TRACK SUBSCRIBED: {track.kind} from {participant.identity} (sid: {track.sid})")


async def main():
    print("=" * 80)
    print("MINIMAL LIVEKIT TRANSCRIPTION LISTENER")
    print("=" * 80)
    print(f"\nConfiguration:")
    print(f"  LiveKit URL: {LIVEKIT_URL}")
    print(f"  Room: {LIVEKIT_ROOM}")
    print(f"  Identity: {IDENTITY}")
    print(f"  Topic: {TOPIC_TRANSCRIPTION}")
    print()

    # Create room
    room = rtc.Room()

    # Generate token
    token = AccessToken(
        LIVEKIT_API_KEY,
        LIVEKIT_API_SECRET
    ).with_identity(IDENTITY).with_name("Test Listener").with_grants(
        VideoGrants(room_join=True, room=LIVEKIT_ROOM)
    ).to_jwt()

    print("Connecting to room...")
    await room.connect(LIVEKIT_URL, token)
    print("✓ Connected to room!")

    # Register text stream handler AFTER connecting
    print(f"\nRegistering text stream handler for topic: {TOPIC_TRANSCRIPTION}")
    room.register_text_stream_handler(TOPIC_TRANSCRIPTION, on_text_stream)
    print("✓ Text stream handler registered!")

    # Register event handlers
    room.on("transcription_received", on_transcription_received)
    room.on("participant_connected", on_participant_connected)
    room.on("track_subscribed", on_track_subscribed)
    print("✓ Event handlers registered!")

    print("\n" + "=" * 80)
    print("LISTENING FOR TRANSCRIPTIONS...")
    print("(Press Ctrl+C to stop)")
    print("=" * 80 + "\n")

    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    finally:
        room.unregister_text_stream_handler(TOPIC_TRANSCRIPTION)
        await room.disconnect()
        print("✓ Disconnected")


if __name__ == "__main__":
    asyncio.run(main())
