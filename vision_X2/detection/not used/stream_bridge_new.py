#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import signal
import sys
import time
import os
import json
import logging
from pathlib import Path
from urllib.request import urlopen

from dotenv import load_dotenv
from livekit import rtc

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parents[2]
VISION_DIR = THIS_DIR.parent

for path in (THIS_DIR, VISION_DIR, REPO_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

import cv2  # noqa: E402

from camera_bridge import CameraCapture, LOG, _send_frame, parse_args  # noqa: E402
from livekit_shared.auth import generate_token  # noqa: E402
from dispatch_client import DetectionDispatchClient  # noqa: E402
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# Polls GET /lock-state from gpu_detector_server and feeds the result to dispatch_client
class LockStateClient:
    def __init__(self):
        self.url = os.getenv("GPU_DETECTOR_LOCK_STATE_URL", "http://127.0.0.1:8765/lock-state")
        self.poll_interval = float(os.getenv("GPU_DETECTOR_POLL_INTERVAL", "0.2"))
        self.timeout = float(os.getenv("GPU_DETECTOR_LOCK_STATE_TIMEOUT", "1.0"))

    def _fetch(self) -> dict | None:
        # Sync HTTP GET — runs in a thread to avoid blocking the event loop.
        try:
            with urlopen(self.url, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    async def run(self, dispatch_client, stop_event):
        # Polls at 5Hz; dispatch_client deduplicates by track_id so this is safe to call every tick.
        while not stop_event.is_set():
            data = await asyncio.to_thread(self._fetch)
            if data is not None:
                await dispatch_client.handle_lock(data.get("locked_track_id"))
            await asyncio.sleep(self.poll_interval)

class HTTPFrameCapture:
    
    def __init__(self, *, jpeg_quality: int = 80) -> None:
        self._base_url = os.getenv("GPU_DETECTOR_BASE_URL", "http://127.0.0.1:8765")
        self._jpeg_quality = jpeg_quality
        self.actual_width: Optional[int] = None
        self.actual_height: Optional[int] = None
        self.actual_fps: Optional[float] = None

    async def open(self) -> None:
        
        from urllib.request import urlopen
        import json as _json
        for attempt in range(30):
            try:
                with urlopen(f"{self._base_url}/frame/info", timeout=1) as r:
                    info = _json.loads(r.read())
                self.actual_width = info["width"]
                self.actual_height = info["height"]
                return
            except Exception:
                await asyncio.sleep(1.0)
        raise RuntimeError("gpu_detector_server /frame/info not available after 30s — is main.py running?")

    async def capture_frame(self):
        from urllib.request import urlopen
        import numpy as np
        data = await asyncio.to_thread(self._fetch_frame)
        buf = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("Failed to decode frame from server")
        return frame

    def _fetch_frame(self) -> bytes:
        from urllib.request import urlopen
        with urlopen(f"{self._base_url}/frame", timeout=1) as r:
            return r.read()

    async def encode_jpeg(self, frame) -> bytes:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
        if not ok:
            raise RuntimeError("Failed to encode JPEG")
        return buf.tobytes()

    def close(self) -> None:
        pass





async def run_detection_stream(cfg) -> None:
    # connect to the LiveKit room.
    room = rtc.Room()
    token = generate_token(cfg.room, identity=cfg.identity, name=cfg.name)
    await room.connect(cfg.url, token)
    LOG.info("Connected to %s as %s", cfg.room, cfg.identity)

    # open physical camera 
    """camera = CameraCapture(
        device=cfg.device,
        width=cfg.width,
        height=cfg.height,
        framerate=cfg.framerate,
        jpeg_quality=cfg.jpeg_quality,
    )"""
    camera = HTTPFrameCapture(jpeg_quality=cfg.jpeg_quality)
    await camera.open()
    if not camera.actual_width or not camera.actual_height:
        raise RuntimeError("Camera opened without reporting a valid frame size")

    # dispatch_client starts conversations when a person is locked.
    # lock_client reads the lock state from gpu_detector_server.
    dispatch_client = DetectionDispatchClient.from_env()
    lock_client = LockStateClient()

    # publish a LiveKit video track
    video_source = rtc.VideoSource(camera.actual_width, camera.actual_height)
    video_track = rtc.LocalVideoTrack.create_video_track(cfg.video_track_name, video_source)
    publish_opts = rtc.TrackPublishOptions()
    publish_opts.source = rtc.TrackSource.SOURCE_CAMERA
    publication = await room.local_participant.publish_track(video_track, publish_opts)
    LOG.info("Published video track %s (%s)", cfg.video_track_name, publication.sid)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _shutdown(*_: object) -> None:
        if not stop_event.is_set():
            LOG.info("Shutdown signal received, stopping detection camera bridge...")
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _shutdown())

    # Start lock state polling as a background task alongside the frame loop.
    asyncio.create_task(lock_client.run(dispatch_client, stop_event))

    target_fps = cfg.framerate or camera.actual_fps or 30.0
    video_interval = max(1.0 / target_fps, 0.01)
    snapshot_interval = cfg.interval
    frame_idx = 0
    next_frame_at = time.monotonic()
    next_snapshot_at = next_frame_at

    try:
        while not stop_event.is_set():
            try:
                frame_started_at = time.monotonic()
                # Raw frame — no detection overlay, YOLO runs in gpu_detector_server.
                frame = await camera.capture_frame()

                # Push raw frame to the LiveKit video track.
                rgba_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
                video_frame = rtc.VideoFrame(
                    camera.actual_width,
                    camera.actual_height,
                    rtc.VideoBufferType.RGBA,
                    rgba_frame.tobytes(),
                )
                video_source.capture_frame(video_frame)

                # Optionally send a JPEG snapshot to the agent byte stream.
                if cfg.send_to_agent and frame_started_at >= next_snapshot_at:
                    payload = await camera.encode_jpeg(frame)
                    await _send_frame(
                        room,
                        payload=payload,
                        cfg=cfg,
                        camera=camera,
                        frame_idx=frame_idx,
                    )
                    frame_idx += 1
                    next_snapshot_at = frame_started_at + snapshot_interval

            except Exception as exc:
                LOG.error("Failed to capture/send frame: %s", exc, exc_info=True)
                await asyncio.sleep(max(video_interval, 0.5))
                continue

            # Pace the loop to the target framerate.
            next_frame_at += video_interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                next_frame_at = time.monotonic()
    finally:
        await room.disconnect()
        camera.close()
        LOG.info("Detection camera bridge shut down cleanly.")


def main() -> None:
    load_dotenv()
    cfg = parse_args()
    try:
        asyncio.run(run_detection_stream(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
