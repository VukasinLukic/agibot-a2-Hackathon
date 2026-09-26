from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.config import BallColor, VisionConfig
from table_tennis.vision.frame import ORIGIN_FILE, Frame
from table_tennis.vision.image import BgrImage
from table_tennis.vision.track import BallTracker, mark_track, write_track_csv

WHITE = (240, 240, 240)
DARK = (30, 40, 20)


def _config(missing: int = 2) -> VisionConfig:
    return VisionConfig(
        camera_id="file-cam",
        origin="file",
        ball=BallColor((0, 0, 200), (179, 40, 255)),
        min_diameter_px=4,
        max_diameter_px=12,
        roi=None,
        missing_frames=missing,
    )


def _frame(seq: int, image: BgrImage) -> Frame:
    return Frame(seq, seq * 50_000_000, image.width, image.height, image, "file-cam", ORIGIN_FILE)


def _paint(width: int, height: int, squares: list[tuple[int, int, int, tuple[int, int, int]]]) -> BgrImage:
    image = BgrImage(width, height)
    image.fill(DARK)
    for x, y, size, color in squares:
        for py in range(y, y + size):
            for px in range(x, x + size):
                image.set(px, py, color)
    return image


class TrackTests(unittest.TestCase):
    def test_import_does_not_pull_a_model(self) -> None:
        import ast

        import table_tennis.vision.track as track

        tree = ast.parse(Path(track.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
                self.assertNotIn("cv2", names)
                self.assertNotIn("ultralytics", names)
                self.assertNotIn("rclpy", names)
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, {"cv2", "ultralytics", "rclpy"})
        self.assertNotIn("ultralytics", sys.modules)
        self.assertNotIn("rclpy", sys.modules)

    def test_refuses_an_unset_ball_color(self) -> None:
        config = VisionConfig(
            camera_id="file-cam",
            origin="file",
            ball=BallColor(None, None),
            min_diameter_px=4,
            max_diameter_px=12,
            roi=None,
            missing_frames=2,
        )
        with self.assertRaises(ValueError):
            BallTracker(config)

    def test_motion_keeps_the_ball_and_drops_a_static_blob(self) -> None:
        tracker = BallTracker(_config())
        still = _paint(80, 60, [(10, 40, 6, WHITE)])
        first = _paint(80, 60, [(10, 40, 6, WHITE), (30, 20, 6, WHITE)])
        second = _paint(80, 60, [(10, 40, 6, WHITE), (42, 20, 6, WHITE)])
        self.assertEqual(tracker.update(_frame(0, still)).observation_kind, "missing")
        appeared = tracker.update(_frame(1, first))
        self.assertEqual(appeared.observation_kind, "observed")
        self.assertLess(appeared.x_px or 99, 40)
        seen = tracker.update(_frame(2, second))
        self.assertEqual(seen.observation_kind, "observed")
        self.assertGreater(seen.x_px or 0, 40)
        self.assertLess(seen.y_px or 99, 30)
        self.assertFalse(seen.proves_bounce)
        self.assertTrue(seen.detected)

    def test_two_movers_stay_ambiguous_and_a_large_blob_is_not_the_ball(self) -> None:
        tracker = BallTracker(_config())
        both = _paint(80, 60, [(8, 8, 6, WHITE), (50, 8, 6, WHITE)])
        shifted = _paint(80, 60, [(16, 8, 6, WHITE), (58, 8, 6, WHITE)])
        tracker.update(_frame(0, both))
        ambiguous = tracker.update(_frame(1, shifted))
        self.assertEqual(ambiguous.observation_kind, "missing")

        wide = BallTracker(_config())
        before = _paint(80, 60, [(4, 30, 20, WHITE), (50, 10, 6, WHITE)])
        after = _paint(80, 60, [(8, 30, 20, WHITE), (58, 10, 6, WHITE)])
        wide.update(_frame(0, before))
        seen = wide.update(_frame(1, after))
        self.assertEqual(seen.observation_kind, "observed")
        self.assertGreater(seen.x_px or 0, 50)

    def test_gap_is_predicted_then_missing_and_does_not_score(self) -> None:
        tracker = BallTracker(_config(missing=2))
        frames = [
            _paint(80, 60, [(10, 20, 6, WHITE)]),
            _paint(80, 60, [(18, 20, 6, WHITE)]),
            _paint(80, 60, [(26, 20, 6, WHITE)]),
            _paint(80, 60, []),
            _paint(80, 60, []),
        ]
        samples = [tracker.update(_frame(index, image)) for index, image in enumerate(frames)]
        self.assertEqual([sample.observation_kind for sample in samples], ["missing", "observed", "observed", "predicted", "missing"])
        predicted = samples[3]
        self.assertIsNotNone(predicted.x_px)
        self.assertFalse(predicted.proves_bounce)
        self.assertFalse(predicted.detected)
        self.assertGreater(predicted.x_px or 0, samples[2].x_px or 0)
        self.assertIsNone(samples[4].x_px)
        marked = mark_track(frames[2], samples[2])
        self.assertNotEqual(marked.get(int(round(samples[2].x_px or 0)) + 4, int(round(samples[2].y_px or 0))), WHITE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "track.csv"
            write_track_csv(path, samples)
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(rows[3]["observation_kind"], "predicted")
        self.assertEqual(rows[4]["x_px"], "")
        self.assertEqual(rows[3]["proves_bounce"], "False")

    def test_numpy_frame_uses_the_same_tracker(self) -> None:
        import numpy as np

        first = _paint(80, 60, [(10, 20, 6, WHITE)])
        second = _paint(80, 60, [(18, 20, 6, WHITE)])
        array = np.frombuffer(bytes(second.data), dtype=np.uint8).reshape(second.height, second.width, 3).copy()
        tracker = BallTracker(_config())
        tracker.update(_frame(0, first))
        sample = tracker.update(Frame(1, 50_000_000, array.shape[1], array.shape[0], array, "file-cam", ORIGIN_FILE))
        self.assertEqual(sample.observation_kind, "observed")
        observation = sample.as_observation()
        self.assertEqual(observation["observation_kind"], "observed")
        self.assertFalse(sample.proves_bounce)

    def test_live_search_requires_the_table(self) -> None:
        from table_tennis.vision.events import MatchVisionProducer

        with self.assertRaises(ValueError):
            MatchVisionProducer([], BallTracker(_config()), object())


if __name__ == "__main__":
    unittest.main()
