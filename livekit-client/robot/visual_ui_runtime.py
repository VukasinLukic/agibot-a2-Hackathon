from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import os
from typing import Any

from humanoid_platform import (
    RobotModelId,
    VisualUiBackend,
    get_visual_ui_spec,
)

DEFAULT_VISUAL_UI_MODEL = RobotModelId.UNITREE_G1_EDU


def _env_first(*names: str) -> str | None:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip():
            return raw.strip()
    return None


def _env_int(*names: str) -> int | None:
    raw = _env_first(*names)
    if raw is None:
        return None

    try:
        return int(raw)
    except ValueError:
        return None


class AgentVisualUiRuntime:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("agent_visual_ui")
        self._controller: Any | None = None
        self._disabled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: Any | None = None
        self._state_handler: Any | None = None
        self._desired_state: str | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._wakeup: asyncio.Event | None = None
        self._closed = False

    def bind(self, *, session: Any, ctx: Any) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        try:
            ctx.add_shutdown_callback(self.aclose)
        except Exception as exc:
            self._logger.warning("Failed to register visual UI shutdown callback: %s", exc)

        try:
            def _on_agent_state_changed(ev: Any) -> None:
                state = self._visual_ui_state_for_agent_state(getattr(ev, "new_state", None))
                if state is not None:
                    self._queue_state(state)

            session.on("agent_state_changed", _on_agent_state_changed)
            self._session = session
            self._state_handler = _on_agent_state_changed
        except Exception as exc:
            self._logger.warning("Failed to attach visual UI session hooks: %s", exc)

    async def set_listening(self) -> bool:
        return await self._set_state("set_listening")

    async def set_thinking(self) -> bool:
        return await self._set_state("set_thinking")

    async def set_speaking(self) -> bool:
        return await self._set_state("set_speaking")

    async def prepare(self) -> bool:
        controller = await self._ensure_controller()
        if controller is None:
            return False

        try:
            prepare = getattr(controller, "prepare", None)
            if prepare is None:
                return True
            return bool(await prepare())
        except Exception as exc:
            self._logger.warning("Visual UI prepare failed: %s", exc)
            return False

    async def aclose(self) -> None:
        self._closed = True
        self._detach_session_hooks()
        worker_task = self._worker_task
        self._worker_task = None
        if worker_task and not worker_task.done():
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        controller = self._controller
        if controller is None:
            return

        try:
            await controller.aclose()
        except Exception as exc:
            self._logger.warning("Visual UI shutdown failed: %s", exc)

    @staticmethod
    def _visual_ui_state_for_agent_state(agent_state: Any) -> str | None:
        if agent_state == "thinking":
            return "thinking"
        if agent_state == "speaking":
            return "speaking"
        if agent_state in {"listening", "idle"}:
            return "listening"
        return None

    def _queue_state(self, state: str) -> None:
        if self._closed:
            return

        loop = self._loop
        if loop is None or loop.is_closed():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._loop = loop

        def _wake_worker() -> None:
            if self._closed:
                return

            self._desired_state = state
            if self._wakeup is None:
                self._wakeup = asyncio.Event()
            if self._worker_task is None or self._worker_task.done():
                self._worker_task = asyncio.create_task(self._run_worker())
            self._wakeup.set()

        try:
            loop.call_soon_threadsafe(_wake_worker)
        except RuntimeError:
            self._logger.debug("Visual UI update skipped: event loop unavailable.")

    async def _run_worker(self) -> None:
        if self._wakeup is None:
            self._wakeup = asyncio.Event()

        try:
            while not self._closed:
                await self._wakeup.wait()
                self._wakeup.clear()

                while not self._closed:
                    state = self._desired_state
                    self._desired_state = None
                    if state is None:
                        break

                    await self._set_visual_ui_state(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.debug("Visual UI worker failed.", exc_info=True)

    async def _set_visual_ui_state(self, state: str) -> bool:
        if state == "thinking":
            return await self.set_thinking()
        if state == "speaking":
            return await self.set_speaking()
        if state == "listening":
            return await self.set_listening()
        return False

    def _detach_session_hooks(self) -> None:
        session = self._session
        handler = self._state_handler
        self._session = None
        self._state_handler = None
        if session is None or handler is None:
            return

        off = getattr(session, "off", None)
        if off is None:
            return

        try:
            off("agent_state_changed", handler)
        except Exception as exc:
            self._logger.debug("Failed to detach visual UI session hook: %s", exc)

    async def _set_state(self, method_name: str) -> bool:
        if self._closed:
            return False

        controller = await self._ensure_controller()
        if controller is None:
            return False

        try:
            method = getattr(controller, method_name)
            return bool(await method())
        except Exception as exc:
            self._logger.warning("Visual UI state update failed via %s: %s", method_name, exc)
            return False

    async def _ensure_controller(self) -> Any | None:
        if self._closed:
            return None
        if self._disabled:
            return None
        if self._controller is not None:
            return self._controller

        try:
            robot_model_id = _resolve_robot_model_id()
            visual_ui_spec = get_visual_ui_spec(robot_model_id)
            if visual_ui_spec is None:
                self._logger.info(
                    "Visual UI disabled: robot model '%s' has no visual UI backend.",
                    robot_model_id.value,
                )
                self._disabled = True
                return None

            if visual_ui_spec.backend != VisualUiBackend.UNITREE_G1_AUDIO_LED:
                self._logger.warning(
                    "Visual UI backend unsupported: %s",
                    visual_ui_spec.backend.value,
                )
                self._disabled = True
                return None

            module = importlib.import_module("robot.visual_ui_controller")
            visual_ui_config = getattr(module, "UnitreeG1AudioLedVisualUiConfig")
            visual_ui_controller = getattr(module, "UnitreeG1AudioLedVisualUiController")
        except Exception as exc:
            self._logger.warning(
                "Visual UI controller import unavailable; continuing without visual UI support: %s",
                exc,
            )
            self._disabled = True
            return None

        try:
            iface = (
                os.getenv("VISUAL_UI_INTERFACE")
                or os.getenv("LED_INTERFACE")
                or os.getenv("ROBOT_INTERFACE")
                or os.getenv("UNITREE_NET_IF")
                or "eth0"
            )
            controller = visual_ui_controller(visual_ui_config(iface=iface))
            force_reassert_ms = _env_int("VISUAL_UI_FORCE_REASSERT_MS", "LED_FORCE_REASSERT_MS")
            if force_reassert_ms and force_reassert_ms > 0:
                controller.enable_force_reassert(force_reassert_ms)
            self._controller = controller
            return controller
        except Exception as exc:
            self._logger.warning(
                "Visual UI controller init failed; continuing without visual UI support: %s",
                exc,
            )
            self._disabled = True
            return None


def _resolve_robot_model_id() -> RobotModelId:
    raw = _env_first("HUMANOID_ROBOT_MODEL", "ROBOT_MODEL")
    if raw is None:
        return DEFAULT_VISUAL_UI_MODEL

    try:
        return RobotModelId(raw)
    except ValueError:
        logging.getLogger("agent_visual_ui").warning(
            "Unknown robot model '%s'; disabling visual UI.",
            raw,
        )
        raise


AgentLedRuntime = AgentVisualUiRuntime
