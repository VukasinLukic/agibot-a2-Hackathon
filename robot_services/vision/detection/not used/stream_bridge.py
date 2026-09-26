#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import signal
import sys
import time
from pathlib import Path

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
from main import PersonLockProcessor  # noqa: E402
from dispatch_client import DetectionDispatchClient  # noqa: E402


async def run_detection_stream(cfg) -> None:
    room = rtc.Room()
    token = generate_token(cfg.room, identity=cfg.identity, name=cfg.name)
    await room.connect(cfg.url, token)
    LOG.info("Connected to %s as %s", cfg.room, cfg.identity)

    camera = CameraCapture(
        device=cfg.device,
        width=cfg.width,
        height=cfg.height,
        framerate=cfg.framerate,
        jpeg_quality=cfg.jpeg_quality,
    )
    await camera.open()
    if not camera.actual_width or not camera.actual_height:
        raise RuntimeError("Camera opened without reporting a valid frame size")

    processor = PersonLockProcessor()
    dispatch_client = DetectionDispatchClient.from_env()
    video_source = rtc.VideoSource(camera.actual_width, camera.actual_height)
    video_track = rtc.LocalVideoTrack.create_video_track(cfg.video_track_name, video_source)
    publish_opts = rtc.TrackPublishOptions()
    publish_opts.source = rtc.TrackSource.SOURCE_CAMERA
    publication = await room.local_participant.publish_track(video_track, publish_opts)
    LOG.info("Published detection video track %s (%s)", cfg.video_track_name, publication.sid)

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
                frame = await camera.capture_frame()
                processed_frame, detection_state = processor.process_frame_with_state(frame)
                await dispatch_client.handle_lock(detection_state["locked_track_id"])

                rgba_frame = cv2.cvtColor(processed_frame, cv2.COLOR_BGR2RGBA)
                video_frame = rtc.VideoFrame(
                    camera.actual_width,
                    camera.actual_height,
                    rtc.VideoBufferType.RGBA,
                    rgba_frame.tobytes(),
                )
                video_source.capture_frame(video_frame)

                if cfg.send_to_agent and frame_started_at >= next_snapshot_at:
                    payload = await camera.encode_jpeg(processed_frame)
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
                LOG.error("Failed to capture/send detection frame: %s", exc, exc_info=True)
                await asyncio.sleep(max(video_interval, 0.5))
                continue

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
