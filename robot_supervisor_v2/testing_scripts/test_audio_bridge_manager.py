import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from robot_services.audio.audio_bridge_manager import (
    AgibotAimaConfig,
    AgibotAimaManager,
    AudioBridgeManager,
    ManagerConfig,
    create_app,
    load_manager_config,
)


class FakeProcess:
    def __init__(self, pid: int = 4321, running: bool = True, returncode=None):
        self.pid = pid
        self.running = running
        self.terminated = False
        self.killed = False
        self.returncode = returncode

    def poll(self):
        return None if self.running else self.returncode

    def terminate(self):
        self.terminated = True
        self.running = False
        self.returncode = 0

    def wait(self, timeout=None):
        self.running = False
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.killed = True
        self.running = False
        self.returncode = -9


class AudioBridgeManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = AudioBridgeManager(self._config())
        self.client = TestClient(create_app(self.manager))

    def tearDown(self):
        self.manager.stop()

    def _config(self, **overrides) -> ManagerConfig:
        values = {
            "manager_token": "secret-token",
            "python_executable": "/usr/bin/python3",
            "working_dir": str(self.root),
            "bridge_script": "robot_services/audio/audio_bridge.py",
            "bridge_name": "audio-streamer",
            "mic_track_name": "g1-mic",
            "child_control_port": 8767,
            "child_startup_timeout_seconds": 0.1,
            "child_request_timeout_seconds": 0.1,
            "child_stop_timeout_seconds": 0.1,
            "device_list_timeout_seconds": 0.1,
            "child_log_path": str(self.root / "child.log"),
            "livekit_url": "ws://pc2:7880",
            "livekit_room": "g1-lab",
            "livekit_api_key": "devkey",
            "livekit_api_secret": "devsecret",
        }
        values.update(overrides)
        return ManagerConfig(**values)

    @property
    def auth(self):
        return {"Authorization": "Bearer secret-token"}

    def test_health_is_public_and_other_routes_require_token(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/status").status_code, 401)
        self.assertEqual(self.client.get("/aima/status").status_code, 401)
        self.assertEqual(
            self.client.get("/status", headers={"Authorization": "Bearer wrong"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/aima/status", headers={"Authorization": "Bearer wrong"}).status_code,
            401,
        )
        self.assertEqual(self.client.get("/status", headers=self.auth).status_code, 200)
        self.assertEqual(self.client.get("/aima/status", headers=self.auth).status_code, 200)

    def test_config_is_sanitized_and_env_overrides_yaml(self):
        config_path = self.root / "manager.yaml"
        config_path.write_text(
            """
manager_token: yaml-token
livekit_url: ws://yaml:7880
livekit_api_key: yaml-key
livekit_api_secret: yaml-secret
bridge_name: yaml-bridge
mic_track_name: yaml-track
""",
            encoding="utf-8",
        )

        with mock.patch.dict(
            "os.environ",
            {
                "AUDIO_BRIDGE_MANAGER_TOKEN": "env-token",
                "LIVEKIT_URL": "ws://env:7880",
                "LIVEKIT_API_KEY": "env-key",
                "LIVEKIT_API_SECRET": "env-secret",
                "AUDIO_BRIDGE_IDENTITY": "env-bridge",
                "LIVEKIT_TRACK_NAME": "env-track",
            },
            clear=False,
        ):
            config = load_manager_config(config_path)

        self.assertEqual(config.manager_token, "env-token")
        self.assertEqual(config.livekit_url, "ws://env:7880")
        self.assertEqual(config.livekit_api_key, "env-key")
        self.assertEqual(config.livekit_api_secret, "env-secret")
        self.assertEqual(config.bridge_name, "env-bridge")
        self.assertEqual(config.mic_track_name, "env-track")
        self.assertEqual(config.sanitized()["manager_token"], "<redacted>")
        self.assertEqual(config.sanitized()["livekit_api_secret"], "<redacted>")

    def test_aima_config_defaults_disabled_and_does_not_run_subprocess(self):
        with mock.patch("robot_services.agibot.aima_em.subprocess.run") as run:
            manager = AudioBridgeManager(self._config())

        self.assertFalse(manager.config.agibot_aima.enabled)
        self.assertEqual(manager.aima.status()["current_mode"], "disabled")
        run.assert_not_called()

    def test_aima_enabled_reconcile_on_startup_stops_audio_owners(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch(
            "robot_services.agibot.aima_em.subprocess.run",
            return_value=completed,
        ) as run:
            manager = AudioBridgeManager(self._config(agibot_aima={"enabled": True}))

        self.assertEqual(manager.aima.status()["current_mode"], "audio_bridge")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["aima", "em", "stop-app", "agent"],
                ["aima", "em", "stop-app", "hal_audio"],
            ],
        )

    def test_aima_mode_switches_and_doctor_run_configured_commands(self):
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        manager = AudioBridgeManager(
            self._config(
                agibot_aima={
                    "enabled": True,
                    "reconcile_on_manager_start": False,
                }
            )
        )
        client = TestClient(create_app(manager))

        with mock.patch(
            "robot_services.agibot.aima_em.subprocess.run",
            return_value=completed,
        ) as run:
            agibot = client.post("/aima/mode/agibot", headers=self.auth)
            audio_bridge = client.post("/aima/mode/audio-bridge", headers=self.auth)
            doctor = client.post("/aima/doctor", headers=self.auth)

        self.assertEqual(agibot.status_code, 200)
        self.assertEqual(agibot.json()["current_mode"], "agibot")
        self.assertEqual(audio_bridge.status_code, 200)
        self.assertEqual(audio_bridge.json()["current_mode"], "audio_bridge")
        self.assertEqual(doctor.status_code, 200)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["aima", "em", "start-app", "hal_audio"],
                ["aima", "em", "start-app", "agent"],
                ["aima", "em", "stop-app", "agent"],
                ["aima", "em", "stop-app", "hal_audio"],
                ["aima", "em", "doctor"],
            ],
        )

    def test_aima_agibot_mode_rejected_while_bridge_child_runs(self):
        manager = AudioBridgeManager(
            self._config(
                agibot_aima={
                    "enabled": True,
                    "reconcile_on_manager_start": False,
                }
            )
        )
        manager.process = FakeProcess()
        manager.started_at = 1.0
        client = TestClient(create_app(manager))

        with mock.patch("robot_services.agibot.aima_em.subprocess.run") as run:
            response = client.post("/aima/mode/agibot", headers=self.auth)

        self.assertEqual(response.status_code, 409)
        self.assertIn("Cannot switch AIMA", response.json()["detail"])
        run.assert_not_called()

    def test_start_in_agibot_mode_rejects_without_spawning_child(self):
        manager = AudioBridgeManager(
            self._config(
                agibot_aima={
                    "enabled": True,
                    "reconcile_on_manager_start": False,
                }
            )
        )
        manager.aima.current_mode = "agibot"
        client = TestClient(create_app(manager))

        with mock.patch("robot_services.audio.audio_bridge_manager.subprocess.Popen") as popen:
            response = client.post("/start", headers=self.auth, json={})

        self.assertEqual(response.status_code, 409)
        self.assertIn("switch to audio_bridge mode first", response.json()["detail"])
        popen.assert_not_called()

    def test_stop_does_not_restore_agibot_services(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch(
            "robot_services.agibot.aima_em.subprocess.run",
            return_value=completed,
        ) as run:
            manager = AudioBridgeManager(self._config(agibot_aima={"enabled": True}))
            run.reset_mock()
            manager.process = FakeProcess()
            manager.started_at = 1.0
            client = TestClient(create_app(manager))
            response = client.post("/stop", headers=self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["aima"]["current_mode"], "audio_bridge")
        run.assert_not_called()

    def test_aima_command_failures_and_timeouts_are_recorded_cleanly(self):
        failed = mock.Mock(returncode=7, stdout="", stderr="missing app")
        manager = AudioBridgeManager(
            self._config(
                agibot_aima={
                    "enabled": True,
                    "reconcile_on_manager_start": False,
                }
            )
        )
        client = TestClient(create_app(manager))

        with mock.patch(
            "robot_services.agibot.aima_em.subprocess.run",
            return_value=failed,
        ):
            response = client.post("/aima/mode/audio-bridge", headers=self.auth)

        self.assertEqual(response.status_code, 500)
        self.assertIn("missing app", response.json()["detail"])
        self.assertIn("missing app", manager.aima.status()["last_error"])

        aima = AgibotAimaManager(AgibotAimaConfig(enabled=True))
        with mock.patch(
            "robot_services.agibot.aima_em.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["aima", "em", "doctor"], timeout=1),
        ):
            with self.assertRaises(RuntimeError):
                aima.doctor()

        self.assertIn("timed out", aima.status()["last_error"])

    def test_devices_shells_out_to_actual_bridge_list_devices(self):
        payload = {
            "input_devices": [{"index": 1, "name": "pc3 mic"}],
            "output_devices": [{"index": 2, "name": "pc3 speaker"}],
        }
        completed = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")

        with mock.patch(
            "robot_services.audio.audio_bridge_manager.subprocess.run",
            return_value=completed,
        ) as run:
            response = self.client.get("/devices", headers=self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/python3",
                str(self.root / "robot_services/audio/audio_bridge.py"),
                "--list-devices",
            ],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(run.call_args.kwargs["env"]["LIVEKIT_URL"], "ws://pc2:7880")

    def test_devices_reports_subprocess_and_json_failures_cleanly(self):
        failed = mock.Mock(returncode=2, stdout="", stderr="no devices")
        with mock.patch("robot_services.audio.audio_bridge_manager.subprocess.run", return_value=failed):
            response = self.client.get("/devices", headers=self.auth)
        self.assertEqual(response.status_code, 500)
        self.assertIn("no devices", response.json()["detail"])

        invalid_json = mock.Mock(returncode=0, stdout="not json", stderr="")
        with mock.patch("robot_services.audio.audio_bridge_manager.subprocess.run", return_value=invalid_json):
            response = self.client.get("/devices", headers=self.auth)
        self.assertEqual(response.status_code, 500)
        self.assertIn("Failed to parse audio device JSON", response.json()["detail"])

    def test_logs_returns_child_log_tail_with_secrets_redacted(self):
        (self.root / "child.log").write_text(
            "\n".join(
                [
                    "line 1",
                    "line 2 devsecret",
                    "line 3 devkey",
                    "line 4 secret-token",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        response = self.client.get("/logs?lines=3", headers=self.auth)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["log_path"], str(self.root / "child.log"))
        self.assertEqual(payload["lines"], 3)
        self.assertNotIn("devsecret", payload["content"])
        self.assertNotIn("devkey", payload["content"])
        self.assertNotIn("secret-token", payload["content"])
        self.assertIn("line 2 <redacted>", payload["content"])
        self.assertIn("line 4 <redacted>", payload["content"])

    def test_start_builds_child_command_from_config_and_transient_payload(self):
        process = FakeProcess()

        with (
            mock.patch(
                "robot_services.audio.audio_bridge_manager.subprocess.Popen",
                return_value=process,
            ) as popen,
            mock.patch.object(self.manager, "_child_request", return_value={"running": True, "ready": True}),
        ):
            response = self.client.post(
                "/start",
                headers=self.auth,
                json={
                    "input_device": "plughw:CARD=RX,DEV=0",
                    "output_devices": ["plughw:CARD=Speaker,DEV=0", "hw:5,0"],
                    "input_capture_channels": 8,
                    "input_mix_channels": [0, 1],
                    "enable_aec": False,
                    "enable_rnnoise": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["child"]["running"])
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/python3")
        self.assertEqual(command[1], str(self.root / "robot_services/audio/audio_bridge.py"))
        self.assertIn("--control-port", command)
        self.assertIn("8767", command)
        self.assertNotIn("--control-host", command)
        self.assertIn("--input-device", command)
        self.assertIn("plughw:CARD=RX,DEV=0", command)
        self.assertIn("--input-capture-channels", command)
        self.assertIn("8", command)
        self.assertEqual(command.count("--input-mix-channel"), 2)
        self.assertIn("0", command)
        self.assertIn("1", command)
        self.assertEqual(command.count("--output-device"), 2)
        self.assertIn("--disable-aec", command)
        self.assertIn("--enable-rnnoise", command)
        self.assertEqual(popen.call_args.kwargs["env"]["LIVEKIT_API_SECRET"], "devsecret")

    def test_start_rejects_invalid_capture_channel_payload(self):
        response = self.client.post(
            "/start",
            headers=self.auth,
            json={
                "input_capture_channels": 2,
                "input_mix_channels": [2],
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_start_waits_for_child_ready_not_just_running_process(self):
        self.manager.config.child_startup_timeout_seconds = 1.0
        process = FakeProcess()

        with (
            mock.patch(
                "robot_services.audio.audio_bridge_manager.subprocess.Popen",
                return_value=process,
            ),
            mock.patch.object(
                self.manager,
                "_child_request",
                side_effect=[
                    {"running": True, "ready": False, "livekit_connected": True},
                    {"running": True, "ready": True, "livekit_connected": True},
                ],
            ) as child_request,
        ):
            response = self.client.post("/start", headers=self.auth, json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["child"]["status"]["ready"])
        self.assertEqual(child_request.call_count, 3)

    def test_start_reports_early_child_exit_with_recent_log(self):
        (self.root / "child.log").write_text(
            "Connecting to LiveKit room...\nError in main: connection refused\n",
            encoding="utf-8",
        )

        with mock.patch(
            "robot_services.audio.audio_bridge_manager.subprocess.Popen",
            return_value=FakeProcess(running=False, returncode=1),
        ):
            response = self.client.post("/start", headers=self.auth, json={})

        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"]
        self.assertIn("Audio bridge exited before becoming ready", detail)
        self.assertIn("Error in main: connection refused", detail)
        self.assertFalse(self.manager.is_running())

    def test_start_rejects_double_start_and_stop_is_idempotent(self):
        self.manager.process = FakeProcess()
        self.manager.started_at = 1.0

        response = self.client.post("/start", headers=self.auth, json={})
        self.assertEqual(response.status_code, 409)
        self.assertIn("already running", response.json()["detail"])

        process = self.manager.process
        response = self.client.post("/stop", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(process.terminated)
        self.assertFalse(response.json()["child"]["running"])

        response = self.client.post("/stop", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["child"]["running"])

    def test_restart_stops_existing_child_then_starts_new_child(self):
        old_process = FakeProcess(pid=100)
        new_process = FakeProcess(pid=200)
        self.manager.process = old_process
        self.manager.started_at = 1.0

        with (
            mock.patch(
                "robot_services.audio.audio_bridge_manager.subprocess.Popen",
                return_value=new_process,
            ) as popen,
            mock.patch.object(self.manager, "_child_request", return_value={"running": True, "ready": True}),
        ):
            response = self.client.post("/restart", headers=self.auth, json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(old_process.terminated)
        self.assertEqual(self.manager.process, new_process)
        self.assertEqual(popen.call_count, 1)

    def test_proxy_controls_require_running_child(self):
        response = self.client.post("/mute", headers=self.auth, json={"muted": True})
        self.assertEqual(response.status_code, 409)

        self.manager.process = FakeProcess()
        self.manager.started_at = 1.0
        with mock.patch.object(self.manager, "_child_request", return_value={"ok": True}) as child_request:
            self.assertEqual(
                self.client.post("/mute", headers=self.auth, json={"muted": True}).status_code,
                200,
            )
            self.assertEqual(
                self.client.post(
                    "/input-gain",
                    headers=self.auth,
                    json={"input_mic_gain_db": -6.0},
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.post("/remote-playback/release", headers=self.auth).status_code,
                200,
            )

        self.assertEqual(
            child_request.call_args_list,
            [
                mock.call("POST", "/mute", {"muted": True}),
                mock.call("POST", "/input-gain", {"input_mic_gain_db": -6.0}),
                mock.call("POST", "/remote-playback/release", {}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
