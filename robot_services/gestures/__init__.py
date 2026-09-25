"""Gesture bridge services and catalog helpers for Robot Supervisor."""

from __future__ import annotations

from .catalog import (
    DEFAULT_GESTURE_CATALOG_ID,
    DEFAULT_GESTURE_SAFETY_POOL,
    GESTURE_SAFETY_POOLS,
    GestureCatalog,
    GestureEntry,
    GestureSafety,
    get_configured_gesture_safety_pool,
    is_gesture_safety_pool,
    normalize_gesture_safety_pool,
)
from .catalogs import get_catalog, get_motion_duration_caps_ms, get_motion_hints, list_catalogs


_DEFAULT_CATALOG = get_catalog(DEFAULT_GESTURE_CATALOG_ID)

GESTURE_NAMES = _DEFAULT_CATALOG.names
GESTURE_MAPPING = _DEFAULT_CATALOG.mapping
GESTURE_CODES = _DEFAULT_CATALOG.codes
GESTURE_SAFETY_GROUPS = _DEFAULT_CATALOG.safety_groups


def normalize_gesture(gesture: str | None, catalog_id: str | None = None) -> str | None:
    return get_catalog(catalog_id).normalize_gesture(gesture)


def get_allowed_gestures(
    pool: str | None = None,
    catalog_id: str | None = None,
) -> tuple[str, ...]:
    return get_catalog(catalog_id).get_allowed_gestures(pool)


def is_gesture_allowed(
    gesture: str | None,
    pool: str | None = None,
    catalog_id: str | None = None,
) -> bool:
    return get_catalog(catalog_id).is_gesture_allowed(gesture, pool)


def build_available_gesture_text(pool: str | None = None, catalog_id: str | None = None) -> str:
    return get_catalog(catalog_id).build_available_gesture_text(pool)


def build_gesture_policy_prompt(pool: str | None = None, catalog_id: str | None = None) -> str:
    return get_catalog(catalog_id).build_gesture_policy_prompt(pool)


def resolve_gesture_catalog_id_for_robot_context(
    robot_context: "Mapping[str, object] | None",
) -> str:
    """Resolve the gesture catalog id for a supervisor robot context dict.

    Falls back to DEFAULT_GESTURE_CATALOG_ID for missing/unknown/unsupported
    robot models, so callers never have to special-case legacy deployments
    that don't set robot context at all.
    """

    if not robot_context:
        return DEFAULT_GESTURE_CATALOG_ID

    model = robot_context.get("model")
    if not model:
        return DEFAULT_GESTURE_CATALOG_ID

    from humanoid_platform import get_gesture_spec

    try:
        spec = get_gesture_spec(str(model))
    except ValueError:
        return DEFAULT_GESTURE_CATALOG_ID

    return spec.catalog_id if spec is not None else DEFAULT_GESTURE_CATALOG_ID


__all__ = [
    "DEFAULT_GESTURE_CATALOG_ID",
    "DEFAULT_GESTURE_SAFETY_POOL",
    "GESTURE_CODES",
    "GESTURE_MAPPING",
    "GESTURE_NAMES",
    "GESTURE_SAFETY_GROUPS",
    "GESTURE_SAFETY_POOLS",
    "GestureCatalog",
    "GestureEntry",
    "GestureSafety",
    "build_available_gesture_text",
    "build_gesture_policy_prompt",
    "get_allowed_gestures",
    "get_catalog",
    "get_configured_gesture_safety_pool",
    "get_motion_duration_caps_ms",
    "get_motion_hints",
    "is_gesture_allowed",
    "is_gesture_safety_pool",
    "list_catalogs",
    "normalize_gesture",
    "normalize_gesture_safety_pool",
    "resolve_gesture_catalog_id_for_robot_context",
]
