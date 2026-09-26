from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.calibration import (
    TABLE_LENGTH_MM,
    TABLE_WIDTH_MM,
    CalibrationGate,
    mark_ends,
    write_calibration,
    write_marked_ppm,
)
from table_tennis.vision.frame import ORIGIN_A2_FISHEYE, Frame
from table_tennis.vision.image import BgrImage


def _frame(seq: int, width: int = 100, height: int = 80, camera: str = "fisheye-left") -> Frame:
    return Frame(
        frame_seq=seq,
        capture_monotonic_ns=seq * 1_000_000,
        width=width,
        height=height,
        image=BgrImage(width, height),
        camera_id=camera,
        origin=ORIGIN_A2_FISHEYE,
    )


CORNERS = ((20, 16), (80, 16), (80, 64), (20, 64))
NET = ((20, 40), (80, 40))


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"00000000-0000-4000-8000-{self._n:012d}"


class _Clock:
    def __init__(self) -> None:
        self._now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        value = self._now
        self._now = value + timedelta(seconds=1)
        return value


class CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = CalibrationGate(ids=_Ids(), clock=_Clock(), margin_px=8)

    def test_ready_calibration_maps_the_plane_and_does_not_prove_a_bounce(self) -> None:
        calibration = self.gate.submit(_frame(1), CORNERS, NET)
        self.assertTrue(calibration.ready)
        self.assertEqual(calibration.corner_order, ("end_a_0", "end_a_1", "end_b_0", "end_b_1"))
        corner = calibration.project_to_table_plane(20, 16)
        center = calibration.project_to_table_plane(50, 40)
        self.assertAlmostEqual(corner.x_mm, 0.0, delta=1.0)
        self.assertAlmostEqual(corner.y_mm, 0.0, delta=1.0)
        self.assertAlmostEqual(center.x_mm, TABLE_WIDTH_MM / 2.0, delta=1.0)
        self.assertAlmostEqual(center.y_mm, TABLE_LENGTH_MM / 2.0, delta=1.0)
        self.assertFalse(center.proves_bounce)
        self.assertIn("not a bounce", str(calibration.to_json()["plane_note"]))

    def test_crossed_corners_and_edge_clicks_stay_unready(self) -> None:
        crossed = self.gate.submit(_frame(1), ((20, 16), (80, 16), (20, 64), (80, 64)), NET)
        self.assertFalse(crossed.ready)
        self.assertEqual(crossed.reason, "self_intersection")
        self.assertIsNone(crossed.calibration_id)
        self.assertIsNone(self.gate.current)
        with self.assertRaises(ValueError):
            write_calibration(Path(tempfile.gettempdir()) / "nope.json", crossed)

        self.gate.camera_moved()
        edged = self.gate.submit(_frame(2), ((2, 16), (80, 16), (80, 64), (20, 64)), NET)
        self.assertEqual(edged.reason, "out_of_frame")
        self.assertFalse(edged.ready)

    def test_rejected_frame_waits_for_a_new_pose(self) -> None:
        self.gate.submit(_frame(1), ((2, 16), (80, 16), (80, 64), (20, 64)), NET)
        retry = self.gate.submit(_frame(1), CORNERS, NET)
        self.assertEqual(retry.reason, "wait_for_new_pose")
        self.gate.camera_moved()
        same_picture = self.gate.submit(_frame(1), CORNERS, NET)
        self.assertEqual(same_picture.reason, "wait_for_new_pose")
        accepted = self.gate.submit(_frame(2), CORNERS, NET)
        self.assertTrue(accepted.ready)

    def test_resolution_or_camera_change_drops_the_old_calibration(self) -> None:
        first = self.gate.submit(_frame(1), CORNERS, NET)
        self.gate.observe_frame(_frame(2, width=120, camera="fisheye-left"))
        self.assertIsNotNone(self.gate.current)
        assert self.gate.current is not None
        self.assertFalse(self.gate.current.ready)
        self.assertEqual(self.gate.current.calibration_id, first.calibration_id)
        second = self.gate.submit(
            _frame(3, width=120),
            ((16, 16), (104, 16), (104, 64), (16, 64)),
            ((16, 40), (104, 40)),
        )
        self.assertTrue(second.ready)
        self.assertNotEqual(second.calibration_id, first.calibration_id)

        self.gate.observe_frame(_frame(4, width=120, camera="fisheye-right"))
        assert self.gate.current is not None
        self.assertFalse(self.gate.current.ready)

    def test_export_marks_end_a_and_writes_json(self) -> None:
        calibration = self.gate.submit(_frame(1), CORNERS, NET)
        image = BgrImage(100, 80)
        image.fill((10, 10, 10))
        marked = mark_ends(image, calibration)
        self.assertEqual(marked.get(30, 16), (255, 160, 32))
        self.assertEqual(marked.get(30, 64), (32, 160, 255))
        self.assertEqual(image.get(30, 16), (10, 10, 10))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_calibration(folder / "calibration.json", calibration)
            write_marked_ppm(folder / "ends.ppm", marked)
            payload = json.loads((folder / "calibration.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["ends"], {"end_a": [0, 1], "end_b": [2, 3]})
            self.assertTrue((folder / "ends.ppm").read_bytes().startswith(b"P6\n"))


if __name__ == "__main__":
    unittest.main()
