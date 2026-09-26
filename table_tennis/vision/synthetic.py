"""A moving white circle on a dark field. This checks the pipe, not the referee."""

from __future__ import annotations

from pathlib import Path

from table_tennis.vision.capture import ClipWriter
from table_tennis.vision.image import BgrImage

BACKGROUND = (40, 50, 30)
BALL = (235, 235, 235)


def write_moving_circle(
    path: Path | str,
    *,
    width: int = 64,
    height: int = 48,
    frames: int = 8,
    period_ns: int = 50_000_000,
    radius: int = 4,
) -> None:
    if type(frames) is not int or frames <= 0:
        raise ValueError("frames must be a positive int")
    if type(radius) is not int or radius <= 0:
        raise ValueError("radius must be a positive int")
    with ClipWriter(path, width, height, period_ns) as writer:
        for index in range(frames):
            image = BgrImage(width, height)
            image.fill(BACKGROUND)
            center_x = radius + 1 + index * max((width - 2 * radius - 2) // max(frames - 1, 1), 1)
            center_y = height // 2
            _fill_circle(image, center_x, center_y, radius, BALL)
            writer.write_frame(image)


def _fill_circle(
    image: BgrImage,
    center_x: int,
    center_y: int,
    radius: int,
    bgr: tuple[int, int, int],
) -> None:
    radius_sq = radius * radius
    for y in range(max(center_y - radius, 0), min(center_y + radius + 1, image.height)):
        for x in range(max(center_x - radius, 0), min(center_x + radius + 1, image.width)):
            dx = x - center_x
            dy = y - center_y
            if dx * dx + dy * dy <= radius_sq:
                image.set(x, y, bgr)
