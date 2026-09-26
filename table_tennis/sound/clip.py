"""Laptop bounce recording. A wav file plus the clock of its first sample.

Reading checks that each later sample is later in time. Nothing here decides
that a sample is a table hit.
"""

from __future__ import annotations

import array
import json
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE_HZ = 48_000
_MAX_CHANNELS = 8
_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class AudioClip:
    """16-bit PCM. ``frames[i][channel]`` is one sample."""

    sample_rate_hz: int
    channels: int
    start_monotonic_ns: int
    frames: tuple[tuple[int, ...], ...]

    def sample_time_ns(self, index: int) -> int:
        if type(index) is not int or isinstance(index, bool) or index < 0 or index >= len(self.frames):
            raise ValueError("sample index is outside the clip")
        return self.start_monotonic_ns + sample_offset_ns(index, self.sample_rate_hz)

    def times_increase(self) -> bool:
        if len(self.frames) < 2:
            return True
        previous = self.sample_time_ns(0)
        for index in range(1, len(self.frames)):
            current = self.sample_time_ns(index)
            if current <= previous:
                return False
            previous = current
        return True


def sample_offset_ns(index: int, sample_rate_hz: int = SAMPLE_RATE_HZ) -> int:
    """Nanoseconds from the first sample to sample ``index``. Truncates less than one nanosecond."""
    if type(index) is not int or isinstance(index, bool) or index < 0:
        raise ValueError("sample index must be a non-negative int")
    if type(sample_rate_hz) is not int or isinstance(sample_rate_hz, bool) or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be a positive int")
    return (index * _NS_PER_SECOND) // sample_rate_hz


def validate_clip(clip: AudioClip) -> None:
    """Refuse a clip that is not 16-bit PCM at the bounce sample rate."""
    _validate(clip)


def write_clip(path: Path | str, clip: AudioClip) -> None:
    """Write a 16-bit wav and a sibling clock file with the first sample time."""
    target = _wav_path(path)
    _validate(clip)
    pcm = array.array("h", (sample for frame in clip.frames for sample in frame))
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(clip.channels)
        handle.setsampwidth(2)
        handle.setframerate(clip.sample_rate_hz)
        handle.writeframes(pcm.tobytes())
    clock_path(target).write_text(
        json.dumps(
            {
                "start_monotonic_ns": clip.start_monotonic_ns,
                "sample_rate_hz": clip.sample_rate_hz,
            }
        ),
        encoding="utf-8",
    )


def read_clip(path: Path | str) -> AudioClip:
    """Read a clip written by ``write_clip``. A missing clock is refused."""
    target = _wav_path(path)
    clock_file = clock_path(target)
    if not clock_file.is_file():
        raise ValueError(f"clip clock not found: {clock_file.name}")
    clock = json.loads(clock_file.read_text(encoding="utf-8"))
    if not isinstance(clock, dict):
        raise ValueError("clip clock must be a mapping")
    start_ns = clock.get("start_monotonic_ns")
    rate = clock.get("sample_rate_hz")
    if type(start_ns) is not int or isinstance(start_ns, bool) or start_ns < 0:
        raise ValueError("start_monotonic_ns must be a non-negative int")
    if type(rate) is not int or isinstance(rate, bool):
        raise ValueError("sample_rate_hz must be an int")
    with wave.open(str(target), "rb") as handle:
        channels = handle.getnchannels()
        if handle.getsampwidth() != 2:
            raise ValueError("clip must be 16-bit PCM")
        if handle.getframerate() != rate:
            raise ValueError("wav rate does not match the clock file")
        raw = handle.readframes(handle.getnframes())
    if rate != SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {SAMPLE_RATE_HZ}")
    pcm = array.array("h")
    pcm.frombytes(raw)
    if sys.byteorder != "little":
        pcm.byteswap()
    if channels < 1 or len(pcm) % channels != 0:
        raise ValueError("wav frame count does not match the channel count")
    frames = tuple(
        tuple(int(pcm[offset + channel]) for channel in range(channels))
        for offset in range(0, len(pcm), channels)
    )
    clip = AudioClip(sample_rate_hz=rate, channels=channels, start_monotonic_ns=start_ns, frames=frames)
    if not clip.times_increase() and len(frames) > 1:
        raise ValueError("sample times do not increase")
    return clip


def write_impulse(
    path: Path | str,
    start_monotonic_ns: int,
    channels: int = 1,
    duration_ms: float = 1.5,
) -> AudioClip:
    """A synthetic burst of 1–2 ms. This does not label the burst as a hit."""
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        raise ValueError("impulse duration must be 1 to 2 ms")
    if not 1 <= float(duration_ms) <= 2:
        raise ValueError("impulse duration must be 1 to 2 ms")
    count = int(round(SAMPLE_RATE_HZ * float(duration_ms) / 1000))
    frame = tuple(8_000 for _ in range(channels))
    clip = AudioClip(
        sample_rate_hz=SAMPLE_RATE_HZ,
        channels=channels,
        start_monotonic_ns=start_monotonic_ns,
        frames=tuple(frame for _ in range(count)),
    )
    write_clip(path, clip)
    return clip


def write_silence(
    path: Path | str,
    start_monotonic_ns: int,
    channels: int = 1,
    duration_ms: float = 20,
) -> AudioClip:
    """A separate all-zero file. It is not an impulse."""
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or float(duration_ms) <= 0:
        raise ValueError("silence duration must be positive")
    count = int(round(SAMPLE_RATE_HZ * float(duration_ms) / 1000))
    if count < 1:
        raise ValueError("silence duration must cover at least one sample")
    frame = tuple(0 for _ in range(channels))
    clip = AudioClip(
        sample_rate_hz=SAMPLE_RATE_HZ,
        channels=channels,
        start_monotonic_ns=start_monotonic_ns,
        frames=tuple(frame for _ in range(count)),
    )
    write_clip(path, clip)
    return clip


def clock_path(wav_path: Path) -> Path:
    return wav_path.with_name(wav_path.name + ".clock.json")


def _wav_path(path: Path | str) -> Path:
    target = Path(path)
    if target.suffix.lower() != ".wav":
        raise ValueError("clip path must end in .wav")
    return target


def _validate(clip: AudioClip) -> None:
    if clip.sample_rate_hz != SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {SAMPLE_RATE_HZ}")
    if type(clip.channels) is not int or isinstance(clip.channels, bool):
        raise ValueError("channels must be an int")
    if not 1 <= clip.channels <= _MAX_CHANNELS:
        raise ValueError(f"channels must be from 1 to {_MAX_CHANNELS}")
    if type(clip.start_monotonic_ns) is not int or isinstance(clip.start_monotonic_ns, bool):
        raise ValueError("start_monotonic_ns must be a non-negative int")
    if clip.start_monotonic_ns < 0:
        raise ValueError("start_monotonic_ns must be a non-negative int")
    if not clip.frames:
        raise ValueError("clip must contain at least one frame")
    for frame in clip.frames:
        if len(frame) != clip.channels:
            raise ValueError("every frame must have one sample per channel")
        for sample in frame:
            if type(sample) is not int or isinstance(sample, bool) or sample < -32768 or sample > 32767:
                raise ValueError("samples must be 16-bit ints")
    if not clip.times_increase():
        raise ValueError("sample times do not increase")
