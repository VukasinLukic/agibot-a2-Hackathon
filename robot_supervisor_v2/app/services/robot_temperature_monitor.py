"""
Robot temperature monitor service.

Subscribes to Unitree SDK2 `rt/lowstate` and exposes the latest motor temperatures
to the supervisor API/UI.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from humanoid_platform import (
    PlatformId,
    RobotModelSpec,
    TemperatureMonitorBackend,
    TemperatureMonitorSpec,
    get_robot_model,
    get_temperature_monitor_spec,
    temperature_monitor_supported_models,
)

from ..supervisor_config import SERVICE_ROBOT_CONTEXT_KEY
from .base import BaseService, ConfigParameter, ServiceState, ServiceStatus


class RobotTemperatureMonitorUnsupportedError(RuntimeError):
    """Raised when the configured robot has no temperature monitor backend."""


class RobotTemperatureMonitorService(BaseService):
    """In-process Unitree low-state subscriber for motor temperatures."""

    _channel_factory_initialized = False
    _channel_factory_interface: Optional[str] = None
    _channel_factory_lock = threading.Lock()

    def __init__(self, name: str, config: Dict[str, Any]):
        service_config = dict(config)
        self._robot_context = service_config.pop(SERVICE_ROBOT_CONTEXT_KEY, None)
        super().__init__(
            name=name,
            display_name=service_config.get("display_name", "Robot Temperature Monitor"),
            config=service_config,
        )
        self._log_file = f"robot_supervisor_v2/logs/{name}.log"
        self._subscriber: Any = None
        self._subscriber_lock = threading.Lock()
        self._sdk_symbols: Optional[Tuple[Any, Any, Any]] = None
        self._temperature_spec: Optional[TemperatureMonitorSpec] = None
        self._latest_snapshot: Optional[Dict[str, Any]] = None
        self._last_message_at: Optional[float] = None
        self._last_message_monotonic: Optional[float] = None
        self._start_monotonic: Optional[float] = None
        self._warn_threshold_c = float(service_config.get("warn_threshold_c", 65.0))
        self._critical_threshold_c = float(service_config.get("critical_threshold_c", 80.0))
        self._logger = self._build_logger()

    def _build_logger(self) -> logging.Logger:
        os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
        logger = logging.getLogger(f"robot_supervisor_v2.services.{self.name}")
        logger.setLevel(logging.INFO)
        logger.propagate = False

        if not any(
            isinstance(handler, logging.FileHandler)
            and os.path.abspath(getattr(handler, "baseFilename", "")) == os.path.abspath(self._log_file)
            for handler in logger.handlers
        ):
            handler = logging.FileHandler(self._log_file)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)

        return logger

    def _resolve_robot_model(self) -> RobotModelSpec:
        if not isinstance(self._robot_context, dict):
            raise RobotTemperatureMonitorUnsupportedError(
                "Robot temperature monitor requires supervisor robot config context"
            )

        raw_model = self._robot_context.get("model")
        if not raw_model:
            raise RobotTemperatureMonitorUnsupportedError(
                "Robot temperature monitor requires robot.model in supervisor config"
            )

        try:
            robot_model = get_robot_model(str(raw_model))
        except ValueError as exc:
            raise RobotTemperatureMonitorUnsupportedError(
                f"Unknown robot.model '{raw_model}' for robot temperature monitor"
            ) from exc

        raw_platform = self._robot_context.get("platform")
        if raw_platform:
            try:
                configured_platform = PlatformId(str(raw_platform))
            except ValueError as exc:
                raise RobotTemperatureMonitorUnsupportedError(
                    f"Unknown robot.platform '{raw_platform}' for robot temperature monitor"
                ) from exc

            if configured_platform != robot_model.platform:
                raise RobotTemperatureMonitorUnsupportedError(
                    "Robot temperature monitor config mismatch: "
                    f"robot.model '{robot_model.id.value}' belongs to platform "
                    f"'{robot_model.platform.value}', not '{configured_platform.value}'"
                )

        return robot_model

    def _require_temperature_monitor_spec(self) -> TemperatureMonitorSpec:
        robot_model = self._resolve_robot_model()
        spec = get_temperature_monitor_spec(robot_model)
        if spec is None:
            supported = ", ".join(
                model_id.value for model_id in temperature_monitor_supported_models()
            )
            raise RobotTemperatureMonitorUnsupportedError(
                "Robot temperature monitor is not supported for "
                f"robot.model '{robot_model.id.value}' "
                f"(platform '{robot_model.platform.value}'). "
                f"Supported models: {supported or 'none'}"
            )

        if spec.backend != TemperatureMonitorBackend.UNITREE_LOWSTATE:
            raise RobotTemperatureMonitorUnsupportedError(
                "Robot temperature monitor backend "
                f"'{spec.backend.value}' is not implemented by this service"
            )

        return spec

    def _support_state(self) -> tuple[bool, Optional[str]]:
        try:
            self._require_temperature_monitor_spec()
        except RobotTemperatureMonitorUnsupportedError as exc:
            return False, str(exc)
        return True, None

    def is_optional(self) -> bool:
        supported, _ = self._support_state()
        return super().is_optional() or not supported

    def _load_sdk_symbols(self) -> Tuple[Any, Any, Any]:
        if self._sdk_symbols is not None:
            return self._sdk_symbols

        from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_

        self._sdk_symbols = (ChannelFactoryInitialize, ChannelSubscriber, LowState_)
        return self._sdk_symbols

    def _ensure_channel_factory(self) -> None:
        requested_interface = self._config.get("network_interface")

        with self._channel_factory_lock:
            if self._channel_factory_initialized:
                if (
                    self._channel_factory_interface
                    and requested_interface
                    and requested_interface != self._channel_factory_interface
                ):
                    raise RuntimeError(
                        "Unitree DDS channel factory already initialized with "
                        f"interface '{self._channel_factory_interface}', cannot reinitialize with "
                        f"'{requested_interface}'"
                    )
                return

            ChannelFactoryInitialize, _, _ = self._load_sdk_symbols()

            if requested_interface:
                ChannelFactoryInitialize(0, str(requested_interface))
            else:
                ChannelFactoryInitialize(0)

            self.__class__._channel_factory_initialized = True
            self.__class__._channel_factory_interface = (
                str(requested_interface) if requested_interface else None
            )

    @staticmethod
    def _coerce_scalar(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
            return value
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            return str(value)
        if math.isnan(coerced) or math.isinf(coerced):
            return None
        return coerced

    def _extract_temperature(self, motor: Any) -> Any:
        for candidate in ("temperature", "temp", "motor_temperature"):
            if hasattr(motor, candidate):
                raw_value = getattr(motor, candidate)
                if isinstance(raw_value, (list, tuple)):
                    readings = [
                        float(value)
                        for value in (self._coerce_scalar(item) for item in raw_value)
                        if isinstance(value, (int, float))
                    ]
                    if readings:
                        return {
                            "primary_c": max(readings),
                            "readings_c": readings,
                        }
                    return {
                        "primary_c": None,
                        "readings_c": [],
                    }

                coerced = self._coerce_scalar(raw_value)
                if isinstance(coerced, (int, float)):
                    return {
                        "primary_c": float(coerced),
                        "readings_c": [float(coerced)],
                    }
                return {
                    "primary_c": None,
                    "readings_c": [],
                }
        return {
            "primary_c": None,
            "readings_c": [],
        }

    def _build_snapshot(self, msg: Any) -> Dict[str, Any]:
        motor_states = getattr(msg, "motor_state", None)
        if motor_states is None:
            available = [field for field in dir(msg) if not field.startswith("_")]
            raise RuntimeError(
                "LowState_ has no 'motor_state' field in this SDK build. "
                f"Available fields: {available}"
            )

        motors: List[Dict[str, Any]] = []
        hottest_motor: Optional[Dict[str, Any]] = None
        max_temperature_c: Optional[float] = None
        warning_count = 0
        critical_count = 0

        for index, motor in enumerate(motor_states):
            temperature = self._extract_temperature(motor)
            temperature_c = temperature["primary_c"]
            entry = {
                "index": index,
                "label": f"motor[{index:02d}]",
                "temperature_c": temperature_c,
                "temperature_readings_c": temperature["readings_c"],
                "mode": self._coerce_scalar(getattr(motor, "mode", None)),
                "q": self._coerce_scalar(getattr(motor, "q", None)),
                "dq": self._coerce_scalar(getattr(motor, "dq", None)),
            }
            motors.append(entry)

            if isinstance(temperature_c, (int, float)):
                if max_temperature_c is None or float(temperature_c) > max_temperature_c:
                    max_temperature_c = float(temperature_c)
                    hottest_motor = entry
                if float(temperature_c) >= self._critical_threshold_c:
                    critical_count += 1
                elif float(temperature_c) >= self._warn_threshold_c:
                    warning_count += 1

        return {
            "topic": self._config.get("topic", "rt/lowstate"),
            "motor_count": len(motors),
            "motors": motors,
            "max_temperature_c": max_temperature_c,
            "hottest_motor": hottest_motor,
            "warning_count": warning_count,
            "critical_count": critical_count,
            "received_at": time.time(),
        }

    def _on_lowstate(self, msg: Any) -> None:
        if self._state in (ServiceState.STOPPED, ServiceState.STOPPING):
            return

        try:
            snapshot = self._build_snapshot(msg)
        except Exception as exc:
            self._last_error = str(exc)
            self._logger.exception("Failed to parse robot temperature snapshot")
            return

        with self._subscriber_lock:
            self._latest_snapshot = snapshot
            self._last_message_at = snapshot["received_at"]
            self._last_message_monotonic = time.monotonic()

        self._state = ServiceState.RUNNING
        self._last_error = None

    def _close_subscriber(self) -> None:
        with self._subscriber_lock:
            subscriber = self._subscriber
            self._subscriber = None

        if subscriber is None:
            return

        for method_name in ("Close", "close", "Stop", "stop", "Uninit", "uninit", "Release", "release"):
            method = getattr(subscriber, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception as exc:
                    self._logger.warning("Failed to close subscriber via %s: %s", method_name, exc)
                break

    def _sync_runtime_state(self) -> None:
        if self._state in (ServiceState.STOPPED, ServiceState.STOPPING):
            return

        if self._last_message_monotonic is None:
            if self._state == ServiceState.STARTING and self._start_monotonic is not None:
                startup_timeout = float(self._config.get("startup_timeout", 5.0))
                startup_age = time.monotonic() - self._start_monotonic
                if startup_age > startup_timeout:
                    self._state = ServiceState.FAILED
                    self._last_error = (
                        f"No low-state messages received on {self._config.get('topic', 'rt/lowstate')} "
                        f"within {startup_timeout:.1f}s"
                    )
            return

        stale_timeout = float(self._config.get("stale_timeout", 3.0))
        message_age = time.monotonic() - self._last_message_monotonic
        if message_age > stale_timeout:
            self._state = ServiceState.FAILED
            self._last_error = f"No low-state update received for {message_age:.1f}s"
        else:
            self._state = ServiceState.RUNNING

    async def start(self) -> None:
        if self._state not in (ServiceState.STOPPED, ServiceState.FAILED):
            raise ValueError(f"Service {self.name} is not stopped (current state: {self._state})")

        self._state = ServiceState.STARTING
        self._last_error = None
        self._latest_snapshot = None
        self._last_message_at = None
        self._last_message_monotonic = None
        self._start_monotonic = time.monotonic()

        try:
            self._temperature_spec = self._require_temperature_monitor_spec()
            self._ensure_channel_factory()
            _, ChannelSubscriber, LowState_ = self._load_sdk_symbols()
            subscriber = ChannelSubscriber(
                self._config.get("topic", "rt/lowstate"),
                LowState_,
            )
            subscriber.Init(self._on_lowstate, int(self._config.get("queue_size", 10)))
            with self._subscriber_lock:
                self._subscriber = subscriber

            startup_timeout = float(self._config.get("startup_timeout", 5.0))
            deadline = time.monotonic() + startup_timeout
            while self._last_message_monotonic is None and time.monotonic() < deadline:
                await asyncio.sleep(0.1)

            self._sync_runtime_state()
            if self._state != ServiceState.RUNNING:
                raise RuntimeError(self._last_error or "Robot temperature monitor failed to receive data")

            self._mark_started()
            self._logger.info(
                "Started robot temperature monitor on topic %s",
                self._config.get("topic", "rt/lowstate"),
            )
        except RobotTemperatureMonitorUnsupportedError as exc:
            self._close_subscriber()
            self._state = ServiceState.FAILED
            self._last_error = str(exc)
            self._logger.warning("%s", exc)
            raise
        except Exception as exc:
            self._close_subscriber()
            self._state = ServiceState.FAILED
            if self._last_error is None:
                self._last_error = str(exc) or "Failed to start robot temperature monitor"
            self._logger.exception("Failed to start robot temperature monitor")
            raise

    async def stop(self) -> None:
        if self._state == ServiceState.STOPPED:
            return

        self._state = ServiceState.STOPPING
        self._close_subscriber()
        self._state = ServiceState.STOPPED
        self._start_time = None
        self._start_monotonic = None
        self._last_error = None
        self._logger.info("Stopped robot temperature monitor")

    async def check_health(self) -> bool:
        self._sync_runtime_state()
        return self._state == ServiceState.RUNNING

    def get_status(self) -> ServiceStatus:
        self._sync_runtime_state()
        return super().get_status()

    def get_log_path(self) -> str:
        return self._log_file

    def get_temperature_state(self) -> Dict[str, Any]:
        self._sync_runtime_state()
        supported, unsupported_reason = self._support_state()

        with self._subscriber_lock:
            snapshot = dict(self._latest_snapshot) if self._latest_snapshot else None
            last_message_at = self._last_message_at
            last_message_monotonic = self._last_message_monotonic

        message_age_seconds = None
        if last_message_monotonic is not None:
            message_age_seconds = max(0.0, time.monotonic() - last_message_monotonic)

        return {
            "configured": True,
            "supported": supported,
            "unsupported_reason": unsupported_reason,
            "robot": dict(self._robot_context) if isinstance(self._robot_context, dict) else None,
            "service_state": self._state.value,
            "topic": self._config.get("topic", "rt/lowstate"),
            "last_update": last_message_at,
            "message_age_seconds": message_age_seconds,
            "stale": self._state != ServiceState.RUNNING,
            "last_error": self._last_error,
            "warn_threshold_c": self._warn_threshold_c,
            "critical_threshold_c": self._critical_threshold_c,
            "motor_count": snapshot.get("motor_count", 0) if snapshot else 0,
            "motors": snapshot.get("motors", []) if snapshot else [],
            "max_temperature_c": snapshot.get("max_temperature_c") if snapshot else None,
            "hottest_motor": snapshot.get("hottest_motor") if snapshot else None,
            "warning_count": snapshot.get("warning_count", 0) if snapshot else 0,
            "critical_count": snapshot.get("critical_count", 0) if snapshot else 0,
        }

    def get_config_parameters(self) -> List[ConfigParameter]:
        return [
            ConfigParameter(
                key="topic",
                value=self._config.get("topic", "rt/lowstate"),
                type="string",
                description="Unitree DDS topic providing low-state motor telemetry",
                required=True,
            ),
            ConfigParameter(
                key="network_interface",
                value=self._config.get("network_interface", ""),
                type="string",
                description="Optional network interface passed to ChannelFactoryInitialize",
                required=False,
            ),
            ConfigParameter(
                key="startup_timeout",
                value=self._config.get("startup_timeout", 5.0),
                type="float",
                description="Seconds to wait for the first low-state sample on startup",
                required=False,
            ),
            ConfigParameter(
                key="stale_timeout",
                value=self._config.get("stale_timeout", 3.0),
                type="float",
                description="Seconds before data is considered stale",
                required=False,
            ),
            ConfigParameter(
                key="warn_threshold_c",
                value=self._config.get("warn_threshold_c", 65.0),
                type="float",
                description="Warning threshold in Celsius",
                required=False,
            ),
            ConfigParameter(
                key="critical_threshold_c",
                value=self._config.get("critical_threshold_c", 80.0),
                type="float",
                description="Critical threshold in Celsius",
                required=False,
            ),
        ]
