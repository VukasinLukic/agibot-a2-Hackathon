from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

LOG = logging.getLogger("robot_led")

VisualUiState = Literal["listening", "thinking", "speaking"]
LedState = VisualUiState


@dataclass(frozen=True)
class UnitreeG1AudioLedVisualUiConfig:
    iface: str
    min_interval_s: float = 0.22  # Unitree docs require >200ms between LED calls.
    force_reassert_min_interval_s: float = 1.0
    listening_rgb: tuple[int, int, int] = (0, 255, 0)      # Green
    thinking_rgb: tuple[int, int, int] = (255, 140, 0)     # Orange
    speaking_rgb: tuple[int, int, int] = (0, 0, 255)     # Blue


def _robot_enabled() -> bool:
    target = os.getenv("AUDIO_TARGET", "robot").strip().lower()
    if target == "host":
        return False

    disable_robot = os.getenv("ROBOT_ENABLE", "").strip().lower()
    if disable_robot in {"0", "false", "no", "off"}:
        return False

    disable_visual_ui = (
        os.getenv("VISUAL_UI_ENABLE")
        or os.getenv("LED_ENABLE")
        or ""
    ).strip().lower()
    if disable_visual_ui in {"0", "false", "no", "off"}:
        return False

    return True


class UnitreeG1AudioLedVisualUiController:
    def __init__(self, cfg: UnitreeG1AudioLedVisualUiConfig) -> None:
        self.cfg = cfg
        self._client: Any | None = None
        self._init_failed = False
        self._desired_state: VisualUiState | None = None
        self._current_state: VisualUiState | None = None
        self._last_set_ts = 0.0
        self._last_normal_set_ts = 0.0
        self._led_failure_active = False
        self._closed = False
        self._init_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._reassert_task: asyncio.Task[None] | None = None
        self._reassert_interval_s = 0.0

    async def set_state(self, state: VisualUiState, *, force: bool = False) -> bool:
        if state not in {"listening", "thinking", "speaking"}:
            raise ValueError(f"Unsupported LED state: {state}")
        if self._closed:
            return False

        self._desired_state = state

        if not _robot_enabled():
            return False

        if not await self._ensure_client():
            return False

        async with self._lock:
            if self._closed:
                return False
            if self._current_state == state and not force:
                return True

            client = self._client
            if client is None:
                return False

            elapsed = time.monotonic() - self._last_set_ts
            wait_s = self.cfg.min_interval_s - elapsed
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            if self._closed:
                return False

            r, g, b = self._rgb_for_state(state)
            try:
                ret = await asyncio.to_thread(client.LedControl, r, g, b)
            except Exception as exc:
                self._log_led_failure("LedControl failed (state=%s rgb=%s): %s", state, (r, g, b), exc)
                return False

            code = int(ret[0]) if isinstance(ret, tuple) else int(ret)
            if code != 0:
                self._log_led_failure("LedControl failed (state=%s rgb=%s ret=%s)", state, (r, g, b), ret)
                return False
            if self._closed:
                return False

            self._current_state = state
            self._last_set_ts = time.monotonic()
            if not force:
                self._last_normal_set_ts = self._last_set_ts
            self._led_failure_active = False
            return True

    async def set_listening(self) -> bool:
        return await self.set_state("listening")

    async def set_thinking(self) -> bool:
        return await self.set_state("thinking")

    async def set_speaking(self) -> bool:
        return await self.set_state("speaking")

    async def prepare(self) -> bool:
        if self._closed:
            return False
        if not _robot_enabled():
            return False
        return await self._ensure_client()

    def enable_force_reassert(self, interval_ms: int) -> None:
        if self._closed:
            return
        if interval_ms <= 0:
            self.disable_force_reassert()
            return

        requested_s = interval_ms / 1000.0
        effective_s = max(
            requested_s,
            self.cfg.min_interval_s,
            self.cfg.force_reassert_min_interval_s,
        )
        if effective_s > requested_s:
            LOG.warning(
                (
                    "LED_FORCE_REASSERT_MS=%s is below the effective LED reassert floor "
                    "(sdk_safe=%.0fms reassert_floor=%.0fms). Using %.0fms instead."
                ),
                interval_ms,
                self.cfg.min_interval_s * 1000.0,
                self.cfg.force_reassert_min_interval_s * 1000.0,
                effective_s * 1000.0,
            )

        restart = self._reassert_task is None or self._reassert_task.done()
        if not restart and self._reassert_interval_s == effective_s:
            return
        if not restart:
            self.disable_force_reassert()

        self._reassert_interval_s = effective_s
        self._reassert_task = asyncio.create_task(self._reassert_loop())
        LOG.info(
            "LED force reassert enabled (requested=%sms effective=%sms)",
            interval_ms,
            int(effective_s * 1000),
        )

    def disable_force_reassert(self) -> None:
        if self._reassert_task and not self._reassert_task.done():
            self._reassert_task.cancel()
        self._reassert_task = None
        self._reassert_interval_s = 0.0

    async def aclose(self) -> None:
        self._closed = True
        task = self._reassert_task
        self._reassert_task = None
        self._reassert_interval_s = 0.0
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _reassert_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._reassert_interval_s)
                if self._closed or not _robot_enabled():
                    continue
                state = self._desired_state
                if state is None:
                    continue
                if self._recent_normal_write(time.monotonic()):
                    continue
                try:
                    await self.set_state(state, force=True)
                except Exception:
                    LOG.debug("LED force reassert failed.", exc_info=True)
        except asyncio.CancelledError:
            raise

    def _rgb_for_state(self, state: VisualUiState) -> tuple[int, int, int]:
        if state == "listening":
            return self.cfg.listening_rgb
        if state == "thinking":
            return self.cfg.thinking_rgb
        return self.cfg.speaking_rgb

    def _recent_normal_write(self, now: float) -> bool:
        return (
            self._reassert_interval_s > 0.0
            and self._last_normal_set_ts > 0.0
            and now - self._last_normal_set_ts < self._reassert_interval_s
        )

    def _log_led_failure(self, msg: str, *args: Any) -> None:
        if self._led_failure_active:
            LOG.debug(msg, *args)
            return

        self._led_failure_active = True
        LOG.warning(msg, *args)

    async def _ensure_client(self) -> bool:
        if not _robot_enabled():
            return False
        if self._closed:
            return False
        if self._init_failed:
            return False
        if self._client is not None:
            return True

        async with self._init_lock:
            if self._closed:
                return False
            if self._init_failed:
                return False
            if self._client is not None:
                return True

            try:
                client = await asyncio.to_thread(self._create_client)
            except Exception as exc:
                LOG.warning("Robot LED init failed; disabling LED control. %s", exc)
                self._init_failed = True
                return False

            if self._closed:
                return False

            self._client = client
            LOG.info("Robot LED controller initialized (%s)", self.cfg.iface)
            return True

    def _create_client(self) -> Any:
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
        except Exception as exc:
            raise RuntimeError(f"Robot LED unavailable: {exc}") from exc

        ChannelFactoryInitialize(0, self.cfg.iface)
        client = AudioClient()
        client.Init()
        client.SetTimeout(10.0)
        return client


RobotLedConfig = UnitreeG1AudioLedVisualUiConfig
RobotLedController = UnitreeG1AudioLedVisualUiController
