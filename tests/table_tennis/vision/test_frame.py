from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision import Frame


class _Image:
    def __init__(self, height: int, width: int, channels: int = 3) -> None:
        self.shape = (height, width, channels)


class FrameTests(unittest.TestCase):
    def test_accepts_file_and_a2_origins(self) -> None:
        image = _Image(480, 640)
        file_frame = Frame(0, 0, 640, 480, image, "laptop-file", "file")
        robot_frame = Frame(1, 1_000_000, 640, 480, image, "a2-interactive-main", "a2_h264")
        self.assertEqual(file_frame.frame_seq, 0)
        self.assertEqual(robot_frame.origin, "a2_h264")

    def test_rejects_shape_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            Frame(1, 10, 100, 80, _Image(480, 640), "cam", "file")

    def test_rejects_non_bgr_image(self) -> None:
        with self.assertRaises(ValueError):
            Frame(1, 10, 2, 2, _Image(2, 2, 1), "cam", "file")

    def test_rejects_negative_sequence_and_bool(self) -> None:
        image = _Image(2, 2)
        with self.assertRaises(ValueError):
            Frame(-1, 0, 2, 2, image, "cam", "file")
        with self.assertRaises(ValueError):
            Frame(True, 0, 2, 2, image, "cam", "file")

    def test_rejects_unknown_origin_and_blank_camera(self) -> None:
        image = _Image(2, 2)
        with self.assertRaises(ValueError):
            Frame(1, 0, 2, 2, image, "cam", "usb")
        with self.assertRaises(ValueError):
            Frame(1, 0, 2, 2, image, "  ", "file")

    def test_import_does_not_pull_robot_stack(self) -> None:
        self.assertNotIn("rclpy", sys.modules)
        self.assertNotIn("ultralytics", sys.modules)
        self.assertNotIn("cv2", sys.modules)


if __name__ == "__main__":
    unittest.main()
