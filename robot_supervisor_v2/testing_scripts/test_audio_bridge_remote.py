import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_supervisor_v2.app.services.audio_bridge import AudioBridgeService
from robot_supervisor_v2.app.services.audio_bridge_remote import AudioBridgeRemoteService
from robot_supervisor_v2.app.services.base import ServiceState
from robot_supervisor_v2.app.services.registry import ServiceRegistry
from robot_supervisor_v2.app.supervisor_config import SERVICE_ROBOT_CONTEXT_KEY


class FakeRemoteAudioBridgeService(AudioBridgeRemoteService):
    def __init__(self, responses=None, config=None):
        super().__init__(
            "audio-bridge",
            {
                "display_name": "Audio Bridge",
                "manager_url": "http://pc3:8766",
                "manager_token": "secret-token",
                "default_microphone": "plughw:CARD=RX,DEV=0",
                "default_speakers": "plughw:CARD=Speaker,DEV=0",
                "enable_rnnoise": True,
                **(config or {}),
            },
        )
        self.requests = []
        self.responses = list(responses or [])

    def _manager_request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return {"child": {"running": True, "pid": 4321, "status": {"running": True}}}


def _robot_context(platform: str, model: str) -> dict:
    return {
        SERVICE_ROBOT_CONTEXT_KEY: {
            "id": f"{model}-test",
            "name": model,
            "platform": platform,
            "model": model,
        }
    }


class AudioBridgeRemoteServiceTests(unittest.TestCase):
    def test_registry_selects_remote_for_x2_and_local_for_local_models(self):
        x2_service = ServiceRegistry.create_service(
            "audio-bridge",
            "audio-bridge",
            {
                "manager_url": "http://pc3:8766",
                "manager_token": "secret-token",
                **_robot_context("agibot", "agibot_x2_ultra"),
            },
        )
        self.assertIsInstance(x2_service, AudioBridgeRemoteService)

        g1_service = ServiceRegistry.create_service(
            "audio-bridge",
            "audio-bridge",
            _robot_context("unitree", "unitree_g1_edu"),
        )
        self.assertIsInstance(g1_service, AudioBridgeService)

        legacy_service = ServiceRegistry.create_service(
            "audio-bridge",
            "audio-bridge",
            {"script": "robot_services/audio/audio_bridge.py"},
        )
        self.assertIsInstance(legacy_service, AudioBridgeService)

    def test_a2_local_audio_bridge_enables_aima_on_pc2(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch(
            "robot_services.agibot.aima_em.subprocess.run",
            return_value=completed,
        ) as run:
            service = ServiceRegistry.create_service(
                "audio-bridge",
                "audio-bridge",
                _robot_context("agibot", "agibot_a2_ultra"),
            )

        self.assertIsInstance(service, AudioBridgeService)
        self.assertEqual(service.aima.status()["current_mode"], "audio_bridge")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["aima", "em", "stop-app", "agent"],
                ["aima", "em", "stop-app", "hal_audio"],
            ],
        )

    def test_a2_local_audio_bridge_aima_can_be_disabled_explicitly(self):
        with mock.patch("robot_services.agibot.aima_em.subprocess.run") as run:
            service = ServiceRegistry.create_service(
                "audio-bridge",
                "audio-bridge",
                {
                    "agibot_aima": {"enabled": False},
                    **_robot_context("agibot", "agibot_a2_ultra"),
                },
            )

        self.assertIsInstance(service, AudioBridgeService)
        self.assertEqual(service.aima.status()["current_mode"], "disabled")
        run.assert_not_called()

    def test_local_aima_methods_control_pc2_mode(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        service = AudioBridgeService(
            "audio-bridge",
            {"agibot_aima": {"enabled": True, "reconcile_on_manager_start": False}},
        )

        with mock.patch(
            "robot_services.agibot.aima_em.subprocess.run",
            return_value=completed,
        ) as run:
            agibot = asyncio.run(service.set_aima_agibot_mode())
            audio_bridge = asyncio.run(service.set_aima_audio_bridge_mode())

        self.assertEqual(agibot["current_mode"], "agibot")
        self.assertEqual(audio_bridge["current_mode"], "audio_bridge")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["aima", "em", "start-app", "hal_audio"],
                ["aima", "em", "start-app", "agent"],
                ["aima", "em", "stop-app", "agent"],
                ["aima", "em", "stop-app", "hal_audio"],
            ],
        )

    def test_local_aima_agibot_mode_rejected_while_bridge_running(self):
        service = AudioBridgeService(
            "audio-bridge",
            {"agibot_aima": {"enabled": True, "reconcile_on_manager_start": False}},
        )
        service._state = ServiceState.RUNNING
        process = mock.Mock()
        process.poll.return_value = None
        service._process = process

        with mock.patch("robot_services.agibot.aima_em.subprocess.run") as run:
            with self.assertRaises(RuntimeError):
                asyncio.run(service.set_aima_agibot_mode())

        run.assert_not_called()

    def test_local_start_passes_input_capture_and_mix_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = AudioBridgeService(
                "audio-bridge",
                {
                    "script": "robot_services/audio/audio_bridge.py",
                    "working_dir": tmp,
                    "default_microphone": "hw:2,0",
                    "default_speakers": "hw:3,0",
                    "input_capture_channels": 8,
                    "input_mix_channels": [0, 1],
                    "agibot_aima": {"enabled": False},
                },
            )
            service._log_file = str(Path(tmp) / "audio-bridge.log")
            process = mock.Mock()
            process.poll.return_value = None

            with (
                mock.patch(
                    "robot_supervisor_v2.app.services.audio_bridge.subprocess.Popen",
                    return_value=process,
                ) as popen,
                mock.patch(
                    "robot_supervisor_v2.app.services.audio_bridge.asyncio.sleep",
                    new=mock.AsyncMock(),
                ),
            ):
                asyncio.run(service.start())

            command = popen.call_args.args[0]
            self.assertIn("--input-device", command)
            self.assertIn("hw:2,0", command)
            self.assertIn("--input-capture-channels", command)
            self.assertIn("8", command)
            self.assertEqual(command.count("--input-mix-channel"), 2)
            self.assertIn("0", command)
            self.assertIn("1", command)

    def test_supervisor_startup_config_injects_robot_context_for_audio_bridge(self):
        from robot_supervisor_v2.app.api.main import _service_startup_config

        config = _service_startup_config(
            {
                "name": "audio-bridge",
                "type": "audio-bridge",
                "config": {"display_name": "Audio Bridge"},
            },
            {
                "id": "agibot-x2-test",
                "name": "X2",
                "platform": "agibot",
                "model": "agibot_x2_ultra",
            },
        )

        self.assertEqual(config[SERVICE_ROBOT_CONTEXT_KEY]["model"], "agibot_x2_ultra")

    def test_registry_rejects_audio_robot_context_platform_mismatch(self):
        with self.assertRaises(ValueError) as raised:
            ServiceRegistry.create_service(
                "audio-bridge",
                "audio-bridge",
                _robot_context("agibot", "unitree_g1_edu"),
            )

        self.assertIn("Audio bridge config mismatch", str(raised.exception))

    def test_start_sends_transient_payload_and_stop_calls_manager(self):
        service = FakeRemoteAudioBridgeService(
            responses=[
                {"child": {"running": True, "pid": 1234, "status": {"running": True}}},
                {"child": {"running": False, "pid": None, "status": None}},
            ],
            config={
                "input_capture_channels": 8,
                "input_mix_channels": [0],
            },
        )

        asyncio.run(service.start())

        self.assertEqual(service.get_status().state, ServiceState.RUNNING)
        self.assertEqual(service.get_status().pid, 1234)
        self.assertEqual(
            service.requests[0],
            (
                "POST",
                "/start",
                {
                    "input_device": "plughw:CARD=RX,DEV=0",
                    "output_devices": ["plughw:CARD=Speaker,DEV=0"],
                    "input_capture_channels": 8,
                    "input_mix_channels": [0],
                    "enable_aec": True,
                    "enable_rnnoise": True,
                },
            ),
        )

        asyncio.run(service.stop())

        self.assertEqual(service.requests[-1], ("POST", "/stop", None))
        self.assertEqual(service.get_status().state, ServiceState.STOPPED)

    def test_start_marks_service_failed_when_manager_does_not_start_child(self):
        service = FakeRemoteAudioBridgeService(
            responses=[{"child": {"running": False, "status": None}}]
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(service.start())

        self.assertEqual(service.get_status().state, ServiceState.FAILED)
        self.assertIn("did not report a ready child", service._last_error)

    def test_failed_service_without_running_child_can_be_stopped_without_manager_call(self):
        service = FakeRemoteAudioBridgeService()
        service._state = ServiceState.FAILED
        service._last_error = "missing manager_url"

        asyncio.run(service.stop())

        self.assertEqual(service.get_status().state, ServiceState.STOPPED)
        self.assertEqual(service.requests, [])

    def test_get_config_redacts_manager_token(self):
        service = FakeRemoteAudioBridgeService()

        config = service.get_config()

        self.assertEqual(config["manager_token"], "<redacted>")
        self.assertEqual(service._config["manager_token"], "secret-token")

    def test_devices_are_read_from_manager_payload(self):
        service = FakeRemoteAudioBridgeService(
            responses=[
                {
                    "input_devices": [
                        {
                            "index": 1,
                            "name": "PC3 Mic",
                            "channels": 1,
                            "sample_rate": 48000,
                            "hostapi": "ALSA",
                            "alsa_device": "plughw:CARD=RX,DEV=0",
                        }
                    ],
                    "output_devices": [
                        {
                            "index": 2,
                            "name": "PC3 Speaker",
                            "channels": 2,
                            "sample_rate": 48000,
                            "hostapi": "ALSA",
                        }
                    ],
                }
            ]
        )

        devices = service.get_audio_devices()

        self.assertEqual(service.requests[0], ("GET", "/devices", None))
        self.assertEqual(devices["input_devices"][0]["name"], "PC3 Mic")
        self.assertTrue(devices["input_devices"][0]["connected"])
        self.assertEqual(devices["output_devices"][0]["name"], "PC3 Speaker")

    def test_status_and_proxy_controls_use_manager_only_while_running(self):
        service = FakeRemoteAudioBridgeService()

        with self.assertRaises(RuntimeError):
            asyncio.run(service.set_muted(True))

        asyncio.run(service.start())
        service.responses.extend(
            [
                {
                    "child": {
                        "running": True,
                        "pid": 4321,
                        "status": {
                            "running": True,
                            "muted": True,
                            "input_mic_gain_db": -6.0,
                            "active_remote_participant_identity": "agent",
                        },
                    }
                },
                {"running": True, "muted": False},
                {"running": True, "input_mic_gain_db": -3.0},
                {"ok": True},
            ]
        )

        status = asyncio.run(service.get_bridge_status())
        mute = asyncio.run(service.set_muted(False))
        gain = asyncio.run(service.set_input_mic_gain_db(-3.0))
        release = asyncio.run(service.release_remote_playback())

        self.assertTrue(status["remote"])
        self.assertTrue(status["muted"])
        self.assertEqual(status["active_remote_participant_identity"], "agent")
        self.assertEqual(mute["muted"], False)
        self.assertEqual(gain["input_mic_gain_db"], -3.0)
        self.assertEqual(release["ok"], True)
        self.assertIn(("POST", "/mute", {"muted": False}), service.requests)
        self.assertIn(("POST", "/input-gain", {"input_mic_gain_db": -3.0}), service.requests)
        self.assertIn(("POST", "/remote-playback/release", {}), service.requests)

    def test_health_checks_child_running_state(self):
        service = FakeRemoteAudioBridgeService()
        asyncio.run(service.start())

        service.responses.append({"child": {"running": True, "pid": 4321}})
        self.assertTrue(asyncio.run(service.check_health()))

        service.responses.append({"child": {"running": False}})
        self.assertFalse(asyncio.run(service.check_health()))

        service._state = ServiceState.RUNNING
        service.responses.append(
            {"child": {"running": True, "status": {"running": True, "ready": False}}}
        )
        self.assertFalse(asyncio.run(service.check_health()))

    def test_tail_logs_reads_from_remote_manager(self):
        service = FakeRemoteAudioBridgeService(
            responses=[{"log_path": "/pc3/audio.log", "lines": 2, "content": "line a\nline b\n"}]
        )

        logs = asyncio.run(service.tail_logs(2))

        self.assertEqual(logs, "line a\nline b\n")
        self.assertEqual(service.requests[0], ("GET", "/logs?lines=2", None))

    def test_aima_methods_proxy_to_remote_manager(self):
        service = FakeRemoteAudioBridgeService(
            responses=[
                {"enabled": True, "current_mode": "audio_bridge"},
                {"enabled": True, "current_mode": "audio_bridge"},
                {"enabled": True, "current_mode": "agibot"},
                {"result": {"returncode": 0}, "status": {"enabled": True}},
            ]
        )

        status = asyncio.run(service.get_aima_status())
        audio_bridge = asyncio.run(service.set_aima_audio_bridge_mode())
        agibot = asyncio.run(service.set_aima_agibot_mode())
        doctor = asyncio.run(service.run_aima_doctor())

        self.assertEqual(status["current_mode"], "audio_bridge")
        self.assertEqual(audio_bridge["current_mode"], "audio_bridge")
        self.assertEqual(agibot["current_mode"], "agibot")
        self.assertEqual(doctor["result"]["returncode"], 0)
        self.assertEqual(
            service.requests,
            [
                ("GET", "/aima/status", None),
                ("POST", "/aima/mode/audio-bridge", {}),
                ("POST", "/aima/mode/agibot", {}),
                ("POST", "/aima/doctor", {}),
            ],
        )

    def test_manager_token_can_come_from_configured_environment_variable(self):
        service = AudioBridgeRemoteService(
            "audio-bridge",
            {
                "manager_url": "http://pc3:8766",
                "manager_token_env": "PC3_AUDIO_TOKEN",
            },
        )

        with mock.patch.dict(os.environ, {"PC3_AUDIO_TOKEN": "env-token"}, clear=False):
            self.assertEqual(service._manager_token(), "env-token")


if __name__ == "__main__":
    unittest.main()
