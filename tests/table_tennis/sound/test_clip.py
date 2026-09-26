"""Phase 1: a laptop wav plus a clock. No hit decision."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.sound.clip import (
    SAMPLE_RATE_HZ,
    clock_path,
    read_clip,
    write_impulse,
    write_silence,
)


class ClipTests(unittest.TestCase):
    def test_impulse_and_silence_round_trip_and_time_increases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            impulse_path = folder / "impulse.wav"
            silence_path = folder / "silence.wav"
            impulse = write_impulse(impulse_path, start_monotonic_ns=1_000_000_000, channels=2, duration_ms=1.5)
            silence = write_silence(silence_path, start_monotonic_ns=2_000_000_000, channels=2, duration_ms=20)
            self.assertEqual(impulse.sample_rate_hz, SAMPLE_RATE_HZ)
            self.assertGreaterEqual(len(impulse.frames), SAMPLE_RATE_HZ // 1000)
            self.assertLessEqual(len(impulse.frames), SAMPLE_RATE_HZ // 500)
            self.assertTrue(all(sample == 8_000 for frame in impulse.frames for sample in frame))
            self.assertTrue(all(sample == 0 for frame in silence.frames for sample in frame))
            self.assertNotEqual(impulse_path, silence_path)

            loaded_impulse = read_clip(impulse_path)
            loaded_silence = read_clip(silence_path)
            self.assertEqual(loaded_impulse, impulse)
            self.assertEqual(loaded_silence, silence)
            self.assertTrue(loaded_impulse.times_increase())
            self.assertTrue(loaded_silence.times_increase())
            self.assertLess(loaded_impulse.sample_time_ns(0), loaded_impulse.sample_time_ns(1))
            self.assertTrue(clock_path(impulse_path).is_file())

    def test_impulse_outside_one_to_two_ms_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.wav"
            with self.assertRaises(ValueError):
                write_impulse(path, 0, duration_ms=0.5)
            with self.assertRaises(ValueError):
                write_impulse(path, 0, duration_ms=3)

    def test_missing_clock_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "impulse.wav"
            write_impulse(path, 10)
            clock_path(path).unlink()
            with self.assertRaises(ValueError):
                read_clip(path)


if __name__ == "__main__":
    unittest.main()
