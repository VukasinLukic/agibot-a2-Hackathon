from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from robot_services.gestures.robot_enable import robot_enabled

LOG = logging.getLogger("agibot_motion_player")

_DEFAULT_MOTION_COMMAND_URL = "http://192.168.100.100:56444"
_DEFAULT_RESOURCE_SERVICE_URL = "http://192.168.100.110:51049"
_DEFAULT_MOTION_DURATION_MS = 5000
_MAX_MOTION_DURATION_MS = 6000  # cap long routines so gestures stay conversational


@dataclass(frozen=True)
class AgibotMotionPlayerConfig:
    action_by_gesture: Mapping[str, int]
    motion_hints: Mapping[int, str]
    motion_duration_caps_ms: Mapping[int, int] = field(default_factory=dict)
    motion_command_url: str = _DEFAULT_MOTION_COMMAND_URL
    resource_service_url: str = _DEFAULT_RESOURCE_SERVICE_URL
    request_timeout_s: float = 5.0


class AgibotMotionPlayerController:
    """Plays curated AIMA preset motions on an Agibot A2 Ultra.

    Resolves each curated gesture's real ``motion_id`` lazily by querying
    ``ResourceService/GetMotion`` once and matching by keyword, since the
    preset library isn't checked into this repo. A gesture whose keyword
    doesn't match anything on the live robot is simply unavailable.
    """

    def __init__(self, cfg: AgibotMotionPlayerConfig) -> None:
        self.cfg = cfg
        self._gesture_lock = threading.Lock()
        self._motion_table: dict[int, tuple[str, int]] | None = None
        self._motion_table_failed = False

    def execute_gesture(self, gesture: str) -> dict[str, object]:
        with self._gesture_lock:
            code = self.cfg.action_by_gesture.get(gesture)
            if code is None:
                LOG.info("Gesture skipped (unknown action mapping): %s", gesture)
                return {
                    "gesture": gesture,
                    "status": "skipped",
                    "reason": "unknown_action_mapping",
                }

            if not robot_enabled():
                LOG.info(
                    "Gesture skipped (robot disabled). Would trigger '%s' (code=%s).",
                    gesture,
                    code,
                )
                return {
                    "gesture": gesture,
                    "status": "skipped",
                    "reason": "robot_disabled",
                    "code": code,
                }

            if not self._ensure_motion_table():
                LOG.info(
                    "Gesture skipped (motion catalog unavailable). Would trigger '%s' (code=%s).",
                    gesture,
                    code,
                )
                return {
                    "gesture": gesture,
                    "status": "skipped",
                    "reason": "robot_unavailable",
                    "code": code,
                }

            resolved = self._motion_table.get(code) if self._motion_table else None
            if resolved is None:
                LOG.warning(
                    "Gesture skipped (no matching preset motion found): %s (code=%s, hint=%r)",
                    gesture,
                    code,
                    self.cfg.motion_hints.get(code),
                )
                return {
                    "gesture": gesture,
                    "status": "skipped",
                    "reason": "motion_not_resolved",
                    "code": code,
                }

            motion_id, duration_ms = resolved
            try:
                response = self._send_motion_command(motion_id, duration_ms)
            except Exception as exc:
                LOG.warning("Gesture execution failed: %s (%s)", gesture, exc)
                return {
                    "gesture": gesture,
                    "status": "error",
                    "reason": str(exc),
                    "code": code,
                    "motion_id": motion_id,
                }

            return {
                "gesture": gesture,
                "status": "ok",
                "code": code,
                "motion_id": motion_id,
                "duration_ms": duration_ms,
                "response": response,
            }

    def _send_motion_command(self, motion_id: str, duration_ms: int) -> Any:
        payload = {
            "motion_id": motion_id,
            "duration_ms": duration_ms,
            "cmd_end": True,
            "cmd_pause": False,
            "cmd_reset": False,
        }
        url = (
            f"{self.cfg.motion_command_url}"
            "/rpc/aimdk.protocol.MotionCommandService/SendMotionCommand"
        )
        with httpx.Client(timeout=self.cfg.request_timeout_s) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()

    def _ensure_motion_table(self) -> bool:
        if self._motion_table is not None:
            return True
        if self._motion_table_failed:
            return False
        if not robot_enabled():
            return False

        try:
            presets = self._fetch_motion_presets()
        except Exception as exc:
            LOG.warning("Motion preset discovery failed; disabling gestures. %s", exc)
            self._motion_table_failed = True
            return False

        table: dict[int, tuple[str, int]] = {}
        for code, hint in self.cfg.motion_hints.items():
            match = _find_preset(presets, hint)
            if match is None:
                LOG.warning("No preset motion matched hint %r for code %s", hint, code)
                continue
            motion_id, duration_ms = match
            duration_cap_ms = self.cfg.motion_duration_caps_ms.get(
                code, _MAX_MOTION_DURATION_MS
            )
            if duration_cap_ms <= 0:
                duration_cap_ms = _MAX_MOTION_DURATION_MS
            capped_duration_ms = (
                min(duration_ms, duration_cap_ms)
                if duration_ms
                else min(_DEFAULT_MOTION_DURATION_MS, duration_cap_ms)
            )
            table[code] = (motion_id, capped_duration_ms)

        self._motion_table = table
        LOG.info(
            "Resolved %d/%d curated gestures against the live preset library",
            len(table),
            len(self.cfg.motion_hints),
        )
        return True

    def _fetch_motion_presets(self) -> list[dict[str, Any]]:
        url = f"{self.cfg.resource_service_url}/rpc/aimdk.protocol.ResourceService/GetMotion"
        with httpx.Client(timeout=self.cfg.request_timeout_s) as client:
            response = client.post(url, json={})
            response.raise_for_status()
            data = response.json()
        return data.get("motions") or data.get("data") or []


def _find_preset(presets: list[dict[str, Any]], hint: str) -> tuple[str, int] | None:
    needle = hint.strip().lower()
    matches = []
    for preset in presets:
        display_en = str(preset.get("display_name_en", "")).lower()
        name = str(preset.get("motion_name", "")).lower()
        path = str(preset.get("motion_path", "")).lower()
        if needle in display_en or needle in name or needle in path:
            matches.append((preset, display_en))

    if not matches:
        return None

    # Several entries have a "_Long" extended-routine sibling that also
    # substring-matches the short name (e.g. "Handshake_right hand" also
    # matches "Handshake_right hand_Long"). Prefer the short variant unless
    # the hint itself asked for the long one.
    if "_long" not in needle:
        short_matches = [m for m in matches if "_long" not in m[1]]
        if short_matches:
            matches = short_matches

    preset = matches[0][0]
    motion_path = preset.get("motion_path")
    if not motion_path:
        return None
    duration_s = float(preset.get("duration") or 0)
    duration_ms = round(duration_s * 1000)
    return str(motion_path), duration_ms


__all__ = ["AgibotMotionPlayerConfig", "AgibotMotionPlayerController"]
