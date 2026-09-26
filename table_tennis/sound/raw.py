"""A copy of a microphone block from before echo cancellation.

The clock is ``monotonic_ns`` of the first sample in the block. A block that
has already passed AEC or noise suppression is refused. This module does not
open the ALSA device and does not change ``audio_bridge.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from table_tennis.sound.clip import SAMPLE_RATE_HZ, AudioClip, _validate

_NS_PER_SAMPLE = 1_000_000_000 // SAMPLE_RATE_HZ


@dataclass(frozen=True, slots=True)
class RawBlock:
    """One unprocessed capture block. ``before_aec`` must stay true."""

    start_monotonic_ns: int
    frames: tuple[tuple[int, ...], ...]
    before_aec: bool
    sample_rate_hz: int = SAMPLE_RATE_HZ
    channels: int = 1


def clip_from_raw_blocks(blocks: Sequence[RawBlock]) -> AudioClip:
    """Join contiguous raw blocks into one clip. A gap is not filled in."""
    if not blocks:
        raise ValueError("at least one raw block is required")
    frames: list[tuple[int, ...]] = []
    channels: int | None = None
    start_ns: int | None = None
    expected: int | None = None
    for block in blocks:
        _check(block)
        if channels is None:
            channels = block.channels
            start_ns = block.start_monotonic_ns
        elif block.channels != channels:
            raise ValueError("raw blocks must share a channel count")
        if expected is not None and block.start_monotonic_ns != expected:
            raise ValueError("raw blocks must meet with no gap and no overlap")
        frames.extend(block.frames)
        expected = block.start_monotonic_ns + len(block.frames) * _NS_PER_SAMPLE
    if channels is None or start_ns is None:
        raise ValueError("at least one raw block is required")
    clip = AudioClip(
        sample_rate_hz=SAMPLE_RATE_HZ,
        channels=channels,
        start_monotonic_ns=start_ns,
        frames=tuple(frames),
    )
    _validate(clip)
    return clip


def _check(block: RawBlock) -> None:
    if block.before_aec is not True:
        raise ValueError("only a block from before AEC is a bounce input")
    if block.sample_rate_hz != SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {SAMPLE_RATE_HZ}")
    if type(block.start_monotonic_ns) is not int or isinstance(block.start_monotonic_ns, bool):
        raise ValueError("start_monotonic_ns must be a non-negative int")
    if block.start_monotonic_ns < 0:
        raise ValueError("start_monotonic_ns must be a non-negative int")
    if not block.frames:
        raise ValueError("raw block must contain at least one frame")
