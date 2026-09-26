from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.config import BallColor, Roi, VisionConfig
from table_tennis.vision.frame import ORIGIN_FILE, Frame

# The file name sorts after test_capture/test_frame: they assert cv2 is not imported yet.
HAS_OPENCV = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
needs_opencv = unittest.skipUnless(HAS_OPENCV, "needs cv2 + numpy (table_tennis/requirements-vision.txt)")

BALL = (235, 240, 245)


def _random_weights(seed: int = 0) -> dict[str, object]:
    import numpy as np

    rng = np.random.default_rng(seed)
    shapes = {
        "c0w": (12, 4, 3, 3),
        "c0b": (12,),
        "c1w": (24, 12, 3, 3),
        "c1b": (24,),
        "c2w": (32, 24, 3, 3),
        "c2b": (32,),
        "l0w": (32, 512),
        "l0b": (32,),
        "l1w": (1, 32),
        "l1b": (1,),
    }
    return {key: (rng.standard_normal(shape) * 0.3).astype(np.float32) for key, shape in shapes.items()}


def _reference_probs(weights: dict[str, object], nchw: object) -> object:
    """Torch semantics written plainly: NCHW, zero pad, C-major flatten. float64."""
    import numpy as np

    x = np.asarray(nchw, dtype=np.float64) - 0.5
    for index in range(3):
        kernel = np.asarray(weights[f"c{index}w"], dtype=np.float64)
        bias = np.asarray(weights[f"c{index}b"], dtype=np.float64)
        count, _channels, height, width = x.shape
        padded = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)))
        out = np.zeros((count, kernel.shape[0], height, width))
        for ky in range(3):
            for kx in range(3):
                window = padded[:, :, ky : ky + height, kx : kx + width]
                out += np.einsum("nchw,oc->nohw", window, kernel[:, :, ky, kx])
        out = np.maximum(out + bias[None, :, None, None], 0.0)
        x = out.reshape(count, kernel.shape[0], height // 2, 2, width // 2, 2).max(axis=(3, 5))
    flat = x.reshape(x.shape[0], -1)
    hidden = np.maximum(flat @ np.asarray(weights["l0w"], np.float64).T + np.asarray(weights["l0b"], np.float64), 0.0)
    logit = hidden @ np.asarray(weights["l1w"], np.float64).T + np.asarray(weights["l1b"], np.float64)
    return 1.0 / (1.0 + np.exp(-logit[:, 0]))


@lru_cache(maxsize=4)
def _texture(width: int, height: int, seed: int = 1) -> object:
    import cv2
    import numpy as np

    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 256, (height, width), dtype=np.uint8)
    base = cv2.GaussianBlur(noise, (0, 0), 3)
    base = cv2.normalize(base, None, 40, 170, cv2.NORM_MINMAX)
    return cv2.merge([base, (base * 0.8).astype(np.uint8), (base * 0.6).astype(np.uint8)])


def _scene(t: int, size: tuple[int, int], radius: int, still: tuple[int, int] | None = (150, 200)) -> tuple[object, tuple[float, float]]:
    """Kamera klizi 4, 2 px po kadru; statična bela tačka je deo scene, loptica leti."""
    import cv2

    width, height = size
    world = _texture(width + 200, height + 200)
    if still is not None:
        cv2.circle(world, (still[0] + 100, still[1] + 100), radius, BALL, -1, lineType=cv2.LINE_AA)
    ox, oy = 20 + 4 * t, 20 + 2 * t
    frame = world[oy : oy + height, ox : ox + width].copy()
    ball = (float(width * 0.2 + 18 * t * width / 640), float(height * 0.3 + 5 * t * height / 360))
    cv2.circle(frame, (round(ball[0]), round(ball[1])), radius, BALL, -1, lineType=cv2.LINE_AA)
    return frame, ball


@dataclass(frozen=True)
class _Point:
    """Tracker vidi samo položaj i okvir; bez OpenCV-a."""

    x: float
    y: float
    bw: int
    bh: int


class _Constant:
    """Scorer koji svakom kandidatu da isti skor: bira samo tracker."""

    def __init__(self, value: float) -> None:
        self.value = value
        self.batches: list[int] = []

    def probs(self, patches: object) -> list[float]:
        count = len(patches)  # type: ignore[arg-type]
        self.batches.append(count)
        return [self.value] * count


def _config(**extra: object) -> VisionConfig:
    return VisionConfig(
        camera_id="file-cam",
        origin="file",
        ball=BallColor(None, None),
        min_diameter_px=4,
        max_diameter_px=48,
        roi=extra.pop("roi", None),  # type: ignore[arg-type]
        missing_frames=8,
        **extra,  # type: ignore[arg-type]
    )


@needs_opencv
class BallNetTests(unittest.TestCase):
    def test_numpy_net_matches_a_plain_reference(self) -> None:
        import numpy as np

        from table_tennis.vision.ballnet import BallNet

        weights = _random_weights()
        rng = np.random.default_rng(3)
        nchw = rng.random((7, 4, 32, 32)).astype(np.float32)
        expected = _reference_probs(weights, nchw)
        got = BallNet(weights).probs(nchw.transpose(0, 2, 3, 1))
        self.assertEqual(got.shape, (7,))
        np.testing.assert_allclose(got, expected, atol=1e-5)
        self.assertEqual(BallNet(weights).probs(np.zeros((0, 32, 32, 4), np.float32)).shape, (0,))

    def test_weights_load_from_npz_and_bad_files_fail_at_startup(self) -> None:
        import numpy as np

        from table_tennis.vision.ballnet import load_ballnet

        weights = _random_weights(5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ballnet.npz"
            np.savez(path, **weights)
            net = load_ballnet(path)
            patch = np.full((1, 32, 32, 4), 0.25, np.float32)
            self.assertAlmostEqual(float(net.probs(patch)[0]), float(_reference_probs(weights, patch.transpose(0, 3, 1, 2))[0]), places=5)
            broken = Path(directory) / "broken.npz"
            np.savez(broken, **{key: value for key, value in weights.items() if key != "l1w"})
            with self.assertRaises(ValueError):
                load_ballnet(broken)
            with self.assertRaises(ValueError):
                load_ballnet(Path(directory) / "missing.npz")


@needs_opencv
class CandidateTests(unittest.TestCase):
    def _run(self, compensate: bool) -> tuple[list[object], tuple[float, float], object]:
        from table_tennis.vision.candidates import CandidateParams, CandidateSource

        source = CandidateSource(640, 360, CandidateParams(compensate=compensate))
        found: list[object] = []
        ball = (0.0, 0.0)
        for t in range(4):
            frame, ball = _scene(t, (640, 360), 5)
            found = source.step(frame)
        return found, ball, source

    def test_compensation_suppresses_the_moving_background(self) -> None:
        import math

        found, ball, source = self._run(compensate=True)
        near = [c for c in found if math.hypot(c.x - ball[0], c.y - ball[1]) < 6]  # type: ignore[attr-defined]
        self.assertEqual(len(near), 1)
        # Freshly revealed content at the image edge may stay; the net drops it.
        inside = [c for c in found if 4 < c.x < 636 and 4 < c.y < 356]  # type: ignore[attr-defined]
        self.assertEqual(inside, near)
        patches = source.patches(found)  # type: ignore[attr-defined]
        self.assertEqual(patches.shape, (len(found), 32, 32, 4))
        self.assertGreaterEqual(float(patches.min()), 0.0)
        self.assertLessEqual(float(patches.max()), 1.0)
        # The ball is bright in the middle of its patch, and brighter than the previous frame.
        index = found.index(near[0])
        self.assertGreater(float(patches[index, 16, 16, :3].mean()), 0.8)
        self.assertGreater(float(patches[index, 16, 16, 3]), 0.6)

        raw, _ball, _source = self._run(compensate=False)
        self.assertGreater(len(raw), len(found) + 20)

    def test_roi_limits_candidates(self) -> None:
        from table_tennis.vision.candidates import CandidateSource

        source = CandidateSource(640, 360, roi=(0, 0, 100, 100))
        for t in range(4):
            frame, _ball = _scene(t, (640, 360), 5)
            found = source.step(frame)
        self.assertEqual(found, [])


class TrackerTests(unittest.TestCase):
    def test_flying_ball_beats_a_static_and_a_one_frame_distractor(self) -> None:
        from table_tennis.vision.mht import Tracker

        tracker = Tracker()
        kinds = []
        for t in range(8):
            ball = _Point(100 + 25 * t, 300 - 10 * t, 24, 24)
            still = _Point(500, 100, 24, 24)
            flash = _Point(60 + (150 * t) % 800, 450, 24, 24)
            # The static blob scores higher than the ball, every frame.
            hit = tracker.step(float(t), [still, flash, ball], [0.85, 0.9, 0.8])
            kinds.append(None if hit is None else hit.kind)
            if hit is not None:
                self.assertAlmostEqual(hit.x, ball.x)
                self.assertAlmostEqual(hit.prob, 0.8)
        self.assertIsNone(kinds[0])
        self.assertTrue(all(kind == "observed" for kind in kinds[1:]))
        # A confirmed track survives one weak frame; nothing new would start at this score.
        weak = tracker.step(8.0, [_Point(300, 220, 24, 24)], [0.3])
        self.assertEqual(weak.kind if weak else None, "observed")
        gap = [tracker.step(9.0, [], []), tracker.step(10.0, [], []), tracker.step(11.0, [], [])]
        self.assertEqual([hit.kind if hit else None for hit in gap], ["predicted", "predicted", None])
        self.assertGreater(gap[0].x if gap[0] else 0.0, 300)

    def test_border_and_tiny_blobs_are_not_the_ball(self) -> None:
        from table_tennis.vision.mht import Tracker

        edge = Tracker()
        tiny = Tracker()
        for t in range(6):
            self.assertIsNone(edge.step(float(t), [_Point(3, 100 + 25 * t, 24, 24)], [0.8]))
            self.assertIsNone(tiny.step(float(t), [_Point(300 + 25 * t, 200, 3, 3)], [0.8]))

    def test_params_scale_with_the_working_frame(self) -> None:
        from table_tennis.vision.mht import TrackerParams

        base = TrackerParams()
        half = base.for_frame(480, 270)
        self.assertAlmostEqual(half.gate_new, base.gate_new / 2)
        self.assertAlmostEqual(half.sp_ref, base.sp_ref / 2)
        self.assertAlmostEqual(half.edge_px, base.edge_px / 2)
        self.assertAlmostEqual(half.size_ref, base.size_ref / 2)
        self.assertAlmostEqual(half.grav, base.grav / 2)
        self.assertEqual((half.width, half.height), (480.0, 270.0))
        self.assertEqual(half.thr_new, base.thr_new)
        self.assertEqual(half.gate_speed, base.gate_speed)


@needs_opencv
class PipelineTests(unittest.TestCase):
    def test_ball_tracker_follows_the_moving_disk_in_original_pixels(self) -> None:
        from table_tennis.vision.track import BallTracker

        scorer = _Constant(0.9)
        tracker = BallTracker(_config(work_width_px=640), ballnet=scorer)
        self.assertIsNotNone(tracker.pipeline)
        samples = []
        balls = []
        for t in range(7):
            image, ball = _scene(t, (1280, 720), 10, still=(300, 400))
            balls.append(ball)
            frame = Frame(t, 1_000 + t * 33_333_333, 1280, 720, image, "file-cam", ORIGIN_FILE)
            samples.append(tracker.update(frame))
        self.assertEqual([s.observation_kind for s in samples[:2]], ["missing", "missing"])
        for sample, ball in list(zip(samples, balls))[3:]:
            self.assertEqual(sample.observation_kind, "observed")
            self.assertTrue(sample.detected)
            self.assertAlmostEqual(sample.x_px or 0.0, ball[0], delta=3.0)
            self.assertAlmostEqual(sample.y_px or 0.0, ball[1], delta=3.0)
            self.assertAlmostEqual(sample.confidence, 0.9)
            self.assertFalse(sample.proves_bounce)
        self.assertTrue(scorer.batches)
        assert tracker.pipeline is not None
        self.assertEqual(set(tracker.pipeline.last_ms), {"candidates", "patches", "net", "tracker"})

    def test_roi_outside_the_ball_keeps_the_track_missing(self) -> None:
        from table_tennis.vision.track import BallTracker

        tracker = BallTracker(_config(work_width_px=640, roi=Roi(900, 0, 380, 200)), ballnet=_Constant(0.9))
        kinds = set()
        for t in range(6):
            image, _ball = _scene(t, (1280, 720), 10, still=None)
            kinds.add(tracker.update(Frame(t, t * 33_333_333, 1280, 720, image, "file-cam", ORIGIN_FILE)).observation_kind)
        self.assertEqual(kinds, {"missing"})

    def test_configured_weights_load_and_blurball_is_exclusive(self) -> None:
        import numpy as np

        from table_tennis.vision.track import BallTracker

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ballnet.npz"
            np.savez(path, **_random_weights())
            tracker = BallTracker(_config(ballnet_path=str(path)))
            self.assertIsNotNone(tracker.pipeline)

            class _Fixed:
                def locate(self, _pixels: bytes, _width: int, _height: int) -> tuple[float, float, float]:
                    return (1.0, 1.0, 0.9)

            with self.assertRaises(ValueError):
                BallTracker(_config(ballnet_path=str(path)), model=_Fixed())
        with self.assertRaises(ValueError):
            BallTracker(_config(ballnet_path=str(Path(directory) / "gone.npz")))


class BallNetConfigTests(unittest.TestCase):
    def _mapping(self, **extra: object) -> dict[str, object]:
        data: dict[str, object] = {
            "camera_id": "cam",
            "origin": "file",
            "ball": {"hsv_lower": None, "hsv_upper": None, "min_diameter_px": 4, "max_diameter_px": 10},
            "roi": None,
            "tracker": {"missing_frames": 3},
        }
        data.update(extra)
        return data

    def test_defaults_and_explicit_values(self) -> None:
        from table_tennis.vision import load_example_config

        example = load_example_config()
        self.assertIsNone(example.ballnet_path)
        self.assertEqual(example.work_width_px, 960)
        self.assertTrue(example.compensate_motion)
        bare = VisionConfig.from_mapping(self._mapping())
        self.assertIsNone(bare.ballnet_path)
        self.assertEqual(bare.work_width_px, 960)
        config = VisionConfig.from_mapping(
            self._mapping(
                ballnet_path="weights/ballnet.npz",
                ballnet={"work_width_px": 640, "compensate_motion": False},
            )
        )
        self.assertEqual(config.ballnet_path, "weights/ballnet.npz")
        self.assertEqual(config.work_width_px, 640)
        self.assertFalse(config.compensate_motion)

    def test_rejects_bad_ballnet_values(self) -> None:
        for extra in (
            {"ballnet_path": ""},
            {"ballnet_path": 3},
            {"ballnet_path": "a.npz", "model_path": "b.pt"},
            {"ballnet": [1, 2]},
            {"ballnet": {"work_width_px": 10}},
            {"ballnet": {"work_width_px": "960"}},
            {"ballnet": {"compensate_motion": "yes"}},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                VisionConfig.from_mapping(self._mapping(**extra))

    def test_yaml_reads_the_ballnet_block(self) -> None:
        from table_tennis.vision import load_config

        text = """
camera_id: cam
origin: file
ball:
  hsv_lower: null
  hsv_upper: null
  min_diameter_px: 4
  max_diameter_px: 10
roi: null
tracker:
  missing_frames: 3
ballnet_path: C:/weights/ballnet.npz
ballnet:
  work_width_px: 800
  compensate_motion: false
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vision.yaml"
            path.write_text(text, encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.ballnet_path, "C:/weights/ballnet.npz")
        self.assertEqual(config.work_width_px, 800)
        self.assertFalse(config.compensate_motion)


if __name__ == "__main__":
    unittest.main()
