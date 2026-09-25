import asyncio
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_supervisor_v2.app.services.base import ServiceState
from robot_supervisor_v2.app.services.robot_temperature_monitor import (
    RobotTemperatureMonitorService,
)
from robot_supervisor_v2.app.supervisor_config import SERVICE_ROBOT_CONTEXT_KEY


def _service_for_robot(*, platform: str, model: str) -> RobotTemperatureMonitorService:
    return RobotTemperatureMonitorService(
        "robot-temperature-monitor",
        {
            "display_name": "Robot Temperature Monitor",
            "topic": "rt/lowstate",
            SERVICE_ROBOT_CONTEXT_KEY: {
                "id": f"{model}-test",
                "name": model,
                "platform": platform,
                "model": model,
            },
        },
    )


class RobotTemperatureMonitorPlatformTests(unittest.TestCase):
    def test_unitree_g1_context_is_supported_without_loading_sdk(self) -> None:
        service = _service_for_robot(platform="unitree", model="unitree_g1_edu")

        state = service.get_temperature_state()

        self.assertFalse(service.is_optional())
        self.assertTrue(state["supported"])
        self.assertIsNone(state["unsupported_reason"])
        self.assertEqual(state["robot"]["platform"], "unitree")
        self.assertEqual(state["robot"]["model"], "unitree_g1_edu")

    def test_agibot_context_fails_cleanly_before_sdk_loading(self) -> None:
        service = _service_for_robot(platform="agibot", model="agibot_x2_ultra")

        self.assertTrue(service.is_optional())
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(service.start())

        self.assertIn("not supported", str(raised.exception))
        self.assertEqual(service.get_status().state, ServiceState.FAILED)

        state = service.get_temperature_state()
        self.assertFalse(state["supported"])
        self.assertIn("not supported", state["unsupported_reason"])
        self.assertIn("not supported", state["last_error"])

    def test_missing_robot_context_fails_cleanly(self) -> None:
        service = RobotTemperatureMonitorService(
            "robot-temperature-monitor",
            {
                "display_name": "Robot Temperature Monitor",
                "topic": "rt/lowstate",
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
