from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.vision.capture import HEADER, HEADER_SIZE, MAGIC, FileCapture
from table_tennis.vision.image import BgrImage
from table_tennis.vision.overlay import write_overlay_clip
from table_tennis.vision.synthetic import write_moving_circle


class FileCaptureTests(unittest.TestCase):
    def test_synthetic_clip_is_monotonic_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "circle.ttclip"
            write_moving_circle(path, frames=8, period_ns=50_000_000)
            with FileCapture(path, "laptop-file") as capture:
                frames = list(capture)
            self.assertEqual(len(frames), 8)
            self.assertEqual([frame.frame_seq for frame in frames], list(range(8)))
            times = [frame.capture_monotonic_ns for frame in frames]
            self.assertEqual(times, [index * 50_000_000 for index in range(8)])
            self.assertEqual(capture.stats.frames_emitted, 8)
            self.assertEqual(capture.stats.frames_dropped, 0)
            self.assertTrue(all(frame.origin == "file" for frame in frames))
            path.unlink()

    def test_short_file_counts_dropped_frames_and_does_not_invent_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "short.ttclip"
            write_moving_circle(path, width=16, height=12, frames=4, period_ns=40_000_000)
            frame_bytes = 16 * 12 * 3
            data = path.read_bytes()
            path.write_bytes(data[: HEADER_SIZE + frame_bytes * 2 + 5])
            with FileCapture(path, "laptop-file") as capture:
                frames = list(capture)
            self.assertEqual(len(frames), 2)
            self.assertEqual(capture.stats.frames_emitted, 2)
            self.assertEqual(capture.stats.frames_dropped, 2)
            self.assertEqual(frames[0].image.get(0, 0), (40, 50, 30))

    def test_bad_magic_closes_the_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.ttclip"
            path.write_bytes(b"NOTACLIP" + bytes(HEADER_SIZE))
            with self.assertRaises(ValueError):
                FileCapture(path, "laptop-file").__enter__()
            path.unlink()

    def test_overlay_stamps_sequence_without_touching_capture_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "circle.ttclip"
            overlaid = Path(tmp) / "overlay.ttclip"
            write_moving_circle(path=source, frames=4, period_ns=50_000_000)
            with FileCapture(source, "laptop-file") as capture:
                frames = list(capture)
                corner_before = frames[0].image.get(1, 1)
                written = write_overlay_clip(frames, overlaid)
            self.assertEqual(written, 4)
            self.assertEqual(frames[0].image.get(1, 1), corner_before)
            with FileCapture(overlaid, "laptop-file") as capture:
                stamped = list(capture)
            self.assertEqual(stamped[0].image.get(1, 1), (255, 255, 255))
            self.assertNotEqual(stamped[0].image.get(1, 1), stamped[1].image.get(1, 1))

    def test_import_does_not_pull_robot_stack(self) -> None:
        self.assertNotIn("cv2", sys.modules)
        self.assertNotIn("rclpy", sys.modules)
        self.assertNotIn("ultralytics", sys.modules)
        self.assertNotIn("requests", sys.modules)

    def test_header_round_trip_size(self) -> None:
        packed = HEADER.pack(MAGIC, 64, 48, 8, 50_000_000)
        self.assertEqual(len(packed), HEADER_SIZE)
        magic, width, height, count, period = HEADER.unpack(packed)
        self.assertEqual((magic, width, height, count, period), (MAGIC, 64, 48, 8, 50_000_000))


class ImageTests(unittest.TestCase):
    def test_copy_does_not_alias_pixels(self) -> None:
        image = BgrImage(4, 4)
        image.fill((1, 2, 3))
        clone = image.copy()
        clone.set(0, 0, (9, 9, 9))
        self.assertEqual(image.get(0, 0), (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
