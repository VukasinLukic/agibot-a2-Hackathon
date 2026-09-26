from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from robot_services.gestures.robot_enable import robot_enabled as _robot_enabled

LOG = logging.getLogger("robot_arm")


@dataclass(frozen=True)
class RobotArmConfig:
    iface: str
    action_by_gesture: Mapping[str, int]
    release_action: int = 99
    action_delay_s: float = 2.0


class RobotArmController:
    def __init__(self, cfg: RobotArmConfig) -> None:
        self.cfg = cfg
        self._client: Any | None = None
        self._init_failed = False
        self._gesture_lock = threading.Lock()

    def execute_gesture(self, gesture: str) -> dict[str, object]:
        with self._gesture_lock:
            action = self.cfg.action_by_gesture.get(gesture)
            if action is None:
                LOG.info("Gesture skipped (unknown action mapping): %s", gesture)
                return {
                    "gesture": gesture,
                    "status": "skipped",
                    "reason": "unknown_action_mapping",
                }

            if not _robot_enabled():
                LOG.info(
                    "Gesture skipped (robot disabled). Would trigger '%s' (action=%s).",
                    gesture,
                    action,
                )
                return {
                    "gesture": gesture,
                    "status": "skipped",
                    "reason": "robot_disabled",
                    "action": action,
                }
            if not self._ensure_client():
                LOG.info(
                    "Gesture skipped (robot unavailable). Would trigger '%s' (action=%s).",
                    gesture,
                    action,
                )
                return {
                    "gesture": gesture,
                    "status": "skipped",
                    "reason": "robot_unavailable",
                    "action": action,
                }

            ret = self._client.ExecuteAction(action)
            time.sleep(self.cfg.action_delay_s)
            ret_release = self._client.ExecuteAction(self.cfg.release_action)

            return {
                "gesture": gesture,
                "status": "ok",
                "action": action,
                "ret": ret,
                "ret_release": ret_release,
            }

    def _ensure_client(self) -> bool:
        if not _robot_enabled():
            LOG.info("Robot arm disabled (AUDIO_TARGET=host or ROBOT_ENABLE=0).")
            return False
        if self._init_failed:
            return False

        if self._client is not None:
            return True

        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient
        except Exception as exc:
            LOG.warning("Robot arm unavailable: %s", exc)
            return False

        try:
            ChannelFactoryInitialize(0, self.cfg.iface)
            client = G1ArmActionClient()
            client.Init()
            self._client = client
            return True
        except Exception as exc:
            LOG.warning("Robot arm init failed; disabling gestures. %s", exc)
            self._init_failed = True
            return False
