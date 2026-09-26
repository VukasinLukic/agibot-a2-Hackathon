#!/usr/bin/env python3
"""Local OpenCV harness for testing ID/card capture from a webcam."""

from __future__ import annotations

import argparse
import base64
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DETECTION_DIR = REPO_ROOT / "robot_services" / "vision" / "detection"
if str(DETECTION_DIR) not in sys.path:
    sys.path.insert(0, str(DETECTION_DIR))

try:
    import cv2
    import numpy as np
except ImportError as exc:
    raise SystemExit(
        "local_id_capture.py requires OpenCV and numpy. "
        "Run it with the project .venv or install opencv-python."
    ) from exc

from card_capture import CardCaptureProcessor, STABILITY_FRAMES
from debug_overlay import (
    card_capture_status_line as status_line,
    draw_card_capture_preview as draw_preview,
    draw_portrait_debug_preview,
    portrait_debug_status_line,
    save_portrait_debug_snapshot,
)


WINDOW_NAME = "Local ID Capture Test"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview and test the portrait-anchor ID/card capture processor."
    )
    parser.add_argument("--camera", default="0", help="Camera index or device path. Defaults to 0.")
    parser.add_argument("--width", type=int, default=960, help="Requested capture width.")
    parser.add_argument("--height", type=int, default=540, help="Requested capture height.")
    parser.add_argument("--fps", type=float, default=30.0, help="Requested capture FPS.")
    parser.add_argument("--save-dir", default="tmp/id_captures", help="Directory for captured JPEGs.")
    parser.add_argument("--request-id", default="local-test", help="Synthetic request id.")
    parser.add_argument("--target-type", default="id_card", help="Synthetic target type metadata.")
    parser.add_argument("--source", default="macos-local", help="Synthetic source metadata.")
    parser.add_argument("--list-devices", action="store_true", help="Probe camera indexes 0..9 and exit.")
    parser.add_argument("--headless", action="store_true", help="Run without an OpenCV preview window.")
    parser.add_argument("--debug", action="store_true", help="Print portrait-edge detection status.")
    parser.add_argument(
        "--portrait-debug",
        action="store_true",
        help="Show printed-portrait anchors and line-derived card edges without running capture.",
    )
    parser.add_argument(
        "--portrait-debug-max",
        type=int,
        default=8,
        help="Maximum portrait edge candidates to draw in --portrait-debug mode.",
    )
    parser.add_argument(
        "--debug-save-dir",
        default="tmp/id_debug",
        help="Directory for explicit debug snapshots saved with s.",
    )
    parser.add_argument(
        "--continue-after-capture",
        action="store_true",
        help="Keep running after a successful capture. Press r to reset in preview mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backend = select_backend()

    if args.list_devices:
        list_devices(backend)
        return 0

    camera = parse_camera(args.camera)
    cap = cv2.VideoCapture(camera, backend) if backend is not None else cv2.VideoCapture(camera)
    if args.width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if args.fps:
        cap.set(cv2.CAP_PROP_FPS, args.fps)

    if not cap.isOpened():
        raise SystemExit(f"Unable to open camera {args.camera!r}")

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    debug_save_dir = Path(args.debug_save_dir)

    processor = CardCaptureProcessor()
    request_state = {
        "request_id": args.request_id,
        "target_type": args.target_type,
        "source": args.source,
        "expires_at": time.time() + 3600.0,
    }
    processor.reset(args.request_id)

    print(
        "Local ID capture test running. "
        "Show an ID in the guide; press q to quit, r to reset."
    )
    print(f"Saving captures to {save_dir}")

    captured_count = 0
    last_status_at = 0.0
    last_candidate: dict[str, Any] | None = None

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("Camera read failed")
                return 1

            if args.portrait_debug:
                debug_info = processor.inspect_portrait_edges(frame, max_candidates=args.portrait_debug_max)
                preview = draw_portrait_debug_preview(frame, debug_info)
                now = time.time()
                if (args.headless or args.debug) and now - last_status_at >= 1.0:
                    print(portrait_debug_status_line(debug_info))
                    last_status_at = now

                if not args.headless:
                    cv2.imshow(WINDOW_NAME, preview)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if key == ord("s"):
                        snapshot_path = save_portrait_debug_snapshot(frame, debug_info, debug_save_dir)
                        print(f"Saved portrait debug snapshot {snapshot_path}")
                continue

            debug_info = processor.debug_frame(frame) if args.debug else None
            candidate = processor._detect_best_candidate(frame)
            if candidate is not None:
                last_candidate = candidate

            result = processor.process_frame(frame, request_state)
            if result:
                captured_count += 1
                image_path = save_capture(result, save_dir, captured_count)
                quality = result.get("metadata", {}).get("quality", {})
                print(f"Captured {image_path}")
                print(json.dumps({"status": result.get("status"), "quality": quality}, indent=2))

                if not args.continue_after_capture:
                    break
                request_state = {
                    **request_state,
                    "request_id": f"{args.request_id}-{captured_count + 1}",
                    "expires_at": time.time() + 3600.0,
                }
                processor.reset(request_state["request_id"])
                last_candidate = None

            preview = draw_preview(frame, candidate, processor, captured_count, debug_info)
            now = time.time()
            if (args.headless or args.debug) and now - last_status_at >= 1.0:
                print(status_line(candidate, processor, debug_info))
                last_status_at = now

            if not args.headless:
                cv2.imshow(WINDOW_NAME, preview)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("r"):
                    request_state = {
                        **request_state,
                        "request_id": f"{args.request_id}-{int(time.time())}",
                        "expires_at": time.time() + 3600.0,
                    }
                    processor.reset(request_state["request_id"])
                    last_candidate = None
                    print(f"Reset request_id={request_state['request_id']}")
    finally:
        cap.release()
        if not args.headless:
            cv2.destroyAllWindows()

    if last_candidate is None and captured_count == 0:
        print("No acceptable card candidate was captured.")
    return 0


def select_backend() -> int | None:
    if platform.system() == "Darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
        return cv2.CAP_AVFOUNDATION
    return None


def parse_camera(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def list_devices(backend: int | None) -> None:
    print("Probing camera indexes 0..9")
    for index in range(10):
        cap = cv2.VideoCapture(index, backend) if backend is not None else cv2.VideoCapture(index)
        try:
            if not cap.isOpened():
                continue
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            print(f"{index}: opened {width}x{height} @ {fps:.2f} fps")
        finally:
            cap.release()


def save_capture(result: dict[str, Any], save_dir: Path, count: int) -> Path:
    metadata = result.get("metadata") or {}
    image_base64 = metadata.get("image_jpeg_base64")
    if not isinstance(image_base64, str) or not image_base64:
        raise RuntimeError("Capture result did not include image_jpeg_base64")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    image_path = save_dir / f"id-capture-{timestamp}-{count:02d}.jpg"
    image_path.write_bytes(base64.b64decode(image_base64))

    metadata_path = image_path.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(
            {
                "request_id": result.get("request_id"),
                "status": result.get("status"),
                "timestamp": result.get("timestamp"),
                "metadata": {
                    key: value
                    for key, value in metadata.items()
                    if key != "image_jpeg_base64"
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return image_path


if __name__ == "__main__":
    raise SystemExit(main())
