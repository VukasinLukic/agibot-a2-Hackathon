#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from livekit import rtc
from pathlib import Path
# Ensure repository root is on sys.path when running as a script
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from livekit_shared.auth import generate_token

try:
    import cv2  # type: ignore
except ImportError as exc:  # pragma: no cover - user env specific
    raise SystemExit(
        "camera_publisher_mac.py requires OpenCV. "
        "Install it with `pip install opencv-python` inside your .venv."
    ) from exc


LOG = logging.getLogger("camera_publisher_mac")


@dataclass
class PublisherConfig:
    url: str
    room: str
    identity: str
    name: str
    topic: str
    interval: float
    camera_index: int
    width: Optional[int]
    height: Optional[int]
    jpeg_quality: int
    max_frames: Optional[int]
    image_prefix: str


class MacCamera:
    """Thin wrapper around OpenCV capture with async-friendly frame reads."""

    def __init__(
        self,
        *,
        index: int,
        width: Optional[int],
        height: Optional[int],
        jpeg_quality: int,
    ):
        self._index = index
        self._width = width
        self._height = height
        self._jpeg_quality = jpeg_quality
        self._capture: Optional[cv2.VideoCapture] = None
        self.actual_width: Optional[int] = None
        self.actual_height: Optional[int] = None

    async def open(self) -> None:
        # AVFoundation is the most reliable backend for macOS webcams.
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else 0
        cap = cv2.VideoCapture(self._index, backend)
        if not cap.isOpened():
            raise RuntimeError(f"Unable to open camera index {self._index}")
        if self._width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        if self._height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._capture = cap
        self.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
        self.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
        LOG.info(
            "Camera ready on index %s (%sx%s)",
            self._index,
            self.actual_width or "auto",
            self.actual_height or "auto",
        )

    async def capture_jpeg(self) -> bytes:
        if not self._capture:
            raise RuntimeError("Camera not opened")
        loop = asyncio.get_running_loop()
        ret, frame = await loop.run_in_executor(None, self._capture.read)
        if not ret or frame is None:
            raise RuntimeError("Failed to grab frame from camera")
        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality],
        )
        if not success:
            raise RuntimeError("Failed to encode frame as JPEG")
        return buffer.tobytes()

    def close(self) -> None:
        if self._capture:
            self._capture.release()
            self._capture = None


def parse_args() -> PublisherConfig:
    parser = argparse.ArgumentParser(
        description="Publish macOS camera frames to a LiveKit byte stream topic."
    )
    parser.add_argument("--url", default=os.getenv("LIVEKIT_URL"), help="LiveKit server URL")
    parser.add_argument(
        "--room",
        default=os.getenv("LIVEKIT_ROOM"),
        help="Room name to join (LIVEKIT_ROOM)",
    )
    parser.add_argument(
        "--identity",
        default=os.getenv("LIVEKIT_CAMERA_IDENTITY", "mac-camera"),
        help="LiveKit identity to use",
    )
    parser.add_argument(
        "--name",
        default=os.getenv("LIVEKIT_CAMERA_NAME"),
        help="Display name (defaults to identity)",
    )
    parser.add_argument(
        "--topic",
        default=os.getenv("LIVEKIT_CAMERA_TOPIC", "images"),
        help="Byte stream topic to publish frames to",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=int(os.getenv("LIVEKIT_CAMERA_INDEX", "0")),
        help="OpenCV camera index (default 0)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("LIVEKIT_CAMERA_INTERVAL", "3.0")),
        help="Seconds between frames",
    )
    parser.add_argument("--width", type=int, default=os.getenv("LIVEKIT_CAMERA_WIDTH"))
    parser.add_argument("--height", type=int, default=os.getenv("LIVEKIT_CAMERA_HEIGHT"))
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=int(os.getenv("LIVEKIT_CAMERA_JPEG_QUALITY", "85")),
        help="JPEG quality (0-100)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Optional limit for number of frames to send",
    )
    parser.add_argument(
        "--image-prefix",
        default=os.getenv("LIVEKIT_CAMERA_PREFIX", "mac-camera"),
        help="Prefix for generated stream names",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("CAMERA_PUBLISHER_LOG", "INFO"),
        help="Logging level",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.url:
        parser.error("LiveKit URL missing. Set LIVEKIT_URL or pass --url.")
    if not args.room:
        parser.error("LiveKit room missing. Set LIVEKIT_ROOM or pass --room.")

    width = int(args.width) if args.width is not None else None
    height = int(args.height) if args.height is not None else None

    return PublisherConfig(
        url=args.url,
        room=args.room,
        identity=args.identity,
        name=args.name or args.identity,
        topic=args.topic,
        interval=args.interval,
        camera_index=args.camera_index,
        width=width,
        height=height,
        jpeg_quality=args.jpeg_quality,
        max_frames=args.max_frames,
        image_prefix=args.image_prefix,
    )


async def _send_frame(
    room: rtc.Room,
    *,
    payload: bytes,
    cfg: PublisherConfig,
    camera: MacCamera,
    frame_idx: int,
) -> None:
    timestamp_ms = int(time.time() * 1000)
    name = f"{cfg.image_prefix}-{timestamp_ms}.jpg"
    attributes = {
        "source": cfg.identity,
        "width": str(camera.actual_width) if camera.actual_width else "",
        "height": str(camera.actual_height) if camera.actual_height else "",
        "frame": str(frame_idx),
    }
    writer = await room.local_participant.stream_bytes(
        name=name,
        total_size=len(payload),
        mime_type="image/jpeg",
        topic=cfg.topic,
        attributes=attributes,
    )
    await writer.write(payload)
    await writer.aclose()
    LOG.info(
        "Published frame %s (%d bytes) on topic '%s'",
        name,
        len(payload),
        cfg.topic,
    )


async def run(cfg: PublisherConfig) -> None:
    room = rtc.Room()
    token = generate_token(cfg.room, identity=cfg.identity, name=cfg.name)
    await room.connect(cfg.url, token)
    LOG.info("Connected to %s as %s", cfg.room, cfg.identity)

    camera = MacCamera(
        index=cfg.camera_index,
        width=cfg.width,
        height=cfg.height,
        jpeg_quality=cfg.jpeg_quality,
    )
    await camera.open()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _shutdown(*_: object) -> None:
        if not stop_event.is_set():
            LOG.info("Shutdown signal received, stopping publisher...")
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(sig, lambda *_: _shutdown())

    frame_idx = 0
    try:
        while not stop_event.is_set():
            try:
                payload = await camera.capture_jpeg()
                await _send_frame(
                    room,
                    payload=payload,
                    cfg=cfg,
                    camera=camera,
                    frame_idx=frame_idx,
                )
                frame_idx += 1
                if cfg.max_frames and frame_idx >= cfg.max_frames:
                    LOG.info("Reached max frame limit (%s); stopping.", cfg.max_frames)
                    break
            except Exception as exc:
                LOG.error("Failed to capture/send frame: %s", exc, exc_info=True)
                await asyncio.sleep(max(cfg.interval, 1.0))
                continue

            await asyncio.sleep(cfg.interval)
    finally:
        await room.disconnect()
        camera.close()
        LOG.info("Camera publisher shut down cleanly.")


def main() -> None:
    load_dotenv()
    cfg = parse_args()
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        LOG.info("Interrupted by user, exiting.")


if __name__ == "__main__":
    main()
