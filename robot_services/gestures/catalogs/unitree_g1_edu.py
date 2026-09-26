from __future__ import annotations

from robot_services.gestures.catalog import GestureCatalog, GestureEntry, GestureSafety


UNITREE_G1_EDU_CATALOG = GestureCatalog(
    catalog_id="unitree_g1_edu",
    entries=(
        GestureEntry("release arm", 99, GestureSafety.SAFE_ONLY),
        GestureEntry("two-hand kiss", 11),
        GestureEntry("left kiss", 12, GestureSafety.SAFE_ONLY),
        GestureEntry("right kiss", 13, GestureSafety.SAFE_ONLY),
        GestureEntry("hands up", 15, GestureSafety.RESTRICTED),
        GestureEntry("clap", 17, GestureSafety.SAFE_ONLY),
        GestureEntry("high five", 18, GestureSafety.SAFE_ONLY),
        GestureEntry("hug", 19, GestureSafety.RESTRICTED),
        GestureEntry("heart", 20),
        GestureEntry("right heart", 21, GestureSafety.RESTRICTED),
        GestureEntry("reject", 22, GestureSafety.SAFE_ONLY),
        GestureEntry("right hand up", 23, GestureSafety.SAFE_ONLY),
        GestureEntry("x ray", 24, GestureSafety.SAFE_ONLY),
        GestureEntry("face wave", 25, GestureSafety.SAFE_ONLY),
        GestureEntry("high wave", 26, GestureSafety.RESTRICTED),
        GestureEntry("shake hand", 27, GestureSafety.SAFE_ONLY),
    ),
)


__all__ = ["UNITREE_G1_EDU_CATALOG"]

