"""Offline energy peaks. A peak is not a table, a racket, or a point."""

from __future__ import annotations

import math

from table_tennis.sound.clip import SAMPLE_RATE_HZ, AudioClip

_DEFAULT_CUTOFF_HZ = 2_000.0
_DEFAULT_WINDOW = 48
_DEFAULT_GAP_NS = 5_000_000


def energy_peaks(
    clip: AudioClip,
    threshold: float,
    cutoff_hz: float = _DEFAULT_CUTOFF_HZ,
    window_samples: int = _DEFAULT_WINDOW,
    min_gap_ns: int = _DEFAULT_GAP_NS,
) -> tuple[tuple[int, float], ...]:
    """High-pass, then short energy. Returns ``(time_ns, energy)`` above ``threshold``.

    ``threshold`` has no default: it is chosen per recording. Peaks closer than
    ``min_gap_ns`` stay one event, so a 1–2 ms contact does not split in two.
    """
    _require_args(threshold, cutoff_hz, window_samples, min_gap_ns)
    if clip.sample_rate_hz != SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {SAMPLE_RATE_HZ}")
    filtered = _high_pass(_mono(clip), cutoff_hz, clip.sample_rate_hz)
    energy = _window_energy(filtered, window_samples)
    candidates: list[tuple[int, float]] = []
    last = len(energy) - 1
    for index, value in enumerate(energy):
        if value < threshold:
            continue
        left = energy[index - 1] if index > 0 else float("-inf")
        right = energy[index + 1] if index < last else float("-inf")
        if value >= left and value > right:
            candidates.append((clip.sample_time_ns(index), value))
    return tuple(_merge(candidates, min_gap_ns))


def _mono(clip: AudioClip) -> list[float]:
    scale = 32768.0 * clip.channels
    return [sum(frame) / scale for frame in clip.frames]


def _high_pass(samples: list[float], cutoff_hz: float, sample_rate_hz: int) -> list[float]:
    dt = 1.0 / sample_rate_hz
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    alpha = rc / (rc + dt)
    filtered = [0.0] * len(samples)
    previous_x = 0.0
    previous_y = 0.0
    for index, sample in enumerate(samples):
        current = alpha * (previous_y + sample - previous_x)
        filtered[index] = current
        previous_x = sample
        previous_y = current
    return filtered


def _window_energy(samples: list[float], window_samples: int) -> list[float]:
    half = window_samples // 2
    prefix = [0.0]
    for sample in samples:
        prefix.append(prefix[-1] + sample * sample)
    energy: list[float] = []
    last = len(samples)
    for index in range(last):
        start = max(0, index - half)
        stop = min(last, index + half + 1)
        energy.append(prefix[stop] - prefix[start])
    return energy


def _merge(candidates: list[tuple[int, float]], min_gap_ns: int) -> list[tuple[int, float]]:
    kept: list[tuple[int, float]] = []
    for time_ns, value in candidates:
        if not kept or time_ns - kept[-1][0] >= min_gap_ns:
            kept.append((time_ns, value))
        elif value > kept[-1][1]:
            kept[-1] = (time_ns, value)
    return kept


def _require_args(threshold: float, cutoff_hz: float, window_samples: int, min_gap_ns: int) -> None:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or float(threshold) <= 0:
        raise ValueError("threshold must be a positive number")
    if isinstance(cutoff_hz, bool) or not isinstance(cutoff_hz, (int, float)):
        raise ValueError("cutoff_hz must be below half the sample rate")
    if not 0 < float(cutoff_hz) < SAMPLE_RATE_HZ / 2:
        raise ValueError("cutoff_hz must be below half the sample rate")
    if type(window_samples) is not int or isinstance(window_samples, bool) or window_samples < 1:
        raise ValueError("window_samples must be a positive int")
    if type(min_gap_ns) is not int or isinstance(min_gap_ns, bool) or min_gap_ns < 0:
        raise ValueError("min_gap_ns must be a non-negative int")
