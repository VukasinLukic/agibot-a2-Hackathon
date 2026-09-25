#!/usr/bin/env python3
"""
Unified audio bridge:
  - Publishes robot microphone audio (UDP multicast) into a LiveKit room.
  - Subscribes to the bot's LiveKit audio and plays it on the Unitree speaker.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import socket
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from livekit import rtc
from livekit.api import AccessToken, VideoGrants

os.environ.setdefault(
    "CYCLONEDDS_URI",
    r"""
<CycloneDDS>
  <Domain>
    <Tracing>
      <Verbosity>warning</Verbosity>
      <OutputFile>stdout</OutputFile>
    </Tracing>
  </Domain>
</CycloneDDS>
""",
)

from unitree_sdk2py.core.channel import ChannelFactory
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

load_dotenv()

LOG = logging.getLogger("robot_audio_bridge")

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH_BYTES


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def build_token(room: str, identity: str) -> str:
    token = (
        AccessToken(_require_env("LIVEKIT_API_KEY"), _require_env("LIVEKIT_API_SECRET"))
        .with_identity(identity)
        .with_name(identity)
        .with_grants(VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .with_ttl(timedelta(hours=12))
        .to_jwt()
    )
    return token


@dataclass
class MicConfig:
    mcast_group: str
    local_iface_ip: str
    frame_ms: int
    queue_size: int
    track_name: str


class MicPublisher:
    def __init__(self, cfg: MicConfig, stop_event: asyncio.Event):
        self.cfg = cfg
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=cfg.queue_size)
        self.stop_event = stop_event
        self._sock: Optional[socket.socket] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._source: Optional[rtc.AudioSource] = None

    async def start(self, room: rtc.Room) -> None:
        self._source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
        track = rtc.LocalAudioTrack.create_audio_track(self.cfg.track_name, self._source)
        publish_opts = rtc.TrackPublishOptions()
        publish_opts.source = rtc.TrackSource.SOURCE_MICROPHONE
        await room.local_participant.publish_track(track, publish_opts)
        LOG.info("Mic track '%s' published", self.cfg.track_name)

        self._sock = self._make_socket()
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._sender_task = asyncio.create_task(self._sender_loop())

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._sender_task:
            self._sender_task.cancel()
        if self._sock:
            with contextlib.suppress(Exception):
                self._sock.close()
        await asyncio.gather(
            *(t for t in [self._reader_task, self._sender_task] if t),
            return_exceptions=True,
        )

    def _make_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 5555))
        mreq = struct.pack("4s4s", socket.inet_aton(self.cfg.mcast_group), socket.inet_aton(self.cfg.local_iface_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except OSError:
            pass
        LOG.info("MicPublisher joined %s:5555 via %s", self.cfg.mcast_group, self.cfg.local_iface_ip)
        return sock

    async def _reader_loop(self) -> None:
        assert self._sock is not None
        loop = asyncio.get_running_loop()
        buffer = bytearray()
        bytes_per_frame = SAMPLE_WIDTH_BYTES * CHANNELS * (SAMPLE_RATE * self.cfg.frame_ms // 1000)
        LOG.info("MicPublisher reader running, frame=%d bytes", bytes_per_frame)

        try:
            while not self.stop_event.is_set():
                data, _ = await loop.run_in_executor(None, self._sock.recvfrom, 65535)
                if not data:
                    continue
                buffer.extend(data)

                while len(buffer) >= bytes_per_frame:
                    frame = bytes(buffer[:bytes_per_frame])
                    del buffer[:bytes_per_frame]

                    if self.queue.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            self.queue.get_nowait()
                    await self.queue.put(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("MicPublisher reader error: %s", exc)
            self.stop_event.set()

    async def _sender_loop(self) -> None:
        assert self._source is not None
        samples_per_frame = SAMPLE_RATE * self.cfg.frame_ms // 1000
        LOG.info("MicPublisher sender running (frame=%d ms)", self.cfg.frame_ms)
        next_deadline = time.monotonic()

        try:
            while not self.stop_event.is_set():
                try:
                    frame_bytes = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                audio_frame = rtc.AudioFrame(
                    data=frame_bytes,
                    sample_rate=SAMPLE_RATE,
                    num_channels=CHANNELS,
                    samples_per_channel=samples_per_frame,
                )
                await self._source.capture_frame(audio_frame)
                self.queue.task_done()

                next_deadline += self.cfg.frame_ms / 1000.0
                sleep_for = next_deadline - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                else:
                    next_deadline = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("MicPublisher sender error: %s", exc)
            self.stop_event.set()


class RobotSpeaker:
    def __init__(
        self,
        iface: str,
        app_name: str,
        chunk_ms: int,
        prebuffer_ms: int,
        volume: Optional[int],
    ) -> None:
        self.channel_factory = ChannelFactory()
        self.channel_factory.Init(0, iface)

        self.client = AudioClient()
        self.client.Init()
        self.client.SetTimeout(10.0)
        if volume is not None:
            clipped = max(0, min(100, volume))
            LOG.info("Setting robot volume to %d", clipped)
            self.client.SetVolume(clipped)

        self.app_name = app_name
        self.chunk_bytes = max(320, int(BYTES_PER_SECOND * (chunk_ms / 1000.0)) // 2 * 2)
        self.prebuffer_bytes = max(320, int(BYTES_PER_SECOND * (prebuffer_ms / 1000.0)) // 2 * 2)
        self.buffer = bytearray()
        self.stream_id: Optional[str] = None
        self.started = False
        self._lock = asyncio.Lock()

    async def start_stream(self, label: str) -> None:
        async with self._lock:
            await self._stop_locked()
            self.stream_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
            self.buffer.clear()
            self.started = False
            LOG.info("Robot speaker session started for %s (stream_id=%s)", label, self.stream_id)

    async def feed(self, payload: bytes) -> None:
        if not self.stream_id or not payload:
            return
        self.buffer.extend(payload)

        if not self.started and len(self.buffer) >= self.prebuffer_bytes:
            pre = bytes(self.buffer[: self.prebuffer_bytes])
            del self.buffer[: self.prebuffer_bytes]
            await self._send(pre)
            self.started = True
            LOG.info("Robot speaker primed (%.1f ms buffered)", (len(pre) / BYTES_PER_SECOND) * 1000.0)

        if not self.started:
            return

        while len(self.buffer) >= self.chunk_bytes:
            chunk = bytes(self.buffer[: self.chunk_bytes])
            del self.buffer[: self.chunk_bytes]
            await self._send(chunk)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if not self.stream_id:
            self.buffer.clear()
            return
        try:
            await asyncio.to_thread(self.client.PlayStop, self.app_name)
            LOG.info("Robot speaker stopped")
        finally:
            self.buffer.clear()
            self.stream_id = None
            self.started = False

    async def _send(self, payload: bytes) -> None:
        if not self.stream_id or not payload:
            return
        data = list(payload)
        ret = await asyncio.to_thread(self.client.PlayStream, self.app_name, self.stream_id, data)
        if isinstance(ret, tuple):
            ok = int(ret[0]) == 0
        else:
            ok = int(ret) == 0
        if not ok:
            raise RuntimeError(f"PlayStream error: {ret}")


def participant_matches_target(participant: rtc.RemoteParticipant, target: Optional[str]) -> bool:
    if not target:
        return True
    t = target.lower()
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
    return any(val and val.lower() == t for val in candidates)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Bidirectional LiveKit <-> Unitree bridge")
    parser.add_argument("--iface", required=True, help="Network interface used for Unitree ChannelFactory")
    parser.add_argument("--app", default="livekit_bridge", help="AudioClient app name")
    parser.add_argument("--identity", default="robot-bridge", help="LiveKit participant identity")
    parser.add_argument("--mic-track-name", default=os.getenv("LIVEKIT_TRACK_NAME", "g1-mic"), help="Track name for mic publishing")
    parser.add_argument("--chunk-ms", type=int, default=200, help="Robot playback chunk size (ms)")
    parser.add_argument("--prebuffer-ms", type=int, default=600, help="Robot playback prebuffer (ms)")
    parser.add_argument("--volume", type=int, default=100, help="Robot speaker volume (0-100)")
    parser.add_argument("--target", default=None, help="Only relay audio from this participant (matches identity/name/attributes)")
    parser.add_argument(
        "--ignore-track",
        action="append",
        default=[os.getenv("LIVEKIT_TRACK_NAME", "g1-mic")],
        help="Track names to ignore when target is not set (repeatable)",
    )
    parser.add_argument("--mic-frame-ms", type=int, default=int(os.getenv("LIVEKIT_FRAME_MS", "20")), help="Mic frame size (10 or 20 ms)")
    parser.add_argument("--mic-queue", type=int, default=int(os.getenv("LIVEKIT_MAX_QUEUE", "50")), help="Mic frame queue length")
    parser.add_argument("--mcast-group", default=os.getenv("MCAST_GRP"), required=False, help="Multicast group for robot mic")
    parser.add_argument("--local-iface-ip", default=os.getenv("LOCAL_IFACE_IP"), required=False, help="Local IP on robot network")
    args = parser.parse_args()

    if not args.mcast_group or not args.local_iface_ip:
        raise RuntimeError("MCAST_GRP and LOCAL_IFACE_IP must be provided (env or CLI)")

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    livekit_url = _require_env("LIVEKIT_URL")
    room_name = _require_env("LIVEKIT_ROOM")

    stop_event = asyncio.Event()

    mic_cfg = MicConfig(
        mcast_group=args.mcast_group,
        local_iface_ip=args.local_iface_ip,
        frame_ms=args.mic_frame_ms,
        queue_size=args.mic_queue,
        track_name=args.mic_track_name,
    )
    mic = MicPublisher(mic_cfg, stop_event)

    speaker = RobotSpeaker(
        iface=args.iface,
        app_name=args.app,
        chunk_ms=args.chunk_ms,
        prebuffer_ms=args.prebuffer_ms,
        volume=args.volume,
    )

    room = rtc.Room()
    active_task: Optional[asyncio.Task] = None
    active_label: Optional[str] = None

    async def relay_audio(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        nonlocal active_task, active_label
        label = participant.identity or participant.sid
        await speaker.start_stream(label)
        audio_stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=CHANNELS)
        try:
            async for frame_event in audio_stream:
                await speaker.feed(frame_event.frame.data.tobytes())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("Relay error (%s): %s", label, exc)
        finally:
            await speaker.stop()
            active_task = None
            active_label = None

    @room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        nonlocal active_task, active_label
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if args.target:
            if not participant_matches_target(participant, args.target):
                LOG.info("Ignoring track from %s (target mismatch)", participant.identity)
                return
        else:
            if args.ignore_track and publication.name in args.ignore_track:
                LOG.info("Ignoring track '%s' from %s (ignore list)", publication.name, participant.identity)
                return
        if active_task:
            LOG.info("Already relaying %s; skipping %s", active_label, participant.identity)
            return
        LOG.info("Subscribing to audio from %s (%s)", participant.identity, publication.name)
        active_label = participant.identity or participant.sid
        active_task = asyncio.create_task(relay_audio(track, participant))

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        nonlocal active_task, active_label
        if active_label and participant.identity == active_label:
            LOG.info("Active participant %s disconnected", participant.identity)
            if active_task:
                active_task.cancel()

    @room.on("disconnected")
    def on_disconnected(reason=None) -> None:
        LOG.info("LiveKit disconnected (%s)", reason)
        stop_event.set()

    token = build_token(room_name, args.identity)
    await room.connect(livekit_url, token)
    LOG.info("Connected to LiveKit room %s as %s", room_name, args.identity)

    await mic.start(room)

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        LOG.info("Interrupt received, shutting down...")
        stop_event.set()
    finally:
        if active_task:
            active_task.cancel()
            await asyncio.gather(active_task, return_exceptions=True)
        await mic.stop()
        await speaker.stop()
        await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
