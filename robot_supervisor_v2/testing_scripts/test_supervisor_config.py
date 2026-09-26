import tempfile
import unittest
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_supervisor_v2.app.supervisor_config import (
    SupervisorConfigError,
    load_supervisor_config,
)


class SupervisorConfigTests(unittest.TestCase):
    def _write_config(self, text: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_lean_sections_and_preserves_legacy_services(self) -> None:
        config_path = self._write_config(
            """
robot:
  id: agibot-a2-ultra-01
  name: "A2 Ultra"
  location: "Ljubljana lab"
  platform: agibot
  model: agibot_a2_ultra

api:
  host: 127.0.0.1
  port: 8081
  token: null

devices:
  audio:
    default_microphone: "1"
    default_speakers: "2"
  camera:
    default_device: "/dev/video10"

services:
  - name: voice-agent
    type: voice-agent
    config:
      selected_agent: agent_main
health_monitoring:
  enabled: true
engagement:
  default_room: main-room
command_presets: []
"""
        )

        config = load_supervisor_config(config_path)

        self.assertEqual(config.robot.id, "agibot-a2-ultra-01")
        self.assertEqual(config.robot.name, "A2 Ultra")
        self.assertEqual(config.api.port, 8081)
        self.assertEqual(config.devices.audio.default_microphone, "1")
        self.assertEqual(config.devices.audio.default_speakers, "2")
        self.assertEqual(config.devices.camera.default_device, "/dev/video10")
        self.assertEqual(config.services[0]["name"], "voice-agent")
        self.assertEqual(
            config.robot.to_service_context(),
            {
                "id": "agibot-a2-ultra-01",
                "name": "A2 Ultra",
                "platform": "agibot",
                "model": "agibot_a2_ultra",
            },
        )

    def test_defaults_optional_device_sections(self) -> None:
        config_path = self._write_config(
            """
robot:
  id: agibot-x2-ultra-01
  name: "X2 Ultra"
  platform: agibot
  model: agibot_x2_ultra
services: []
"""
        )

        config = load_supervisor_config(config_path)

        self.assertEqual(config.api.host, "0.0.0.0")
        self.assertEqual(config.api.port, 8080)
        self.assertIsNone(config.devices.audio.default_microphone)
        self.assertIsNone(config.devices.audio.default_speakers)
        self.assertIsNone(config.devices.camera.default_device)

    def test_missing_robot_name_falls_back_to_model_display_name(self) -> None:
        config_path = self._write_config(
            """
robot:
  id: agibot-a2-ultra-01
  platform: agibot
  model: agibot_a2_ultra
services: []
"""
        )

        config = load_supervisor_config(config_path)

        self.assertEqual(config.robot.name, "Agibot A2 Ultra")
        self.assertEqual(config.robot.to_service_context()["name"], "Agibot A2 Ultra")

    def test_null_robot_name_falls_back_to_model_display_name(self) -> None:
        config_path = self._write_config(
            """
robot:
  id: agibot-x2-ultra-01
  name: null
  platform: agibot
  model: agibot_x2_ultra
services: []
"""
        )

        config = load_supervisor_config(config_path)

        self.assertEqual(config.robot.name, "Agibot X2 Ultra")
        self.assertEqual(config.robot.to_service_context()["name"], "Agibot X2 Ultra")

    def test_blank_robot_name_falls_back_to_model_display_name(self) -> None:
        config_path = self._write_config(
            """
robot:
  id: unitree-g1-edu-01
  name: "   "
  platform: unitree
  model: unitree_g1_edu
services: []
"""
        )

        config = load_supervisor_config(config_path)

        self.assertEqual(config.robot.name, "Unitree G1 Edu")
        self.assertEqual(config.robot.to_service_context()["name"], "Unitree G1 Edu")

    def test_robot_name_is_trimmed_when_configured(self) -> None:
        config_path = self._write_config(
            """
robot:
  id: unitree-g1-edu-01
  name: "  Primus  "
  platform: unitree
  model: unitree_g1_edu
services: []
"""
        )

        config = load_supervisor_config(config_path)

        self.assertEqual(config.robot.name, "Primus")
        self.assertEqual(config.robot.to_service_context()["name"], "Primus")

    def test_rejects_model_platform_mismatch(self) -> None:
        config_path = self._write_config(
            """
robot:
  id: bad-robot
  name: "Bad Robot"
  platform: agibot
  model: unitree_g1_edu
services: []
"""
        )

        with self.assertRaises(SupervisorConfigError) as raised:
            load_supervisor_config(config_path)

        self.assertIn("belongs to platform 'unitree'", str(raised.exception))

    def test_example_config_is_valid(self) -> None:
        config = load_supervisor_config(Path("robot_supervisor_v2/config.example.yaml"))

        self.assertEqual(config.robot.platform.value, "unitree")
        self.assertTrue(config.services)


if __name__ == "__main__":
    unittest.main()
