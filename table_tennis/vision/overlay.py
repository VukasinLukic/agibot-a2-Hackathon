"""Stamp frame number and fps onto a copy. This is not part of capture."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

from table_tennis.vision.capture import ClipWriter
from table_tennis.vision.frame import Frame
from table_tennis.vision.image import BgrImage

_DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}
INK = (255, 255, 255)


def write_overlay_clip(frames: Iterable[Frame], path: Path | str) -> int:
    """Write a new clip with the sequence and fps burned into the corner.

    One frame is held so the fps label can come from the first real interval.
    The capture loop itself never calls this.
    """
    stream = iter(frames)
    try:
        first = next(stream)
    except StopIteration:
        return 0
    try:
        second = next(stream)
    except StopIteration:
        _write_stamped(path, [first], period_ns=1_000_000_000, fps=0)
        return 1
    period_ns = second.capture_monotonic_ns - first.capture_monotonic_ns
    if period_ns <= 0:
        raise ValueError("overlay timestamps must increase")
    return _write_stamped(path, _chain(first, second, stream), period_ns=period_ns, fps=_fps(period_ns))


def _chain(first: Frame, second: Frame, rest: Iterator[Frame]) -> Iterator[Frame]:
    yield first
    yield second
    yield from rest


def _write_stamped(path: Path | str, frames: Iterable[Frame], *, period_ns: int, fps: int) -> int:
    written = 0
    writer: ClipWriter | None = None
    previous_ns: int | None = None
    try:
        for frame in frames:
            image = frame.image
            if not isinstance(image, BgrImage):
                raise ValueError("overlay expects a BgrImage")
            if previous_ns is not None and frame.capture_monotonic_ns <= previous_ns:
                raise ValueError("overlay timestamps must increase")
            if writer is None:
                writer = ClipWriter(path, frame.width, frame.height, period_ns).__enter__()
            writer.write_frame(_stamp(image, frame.frame_seq, fps))
            previous_ns = frame.capture_monotonic_ns
            written += 1
    finally:
        if writer is not None:
            writer.__exit__(None, None, None)
    return written


def _fps(period_ns: int) -> int:
    return int(round(1_000_000_000 / period_ns))


def _stamp(source: BgrImage, frame_seq: int, fps: int) -> BgrImage:
    image = source.copy()
    _draw_text(image, 1, 1, str(frame_seq))
    _draw_text(image, 1, 8, str(fps))
    return image


def _draw_text(image: BgrImage, x: int, y: int, text: str) -> None:
    cursor = x
    for char in text:
        glyph = _DIGITS.get(char)
        if glyph is None:
            continue
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "1":
                    px = cursor + col
                    py = y + row
                    if 0 <= px < image.width and 0 <= py < image.height:
                        image.set(px, py, INK)
        cursor += 4
