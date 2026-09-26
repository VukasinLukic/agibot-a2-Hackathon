"""Phase 2: energy peaks. No surface class and no point proposal."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.sound.clip import SAMPLE_RATE_HZ, AudioClip, read_clip, write_impulse, write_silence
from table_tennis.sound.peaks import energy_peaks


def _ms(duration_ms: float) -> int:
    return int(round(SAMPLE_RATE_HZ * duration_ms / 1000))


def _clip(pieces: list[tuple[int, int]], start_ns: int = 0) -> AudioClip:
    frames: list[tuple[int, ...]] = []
    for count, level in pieces:
        frames.extend((level,) for _ in range(count))
    return AudioClip(
        sample_rate_hz=SAMPLE_RATE_HZ,
        channels=1,
        start_monotonic_ns=start_ns,
        frames=tuple(frames),
    )


class PeakTests(unittest.TestCase):
    def test_silence_has_no_peak_and_one_burst_is_one_peak(self) -> None:
        silence = _clip([(_ms(20), 0)])
        self.assertEqual(energy_peaks(silence, threshold=1e-6), ())

        burst = _clip([(_ms(5), 0), (_ms(1.5), 8_000), (_ms(10), 0)])
        peaks = energy_peaks(burst, threshold=1e-6)
        self.assertEqual(len(peaks), 1)
        time_ns, energy = peaks[0]
        burst_start = burst.sample_time_ns(_ms(5))
        self.assertGreaterEqual(time_ns, burst_start)
        self.assertLess(time_ns, burst.sample_time_ns(_ms(5) + _ms(1.5) + _ms(3)))
        self.assertGreater(energy, 0.0)
        self.assertEqual(energy_peaks(burst, threshold=energy + 1.0), ())

    def test_two_separated_bursts_stay_two_events(self) -> None:
        gap = _ms(30)
        burst = _ms(1.5)
        lead = _ms(5)
        clip = _clip([(lead, 0), (burst, 8_000), (gap, 0), (burst, 8_000), (lead, 0)])
        peaks = energy_peaks(clip, threshold=1e-6)
        self.assertEqual(len(peaks), 2)
        self.assertLess(peaks[0][0], peaks[1][0])
        self.assertGreater(peaks[1][0] - peaks[0][0], 20_000_000)
        collapsed = energy_peaks(clip, threshold=1e-6, min_gap_ns=100_000_000)
        self.assertEqual(len(collapsed), 1)

    def test_written_impulse_file_is_one_peak_and_silence_file_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_impulse(folder / "impulse.wav", 5_000_000, duration_ms=1.5)
            write_silence(folder / "silence.wav", 0, duration_ms=20)
            impulse = read_clip(folder / "impulse.wav")
            silence = read_clip(folder / "silence.wav")
        found = energy_peaks(impulse, threshold=1e-6)
        self.assertEqual(len(found), 1)
        self.assertEqual(energy_peaks(silence, threshold=1e-6), ())


if __name__ == "__main__":
    unittest.main()
