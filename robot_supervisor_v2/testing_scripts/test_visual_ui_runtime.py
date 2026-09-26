import asyncio
import logging
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LIVEKIT_CLIENT_ROOT = REPO_ROOT / "livekit-client"
if str(LIVEKIT_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEKIT_CLIENT_ROOT))


from robot.visual_ui_controller import UnitreeG1AudioLedVisualUiConfig, UnitreeG1AudioLedVisualUiController
from robot.visual_ui_runtime import AgentVisualUiRuntime


UNITREE_MODULES = (
    "unitree_sdk2py",
    "unitree_sdk2py.core",
    "unitree_sdk2py.core.channel",
    "unitree_sdk2py.g1",
    "unitree_sdk2py.g1.audio",
    "unitree_sdk2py.g1.audio.g1_audio_client",
)


class FakeContext:
    def __init__(self) -> None:
        self.shutdown_callbacks = []

    def add_shutdown_callback(self, callback) -> None:
        self.shutdown_callbacks.append(callback)


class FakeSession:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event: str, callback=None):
        if callback is None:
            def _decorator(handler):
                self.handlers.setdefault(event, []).append(handler)
                return handler

            return _decorator

        self.handlers.setdefault(event, []).append(callback)
        return callback

    def off(self, event: str, callback) -> None:
        handlers = self.handlers.get(event, [])
        if callback in handlers:
            handlers.remove(callback)

    def emit(self, event: str, payload) -> None:
        for handler in list(self.handlers.get(event, [])):
            handler(payload)

    def handler_count(self, event: str) -> int:
        return len(self.handlers.get(event, []))


class FakeVisualUiController:
    def __init__(
        self,
        *,
        block_state: str | None = None,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.close_count = 0
        self.block_state = block_state
        self.started = started
        self.release = release

    async def set_listening(self) -> bool:
        return await self._record("listening")

    async def set_thinking(self) -> bool:
        return await self._record("thinking")

    async def set_speaking(self) -> bool:
        return await self._record("speaking")

    async def aclose(self) -> None:
        self.close_count += 1

    async def _record(self, state: str) -> bool:
        self.calls.append(state)
        if state == self.block_state:
            if self.started is not None:
                self.started.set()
            if self.release is not None:
                await self.release.wait()
        return True


async def wait_until(predicate, *, timeout_s: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for condition")
        await asyncio.sleep(0.001)


class VisualUiRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._old_env = {
            "AUDIO_TARGET": os.environ.get("AUDIO_TARGET"),
            "HUMANOID_ROBOT_MODEL": os.environ.get("HUMANOID_ROBOT_MODEL"),
            "ROBOT_ENABLE": os.environ.get("ROBOT_ENABLE"),
            "VISUAL_UI_ENABLE": os.environ.get("VISUAL_UI_ENABLE"),
            "VISUAL_UI_INTERFACE": os.environ.get("VISUAL_UI_INTERFACE"),
            "VISUAL_UI_FORCE_REASSERT_MS": os.environ.get("VISUAL_UI_FORCE_REASSERT_MS"),
            "LED_ENABLE": os.environ.get("LED_ENABLE"),
            "LED_INTERFACE": os.environ.get("LED_INTERFACE"),
            "ROBOT_INTERFACE": os.environ.get("ROBOT_INTERFACE"),
            "UNITREE_NET_IF": os.environ.get("UNITREE_NET_IF"),
            "LED_FORCE_REASSERT_MS": os.environ.get("LED_FORCE_REASSERT_MS"),
        }
        os.environ["AUDIO_TARGET"] = "robot"
        for key in (
            "HUMANOID_ROBOT_MODEL",
            "ROBOT_ENABLE",
            "VISUAL_UI_ENABLE",
            "VISUAL_UI_INTERFACE",
            "VISUAL_UI_FORCE_REASSERT_MS",
            "LED_ENABLE",
            "LED_INTERFACE",
            "ROBOT_INTERFACE",
            "UNITREE_NET_IF",
            "LED_FORCE_REASSERT_MS",
        ):
            os.environ.pop(key, None)

        self._old_unitree_modules = {
            name: sys.modules.get(name)
            for name in UNITREE_MODULES
            if name in sys.modules
        }
        for name in UNITREE_MODULES:
            sys.modules.pop(name, None)

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        for name in UNITREE_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(self._old_unitree_modules)

    def _install_unitree_fake(
        self,
        *,
        fail_init: bool = False,
        block_init: threading.Event | None = None,
        init_started: threading.Event | None = None,
        led_results: list | None = None,
    ) -> dict:
        stats = {
            "channel_init_count": 0,
            "audio_init_count": 0,
            "timeout_values": [],
            "led_calls": [],
            "led_call_times": [],
            "init_thread_ids": [],
        }
        led_results = list(led_results or [])

        def channel_factory_initialize(domain: int, iface: str) -> None:
            stats["channel_init_count"] += 1
            stats["init_thread_ids"].append(threading.get_ident())
            if init_started is not None:
                init_started.set()
            if block_init is not None:
                block_init.wait(timeout=2.0)
            if fail_init:
                raise RuntimeError("fake init failure")

        class FakeAudioClient:
            def Init(self) -> None:
                stats["audio_init_count"] += 1

            def SetTimeout(self, timeout_s: float) -> None:
                stats["timeout_values"].append(timeout_s)

            def LedControl(self, r: int, g: int, b: int):
                stats["led_calls"].append((r, g, b))
                stats["led_call_times"].append(time.monotonic())
                if led_results:
                    result = led_results.pop(0)
                    if isinstance(result, BaseException):
                        raise result
                    return result
                return (0,)

        sys.modules["unitree_sdk2py"] = types.ModuleType("unitree_sdk2py")
        sys.modules["unitree_sdk2py.core"] = types.ModuleType("unitree_sdk2py.core")
        channel_module = types.ModuleType("unitree_sdk2py.core.channel")
        channel_module.ChannelFactoryInitialize = channel_factory_initialize
        sys.modules["unitree_sdk2py.core.channel"] = channel_module
        sys.modules["unitree_sdk2py.g1"] = types.ModuleType("unitree_sdk2py.g1")
        sys.modules["unitree_sdk2py.g1.audio"] = types.ModuleType("unitree_sdk2py.g1.audio")
        audio_module = types.ModuleType("unitree_sdk2py.g1.audio.g1_audio_client")
        audio_module.AudioClient = FakeAudioClient
        sys.modules["unitree_sdk2py.g1.audio.g1_audio_client"] = audio_module
        return stats

    async def test_first_set_initializes_off_loop_and_sets_led(self):
        stats = self._install_unitree_fake()
        main_thread_id = threading.get_ident()
        controller = UnitreeG1AudioLedVisualUiController(UnitreeG1AudioLedVisualUiConfig(iface="test0", min_interval_s=0.0))

        self.assertTrue(await controller.set_listening())

        self.assertEqual(stats["channel_init_count"], 1)
        self.assertEqual(stats["audio_init_count"], 1)
        self.assertEqual(stats["timeout_values"], [10.0])
        self.assertEqual(stats["led_calls"], [(0, 255, 0)])
        self.assertNotEqual(stats["init_thread_ids"], [main_thread_id])

    async def test_concurrent_sets_share_one_initialization(self):
        stats = self._install_unitree_fake()
        controller = UnitreeG1AudioLedVisualUiController(UnitreeG1AudioLedVisualUiConfig(iface="test0", min_interval_s=0.0))

        results = await asyncio.gather(
            controller.set_listening(),
            controller.set_speaking(),
        )

        self.assertEqual(results, [True, True])
        self.assertEqual(stats["channel_init_count"], 1)
        self.assertEqual(stats["audio_init_count"], 1)
        self.assertEqual(stats["led_calls"], [(0, 255, 0), (0, 0, 255)])

    async def test_runtime_close_before_use_does_not_import_controller(self):
        sys.modules.pop("robot.visual_ui_controller", None)
        runtime = AgentVisualUiRuntime()

        await runtime.aclose()

        self.assertNotIn("robot.visual_ui_controller", sys.modules)

    async def test_runtime_close_after_controller_creation_closes_existing_controller(self):
        class FakeController:
            def __init__(self) -> None:
                self.close_count = 0

            async def aclose(self) -> None:
                self.close_count += 1

        controller = FakeController()
        runtime = AgentVisualUiRuntime()
        runtime._controller = controller

        await runtime.aclose()

        self.assertEqual(controller.close_count, 1)

    async def test_set_after_close_returns_false_without_led_call(self):
        stats = self._install_unitree_fake()
        controller = UnitreeG1AudioLedVisualUiController(UnitreeG1AudioLedVisualUiConfig(iface="test0", min_interval_s=0.0))
        self.assertTrue(await controller.set_listening())
        stats["led_calls"].clear()

        await controller.aclose()

        self.assertFalse(await controller.set_thinking())
        self.assertEqual(stats["led_calls"], [])

    async def test_failed_initialization_is_not_retried(self):
        stats = self._install_unitree_fake(fail_init=True)
        controller = UnitreeG1AudioLedVisualUiController(UnitreeG1AudioLedVisualUiConfig(iface="test0", min_interval_s=0.0))

        with self.assertLogs("robot_led", level="WARNING") as captured:
            self.assertFalse(await controller.set_listening())
        self.assertFalse(await controller.set_speaking())

        self.assertEqual(len(captured.records), 1)
        self.assertEqual(stats["channel_init_count"], 1)
        self.assertEqual(stats["audio_init_count"], 0)
        self.assertEqual(stats["led_calls"], [])

    async def test_close_during_initialization_discards_client_without_led_call(self):
        block_init = threading.Event()
        init_started = threading.Event()
        stats = self._install_unitree_fake(
            block_init=block_init,
            init_started=init_started,
        )
        controller = UnitreeG1AudioLedVisualUiController(UnitreeG1AudioLedVisualUiConfig(iface="test0", min_interval_s=0.0))

        set_task = asyncio.create_task(controller.set_listening())
        self.assertTrue(await asyncio.to_thread(init_started.wait, 1.0))
        await controller.aclose()
        block_init.set()

        self.assertFalse(await set_task)
        self.assertIsNone(controller._client)
        self.assertEqual(stats["led_calls"], [])

    async def test_runtime_prepare_initializes_without_setting_led(self):
        stats = self._install_unitree_fake()
        runtime = AgentVisualUiRuntime()

        self.assertTrue(await runtime.prepare())

        self.assertEqual(stats["channel_init_count"], 1)
        self.assertEqual(stats["audio_init_count"], 1)
        self.assertEqual(stats["led_calls"], [])

    async def test_runtime_prepare_for_agibot_model_does_not_import_controller(self):
        os.environ["HUMANOID_ROBOT_MODEL"] = "agibot_a2_ultra"
        sys.modules.pop("robot.visual_ui_controller", None)
        runtime = AgentVisualUiRuntime()

        self.assertFalse(await runtime.prepare())

        self.assertIsNone(runtime._controller)
        self.assertNotIn("robot.visual_ui_controller", sys.modules)

    async def test_legacy_led_runtime_import_remains_supported(self):
        from robot.led_runtime import AgentLedRuntime

        self.assertIs(AgentLedRuntime, AgentVisualUiRuntime)

    async def test_legacy_led_controller_import_remains_supported(self):
        from robot.led_controller import RobotLedConfig, RobotLedController

        self.assertEqual(RobotLedConfig.__name__, "UnitreeG1AudioLedVisualUiConfig")
        self.assertEqual(RobotLedController.__name__, "UnitreeG1AudioLedVisualUiController")
        controller = RobotLedController(RobotLedConfig(iface="test0", min_interval_s=0.0))
        self.assertEqual(controller.cfg.iface, "test0")

    async def test_runtime_force_reassert_env_absent_zero_or_invalid_keeps_reassert_disabled(self):
        for raw_value in (None, "0", "not-an-int"):
            stats = self._install_unitree_fake()
            if raw_value is None:
                os.environ.pop("LED_FORCE_REASSERT_MS", None)
            else:
                os.environ["LED_FORCE_REASSERT_MS"] = raw_value

            runtime = AgentVisualUiRuntime()
            self.assertTrue(await runtime.prepare())

            controller = runtime._controller
            self.assertIsNotNone(controller)
            self.assertIsNone(controller._reassert_task)
            self.assertEqual(stats["led_calls"], [])
            await runtime.aclose()

    async def test_force_reassert_interval_clamps_to_reassert_floor(self):
        controller = UnitreeG1AudioLedVisualUiController(
            UnitreeG1AudioLedVisualUiConfig(
                iface="test0",
                min_interval_s=0.0,
                force_reassert_min_interval_s=0.05,
            )
        )

        with self.assertLogs("robot_led", level="WARNING") as captured:
            controller.enable_force_reassert(1)

        self.assertEqual(len(captured.records), 1)
        self.assertAlmostEqual(controller._reassert_interval_s, 0.05)
        self.assertIsNotNone(controller._reassert_task)
        await controller.aclose()

    async def test_force_reassert_repeats_latest_desired_state(self):
        stats = self._install_unitree_fake()
        controller = UnitreeG1AudioLedVisualUiController(
            UnitreeG1AudioLedVisualUiConfig(
                iface="test0",
                min_interval_s=0.0,
                force_reassert_min_interval_s=0.02,
            )
        )
        controller.enable_force_reassert(20)

        self.assertTrue(await controller.set_listening())
        await wait_until(lambda: len(stats["led_calls"]) >= 2, timeout_s=0.2)

        self.assertEqual(stats["led_calls"][:2], [(0, 255, 0), (0, 255, 0)])
        await controller.aclose()

    async def test_normal_state_change_is_not_delayed_by_reassert_interval(self):
        stats = self._install_unitree_fake()
        controller = UnitreeG1AudioLedVisualUiController(
            UnitreeG1AudioLedVisualUiConfig(
                iface="test0",
                min_interval_s=0.0,
                force_reassert_min_interval_s=0.2,
            )
        )
        controller.enable_force_reassert(200)

        self.assertTrue(await controller.set_listening())
        started_at = time.monotonic()
        self.assertTrue(await controller.set_speaking())

        self.assertLess(time.monotonic() - started_at, 0.1)
        self.assertEqual(stats["led_calls"][:2], [(0, 255, 0), (0, 0, 255)])
        await controller.aclose()

    async def test_recent_normal_write_suppresses_next_reassert_cycle(self):
        stats = self._install_unitree_fake()
        controller = UnitreeG1AudioLedVisualUiController(
            UnitreeG1AudioLedVisualUiConfig(
                iface="test0",
                min_interval_s=0.0,
                force_reassert_min_interval_s=0.05,
            )
        )
        controller.enable_force_reassert(50)

        self.assertTrue(await controller.set_listening())
        await asyncio.sleep(0.03)
        controller._last_normal_set_ts = time.monotonic()
        await asyncio.sleep(0.04)

        self.assertEqual(stats["led_calls"], [(0, 255, 0)])
        await controller.aclose()

    async def test_disable_force_reassert_cancels_loop(self):
        controller = UnitreeG1AudioLedVisualUiController(
            UnitreeG1AudioLedVisualUiConfig(
                iface="test0",
                min_interval_s=0.0,
                force_reassert_min_interval_s=0.05,
            )
        )
        controller.enable_force_reassert(50)
        task = controller._reassert_task
        self.assertIsNotNone(task)

        controller.disable_force_reassert()
        await asyncio.sleep(0.01)

        self.assertIsNone(controller._reassert_task)
        self.assertTrue(task.done())
        await controller.aclose()

    async def test_close_cancels_reassert_loop_and_prevents_restart(self):
        controller = UnitreeG1AudioLedVisualUiController(
            UnitreeG1AudioLedVisualUiConfig(
                iface="test0",
                min_interval_s=0.0,
                force_reassert_min_interval_s=0.05,
            )
        )
        controller.enable_force_reassert(50)
        task = controller._reassert_task
        self.assertIsNotNone(task)

        await controller.aclose()
        controller.enable_force_reassert(50)

        self.assertTrue(task.done())
        self.assertIsNone(controller._reassert_task)

    async def test_led_control_failure_logs_warning_once_until_success(self):
        self._install_unitree_fake(led_results=[(1,), (1,), (0,), (1,)])
        controller = UnitreeG1AudioLedVisualUiController(UnitreeG1AudioLedVisualUiConfig(iface="test0", min_interval_s=0.0))

        with self.assertLogs("robot_led", level="DEBUG") as captured:
            self.assertFalse(await controller.set_state("listening", force=True))
            self.assertFalse(await controller.set_state("listening", force=True))
            self.assertTrue(await controller.set_state("listening", force=True))
            self.assertFalse(await controller.set_state("listening", force=True))

        led_failure_records = [
            record
            for record in captured.records
            if "LedControl failed" in record.getMessage()
        ]
        warning_count = sum(1 for record in led_failure_records if record.levelno == logging.WARNING)
        debug_count = sum(1 for record in led_failure_records if record.levelno == logging.DEBUG)
        self.assertEqual(warning_count, 2)
        self.assertEqual(debug_count, 1)
        await controller.aclose()

    async def test_agent_state_changed_thinking_queues_one_update(self):
        runtime = AgentVisualUiRuntime()
        controller = FakeVisualUiController()
        runtime._controller = controller
        session = FakeSession()
        runtime.bind(session=session, ctx=FakeContext())

        session.emit("agent_state_changed", types.SimpleNamespace(new_state="thinking"))

        await wait_until(lambda: controller.calls == ["thinking"])
        await runtime.aclose()

    async def test_agent_state_events_coalesce_to_latest_while_worker_blocked(self):
        started = asyncio.Event()
        release = asyncio.Event()
        runtime = AgentVisualUiRuntime()
        controller = FakeVisualUiController(
            block_state="thinking",
            started=started,
            release=release,
        )
        runtime._controller = controller
        session = FakeSession()
        runtime.bind(session=session, ctx=FakeContext())

        session.emit("agent_state_changed", types.SimpleNamespace(new_state="thinking"))
        await wait_until(started.is_set)
        session.emit("agent_state_changed", types.SimpleNamespace(new_state="speaking"))
        session.emit("agent_state_changed", types.SimpleNamespace(new_state="listening"))
        release.set()

        await wait_until(lambda: controller.calls == ["thinking", "listening"])
        self.assertNotIn("speaking", controller.calls)
        await runtime.aclose()

    async def test_old_transcript_and_speech_events_do_not_affect_visual_ui(self):
        runtime = AgentVisualUiRuntime()
        controller = FakeVisualUiController()
        runtime._controller = controller
        session = FakeSession()
        runtime.bind(session=session, ctx=FakeContext())

        session.emit("user_input_transcribed", types.SimpleNamespace(is_final=True))
        session.emit("speech_created", types.SimpleNamespace(source="generate_reply"))
        await asyncio.sleep(0.01)

        self.assertEqual(controller.calls, [])
        self.assertEqual(session.handler_count("user_input_transcribed"), 0)
        self.assertEqual(session.handler_count("speech_created"), 0)
        await runtime.aclose()

    async def test_say_path_uses_speaking_agent_state(self):
        runtime = AgentVisualUiRuntime()
        controller = FakeVisualUiController()
        runtime._controller = controller
        session = FakeSession()
        runtime.bind(session=session, ctx=FakeContext())

        session.emit("agent_state_changed", types.SimpleNamespace(new_state="speaking"))

        await wait_until(lambda: controller.calls == ["speaking"])
        await runtime.aclose()

    async def test_idle_maps_to_listening_and_initializing_is_ignored(self):
        runtime = AgentVisualUiRuntime()
        controller = FakeVisualUiController()
        runtime._controller = controller
        session = FakeSession()
        runtime.bind(session=session, ctx=FakeContext())

        session.emit("agent_state_changed", types.SimpleNamespace(new_state="initializing"))
        session.emit("agent_state_changed", types.SimpleNamespace(new_state="idle"))

        await wait_until(lambda: controller.calls == ["listening"])
        await runtime.aclose()

    async def test_close_unregisters_agent_state_handler_and_does_not_import_controller(self):
        sys.modules.pop("robot.visual_ui_controller", None)
        runtime = AgentVisualUiRuntime()
        session = FakeSession()
        runtime.bind(session=session, ctx=FakeContext())

        self.assertEqual(session.handler_count("agent_state_changed"), 1)
        await runtime.aclose()

        self.assertEqual(session.handler_count("agent_state_changed"), 0)
        self.assertNotIn("robot.visual_ui_controller", sys.modules)

    async def test_close_cancels_worker_and_closes_existing_controller(self):
        started = asyncio.Event()
        release = asyncio.Event()
        runtime = AgentVisualUiRuntime()
        controller = FakeVisualUiController(
            block_state="thinking",
            started=started,
            release=release,
        )
        runtime._controller = controller
        session = FakeSession()
        runtime.bind(session=session, ctx=FakeContext())

        session.emit("agent_state_changed", types.SimpleNamespace(new_state="thinking"))
        await wait_until(started.is_set)
        await runtime.aclose()
        release.set()

        self.assertEqual(session.handler_count("agent_state_changed"), 0)
        self.assertEqual(controller.close_count, 1)
        self.assertTrue(runtime._worker_task is None or runtime._worker_task.done())


if __name__ == "__main__":
    unittest.main()
