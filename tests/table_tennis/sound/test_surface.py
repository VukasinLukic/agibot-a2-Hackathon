"""Phase 4: three synthetic contacts, and noise that stays quiet."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.sound.clip import SAMPLE_RATE_HZ, AudioClip
from table_tennis.sound.surface import classify_contact

_MIN_RMS = 0.05


def _tone(frequency_hz: float, duration_ms: float, amplitude: int) -> tuple[AudioClip, int]:
    pad = int(round(SAMPLE_RATE_HZ * 0.01))
    count = int(round(SAMPLE_RATE_HZ * duration_ms / 1000))
    frames: list[tuple[int, ...]] = [(0,)] * pad
    for index in range(count):
        sample = int(amplitude * math.sin(2.0 * math.pi * frequency_hz * index / SAMPLE_RATE_HZ))
        frames.append((max(-32768, min(32767, sample)),))
    frames.extend((0,) for _ in range(pad))
    clip = AudioClip(
        sample_rate_hz=SAMPLE_RATE_HZ,
        channels=1,
        start_monotonic_ns=0,
        frames=tuple(frames),
    )
    middle = pad + count // 2
    return clip, clip.sample_time_ns(middle)


class SurfaceTests(unittest.TestCase):
    def test_three_tones_differ_and_noise_stays_quiet(self) -> None:
        table, table_ns = _tone(7_000, 1.0, 12_000)
        racket, racket_ns = _tone(2_200, 1.5, 12_000)
        floor, floor_ns = _tone(300, 8.0, 12_000)
        self.assertEqual(classify_contact(table, table_ns, _MIN_RMS), "table")
        self.assertEqual(classify_contact(racket, racket_ns, _MIN_RMS), "racket")
        self.assertEqual(classify_contact(floor, floor_ns, _MIN_RMS), "floor")
        self.assertEqual(len({
            classify_contact(table, table_ns, _MIN_RMS),
            classify_contact(racket, racket_ns, _MIN_RMS),
            classify_contact(floor, floor_ns, _MIN_RMS),
        }), 3)

        quiet, quiet_ns = _tone(1_000, 5.0, 40)
        self.assertEqual(classify_contact(quiet, quiet_ns, _MIN_RMS), "abstain")
        long_bright, long_ns = _tone(6_000, 15.0, 12_000)
        self.assertEqual(classify_contact(long_bright, long_ns, _MIN_RMS), "abstain")


if __name__ == "__main__":
    unittest.main()
