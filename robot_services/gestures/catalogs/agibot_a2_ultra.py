from __future__ import annotations

from robot_services.gestures.catalog import GestureCatalog, GestureEntry, GestureSafety


AGIBOT_A2_ULTRA_CATALOG = GestureCatalog(
    catalog_id="agibot_a2_ultra",
    entries=(
        GestureEntry("wave", 1, GestureSafety.SAFE_ONLY),
        GestureEntry("greeting bow", 2, GestureSafety.SAFE_ONLY),
        GestureEntry("cheer", 3, GestureSafety.SAFE_ONLY),
        GestureEntry("thumbs up", 4, GestureSafety.SAFE_ONLY),
        GestureEntry("welcome gesture", 5, GestureSafety.SAFE_ONLY),
        GestureEntry("goodbye wave", 6, GestureSafety.SAFE_ONLY),
        GestureEntry("presentation point", 7, GestureSafety.SAFE_ONLY),
        GestureEntry("nod thanks", 8, GestureSafety.SAFE_ONLY),
        GestureEntry("handshake", 9, GestureSafety.SAFE_ONLY),
        GestureEntry("v sign", 10, GestureSafety.SAFE_ONLY),
        GestureEntry("fist bump", 11, GestureSafety.SAFE_ONLY),
        GestureEntry("finger heart", 12, GestureSafety.SAFE_ONLY),
        GestureEntry("point left", 13, GestureSafety.SAFE_ONLY),
        GestureEntry("point right", 14, GestureSafety.SAFE_ONLY),
        GestureEntry("no", 15, GestureSafety.SAFE_ONLY),
        GestureEntry("ok sign", 16, GestureSafety.SAFE_ONLY),
        GestureEntry("raise hand", 17, GestureSafety.SAFE_ONLY),
        GestureEntry("panel explanation", 18, GestureSafety.SAFE_ONLY),
        GestureEntry("panel explanation extended", 19, GestureSafety.SAFE_ONLY),
        GestureEntry("panel explanation long", 20, GestureSafety.SAFE_ONLY),
    ),
)


# Keyword used to resolve each curated gesture against the live preset list
# returned by aimdk.protocol.ResourceService/GetMotion, matched against each
# entry's display_name_en. Verified against this A2 unit's real 133-entry
# catalog on 2026-08-03 (no built-in "clap" exists here, hence "cheer").
AGIBOT_A2_MOTION_HINTS: dict[int, str] = {
    1: "Wave hand_right hand",
    2: "Greeting at the beginning",
    3: "Yeah_right hand",
    4: "Thum-up",
    5: "Welcome to the left",
    6: "Wave hand_left hand",
    7: "Direction_point to forward_right hand",
    8: "Nod head",
    9: "Handshake_right hand",
    10: 'Both hands "V" sign',
    11: "Bump fists_right hand",
    12: "Finger heart_right hand",
    13: "Direction_point to the left",
    14: "Direction_point to the right",
    15: "Shake head",
    16: "OK_right hand",
    17: "Pose_Raise the right hand",
    18: "General explanation actions_22s",
    19: "General explanation actions_25s",
    20: "General explanation actions_29s",
}


# Long motions remain capped at six seconds by default. This single curated
# upper-body explanation routine is allowed to play for its full catalog
# duration so it can accompany one Hall of Fame panel narration.
AGIBOT_A2_MOTION_DURATION_CAPS_MS: dict[int, int] = {
    18: 23_000,
    19: 26_000,
    20: 30_000,
}


__all__ = [
    "AGIBOT_A2_MOTION_DURATION_CAPS_MS",
    "AGIBOT_A2_MOTION_HINTS",
    "AGIBOT_A2_ULTRA_CATALOG",
]
