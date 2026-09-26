from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.a2 import A2FisheyeCapture, marks_inside, require_raw_fisheye
from table_tennis.vision.image import BgrImage


class _Reader:
    def __init__(self, images: list[BgrImage]) -> None:
        self._images = list(images)
        self.released = False

    def read(self):
        if not self._images:
            return False, None
        return True, self._images.pop(0)

    def release(self) -> None:
        self.released = True


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000

    def __call__(self) -> int:
        current = self.value
        self.value += 50_000_000
        return current


def _image(fill: tuple[int, int, int]) -> BgrImage:
    image = BgrImage(8, 6)
    image.fill(fill)
    return image


class A2FisheyeTests(unittest.TestCase):
    def test_raw_alias_resolves_and_h264_is_refused(self) -> None:
        left = require_raw_fisheye("CHEST_LEFT_FISHEYE")
        right = require_raw_fisheye("ros2:CHEST_RIGHT_FISHEYE")
        self.assertTrue(left.endswith("/fish_eye_camera/chest_left/color"))
        self.assertTrue(right.endswith("/fish_eye_camera/chest_right/color"))
        with self.assertRaises(ValueError):
            require_raw_fisheye("INTERACTIVE_MAIN")
        with self.assertRaises(ValueError):
            require_raw_fisheye("/aima/hal/fish_eye_camera/chest_left/color/h264")
        self.assertNotIn("rclpy", sys.modules)

    def test_duplicate_read_does_not_advance_sequence(self) -> None:
        first = _image((10, 20, 30))
        second = _image((10, 20, 30))
        third = _image((40, 50, 60))
        reader = _Reader([first, second, third])
        clock = _Clock()
        with A2FisheyeCapture("CHEST_LEFT_FISHEYE", reader=reader, now_ns=clock) as capture:
            frames = list(capture)
        self.assertEqual([frame.frame_seq for frame in frames], [0, 1])
        self.assertEqual(frames[0].origin, "a2_fisheye")
        self.assertEqual(frames[0].camera_id, capture.topic)
        self.assertLess(frames[0].capture_monotonic_ns, frames[1].capture_monotonic_ns)
        self.assertEqual(capture.stats.frames_emitted, 2)
        self.assertEqual(capture.stats.duplicate_frames, 1)
        self.assertTrue(capture.camera_missing)
        self.assertFalse(reader.released)

    def test_table_marks_must_clear_the_edge(self) -> None:
        reader = _Reader([_image((1, 2, 3))])
        with A2FisheyeCapture("CHEST_RIGHT_FISHEYE", reader=reader, now_ns=_Clock(), margin_px=2) as capture:
            self.assertFalse(capture.table_in_frame([(3, 3), (4, 3), (4, 4), (3, 4)]))
            next(iter(capture))
            inside = [(2, 2), (5, 2), (5, 3), (2, 3)]
            self.assertTrue(capture.table_in_frame(inside))
            self.assertFalse(capture.table_in_frame([(1, 2), (5, 2), (5, 3), (2, 3)]))
        self.assertTrue(marks_inside(8, 6, [(2, 2)], 2))
        self.assertFalse(marks_inside(8, 6, [], 2))


if __name__ == "__main__":
    unittest.main()
