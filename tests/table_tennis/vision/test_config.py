from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision import load_config, load_example_config
from table_tennis.vision.config import VisionConfig


class ExampleConfigTests(unittest.TestCase):
    def test_example_leaves_ball_color_unset(self) -> None:
        config = load_example_config()
        self.assertEqual(config.camera_id, "CHEST_LEFT_FISHEYE")
        self.assertEqual(config.origin, "a2_fisheye")
        self.assertFalse(config.ball.configured)
        self.assertIsNone(config.ball.hsv_lower)
        self.assertIsNone(config.ball.hsv_upper)
        self.assertEqual(config.min_diameter_px, 4)
        self.assertEqual(config.max_diameter_px, 48)
        self.assertIsNone(config.roi)
        self.assertEqual(config.missing_frames, 8)

    def test_accepts_explicit_hsv_and_roi(self) -> None:
        text = """
camera_id: side-cam
origin: file
ball:
  hsv_lower: [10, 80, 80]
  hsv_upper: [25, 255, 255]
  min_diameter_px: 6
  max_diameter_px: 40
roi: [10, 20, 300, 200]
tracker:
  missing_frames: 5
"""
        config = _load_text(text)
        self.assertTrue(config.ball.configured)
        self.assertEqual(config.ball.hsv_lower, (10, 80, 80))
        self.assertIsNotNone(config.roi)
        assert config.roi is not None
        self.assertEqual((config.roi.x, config.roi.y, config.roi.width, config.roi.height), (10, 20, 300, 200))

    def test_rejects_inverted_diameter_and_bad_hsv(self) -> None:
        with self.assertRaises(ValueError):
            VisionConfig.from_mapping(
                {
                    "camera_id": "cam",
                    "origin": "file",
                    "ball": {
                        "hsv_lower": None,
                        "hsv_upper": None,
                        "min_diameter_px": 20,
                        "max_diameter_px": 4,
                    },
                    "roi": None,
                    "tracker": {"missing_frames": 3},
                }
            )
        with self.assertRaises(ValueError):
            _load_text(
                """
camera_id: cam
origin: file
ball:
  hsv_lower: [200, 0, 0]
  hsv_upper: [10, 10, 10]
  min_diameter_px: 4
  max_diameter_px: 10
roi: null
tracker:
  missing_frames: 3
"""
            )


def _load_text(text: str) -> VisionConfig:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "vision.yaml"
        path.write_text(text, encoding="utf-8")
        return load_config(path)


if __name__ == "__main__":
    unittest.main()
