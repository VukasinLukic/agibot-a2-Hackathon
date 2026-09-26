"""Laptop test path: a phone recording as --clip, video pacing, calibration points."""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytest.importorskip("numpy")

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision import live
from table_tennis.vision.calibrate import parse_points
from table_tennis.vision.capture import ClipWriter, FileCapture
from table_tennis.vision.video import VideoFileCapture


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class ClipSourceTests(unittest.TestCase):
    def test_ttclip_stays_on_file_capture_and_video_goes_to_opencv(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "clip.ttclip"
            with ClipWriter(clip, 4, 4, 33_333_333):
                pass
            video = Path(folder) / "snimak.mov"
            video.write_bytes(b"\x00\x00\x00\x14ftypqt  ")
            self.assertTrue(live.is_ttclip(clip))
            self.assertFalse(live.is_ttclip(video))
            args = argparse.Namespace(clip=clip, device=None, start=0.0)
            self.assertIsInstance(live._open_capture(args, "cam"), FileCapture)
            args.clip = video
            self.assertIsInstance(live._open_capture(args, "cam"), VideoFileCapture)


class VideoPaceTests(unittest.TestCase):
    def test_frames_wait_for_their_moment(self) -> None:
        clock = _Clock()
        capture = VideoFileCapture("x.mp4", "cam", now=clock.now, sleep=clock.sleep)
        anchor = capture._pace(None, 0.0)
        anchor = capture._pace(anchor, 1 / 30)
        self.assertAlmostEqual(clock.slept[-1], 1 / 30)
        self.assertEqual(anchor, 100.0)

    def test_after_a_pause_it_continues_instead_of_rushing(self) -> None:
        clock = _Clock()
        capture = VideoFileCapture("x.mp4", "cam", now=clock.now, sleep=clock.sleep)
        anchor = capture._pace(None, 0.0)
        clock.t += 10.0
        new_anchor = capture._pace(anchor, 1.0)
        self.assertEqual(clock.slept, [])
        self.assertAlmostEqual(new_anchor, clock.t - 1.0)


class CalibrationPointsTests(unittest.TestCase):
    def test_six_points(self) -> None:
        points = parse_points("10,20 30,40 50,60 70,80 90,100 110,120.6")
        self.assertEqual(points[-1], (110, 121))
        self.assertEqual(len(points), 6)

    def test_wrong_count_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            parse_points("10,20 30,40")


if __name__ == "__main__":
    unittest.main()
