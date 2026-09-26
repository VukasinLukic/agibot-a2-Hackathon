from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass

LOG = logging.getLogger("gesture_policy")


def _str_to_bool(value: str | None, *, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(value: str | None, *, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_allowlist(value: str | None) -> set[str] | None:
    if not value or not value.strip():
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


@dataclass(frozen=True)
class GesturePolicyConfig:
    enabled: bool
    cooldown_s: float
    probability: float
    allowlist: set[str] | None


class GesturePolicy:
    def __init__(self, cfg: GesturePolicyConfig) -> None:
        self._cfg = cfg
        self._last_trigger_ts: float | None = None

    @classmethod
    def from_env(cls) -> "GesturePolicy":
        cfg = GesturePolicyConfig(
            enabled=_str_to_bool(os.getenv("GESTURE_ENABLE"), default=True),
            cooldown_s=_parse_float(os.getenv("GESTURE_COOLDOWN_SEC"), default=30.0),
            probability=_parse_float(os.getenv("GESTURE_PROBABILITY"), default=0.25),
            allowlist=_parse_allowlist(os.getenv("GESTURE_ALLOWLIST")),
        )
        return cls(cfg)

    def should_trigger(self, gesture: str) -> tuple[bool, str]:
        if not self._cfg.enabled:
            return False, "disabled"
        if self._cfg.allowlist is not None and gesture not in self._cfg.allowlist:
            return False, "not_allowed"

        now = time.monotonic()
        if self._last_trigger_ts is not None:
            elapsed = now - self._last_trigger_ts
            if elapsed < self._cfg.cooldown_s:
                return False, "cooldown"

        if self._cfg.probability < 1.0 and random.random() > self._cfg.probability:
            return False, "probability"

        self._last_trigger_ts = now
        return True, "ok"


__all__ = ["GesturePolicy", "GesturePolicyConfig"]