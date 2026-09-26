#!/usr/bin/env python3
"""Unit tests for request-gated vision card capture state."""

from __future__ import annotations

import asyncio
import sys
import time
import types
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

import numpy as np


if "livekit" not in sys.modules:
    livekit_module = types.ModuleType("livekit")
    livekit_module.api = types.SimpleNamespace(
        AccessToken=object,
        LiveKitAPI=object,
        VideoGrants=object,
    )
    livekit_module.rtc = types.SimpleNamespace(Room=object)
    sys.modules["livekit"] = livekit_module
    sys.modules["livekit.api"] = livekit_module.api
    sys.modules["livekit.rtc"] = livekit_module.rtc

from robot_supervisor_v2.app.controllers.vision import VisionController
from robot_supervisor_v2.app.services.base import ServiceState
from robot_supervisor_v2.app.services.vision_controller import VisionControllerService

REPO_ROOT = Path(__file__).resolve().parents[2]
DETECTION_DIR = REPO_ROOT / "robot_services" / "vision" / "detection"
if str(DETECTION_DIR) not in sys.path:
    sys.path.insert(0, str(DETECTION_DIR))

import card_capture
import main as vision_main
from main import PersonLockProcessor, VisionEventPublisher
from card_capture import CardCaptureProcessor


class FakeManualController:
    def __init__(self):
        self.wrap_calls = 0

    def get_status(self):
        return {"state": "idle", "room": "test-room"}

    async def wrap_conversation(self, graceful=True):
        self.wrap_calls += 1
        return {"status": "wrapped", "graceful": graceful}

    async def dispatch_conversation(self):
        return {"status": "dispatched"}


class FakeService:
    def get_status(self):
        return SimpleNamespace(state=SimpleNamespace(value="running"), pid=1234)


class FakeServiceManager:
    def get(self, name):
        if name == "vision-controller":
            return FakeService()
        return None


class VisionCardCaptureTests(unittest.TestCase):
    def setUp(self):
        self.manual_controller = FakeManualController()
        self.controller = VisionController(self.manual_controller, FakeServiceManager())

    def run_async(self, coro):
        return asyncio.run(coro)

    def mark_person_present(self):
        self.controller._person_present = True
        self.controller._active_track_id = "track-1"
        self.controller._last_detected_at = time.time()

    def test_start_request_from_idle(self):
        result = self.run_async(self.controller.request_card_capture(source="unit-test"))

        self.assertEqual(result["state"], "pending")
        self.assertTrue(result["active"])
        self.assertIsNotNone(result["request_id"])
        self.assertEqual(result["source"], "unit-test")

    def test_reject_second_active_request(self):
        first = self.run_async(self.controller.request_card_capture())
        second = self.run_async(self.controller.request_card_capture())

        self.assertEqual(second["status"], "already_active")
        self.assertEqual(
            second["card_capture"]["request_id"],
            first["request_id"],
        )

    def test_cancel_active_request(self):
        started = self.run_async(self.controller.request_card_capture())
        result = self.run_async(
            self.controller.cancel_card_capture(started["request_id"])
        )

        self.assertEqual(result["state"], "cancelled")
        self.assertFalse(result["active"])

    def test_accept_matching_result(self):
        started = self.run_async(self.controller.request_card_capture())
        result = self.run_async(
            self.controller.complete_card_capture(
                request_id=started["request_id"],
                status="captured",
                metadata={"processor_stub": True},
            )
        )

        self.assertEqual(result["state"], "captured")
        self.assertFalse(result["active"])
        self.assertTrue(result["result"]["metadata"]["processor_stub"])

    def test_general_status_omits_captured_image_data(self):
        started = self.run_async(self.controller.request_card_capture())
        self.run_async(
            self.controller.complete_card_capture(
                request_id=started["request_id"],
                status="captured",
                metadata={
                    "processor": "portrait_edges",
                    "image_jpeg_base64": "large-image-payload",
                },
            )
        )

        request_status = self.run_async(self.controller.get_card_capture(started["request_id"]))
        general_status = self.controller.get_status()

        self.assertEqual(
            request_status["result"]["metadata"]["image_jpeg_base64"],
            "large-image-payload",
        )
        self.assertNotIn(
            "metadata",
            general_status["features"]["card_capture"]["result"],
        )
        self.assertNotIn(
            "image_jpeg_base64",
            general_status["features"]["card_capture"]["metadata"],
        )

    def test_reject_unknown_result(self):
        result = self.run_async(
            self.controller.complete_card_capture(
                request_id="missing",
                status="captured",
            )
        )

        self.assertEqual(result["status"], "not_found")

    def test_expire_request_after_timeout(self):
        started = self.run_async(self.controller.request_card_capture(timeout_s=0.1))
        time.sleep(0.2)
        result = self.run_async(
            self.controller.get_card_capture(started["request_id"])
        )

        self.assertEqual(result["state"], "expired")
        self.assertFalse(result["active"])

    def test_active_card_capture_suppresses_person_left(self):
        self.mark_person_present()
        started = self.run_async(self.controller.request_card_capture())
        result = self.run_async(self.controller.queue_person_left(timestamp=time.time()))

        self.assertEqual(result["status"], "suppressed_card_capture_active")
        self.assertTrue(result["person_present"])
        self.assertEqual(result["track_id"], "track-1")
        self.assertEqual(result["card_capture"]["request_id"], started["request_id"])

    def test_active_card_capture_marks_dispatch_paused_without_disabling_tracker(self):
        self.mark_person_present()
        started = self.run_async(self.controller.request_card_capture())

        status = self.controller.get_status()

        self.assertTrue(status["active"])
        self.assertTrue(status["enabled"])
        self.assertTrue(status["service"]["running"])
        self.assertTrue(status["card_capture_dispatch_paused"])
        self.assertTrue(status["features"]["dispatch"]["enabled"])
        self.assertFalse(status["features"]["dispatch"]["active"])
        self.assertTrue(status["features"]["dispatch"]["paused"])
        self.assertTrue(status["features"]["card_capture"]["active"])
        self.assertEqual(status["features"]["card_capture"]["request_id"], started["request_id"])

    def test_completed_card_capture_resumes_person_left_without_release(self):
        self.mark_person_present()
        started = self.run_async(self.controller.request_card_capture())
        self.run_async(
            self.controller.complete_card_capture(
                request_id=started["request_id"],
                status="captured",
            )
        )
        result = self.run_async(self.controller.queue_person_left(timestamp=time.time()))

        self.assertEqual(result["status"], "accepted")
        self.assertFalse(self.controller.get_status()["card_capture_person_left_hold"])
        self.assertEqual(self.manual_controller.wrap_calls, 0)

    def test_failed_card_capture_resumes_person_left(self):
        self.mark_person_present()
        started = self.run_async(self.controller.request_card_capture())
        self.run_async(
            self.controller.complete_card_capture(
                request_id=started["request_id"],
                status="failed",
            )
        )

        result = self.run_async(self.controller.queue_person_left(timestamp=time.time()))

        self.assertEqual(result["status"], "accepted")
        self.assertFalse(self.controller.get_status()["card_capture_person_left_hold"])

    def test_cancelled_card_capture_resumes_person_left(self):
        self.mark_person_present()
        started = self.run_async(self.controller.request_card_capture())
        self.run_async(self.controller.cancel_card_capture(started["request_id"]))

        result = self.run_async(self.controller.queue_person_left(timestamp=time.time()))

        self.assertEqual(result["status"], "accepted")
        self.assertFalse(self.controller.get_status()["card_capture_person_left_hold"])

    def test_expired_card_capture_resumes_person_left(self):
        self.mark_person_present()
        self.run_async(self.controller.request_card_capture(timeout_s=0.1))
        time.sleep(0.2)

        result = self.run_async(self.controller.queue_person_left(timestamp=time.time()))

        self.assertEqual(result["status"], "accepted")
        self.assertFalse(self.controller.get_status()["card_capture_person_left_hold"])

    def test_person_detected_after_completed_card_capture_allows_person_left(self):
        self.mark_person_present()
        started = self.run_async(self.controller.request_card_capture())
        self.run_async(
            self.controller.complete_card_capture(
                request_id=started["request_id"],
                status="captured",
            )
        )

        detected = self.run_async(self.controller.queue_person_detected(track_id="track-1"))
        result = self.run_async(self.controller.queue_person_left(timestamp=time.time()))

        self.assertEqual(detected["status"], "already_present")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(self.manual_controller.wrap_calls, 0)

    def test_card_capture_reacquire_timeout_person_left_bypasses_active_capture_hold(self):
        self.mark_person_present()
        started = self.run_async(self.controller.request_card_capture())
        result = self.run_async(
            self.controller.queue_person_left(
                timestamp=time.time(),
                reason=vision_main.CARD_CAPTURE_REACQUIRE_TIMEOUT_REASON,
            )
        )

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["process_delay_s"], 0.0)
        self.assertEqual(result["previous_track_id"], "track-1")
        self.assertFalse(self.controller.get_status()["card_capture_person_left_hold"])
        self.assertEqual(self.manual_controller.wrap_calls, 0)

    def test_card_capture_reacquire_timeout_complete_wraps_even_while_capture_active(self):
        self.mark_person_present()
        self.run_async(self.controller.request_card_capture())
        queued = self.run_async(
            self.controller.queue_person_left(
                timestamp=time.time(),
                reason=vision_main.CARD_CAPTURE_REACQUIRE_TIMEOUT_REASON,
            )
        )

        result = self.run_async(
            self.controller.complete_person_left(
                queued["event_sequence"],
                previous_track_id=queued["previous_track_id"],
            )
        )

        self.assertEqual(result["status"], "person_left")
        self.assertEqual(self.manual_controller.wrap_calls, 1)

    def test_stale_queued_person_left_is_dropped_when_card_capture_starts(self):
        self.mark_person_present()
        queued = self.run_async(self.controller.queue_person_left(timestamp=time.time()))
        started = self.run_async(self.controller.request_card_capture())
        self.run_async(
            self.controller.complete_card_capture(
                request_id=started["request_id"],
                status="captured",
            )
        )

        result = self.run_async(
            self.controller.complete_person_left(
                queued["event_sequence"],
                previous_track_id=queued["previous_track_id"],
            )
        )

        self.assertEqual(result["status"], "event_not_found")
        self.assertEqual(self.manual_controller.wrap_calls, 0)

    def test_idle_card_capture_allows_person_left(self):
        self.mark_person_present()
        result = self.run_async(self.controller.queue_person_left(timestamp=time.time()))

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["previous_track_id"], "track-1")
        self.assertGreaterEqual(result["process_delay_s"], 0)

    def test_person_left_after_detected_conversation_is_confirmed_later(self):
        detected = self.run_async(self.controller.person_detected(track_id="track-1"))
        result = self.run_async(self.controller.queue_person_left(timestamp=time.time()))

        self.assertEqual(detected["status"], "person_detected")
        self.assertEqual(result["status"], "accepted")
        self.assertGreater(result["process_delay_s"], 0)
        self.assertEqual(self.manual_controller.wrap_calls, 0)

    def test_person_reacquisition_clears_pending_person_left(self):
        self.mark_person_present()
        queued = self.run_async(self.controller.queue_person_left(timestamp=time.time()))
        detected = self.run_async(self.controller.queue_person_detected(track_id="track-1"))

        result = self.run_async(
            self.controller.complete_person_left(
                queued["event_sequence"],
                previous_track_id=queued["previous_track_id"],
            )
        )

        self.assertEqual(detected["status"], "already_present")
        self.assertEqual(result["status"], "event_not_found")
        self.assertEqual(self.manual_controller.wrap_calls, 0)


class VisionControllerServiceConfigTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_id_scanning_config_update_does_not_restart_running_detector(self):
        service = VisionControllerService(
            "vision-controller",
            {
                "detector_script": "robot_services/vision/detection/main.py",
                "enable_id_scanning": False,
            },
        )
        service._state = ServiceState.RUNNING

        with mock.patch.object(service, "restart", new=mock.AsyncMock()) as restart:
            self.run_async(service.update_config({"enable_id_scanning": True}))

        restart.assert_not_awaited()
        self.assertTrue(service.get_config()["enable_id_scanning"])

    def test_camera_config_update_restarts_running_detector(self):
        service = VisionControllerService(
            "vision-controller",
            {
                "detector_script": "robot_services/vision/detection/main.py",
                "camera_id": "0",
            },
        )
        service._state = ServiceState.RUNNING

        with mock.patch.object(service, "restart", new=mock.AsyncMock()) as restart:
            self.run_async(service.update_config({"camera_id": "1"}))

        restart.assert_awaited_once()
        self.assertEqual(service.get_config()["camera_id"], "1")


class FakePublisher:
    def __init__(self):
        self.calls = []

    def reset(self):
        self.calls = []

    def publish_if_changed(self, locked_track_id, timings=None):
        self.calls.append((locked_track_id, timings))
        return "locked" if locked_track_id is not None else "unlocked"


class FakeDetector:
    def detect_and_track(self, frame):
        return []


class FakeLockManager:
    def __init__(self, locked_track_id=None, update_result=None):
        self.locked_track_id = locked_track_id
        self.update_result = update_result

    def update(self, persons, candidate_person=None):
        return self.update_result


class PersonLockProcessorSuppressionTests(unittest.TestCase):
    def make_processor(self):
        processor = PersonLockProcessor.__new__(PersonLockProcessor)
        processor.detector = FakeDetector()
        processor.lock_manager = FakeLockManager()
        processor.publisher = FakePublisher()
        processor.detection_counter = None
        processor.last_locked_visible = False
        return processor

    def test_constructor_defaults_to_live_publisher_and_accepts_injected_local_publisher(self):
        publisher = FakePublisher()

        with mock.patch.object(vision_main.torch.cuda, "is_available", return_value=False), mock.patch.object(
            vision_main,
            "PersonDetector",
        ), mock.patch.object(vision_main, "LockManager"):
            processor = PersonLockProcessor(publisher=publisher)
            default_processor = PersonLockProcessor()

        self.assertIs(processor.publisher, publisher)
        self.assertIsInstance(default_processor.publisher, VisionEventPublisher)

    def test_suppressed_unlock_does_not_publish_person_left(self):
        processor = self.make_processor()
        frame = np.zeros((32, 32, 3), dtype=np.uint8)

        locked_track_id, event = processor.process_frame(
            frame,
            suppress_unlock_event=True,
        )

        self.assertIsNone(locked_track_id)
        self.assertIsNone(event)
        self.assertEqual(processor.publisher.calls, [])

    def test_unsuppressed_unlock_publishes_person_left(self):
        processor = self.make_processor()
        frame = np.zeros((32, 32, 3), dtype=np.uint8)

        locked_track_id, event = processor.process_frame(
            frame,
            suppress_unlock_event=False,
        )

        self.assertIsNone(locked_track_id)
        self.assertEqual(event, "unlocked")
        self.assertEqual(len(processor.publisher.calls), 1)

    def test_suppressed_stale_lock_does_not_publish_person_detected(self):
        processor = self.make_processor()
        processor.lock_manager = FakeLockManager(locked_track_id="old-track", update_result="old-track")
        frame = np.zeros((32, 32, 3), dtype=np.uint8)

        locked_track_id, event = processor.process_frame(
            frame,
            suppress_unlock_event=True,
        )

        self.assertEqual(locked_track_id, "old-track")
        self.assertFalse(processor.last_locked_visible)
        self.assertIsNone(event)
        self.assertEqual(processor.publisher.calls, [])

    def test_suppressed_visible_lock_does_not_publish_person_detected(self):
        processor = self.make_processor()
        processor.detector = SimpleNamespace(
            detect_and_track=lambda frame: [
                {"track_id": "track-1", "bbox": [4, 0, 24, 31]},
            ]
        )
        processor.lock_manager = FakeLockManager(locked_track_id="track-1", update_result="track-1")
        frame = np.zeros((32, 32, 3), dtype=np.uint8)

        locked_track_id, event = processor.process_frame(
            frame,
            suppress_unlock_event=True,
        )

        self.assertEqual(locked_track_id, "track-1")
        self.assertTrue(processor.last_locked_visible)
        self.assertIsNone(event)
        self.assertEqual(processor.publisher.calls, [])


class CardCaptureReacquireHoldTests(unittest.TestCase):
    def make_hold(self, timeout_s=10.0):
        return vision_main.CardCaptureReacquireHold(timeout_s, log=mock.Mock())

    def test_timeout_publishes_only_until_marked(self):
        hold = self.make_hold(timeout_s=10.0)
        hold.arm("capture-1", now=100.0)

        self.assertFalse(hold.should_publish_timeout_left(109.9))
        self.assertTrue(hold.should_publish_timeout_left(110.0))
        hold.mark_left_published()
        self.assertFalse(hold.should_publish_timeout_left(111.0))

    def test_observation_does_not_start_timeout_until_armed(self):
        hold = self.make_hold(timeout_s=10.0)
        hold.observe("capture-1")

        self.assertTrue(hold.active)
        self.assertFalse(hold.armed)
        self.assertFalse(hold.should_publish_timeout_left(120.0))

        hold.arm("capture-1", now=120.0)

        self.assertTrue(hold.armed)
        self.assertFalse(hold.should_publish_timeout_left(129.9))
        self.assertTrue(hold.should_publish_timeout_left(130.0))

    def test_reacquired_lock_prevents_timeout_left(self):
        hold = self.make_hold(timeout_s=10.0)
        hold.observe("capture-1")

        self.assertTrue(hold.note_visible_lock("track-1"))
        hold.arm("capture-1", now=100.0)
        self.assertFalse(hold.should_publish_timeout_left(120.0))
        self.assertTrue(hold.reacquired)
        self.assertEqual(hold.track_id, "track-1")


class FakeVisionStatusResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class VisionRuntimeStateTests(unittest.TestCase):
    def test_dispatch_paused_keeps_dispatch_processing_enabled_for_suppressed_tracking(self):
        runtime_state = vision_main.VisionRuntimeState(
            "http://test.local/api/vision",
            poll_interval_seconds=0.0,
            default_active=True,
        )
        response = FakeVisionStatusResponse(
            {
                "active": True,
                "features": {
                    "dispatch": {
                        "enabled": True,
                        "active": False,
                        "paused": True,
                    },
                    "card_capture": {
                        "active": False,
                        "state": "idle",
                    },
                },
            }
        )

        with mock.patch.object(vision_main.requests, "get", return_value=response):
            state = runtime_state.refresh_if_due(now=0.0)

        self.assertTrue(state["dispatch_active"])
        self.assertTrue(state["dispatch_paused"])


def make_card_frame(
    *,
    width=960,
    height=540,
    card_width=430,
    card_height=270,
    center_x=480,
    center_y=270,
    detail=True,
):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    x1 = int(center_x - card_width / 2)
    y1 = int(center_y - card_height / 2)
    x2 = int(center_x + card_width / 2)
    y2 = int(center_y + card_height / 2)
    cv2 = __import__("cv2")
    cv2.rectangle(frame, (x1, y1), (x2, y2), (185, 185, 185), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (35, 35, 35), 3)
    if detail:
        cv2.rectangle(frame, (x1 + 30, y1 + 35), (x1 + 125, y1 + 130), (70, 70, 70), 2)
        for index in range(5):
            y = y1 + 45 + index * 32
            cv2.line(frame, (x1 + 160, y), (x2 - 35, y), (45, 45, 45), 3)
        cv2.line(frame, (x1 + 30, y2 - 55), (x2 - 30, y2 - 55), (60, 60, 60), 3)
        cv2.line(frame, (x1 + 30, y2 - 30), (x2 - 120, y2 - 30), (60, 60, 60), 3)
    return frame


class CardCaptureProcessorOpenCVTests(unittest.TestCase):
    def process_frames(self, processor, frames):
        result = None
        request = {"request_id": "capture-1", "target_type": "id_card", "source": "unit-test"}
        for frame in frames:
            result = processor.process_frame(frame, request)
        return result

    def test_portrait_debug_reports_no_anchor_without_face_detection(self):
        processor = CardCaptureProcessor()
        frame = make_card_frame()

        with patched_face_boxes([]):
            debug_info = processor.inspect_portrait_edges(frame)

        self.assertEqual(debug_info["mode"], "portrait_debug")
        self.assertEqual(debug_info["face_count"], 0)
        self.assertIsNone(debug_info["portrait"])
        self.assertEqual(debug_info["candidates"], [])
        self.assertEqual(debug_info["reason"], "no_portrait_anchor")

    def test_portrait_debug_uses_small_face_anchor_to_find_card_edges(self):
        processor = CardCaptureProcessor()
        frame = make_card_frame()
        face_box = roi_local_box(frame, (295, 170, 95, 95))

        with patched_face_boxes([face_box]):
            debug_info = processor.inspect_portrait_edges(frame)

        self.assertEqual(debug_info["mode"], "portrait_debug")
        self.assertEqual(debug_info["face_count"], 1)
        self.assertIsNotNone(debug_info["portrait"])
        self.assertGreater(debug_info["line_count"], 0)
        self.assertGreater(len(debug_info["candidates"]), 0)
        best = debug_info["candidates"][0]
        self.assertTrue(best["accepted"])
        self.assertAlmostEqual(best["aspect_ratio"], 1.58, delta=0.35)
        self.assertGreater(best["line_coverage"], 0.25)

    def test_portrait_edges_captures_through_id_flow_after_stable_frames(self):
        processor = CardCaptureProcessor()
        frame = make_card_frame()
        face_box = roi_local_box(frame, (295, 170, 95, 95))

        with patched_face_boxes([face_box]):
            result = self.process_frames(processor, [frame, frame, frame])

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["metadata"]["processor"], "portrait_edges")
        self.assertGreater(len(result["metadata"]["image_jpeg_base64"]), 1000)
        quality = result["metadata"]["quality"]
        self.assertGreaterEqual(quality["stable_frames"], 3)
        self.assertGreaterEqual(quality["line_coverage"], 0.25)
        self.assertGreater(quality["portrait_area_ratio"], 0.003)
        self.assertGreaterEqual(quality["portrait_frame_area_ratio"], 0.12)
        self.assertEqual(quality["portrait_min_frame_area_ratio"], 0.12)

    def test_portrait_edges_rejects_small_far_card_in_id_flow(self):
        processor = CardCaptureProcessor()
        frame = make_card_frame(card_width=280, card_height=176)
        face_box = roi_local_box(frame, (400, 225, 46, 46))

        with patched_face_boxes([face_box]):
            debug_info = processor.inspect_portrait_edges(frame)
            result = self.process_frames(processor, [frame, frame, frame])

        self.assertGreater(len(debug_info["candidates"]), 0)
        self.assertTrue(debug_info["candidates"][0]["accepted"])
        self.assertLess(debug_info["candidates"][0]["area_ratio"], 0.20)
        self.assertIsNone(result)

    def test_portrait_debug_rejects_large_live_face_anchor(self):
        processor = CardCaptureProcessor()
        frame = make_card_frame()
        face_box = roi_local_box(frame, (300, 110, 260, 260))

        with patched_face_boxes([face_box]):
            debug_info = processor.inspect_portrait_edges(frame)

        self.assertEqual(debug_info["face_count"], 1)
        self.assertIsNone(debug_info["portrait"])
        self.assertEqual(debug_info["candidates"], [])
        self.assertIn("face_area=", debug_info["faces"][0]["reason"])

def roi_local_box(frame, frame_box):
    frame_height, frame_width = frame.shape[:2]
    roi_points = card_capture._guide_points(frame_width, frame_height)
    x1, y1, _x2, _y2 = card_capture._axis_aligned_bounds(roi_points, frame_width, frame_height)
    x, y, width, height = frame_box
    return (x - x1, y - y1, width, height)


class patched_face_boxes:
    def __init__(self, boxes):
        self.boxes = list(boxes)
        self.original = None

    def __enter__(self):
        self.original = card_capture._detect_face_boxes

        def fake_detect_face_boxes(_image):
            return list(self.boxes)

        card_capture._detect_face_boxes = fake_detect_face_boxes

    def __exit__(self, exc_type, exc, tb):
        card_capture._detect_face_boxes = self.original


if __name__ == "__main__":
    unittest.main()
