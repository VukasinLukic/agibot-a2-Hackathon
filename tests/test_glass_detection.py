"""Contract tests for the LiDAR glass detector (a2_costmap.GlassDetector).

WHY THESE EXIST
---------------
The detector is a pile of tunable thresholds, and every one of them is there to
reject a specific false positive that was observed for real. Nudging a default
to make glass show up more readily is exactly the kind of change that silently
reintroduces one of those, so each rejection case is pinned here:

  * a SOLID WALL must never be called glass (it is always present)
  * a WALKING PERSON must never be called glass (two transitions, not dozens)
  * SUSTAINED ROBOT MOTION must not manufacture glass out of the smear
  * a scene that was STILL AND THEN MOVED must be recognised and discarded,
    so evidence gathered while stationary is not corrupted
  * genuine INTERMITTENT returns forming a PLANE must be found, and must FADE
    once they stop

The scene generators below are deliberately crude — this is testing the decision
logic, not the sensor model.
"""
from __future__ import annotations

import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "robot_services", "autonomous_navigation", "testing_controls"))

# a2_costmap scans sys.argv for --domain at import time; keep it clean so the
# test runner's own flags are not misread as ROS options.
_argv, sys.argv = sys.argv, [sys.argv[0]]
try:
    from a2_costmap import GlassDetector
finally:
    sys.argv = _argv

N, RES, RAD = 100, 0.1, 5.0
LINE_KW = dict(max_lines=4, min_inliers=10, tol=0.15, min_length=0.5)


def cell(x: float, y: float) -> tuple[int, int]:
    """Metres -> cell index.

    Uses floor with a nudge: plain int() truncation collapses adjacent values
    (int((-0.9 + 5) / 0.1) == 40, the same cell as -1.0) which silently shrinks
    every test scene and was good for an afternoon of confusion.
    """
    return (int(math.floor((x + RAD) / RES + 1e-9)),
            int(math.floor((y + RAD) / RES + 1e-9)))


def make_detector(**overrides) -> GlassDetector:
    params = dict(window=40, min_hit=0.15, max_hit=0.85, min_flicker=0.15,
                  evidence_decay=0.7, threshold=0.45, max_range=8.0,
                  motion_gate=0.35, variance_boost=True)
    params.update(overrides)
    return GlassDetector(N, RES, RAD, **params)


def points_for(mask: np.ndarray, extra=()) -> np.ndarray:
    ix, iy = np.nonzero(mask)
    x = (ix + 0.5) * RES - RAD
    y = (iy + 0.5) * RES - RAD
    pts = np.column_stack([x, y, np.zeros_like(x)])
    if len(extra):
        pts = np.vstack([pts, np.asarray(extra, dtype=float)])
    return pts


def play(detector: GlassDetector, scene, frames: int = 200) -> GlassDetector:
    for f in range(frames):
        mask = np.zeros((N, N), dtype=bool)
        extra = scene(f, mask)
        detector.observe(mask, points_for(mask, extra or ()))
    return detector


def glass_result(detector: GlassDetector):
    """(cells, lines) after the same line-filtering the sidecar applies."""
    cells, conf = detector.suspected()
    lines, cells, _ = detector.lines(cells, conf, **LINE_KW)
    return cells, lines


# ---------------------------------------------------------------- scenes
def scene_wall(f, mask):
    """A solid surface at x = 2.0 m, present in every single frame."""
    for y in np.arange(-1.0, 1.0, RES):
        mask[cell(2.0, y)] = True


def make_scene_glass(seed: int = 7):
    """A pane at y = 2.0 m: each cell returns ~50% of frames, independently.

    When the beam does NOT stop at the pane it carries on to something further
    away, which is the bimodal range signature the detector corroborates with.
    """
    rng = np.random.default_rng(seed)

    def scene(f, mask):
        behind = []
        for x in np.arange(-1.0, 1.0, RES):
            if rng.random() < 0.5:
                mask[cell(x, 2.0)] = True
            else:
                behind.append([x, 4.5, 0.0])
        return behind

    return scene


def scene_person(f, mask):
    """A solid 0.3 m blob tracking across the view — brief per cell, not flickery."""
    px = -2.0 + (f * 0.04) % 4.0
    for dx in (-0.1, 0.0, 0.1):
        for dy in (-0.1, 0.0, 0.1):
            mask[cell(px + dx, 1.0 + dy)] = True


def scene_moving(f, mask):
    """The robot approaching a corner.

    The motion MUST be monotonic. A sawtooth that returns to the same place
    every N frames is a periodic flicker, which is precisely what glass looks
    like — the detector is right to flag that, so testing with one tests nothing.
    """
    off = f * 0.015
    for x in np.arange(-1.0, 1.0, RES):
        mask[cell(x, 3.0 - off)] = True
    for y in np.arange(-1.0, 1.0, RES):
        mask[cell(3.0 - off, y)] = True


def scene_empty(f, mask):
    return None


# ---------------------------------------------------------------- tests
class GlassDetectorFalsePositives(unittest.TestCase):
    """Everything that must NOT be reported as glass."""

    def test_solid_wall_is_not_glass(self):
        cells, lines = glass_result(play(make_detector(), scene_wall))
        self.assertEqual(cells.shape[0], 0, "an always-present wall was called glass")
        self.assertEqual(lines, [])

    def test_walking_person_is_not_glass(self):
        cells, _ = glass_result(play(make_detector(), scene_person))
        self.assertEqual(cells.shape[0], 0,
                         "a pedestrian left a permanent glass wall behind")

    def test_sustained_motion_does_not_manufacture_glass(self):
        # Nothing is ever stable during continuous motion, so the motion gate
        # cannot fire. The hit-fraction and flicker floors have to carry this
        # case on their own.
        cells, _ = glass_result(play(make_detector(), scene_moving))
        self.assertEqual(cells.shape[0], 0, "driving smear was called glass")


class GlassDetectorMotionGate(unittest.TestCase):
    def test_gate_fires_when_a_settled_scene_shifts(self):
        det = make_detector()
        play(det, scene_wall, frames=120)          # settle with solid structure
        before = det.stats()["windows_discarded"]

        def shifted(f, mask):
            for y in np.arange(-1.0, 1.0, RES):
                mask[cell(0.5, y)] = True          # same wall, 1.5 m nearer

        play(det, shifted, frames=120)
        self.assertGreaterEqual(
            det.stats()["windows_discarded"] - before, 1,
            "the scene shifted but no window was discarded")


class GlassDetectorHonestyAboutBlindness(unittest.TestCase):
    """`observing` must distinguish "looked, saw nothing" from "could not look".

    This is the safety-relevant one. Measured 2026-09-23: from about 0.05 m/s the
    structure smears so badly that no cell stays stable, both motion tests
    abstain, and the detector reported zero glass while completely blind. A
    caller reading only `n_glass == 0` would take that as an all-clear.
    """

    @staticmethod
    def _drifting(speed_mps: float, seed: int = 1):
        rng = np.random.default_rng(seed)

        def scene(f, mask):
            off = f * speed_mps / 10.0        # frames arrive at ~10 Hz
            for x in np.arange(-1.0, 1.0, RES):
                if rng.random() < 0.5:
                    mask[cell(x, 2.0 - off)] = True      # the pane
            for y in np.arange(-1.0, 1.0, RES):
                mask[cell(3.0 - off, y)] = True          # solid reference wall

        return scene

    def test_stationary_reports_observing_and_finds_the_pane(self):
        det = play(make_detector(), self._drifting(0.0), frames=240)
        self.assertTrue(det.stats()["observing"])
        self.assertGreater(det.suspected()[0].shape[0], 0)

    def test_every_nonzero_speed_reports_not_observing(self):
        # Includes a crawl: "go slower so it can see better" does not work, and
        # the detector must say so rather than returning a quiet zero.
        for speed in (0.02, 0.05, 0.1, 0.4):
            with self.subTest(speed=speed):
                det = play(make_detector(), self._drifting(speed), frames=240)
                self.assertFalse(det.stats()["observing"],
                                 f"claimed to be observing while moving at {speed} m/s")
                self.assertEqual(det.suspected()[0].shape[0], 0)


class GlassDetectorTruePositives(unittest.TestCase):
    def test_intermittent_plane_is_detected_and_fitted(self):
        cells, lines = glass_result(play(make_detector(), make_scene_glass()))
        self.assertGreaterEqual(cells.shape[0], 8, "the pane was not detected")
        self.assertTrue(lines, "no segment was fitted through the pane")

        best = lines[0]
        # The pane spans x in [-1, 1] at y = 2.0.
        self.assertGreater(best["length"], 1.0)
        self.assertAlmostEqual(best["y1"], 2.05, delta=0.2)
        self.assertAlmostEqual(best["y2"], 2.05, delta=0.2)
        self.assertLess(best["rms"], 0.1, "fit is not tight enough to be a plane")
        self.assertGreaterEqual(best["density"], 0.55)

    def test_evidence_fades_once_the_pane_is_gone(self):
        det = play(make_detector(), make_scene_glass())
        before = det.suspected()[0].shape[0]
        self.assertGreaterEqual(before, 8, "precondition: the pane must be detected")

        play(det, scene_empty)
        self.assertEqual(det.suspected()[0].shape[0], 0,
                         "glass evidence never decayed after the pane disappeared")


class GlassLineDensity(unittest.TestCase):
    """The density rule is the single thing that makes this usable on real data.

    Measured 2026-09-22: ~20-25% of touched cells pass the per-cell flicker gate
    at every range band, purely from the non-repetitive scan pattern. Without a
    density requirement RANSAC fits confident 11 m diagonals through that noise.
    """

    def test_scattered_noise_yields_no_segments(self):
        from a2_costmap import fit_lines
        rng = np.random.default_rng(3)
        xy = rng.uniform(-5.0, 5.0, size=(400, 2))
        lines, keep = fit_lines(xy, resolution=RES, **LINE_KW)
        self.assertEqual(lines, [], "fitted a plane through uniform noise")
        self.assertFalse(keep.any())

    def test_dense_short_run_is_kept(self):
        from a2_costmap import fit_lines
        xy = np.column_stack([np.arange(-0.9, 0.9, RES),
                              np.full(18, 2.0)])
        lines, keep = fit_lines(xy, resolution=RES, **LINE_KW)
        self.assertEqual(len(lines), 1)
        self.assertGreaterEqual(lines[0]["density"], 0.9)
        self.assertEqual(int(keep.sum()), xy.shape[0])


def _load_lines_to_map():
    """nav_missions pulls in fastapi/pydantic/dotenv, which only exist in the
    supervisor's own venv. Skip rather than fail when running on system python."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "robot_supervisor_v2"))
    try:
        from app.api.nav_missions import _lines_to_map
        return _lines_to_map
    except Exception:
        return None


_LINES_TO_MAP = _load_lines_to_map()


@unittest.skipIf(_LINES_TO_MAP is None,
                 "nav_missions needs the supervisor venv (fastapi/pydantic/dotenv)")
class GlassSurveyPlacement(unittest.TestCase):
    """base_link -> map transform for survey findings.

    A sign error here would draw real glass in the wrong place on the map, which
    is worse than drawing nothing, so the rotation is pinned in both axes.
    """

    def test_identity_pose_passes_through(self):
        out = _LINES_TO_MAP([{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}],
                            {"x": 0.0, "y": 0.0, "yaw": 0.0})
        self.assertAlmostEqual(out[0]["x1"], 1.0)
        self.assertAlmostEqual(out[0]["y1"], 2.0)
        self.assertAlmostEqual(out[0]["x2"], 3.0)
        self.assertAlmostEqual(out[0]["y2"], 4.0)

    def test_translation_only(self):
        out = _LINES_TO_MAP([{"x1": 1.0, "y1": 0.0, "x2": 2.0, "y2": 0.0}],
                            {"x": 10.0, "y": -5.0, "yaw": 0.0})
        self.assertAlmostEqual(out[0]["x1"], 11.0)
        self.assertAlmostEqual(out[0]["y1"], -5.0)

    def test_yaw_90_maps_robot_forward_onto_map_y(self):
        # Facing +90 deg, "1 m in front of the robot" is +1 m in map Y.
        out = _LINES_TO_MAP([{"x1": 1.0, "y1": 0.0, "x2": 2.0, "y2": 0.0}],
                            {"x": 10.0, "y": 5.0, "yaw": math.pi / 2})
        self.assertAlmostEqual(out[0]["x1"], 10.0, places=6)
        self.assertAlmostEqual(out[0]["y1"], 6.0, places=6)
        self.assertAlmostEqual(out[0]["y2"], 7.0, places=6)

    def test_yaw_90_maps_robot_left_onto_negative_map_x(self):
        # base_link +y is LEFT. Facing +90 deg, left points at map -x.
        out = _LINES_TO_MAP([{"x1": 0.0, "y1": 1.0, "x2": 0.0, "y2": 1.0}],
                            {"x": 0.0, "y": 0.0, "yaw": math.pi / 2})
        self.assertAlmostEqual(out[0]["x1"], -1.0, places=6)
        self.assertAlmostEqual(out[0]["y1"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
