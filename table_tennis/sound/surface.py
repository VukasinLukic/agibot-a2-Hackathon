"""Label one contact from its length and brightness. Unclear sound stays quiet.

The three bands separate synthetic tones. They are not a calibration taken
from a robot recording, and they do not propose a point.
"""

from __future__ import annotations

import math

from table_tennis.sound.clip import SAMPLE_RATE_HZ, AudioClip

_SHORT_MS = 3.0
_LONG_MS = 4.0
_BRIGHT_HZ = 5_500.0
_DULL_HZ = 1_500.0
_ENVELOPE = 96


def classify_contact(clip: AudioClip, time_ns: int, min_rms: float) -> str:
    """Return ``table``, ``racket``, ``floor``, or ``abstain``."""
    if clip.sample_rate_hz != SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {SAMPLE_RATE_HZ}")
    if type(time_ns) is not int or isinstance(time_ns, bool) or time_ns < 0:
        raise ValueError("time_ns must be a non-negative int")
    if isinstance(min_rms, bool) or not isinstance(min_rms, (int, float)) or float(min_rms) <= 0:
        raise ValueError("min_rms must be a positive number")
    index = _nearest_index(clip, time_ns)
    rms, duration_ms, centroid_hz = _measures(clip, index)
    if rms < float(min_rms):
        return "abstain"
    if duration_ms <= _SHORT_MS and centroid_hz >= _BRIGHT_HZ:
        return "table"
    if duration_ms <= _SHORT_MS and _DULL_HZ <= centroid_hz < _BRIGHT_HZ:
        return "racket"
    if duration_ms >= _LONG_MS and centroid_hz < _DULL_HZ:
        return "floor"
    return "abstain"


def _nearest_index(clip: AudioClip, time_ns: int) -> int:
    count = len(clip.frames)
    if count == 0:
        raise ValueError("clip must contain at least one frame")
    low = 0
    high = count - 1
    while low < high:
        mid = (low + high) // 2
        if clip.sample_time_ns(mid) < time_ns:
            low = mid + 1
        else:
            high = mid
    if low == 0:
        return 0
    previous = low - 1
    if abs(clip.sample_time_ns(previous) - time_ns) <= abs(clip.sample_time_ns(low) - time_ns):
        return previous
    return low


def _measures(clip: AudioClip, index: int) -> tuple[float, float, float]:
    half = int(0.02 * SAMPLE_RATE_HZ)
    start = max(0, index - half)
    stop = min(len(clip.frames), index + half)
    segment = _mono(clip, start, stop)
    if not segment:
        return 0.0, 0.0, 0.0
    rms = math.sqrt(sum(sample * sample for sample in segment) / len(segment))
    envelope = _envelope(segment)
    if not envelope:
        return rms, 0.0, 0.0
    peak = max(envelope)
    if peak <= 0:
        return rms, 0.0, 0.0
    center = min(range(len(envelope)), key=lambda bin_index: abs((start + bin_index * _ENVELOPE) - index))
    low = center
    high = center
    floor = 0.25 * peak
    while low > 0 and envelope[low - 1] >= floor:
        low -= 1
    while high + 1 < len(envelope) and envelope[high + 1] >= floor:
        high += 1
    duration_ms = (high - low + 1) * _ENVELOPE / SAMPLE_RATE_HZ * 1000
    loud_start = low * _ENVELOPE
    loud_stop = min(len(segment), (high + 1) * _ENVELOPE)
    centroid_hz = _centroid(segment[loud_start:loud_stop])
    return rms, duration_ms, centroid_hz


def _mono(clip: AudioClip, start: int, stop: int) -> list[float]:
    scale = 32768.0 * clip.channels
    return [sum(clip.frames[index]) / scale for index in range(start, stop)]


def _envelope(samples: list[float]) -> list[float]:
    bins: list[float] = []
    for offset in range(0, len(samples) - _ENVELOPE + 1, _ENVELOPE):
        window = samples[offset : offset + _ENVELOPE]
        bins.append(sum(sample * sample for sample in window) / _ENVELOPE)
    return bins


def _centroid(samples: list[float]) -> float:
    count = min(len(samples), 512)
    if count < 16:
        return 0.0
    window = samples[:count]
    mean = sum(window) / count
    centered = []
    for sample_index, sample in enumerate(window):
        hann = 0.5 - 0.5 * math.cos(2.0 * math.pi * sample_index / max(count - 1, 1))
        centered.append((sample - mean) * hann)
    weighted = 0.0
    total = 0.0
    for bin_index in range(1, count // 2):
        real = 0.0
        imag = 0.0
        for sample_index, sample in enumerate(centered):
            angle = 2.0 * math.pi * bin_index * sample_index / count
            real += sample * math.cos(angle)
            imag -= sample * math.sin(angle)
        magnitude = math.hypot(real, imag)
        weighted += (bin_index * SAMPLE_RATE_HZ / count) * magnitude
        total += magnitude
    if total == 0.0:
        return 0.0
    return weighted / total
