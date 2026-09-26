"""Phase 6: a raw block keeps its start clock and refuses processed audio."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.sound.clip import SAMPLE_RATE_HZ, sample_offset_ns
from table_tennis.sound.raw import RawBlock, clip_from_raw_blocks


def _block(start_ns: int, count: int, level: int, *, before_aec: bool = True, rate: int = SAMPLE_RATE_HZ) -> RawBlock:
    return RawBlock(
        start_monotonic_ns=start_ns,
        frames=tuple((level,) for _ in range(count)),
        before_aec=before_aec,
        sample_rate_hz=rate,
    )


class RawBlockTests(unittest.TestCase):
    def test_contiguous_blocks_keep_the_first_clock(self) -> None:
        first = _block(1_000, 10, 1000)
        second = _block(1_000 + sample_offset_ns(10), 5, 2000)
        clip = clip_from_raw_blocks((first, second))
        self.assertEqual(clip.start_monotonic_ns, 1_000)
        self.assertEqual(clip.sample_rate_hz, SAMPLE_RATE_HZ)
        self.assertEqual(len(clip.frames), 15)
        self.assertEqual(clip.sample_time_ns(10), second.start_monotonic_ns)
        self.assertTrue(clip.times_increase())
        self.assertNotIn("sounddevice", sys.modules)

    def test_processed_audio_and_a_gap_are_refused(self) -> None:
        processed = _block(0, 4, 1000, before_aec=False)
        with self.assertRaises(ValueError):
            clip_from_raw_blocks((processed,))
        wrong_rate = _block(0, 4, 1000, rate=44_100)
        with self.assertRaises(ValueError):
            clip_from_raw_blocks((wrong_rate,))
        first = _block(0, 10, 1000)
        gapped = _block(sample_offset_ns(10) + 1, 4, 1000)
        with self.assertRaises(ValueError):
            clip_from_raw_blocks((first, gapped))


if __name__ == "__main__":
    unittest.main()
