from __future__ import annotations

from robot_services.gestures.catalog import DEFAULT_GESTURE_CATALOG_ID, GestureCatalog

from .agibot_a2_ultra import (
    AGIBOT_A2_MOTION_DURATION_CAPS_MS,
    AGIBOT_A2_MOTION_HINTS,
    AGIBOT_A2_ULTRA_CATALOG,
)
from .unitree_g1_edu import UNITREE_G1_EDU_CATALOG


_CATALOGS: dict[str, GestureCatalog] = {
    UNITREE_G1_EDU_CATALOG.id: UNITREE_G1_EDU_CATALOG,
    AGIBOT_A2_ULTRA_CATALOG.id: AGIBOT_A2_ULTRA_CATALOG,
}

_MOTION_HINTS: dict[str, dict[int, str]] = {
    AGIBOT_A2_ULTRA_CATALOG.id: AGIBOT_A2_MOTION_HINTS,
}

_MOTION_DURATION_CAPS_MS: dict[str, dict[int, int]] = {
    AGIBOT_A2_ULTRA_CATALOG.id: AGIBOT_A2_MOTION_DURATION_CAPS_MS,
}


def get_catalog(catalog_id: str | None = None) -> GestureCatalog:
    requested = (catalog_id or DEFAULT_GESTURE_CATALOG_ID).strip()
    try:
        return _CATALOGS[requested]
    except KeyError as exc:
        known = ", ".join(sorted(_CATALOGS))
        raise ValueError(f"Unknown gesture catalog '{requested}'. Known catalogs: {known}") from exc


def get_motion_hints(catalog_id: str) -> dict[int, str]:
    """Return the code->keyword resolution hints for a catalog, if any.

    Only catalogs backed by runtime motion discovery (currently
    ``agibot_a2_ultra``) have hints; other backends drive gestures directly
    from the catalog's numeric codes and return an empty mapping.
    """

    return dict(_MOTION_HINTS.get(catalog_id, {}))


def get_motion_duration_caps_ms(catalog_id: str) -> dict[int, int]:
    """Return per-action duration caps for explicitly approved long motions."""

    return dict(_MOTION_DURATION_CAPS_MS.get(catalog_id, {}))


def list_catalogs() -> tuple[str, ...]:
    return tuple(_CATALOGS)


__all__ = [
    "AGIBOT_A2_ULTRA_CATALOG",
    "UNITREE_G1_EDU_CATALOG",
    "get_catalog",
    "get_motion_duration_caps_ms",
    "get_motion_hints",
    "list_catalogs",
]
