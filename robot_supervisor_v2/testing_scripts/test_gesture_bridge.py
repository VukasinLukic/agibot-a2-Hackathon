import asyncio
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_supervisor_v2.app.services.base import ServiceState
from robot_supervisor_v2.app.services.gesture_bridge import GestureBridgeService
from robot_supervisor_v2.app.supervisor_config import SERVICE_ROBOT_CONTEXT_KEY


def _service_for_robot(*, platform: str, model: str) -> GestureBridgeService:
    return GestureBridgeService(
        "gesture-bridge",
        {
            "display_name": "Gesture Bridge",
            "script": "robot_services/gestures/gesture_api.py",
            "port": 8090,
            SERVICE_ROBOT_CONTEXT_KEY: {
                "id": f"{model}-test",
                "name": model,
                "platform": platform,
                "model": model,
            },
        },
    )


class GestureBridgePlatformTests(unittest.TestCase):
    def test_unitree_context_resolves_backend_without_loading_sdk(self) -> None:
        sys.modules.pop("unitree_sdk2py", None)
        service = _service_for_robot(platform="unitree", model="unitree_g1_edu")

        state = service.get_gesture_state()

        self.assertFalse(service.is_optional())
        self.assertTrue(state["supported"])
        self.assertIsNone(state["unsupported_reason"])
        self.assertEqual(state["backend"], "unitree_g1_arm_actions")
        self.assertEqual(state["catalog_id"], "unitree_g1_edu")
        self.assertNotIn("unitree_sdk2py", sys.modules)

    def test_agibot_a2_context_resolves_backend(self) -> None:
        service = _service_for_robot(platform="agibot", model="agibot_a2_ultra")

        state = service.get_gesture_state()

        self.assertFalse(service.is_optional())
        self.assertTrue(state["supported"])
        self.assertIsNone(state["unsupported_reason"])
        self.assertEqual(state["backend"], "agibot_a2_motion_player")
        self.assertEqual(state["catalog_id"], "agibot_a2_ultra")

    def test_agibot_x2_context_fails_cleanly_before_subprocess_start(self) -> None:
        service = _service_for_robot(platform="agibot", model="agibot_x2_ultra")

        self.assertTrue(service.is_optional())
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(service.start())

        self.assertIn("not supported", str(raised.exception))
        self.assertEqual(service.get_status().state, ServiceState.FAILED)
        self.assertIsNone(service.get_status().pid)

        state = service.get_gesture_state()
        self.assertFalse(state["supported"])
        self.assertIn("not supported", state["unsupported_reason"])
        self.assertIn("not supported", state["last_error"])

    def test_missing_robot_context_fails_cleanly(self) -> None:
        service = GestureBridgeService(
            "gesture-bridge",
            {
                "display_name": "Gesture Bridge",
                "script": "robot_services/gestures/gesture_api.py",
                "port": 8090,
            },
        )

        self.assertTrue(service.is_optional())
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(service.start())

        self.assertIn("requires supervisor robot config context", str(raised.exception))
        self.assertEqual(service.get_status().state, ServiceState.FAILED)

    def test_robot_context_platform_mismatch_fails_cleanly(self) -> None:
        service = _service_for_robot(platform="agibot", model="unitree_g1_edu")

        self.assertTrue(service.is_optional())
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(service.start())

        self.assertIn("config mismatch", str(raised.exception))
        self.assertEqual(service.get_status().state, ServiceState.FAILED)


if __name__ == "__main__":
    unittest.main()

