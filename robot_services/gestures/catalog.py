from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


DEFAULT_GESTURE_CATALOG_ID = "unitree_g1_edu"
DEFAULT_GESTURE_SAFETY_POOL = "safe_only"


class GestureSafety(str, Enum):
    """Safety membership for a gesture entry."""

    UNRESTRICTED = "unrestricted"
    RESTRICTED = "restricted"
    SAFE_ONLY = "safe_only"


GESTURE_SAFETY_POOLS = tuple(safety.value for safety in GestureSafety)
_CANONICAL_POOLS = frozenset(GESTURE_SAFETY_POOLS)


@dataclass(frozen=True)
class GestureEntry:
    name: str
    id: int
    safety: GestureSafety = GestureSafety.UNRESTRICTED


class GestureCatalog:
    """A concrete executable gesture catalog for one robot/backend family."""

    def __init__(self, *, catalog_id: str, entries: tuple[GestureEntry, ...]) -> None:
        if not catalog_id.strip():
            raise ValueError("Gesture catalog id must not be empty")
        if not entries:
            raise ValueError(f"Gesture catalog '{catalog_id}' must contain at least one entry")

        self.id = catalog_id
        self.entries = entries

        mapping: dict[str, int] = {}
        codes: dict[int, str] = {}
        aliases: dict[str, str] = {}
        for entry in entries:
            name = self._stored_name(entry.name)
            if name in mapping:
                raise ValueError(f"Duplicate gesture name in catalog '{catalog_id}': {name}")
            if entry.id in codes:
                raise ValueError(f"Duplicate gesture id in catalog '{catalog_id}': {entry.id}")
            lookup_key = self._lookup_key(entry.name)
            if lookup_key in aliases:
                raise ValueError(
                    f"Duplicate gesture lookup alias in catalog '{catalog_id}': {lookup_key}"
                )
            mapping[name] = entry.id
            codes[entry.id] = name
            aliases[name] = name
            aliases[lookup_key] = name

        self._mapping = MappingProxyType(mapping)
        self._codes = MappingProxyType(codes)
        self._aliases = MappingProxyType(aliases)
        self._names = tuple(mapping.keys())
        self._canonical_gestures = frozenset(self._names)
        self._safety_groups = MappingProxyType(
            {
                GestureSafety.UNRESTRICTED.value: self._names,
                GestureSafety.RESTRICTED.value: tuple(
                    self._stored_name(entry.name)
                    for entry in entries
                    if entry.safety in {GestureSafety.RESTRICTED, GestureSafety.SAFE_ONLY}
                ),
                GestureSafety.SAFE_ONLY.value: tuple(
                    self._stored_name(entry.name)
                    for entry in entries
                    if entry.safety == GestureSafety.SAFE_ONLY
                ),
            }
        )

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    @property
    def mapping(self) -> Mapping[str, int]:
        return self._mapping

    @property
    def codes(self) -> Mapping[int, str]:
        return self._codes

    @property
    def safety_groups(self) -> Mapping[str, tuple[str, ...]]:
        return self._safety_groups

    @staticmethod
    def _stored_name(name: str) -> str:
        return str(name).strip().lower()

    @staticmethod
    def _lookup_key(name: str) -> str:
        return str(name).strip().lower().replace("_", " ").replace("-", " ")

    def normalize_gesture(self, gesture: str | None) -> str | None:
        if gesture is None:
            return None

        raw = str(gesture).strip()
        if not raw:
            return None

        if raw.isdigit():
            return self._codes.get(int(raw))

        exact = self._stored_name(raw)
        if exact in self._canonical_gestures:
            return exact

        return self._aliases.get(self._lookup_key(raw))

    def get_allowed_gestures(self, pool: str | None = None) -> tuple[str, ...]:
        return self._safety_groups[normalize_gesture_safety_pool(pool)]

    def is_gesture_allowed(self, gesture: str | None, pool: str | None = None) -> bool:
        normalized_gesture = self.normalize_gesture(gesture)
        if normalized_gesture is None:
            return False
        return normalized_gesture in self.get_allowed_gestures(pool)

    def build_available_gesture_text(self, pool: str | None = None) -> str:
        return ", ".join(self.get_allowed_gestures(pool))

    def build_gesture_policy_prompt(self, pool: str | None = None) -> str:
        gestures = self.get_allowed_gestures(pool)
        lines = [
            "  - You have access to a few standard gestures you can perform.",
            "  - Available gestures you can perform (and only these):",
        ]
        lines.extend(f"    - {gesture}" for gesture in gestures)
        lines.extend(
            (
                "  - You should trigger appropriate gestures when requested.",
                "  - You should also trigger appropriate gestures by yourself when the situation is relevant "
                "using only a gesture from the available list above.",
            )
        )
        return "\n".join(lines)


def is_gesture_safety_pool(pool: str | None) -> bool:
    if pool is None:
        return False
    normalized = str(pool).strip().lower().replace("-", "_")
    return normalized in _CANONICAL_POOLS


def normalize_gesture_safety_pool(pool: str | None) -> str:
    if pool is None:
        return DEFAULT_GESTURE_SAFETY_POOL
    normalized = str(pool).strip().lower().replace("-", "_")
    if normalized in _CANONICAL_POOLS:
        return normalized
    return DEFAULT_GESTURE_SAFETY_POOL


def get_configured_gesture_safety_pool(pool: str | None = None) -> str:
    if pool is None:
        pool = os.getenv("GESTURE_SAFETY_POOL", DEFAULT_GESTURE_SAFETY_POOL)
    return normalize_gesture_safety_pool(pool)
