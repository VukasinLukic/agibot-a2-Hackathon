import tempfile
import unittest
import asyncio
import base64
import json
from pathlib import Path
from unittest import mock
import numpy as np

from robot_supervisor_v2.app.services.base import ServiceState
from robot_supervisor_v2.app.services.registry import ServiceRegistry
from robot_supervisor_v2.app.services.video_recording import (
    DEFAULT_DEVICE,
    DEFAULT_FRAMERATE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RESOLUTION,
    VideoRecordingService,
)


class FakeStdin:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, returncode=0):
        self.stdin = FakeStdin()
        self.returncode = returncode

    def communicate(self, timeout=None):
        return b"", b""

    def kill(self):
        self.returncode = -9


class FakeCapture:
    def read(self):
        return False, None

    def release(self):
        pass


class FakePortraitProcessor:
    def __init__(self):
        self.reset_ids: list[str] = []
        self.process_calls = 0

    def reset(self, request_id=None):
        self.reset_ids.append(request_id)

    def inspect_portrait_edges(self, frame, max_candidates=8):
        return {
            "face_count": 1,
            "portrait": {"area_ratio": 0.01},
            "line_count": 2,
            "candidates": [],
            "reason": "ready",
        }

    def process_frame(self, frame, request_state):
        self.process_calls += 1
        return {
            "request_id": request_state["request_id"],
            "status": "captured",
            "timestamp": 1.0,
            "metadata": {"quality": {"stable_frames": 3}},
        }


class FakeCardProcessor:
    def __init__(self):
        self.stable_count = 3
        self.process_calls = 0
        self.candidate = {
            "points": np.array([[2, 2], [17, 2], [17, 12], [2, 12]], dtype=np.float32),
            "source": "test",
            "quality": {
                "score": 0.9,
                "blur": 120.0,
                "area_ratio": 0.4,
                "aspect_ratio": 1.55,
                "line_coverage": 0.8,
                "portrait_area_ratio": 0.1,
                "portrait_frame_area_ratio": 0.2,
                "glare_ratio": 0.0,
            },
        }

    def debug_frame(self, frame):
        return {"rejected": [], "line_count": 4, "face_count": 1}

    def _detect_best_candidate(self, frame):
        return self.candidate

    def reset(self, request_id=None):
        pass

    def process_frame(self, frame, request_state):
        self.process_calls += 1
        return {
            "request_id": request_state["request_id"],
            "status": "captured",
            "timestamp": 1.0,
            "metadata": {"image_jpeg_base64": base64.b64encode(b"crop").decode("ascii")},
        }


class FakeDispatchProcessor:
    def __init__(self, publisher):
        self.publisher = publisher
        self.process_calls = 0

    def reset(self):
        self.publisher.reset()

    def process_frame(self, frame):
        self.process_calls += 1
        if self.process_calls == 1:
            event = self.publisher.publish_if_changed(42, {"detect_s": 0.01})
            return 42, event
        event = self.publisher.publish_if_changed(None, {"detect_s": 0.02})
        return None, event


class VideoRecordingServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.tmp.name)
        self.service = VideoRecordingService(
            "video-recording-service",
            {
                "display_name": "Video Recording Service",
                "output_dir": str(self.output_dir),
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_registry_creates_video_recording_service(self):
        service = ServiceRegistry.create_service(
            "video-recording-service",
            "video-recording-service",
            {},
        )

        self.assertIsInstance(service, VideoRecordingService)
        config = service.get_config()
        self.assertEqual(config["device"], DEFAULT_DEVICE)
        self.assertEqual(config["resolution"], DEFAULT_RESOLUTION)
        self.assertEqual(config["framerate"], DEFAULT_FRAMERATE)
        self.assertEqual(config["output_dir"], DEFAULT_OUTPUT_DIR)
        self.assertFalse(config["record_overlay_enabled"])
        self.assertFalse(config["id_debug_enabled"])
        self.assertFalse(config["id_debug_overlay_enabled"])
        self.assertFalse(config["id_debug_record_captures_enabled"])
        self.assertEqual(config["id_debug_mode"], "capture")
        self.assertEqual(config["id_debug_capture_image_mode"], "crop")
        self.assertFalse(config["cv_debug_enabled"])
        self.assertEqual(config["cv_debug_mode"], "id_capture")
        self.assertNotIn("id_debug_card_capture_fps", config)
        self.assertNotIn("id_debug_rearm_cooldown_s", config)
        self.assertTrue(config["manual_only"])
        self.assertTrue(config["optional"])
        self.assertFalse(service.runtime_status()["record_overlay_enabled"])

    def test_id_debug_status_defaults_and_reset(self):
        status = self.service.id_debug_status()

        self.assertFalse(status["enabled"])
        self.assertFalse(status["overlay_enabled"])
        self.assertFalse(status["record_captures_enabled"])
        self.assertEqual(status["mode"], "capture")
        self.assertEqual(status["capture_image_mode"], "crop")
        self.assertEqual(status["captured_count"], 0)

        reset_status = self.service.reset_id_debug()

        self.assertFalse(reset_status["enabled"])
        self.assertIsNotNone(reset_status["request_id"])
        self.assertEqual(self.service.events_status()[0]["type"], "id_debug_reset")

    def test_debug_config_update_does_not_restart_running_service_or_clear_flags(self):
        self.service._state = ServiceState.RUNNING

        with mock.patch.object(self.service, "restart", new=mock.AsyncMock()) as restart:
            asyncio.run(
                self.service.update_config(
                    {
                        "cv_debug_enabled": True,
                        "cv_debug_mode": "id_capture",
                        "id_debug_enabled": True,
                        "id_debug_overlay_enabled": True,
                    }
                )
            )

        restart.assert_not_awaited()
        self.assertTrue(self.service.cv_debug_status()["enabled"])
        self.assertTrue(self.service.id_debug_status()["enabled"])
        self.assertTrue(self.service.id_debug_status()["overlay_enabled"])

    def test_id_debug_overlay_and_record_flags_are_effective_only_when_active(self):
        self.service._config.update(
            {
                "id_debug_enabled": False,
                "id_debug_overlay_enabled": True,
                "id_debug_record_captures_enabled": True,
            }
        )

        inactive_status = self.service.id_debug_status()

        self.assertFalse(inactive_status["enabled"])
        self.assertFalse(inactive_status["overlay_enabled"])
        self.assertFalse(inactive_status["record_captures_enabled"])

        self.service._config["id_debug_enabled"] = True
        active_status = self.service.id_debug_status()

        self.assertTrue(active_status["enabled"])
        self.assertTrue(active_status["overlay_enabled"])
        self.assertTrue(active_status["record_captures_enabled"])

        self.service._config["id_debug_overlay_enabled"] = False
        self.assertTrue(self.service.id_debug_status()["overlay_enabled"])

    def test_event_log_is_bounded_and_exposed_in_runtime_status(self):
        for index in range(45):
            self.service._append_event("test_event", f"Event {index}")

        events = self.service.events_status()

        self.assertEqual(len(events), 40)
        self.assertEqual(events[0]["title"], "Event 44")
        self.assertEqual(events[-1]["title"], "Event 5")
        self.assertEqual(self.service.runtime_status()["events"], events)

    def test_portrait_debug_mode_emits_capture_events(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        processor = FakePortraitProcessor()
        self.service._config.update(
            {
                "id_debug_enabled": True,
                "id_debug_mode": "portrait",
                "id_debug_record_captures_enabled": False,
            }
        )
        self.service._id_debug_processor = processor

        with mock.patch(
            "robot_supervisor_v2.app.services.video_recording.portrait_debug_status_line",
            return_value="portrait_debug ready",
        ), mock.patch(
            "robot_supervisor_v2.app.services.video_recording.draw_portrait_debug_preview",
            side_effect=lambda input_frame, debug_info: input_frame,
        ):
            self.service._build_preview_frame(frame)

        events = self.service.events_status()

        self.assertEqual(events[0]["type"], "id_debug_capture")
        self.assertEqual(events[0]["title"], "ID capture detected")
        self.assertEqual(events[0]["details"]["mode"], "portrait")
        self.assertFalse(events[0]["details"]["saved"])
        self.assertEqual(self.service.id_debug_status()["captured_count"], 1)
        self.assertTrue(self.service.id_debug_status()["capture_completed"])

    def test_id_debug_capture_does_not_auto_rearm_after_success(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        processor = FakePortraitProcessor()
        self.service._config.update(
            {
                "id_debug_enabled": True,
                "id_debug_mode": "portrait",
                "id_debug_record_captures_enabled": False,
            }
        )
        self.service._id_debug_processor = processor

        with mock.patch(
            "robot_supervisor_v2.app.services.video_recording.portrait_debug_status_line",
            return_value="portrait_debug ready",
        ), mock.patch(
            "robot_supervisor_v2.app.services.video_recording.draw_portrait_debug_preview",
            side_effect=lambda input_frame, debug_info: input_frame,
        ):
            self.service._build_preview_frame(frame)
            self.service._build_preview_frame(frame)

        events = [event for event in self.service.events_status() if event["type"] == "id_debug_capture"]

        self.assertEqual(len(events), 1)
        self.assertEqual(processor.process_calls, 1)

    def test_id_capture_success_preview_keeps_candidate_overlay(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        processor = FakeCardProcessor()
        preview_frame = np.full((20, 20, 3), 200, dtype=np.uint8)
        drawn_candidates = []
        self.service._config.update(
            {
                "cv_debug_enabled": True,
                "cv_debug_mode": "id_capture",
                "id_debug_enabled": True,
                "id_debug_record_captures_enabled": False,
            }
        )
        self.service._id_debug_processor = processor

        def fake_draw_preview(input_frame, candidate, preview_processor, captured_count, debug_info):
            drawn_candidates.append(candidate)
            return preview_frame

        with mock.patch(
            "robot_supervisor_v2.app.services.video_recording.draw_card_capture_preview",
            side_effect=fake_draw_preview,
        ):
            result = self.service._build_preview_frame(frame)

        self.assertIs(result, preview_frame)
        self.assertEqual(len(drawn_candidates), 1)
        self.assertIs(drawn_candidates[0], processor.candidate)

    def test_id_debug_processing_uses_production_card_capture_fps_cadence(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        processor = FakePortraitProcessor()
        self.service.set_id_debug_card_capture_fps(8)
        self.service._config.update(
            {
                "id_debug_enabled": True,
                "id_debug_mode": "portrait",
                "id_debug_record_captures_enabled": False,
            }
        )
        self.service._id_debug_processor = processor

        with mock.patch(
            "robot_supervisor_v2.app.services.video_recording.time.monotonic",
            side_effect=[0.0, 0.05, 0.13],
        ):
            first = self.service._process_id_debug_capture_frame(processor, frame)
            second = self.service._process_id_debug_capture_frame(processor, frame)
            third = self.service._process_id_debug_capture_frame(processor, frame)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)
        self.assertEqual(processor.process_calls, 2)

    def test_vision_dispatch_debug_uses_local_publisher_events(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        created_processors = []

        def create_processor(publisher=None):
            processor = FakeDispatchProcessor(publisher)
            created_processors.append(processor)
            return processor

        self.service.set_vision_dispatch_detection_fps(8)
        self.service._config.update(
            {
                "cv_debug_enabled": True,
                "cv_debug_mode": "vision_dispatch",
                "id_debug_enabled": False,
                "id_debug_record_captures_enabled": False,
            }
        )

        with mock.patch(
            "robot_supervisor_v2.app.services.video_recording.PersonLockProcessor",
            side_effect=create_processor,
        ), mock.patch(
            "robot_supervisor_v2.app.services.video_recording.time.monotonic",
            side_effect=[0.0, 0.2],
        ):
            self.service._build_preview_frame(frame)
            self.service._build_preview_frame(frame)

        events = self.service.events_status()

        self.assertEqual(created_processors[0].process_calls, 2)
        self.assertEqual([event["type"] for event in events], ["vision_dispatch_unlocked", "vision_dispatch_locked"])
        self.assertEqual(events[1]["details"]["track_id"], "42")
        status = self.service.cv_debug_status()
        self.assertEqual(status["mode"], "vision_dispatch")
        self.assertEqual(status["vision_dispatch"]["event_count"], 2)
        self.assertFalse(status["vision_dispatch"]["locked"])
        self.assertIsNone(status["vision_dispatch"]["track_id"])
        self.assertEqual(status["id_capture"]["captured_count"], 0)
        self.assertFalse(events[1]["details"]["saved"])

    def test_vision_dispatch_debug_record_captures_saves_lock_and_unlock_frames(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)

        def create_processor(publisher=None):
            return FakeDispatchProcessor(publisher)

        self.service.set_vision_dispatch_detection_fps(8)
        self.service._config.update(
            {
                "cv_debug_enabled": True,
                "cv_debug_mode": "vision_dispatch",
                "id_debug_enabled": False,
                "id_debug_record_captures_enabled": True,
            }
        )

        with mock.patch(
            "robot_supervisor_v2.app.services.video_recording.PersonLockProcessor",
            side_effect=create_processor,
        ), mock.patch(
            "robot_supervisor_v2.app.services.video_recording.time.monotonic",
            side_effect=[0.0, 0.2],
        ):
            self.service._build_preview_frame(frame)
            self.service._build_preview_frame(frame)

        events = self.service.events_status()
        event_files = [event["file"] for event in events if event["type"].startswith("vision_dispatch_")]

        self.assertEqual(len(event_files), 2)
        self.assertTrue(all(file["id"].startswith("vision-dispatch--") for file in event_files))
        self.assertEqual(event_files[0]["recording_type"], "vision_dispatch")
        self.assertTrue(self.service.resolve_file(event_files[0]["id"]).exists())
        self.assertTrue(self.service.sidecar_metadata_for_file(event_files[0]["id"]).exists())
        metadata = json.loads(self.service.sidecar_metadata_for_file(event_files[0]["id"]).read_text(encoding="utf-8"))
        self.assertIn(metadata["event"], {"locked", "unlocked"})
        self.assertIn(event_files[0]["id"], {item["id"] for item in self.service.list_files()})
        status = self.service.cv_debug_status()
        self.assertTrue(status["record_captures_enabled"])
        self.assertTrue(status["vision_dispatch"]["record_captures_enabled"])
        self.assertEqual(status["vision_dispatch"]["last_capture"]["id"], event_files[0]["id"])

    def test_vision_dispatch_debug_cadence_throttles_processor(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        processor = FakeDispatchProcessor(publisher=mock.Mock(reset=mock.Mock(), publish_if_changed=mock.Mock(return_value=None)))
        self.service.set_vision_dispatch_detection_fps(8)
        self.service._config.update({"cv_debug_enabled": True, "cv_debug_mode": "vision_dispatch"})
        self.service._vision_dispatch_processor = processor

        with mock.patch(
            "robot_supervisor_v2.app.services.video_recording.time.monotonic",
            side_effect=[0.0, 0.05, 0.13],
        ):
            self.service._process_vision_dispatch_frame(frame)
            self.service._process_vision_dispatch_frame(frame)
            self.service._process_vision_dispatch_frame(frame)

        self.assertEqual(processor.process_calls, 2)

    def test_list_files_filters_temp_and_unsupported_files(self):
        (self.output_dir / "capture-1.jpg").write_bytes(b"jpg")
        (self.output_dir / "recording-1.mp4").write_bytes(b"mp4")
        (self.output_dir / ".recording-1.mp4.tmp").write_bytes(b"tmp")
        (self.output_dir / "notes.txt").write_text("ignore", encoding="utf-8")

        files = self.service.list_files()

        self.assertEqual({item["filename"] for item in files}, {"capture-1.jpg", "recording-1.mp4"})
        self.assertEqual({item["type"] for item in files}, {"image", "video"})

    def test_manual_capture_writes_normal_metadata_sidecar_by_default(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        self.service._state = ServiceState.RUNNING
        self.service._latest_frame = frame
        self.service._latest_save_frame = np.full((20, 20, 3), 255, dtype=np.uint8)

        result = self.service.capture_image()

        metadata_path = self.output_dir / result["metadata_id"]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(result["content_variant"], "normal")
        self.assertEqual(metadata["content_variant"], "normal")
        self.assertFalse(metadata["record_overlay_enabled"])
        self.assertEqual(metadata["filename"], result["filename"])

    def test_manual_capture_uses_preview_save_frame_when_record_overlay_enabled(self):
        raw_frame = np.zeros((20, 20, 3), dtype=np.uint8)
        save_frame = np.full((20, 20, 3), 255, dtype=np.uint8)
        written_frames = []
        self.service._state = ServiceState.RUNNING
        self.service._config["record_overlay_enabled"] = True
        self.service._latest_frame = raw_frame
        self.service._latest_save_frame = save_frame

        def fake_imwrite(path, frame):
            written_frames.append(frame.copy())
            Path(path).write_bytes(b"jpg")
            return True

        with mock.patch("robot_supervisor_v2.app.services.video_recording.cv2.imwrite", side_effect=fake_imwrite):
            result = self.service.capture_image()

        metadata = json.loads((self.output_dir / result["metadata_id"]).read_text(encoding="utf-8"))
        self.assertEqual(result["content_variant"], "overlay")
        self.assertEqual(metadata["content_variant"], "overlay")
        self.assertTrue(metadata["record_overlay_enabled"])
        self.assertTrue(np.array_equal(written_frames[0], save_frame))
        self.assertFalse(np.array_equal(written_frames[0], raw_frame))

    def test_id_debug_capture_payload_uses_resolvable_file_id(self):
        result = {
            "request_id": "debug-request",
            "status": "captured",
            "timestamp": 1.0,
            "metadata": {
                "image_jpeg_base64": base64.b64encode(b"jpg").decode("ascii"),
                "quality": {"stable_frames": 3},
            },
        }
        self.service._id_debug_captured_count = 1

        payload = self.service._save_id_debug_capture(result)

        self.assertTrue(payload["id"].startswith("id-debug--"))
        self.assertEqual(payload["image_mode"], "crop")
        self.assertEqual(payload["content_variant"], "normal")
        self.assertEqual(self.service.resolve_file(payload["id"]).name, payload["filename"])
        self.assertIn(payload["id"], {item["id"] for item in self.service.list_files()})
        metadata_path = self.service.sidecar_metadata_for_file(payload["id"])
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["image_mode"], "crop")
        self.assertEqual(metadata["content_variant"], "normal")

    def test_id_debug_whole_image_capture_writes_frame_instead_of_crop_payload(self):
        result = {
            "request_id": "debug-request",
            "status": "captured",
            "timestamp": 1.0,
            "metadata": {
                "image_jpeg_base64": base64.b64encode(b"crop").decode("ascii"),
                "quality": {"stable_frames": 3},
            },
        }
        frame = np.full((20, 20, 3), 255, dtype=np.uint8)
        written_frames = []
        self.service._config["id_debug_capture_image_mode"] = "whole"
        self.service._id_debug_captured_count = 1

        def fake_imwrite(path, image):
            written_frames.append(image.copy())
            Path(path).write_bytes(b"whole")
            return True

        with mock.patch("robot_supervisor_v2.app.services.video_recording.cv2.imwrite", side_effect=fake_imwrite):
            payload = self.service._save_id_debug_capture(result, frame=frame, display_frame=np.zeros_like(frame))

        metadata = json.loads(self.service.sidecar_metadata_for_file(payload["id"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["image_mode"], "whole")
        self.assertEqual(payload["content_variant"], "normal")
        self.assertEqual(metadata["image_mode"], "whole")
        self.assertTrue(np.array_equal(written_frames[0], frame))

    def test_id_debug_overlay_capture_forces_whole_image_and_writes_display_frame(self):
        result = {
            "request_id": "debug-request",
            "status": "captured",
            "timestamp": 1.0,
            "metadata": {
                "image_jpeg_base64": base64.b64encode(b"crop").decode("ascii"),
            },
        }
        raw_frame = np.zeros((20, 20, 3), dtype=np.uint8)
        display_frame = np.full((20, 20, 3), 200, dtype=np.uint8)
        written_frames = []
        self.service._config.update({"record_overlay_enabled": True, "id_debug_capture_image_mode": "crop"})
        self.service._id_debug_captured_count = 1

        def fake_imwrite(path, image):
            written_frames.append(image.copy())
            Path(path).write_bytes(b"overlay")
            return True

        with mock.patch("robot_supervisor_v2.app.services.video_recording.cv2.imwrite", side_effect=fake_imwrite):
            payload = self.service._save_id_debug_capture(result, frame=raw_frame, display_frame=display_frame)

        metadata = json.loads(self.service.sidecar_metadata_for_file(payload["id"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["image_mode"], "whole")
        self.assertEqual(payload["content_variant"], "overlay")
        self.assertEqual(metadata["image_mode"], "whole")
        self.assertTrue(metadata["record_overlay_enabled"])
        self.assertTrue(np.array_equal(written_frames[0], display_frame))

    def test_resolve_file_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            self.service.resolve_file("../secret.mp4")
        with self.assertRaises(ValueError):
            self.service.resolve_file(".hidden.mp4")

    def test_rename_preserves_extension_and_rejects_collision(self):
        source = self.output_dir / "capture-1.jpg"
        source.write_bytes(b"jpg")

        result = self.service.rename_file("capture-1.jpg", "front camera.mp4")

        self.assertEqual(result["filename"], "front camera.jpg")
        self.assertTrue((self.output_dir / "front camera.jpg").exists())
        (self.output_dir / "taken.jpg").write_bytes(b"other")
        with self.assertRaises(FileExistsError):
            self.service.rename_file("front camera.jpg", "taken")

    def test_rename_recording_moves_metadata_sidecar_and_updates_filename(self):
        source = self.output_dir / "recording-1.mp4"
        metadata = self.output_dir / "recording-1.json"
        source.write_bytes(b"mp4")
        metadata.write_text(json.dumps({"filename": "recording-1.mp4", "recording_type": "id_scan"}), encoding="utf-8")

        result = self.service.rename_file("recording-1.mp4", "renamed recording")

        self.assertEqual(result["filename"], "renamed recording.mp4")
        self.assertEqual(result["recording_type"], "id_scan")
        self.assertTrue((self.output_dir / "renamed recording.mp4").exists())
        self.assertTrue((self.output_dir / "renamed recording.json").exists())
        self.assertFalse(metadata.exists())
        renamed_metadata = json.loads((self.output_dir / "renamed recording.json").read_text(encoding="utf-8"))
        self.assertEqual(renamed_metadata["filename"], "renamed recording.mp4")

    def test_rename_id_debug_file_preserves_resolvable_prefix(self):
        debug_dir = self.output_dir / "id_debug"
        debug_dir.mkdir()
        source = debug_dir / "id-debug-capture-1.jpg"
        source.write_bytes(b"jpg")

        result = self.service.rename_file("id-debug--id-debug-capture-1.jpg", "renamed")

        self.assertEqual(result["id"], "id-debug--renamed.jpg")
        self.assertEqual(result["filename"], "renamed.jpg")
        self.assertEqual(self.service.resolve_file(result["id"]).name, "renamed.jpg")

    def test_delete_file_removes_file(self):
        path = self.output_dir / "recording-1.mp4"
        metadata = self.output_dir / "recording-1.json"
        path.write_bytes(b"mp4")
        metadata.write_text("{}", encoding="utf-8")

        result = self.service.delete_file("recording-1.mp4")

        self.assertTrue(result["success"])
        self.assertFalse(path.exists())
        self.assertFalse(metadata.exists())

    def test_start_recording_requires_ffmpeg(self):
        self.service._state = ServiceState.RUNNING
        self.service._actual_width = 640
        self.service._actual_height = 480

        with mock.patch("robot_supervisor_v2.app.services.video_recording.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg is required"):
                self.service.start_recording()

    def test_start_allows_retry_from_failed_state(self):
        async def run():
            self.service._state = ServiceState.FAILED
            with mock.patch.object(self.service, "_open_capture", return_value=FakeCapture()):
                await self.service.start()
            self.assertEqual(self.service.get_status().state, ServiceState.RUNNING)
            await self.service.stop()

        asyncio.run(run())

    def test_stop_clears_cv_debug_processors_and_marks_debug_stopped(self):
        async def run():
            id_processor = mock.Mock()
            dispatch_processor = mock.Mock()
            self.service._state = ServiceState.RUNNING
            self.service._config.update({"cv_debug_enabled": True, "cv_debug_mode": "vision_dispatch"})
            self.service._id_debug_processor = id_processor
            self.service._vision_dispatch_processor = dispatch_processor
            self.service._vision_dispatch_publisher = object()

            await self.service.stop()

            self.assertIsNone(self.service._id_debug_processor)
            self.assertIsNone(self.service._vision_dispatch_processor)
            self.assertIsNone(self.service._vision_dispatch_publisher)
            self.assertFalse(self.service.cv_debug_status()["enabled"])
            self.assertFalse(self.service.id_debug_status()["enabled"])
            self.assertFalse(self.service.get_config()["cv_debug_enabled"])
            self.assertFalse(self.service.get_config()["id_debug_enabled"])
            id_processor.close.assert_called_once()
            dispatch_processor.close.assert_called_once()

        asyncio.run(run())

    def test_failed_camera_open_does_not_allocate_vision_dispatch_processor(self):
        async def run():
            self.service._config.update({"cv_debug_enabled": True, "cv_debug_mode": "vision_dispatch"})
            with mock.patch.object(
                self.service,
                "_open_capture",
                side_effect=RuntimeError("camera unavailable"),
            ), mock.patch(
                "robot_supervisor_v2.app.services.video_recording.PersonLockProcessor",
                side_effect=lambda publisher=None: FakeDispatchProcessor(publisher),
            ) as person_lock_processor:
                with self.assertRaisesRegex(RuntimeError, "camera unavailable"):
                    await self.service.start()

            person_lock_processor.assert_not_called()
            self.assertIsNone(self.service._vision_dispatch_processor)
            self.assertIsNone(self.service._vision_dispatch_publisher)
            self.assertEqual(self.service.get_status().state, ServiceState.FAILED)

        asyncio.run(run())

    def test_stop_recording_atomically_finalizes_temp_file(self):
        temp_path = self.output_dir / ".recording-1.mp4.tmp"
        final_path = self.output_dir / "recording-1.mp4"
        temp_path.write_bytes(b"mp4")
        self.service._recording_process = FakeProcess()
        self.service._recording_temp_path = temp_path
        self.service._recording_final_path = final_path
        self.service._recording_started_at = 1.0
        self.service._recording_metadata_snapshot = {
            "schema_version": 1,
            "filename": final_path.name,
            "recording_type": "vision_dispatch",
            "content_variant": "overlay",
            "record_overlay_enabled": True,
            "started_at": 1.0,
            "device": "/dev/video10",
            "resolution": "960x540",
            "framerate": 30.0,
            "actual_width": 640,
            "actual_height": 480,
            "actual_fps": 30.0,
            "cv_debug_enabled": True,
            "cv_debug_mode": "vision_dispatch",
            "id_scan": {"enabled": False},
            "vision_dispatch": {"enabled": True, "event_count_at_start": 1},
        }
        self.service._vision_dispatch_event_count = 3

        result = self.service.stop_recording()

        self.assertEqual(result["filename"], "recording-1.mp4")
        self.assertEqual(result["recording_type"], "vision_dispatch")
        self.assertTrue(final_path.exists())
        metadata_path = self.output_dir / "recording-1.json"
        self.assertTrue(metadata_path.exists())
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["recording_type"], "vision_dispatch")
        self.assertEqual(metadata["content_variant"], "overlay")
        self.assertTrue(metadata["record_overlay_enabled"])
        self.assertEqual(metadata["filename"], "recording-1.mp4")
        self.assertEqual(metadata["vision_dispatch"]["event_count_at_stop"], 3)
        self.assertIn("duration_s", metadata)
        self.assertFalse(temp_path.exists())
        self.assertFalse(self.service.is_recording)
        self.assertEqual(self.service.events_status()[0]["type"], "recording_saved")
        self.assertEqual(self.service.events_status()[0]["file"]["filename"], "recording-1.mp4")


if __name__ == "__main__":
    unittest.main()
