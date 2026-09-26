"""Render short flash-clips (time/weather readouts) for the head screen.

The head screen has no "draw text" API -- the only way to show custom content
is a video file (see visual_ui_controller.play_video_file). This renders a
tiny clip on demand; nothing here is meant to be kept or reused across calls.
"""

from __future__ import annotations

import cv2
import numpy as np

DEFAULT_OUTPUT_PATH = "/tmp/x2_qa_display.mp4"
DEFAULT_DURATION_S = 2.0
DEFAULT_FPS = 15
# Confirmed from the live flutter-pi process args on PC3: `--dimensions 800,480
# --orientation landscape_right`.
FRAME_WIDTH = 800
FRAME_HEIGHT = 480


def render_flash_video(
    primary_text: str,
    secondary_text: str = "",
    *,
    duration_s: float = DEFAULT_DURATION_S,
    out_path: str = DEFAULT_OUTPUT_PATH,
    fps: int = DEFAULT_FPS,
) -> str:
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)

    if secondary_text:
        _draw_centered_text(frame, secondary_text, y=FRAME_HEIGHT // 3, scale=1.0, thickness=2)
    _draw_centered_text(frame, primary_text, y=FRAME_HEIGHT // 2 + 20, scale=2.2, thickness=4)

    writer = cv2.VideoWriter(
        out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (FRAME_WIDTH, FRAME_HEIGHT)
    )
    try:
        for _ in range(max(1, int(duration_s * fps))):
            writer.write(frame)
    finally:
        writer.release()

    return out_path


def _draw_centered_text(frame: np.ndarray, text: str, *, y: int, scale: float, thickness: int) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    (text_width, _text_height), _baseline = cv2.getTextSize(text, font, scale, thickness)
    x = max(0, (FRAME_WIDTH - text_width) // 2)
    cv2.putText(frame, text, (x, y), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)
