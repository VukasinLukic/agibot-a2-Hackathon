import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_services.gestures import (
    DEFAULT_GESTURE_CATALOG_ID,
    GESTURE_CODES,
    GESTURE_MAPPING,
    GESTURE_NAMES,
    GESTURE_SAFETY_GROUPS,
    build_available_gesture_text,
    build_gesture_policy_prompt,
    get_allowed_gestures,
    get_catalog,
    get_motion_duration_caps_ms,
    normalize_gesture,
)


class GestureCatalogTests(unittest.TestCase):
    def test_unitree_catalog_preserves_current_names_and_ids(self) -> None:
        self.assertEqual(DEFAULT_GESTURE_CATALOG_ID, "unitree_g1_edu")
        self.assertEqual(
            GESTURE_MAPPING,
            {
                "release arm": 99,
                "two-hand kiss": 11,
                "left kiss": 12,
                "right kiss": 13,
                "hands up": 15,
                "clap": 17,
                "high five": 18,
                "hug": 19,
                "heart": 20,
                "right heart": 21,
                "reject": 22,
                "right hand up": 23,
                "x ray": 24,
                "face wave": 25,
                "high wave": 26,
                "shake hand": 27,
            },
        )
        self.assertEqual(GESTURE_CODES[25], "face wave")
        self.assertEqual(GESTURE_NAMES, tuple(GESTURE_MAPPING.keys()))

    def test_unitree_safety_pools_match_existing_behavior(self) -> None:
        self.assertEqual(
            GESTURE_SAFETY_GROUPS["unrestricted"],
            (
                "release arm",
                "two-hand kiss",
                "left kiss",
                "right kiss",
                "hands up",
                "clap",
                "high five",
                "hug",
                "heart",
                "right heart",
                "reject",
                "right hand up",
                "x ray",
                "face wave",
                "high wave",
                "shake hand",
            ),
        )
        self.assertEqual(
            get_allowed_gestures("restricted"),
            (
                "release arm",
                "left kiss",
                "right kiss",
                "hands up",
                "clap",
                "high five",
                "hug",
                "right heart",
                "reject",
                "right hand up",
                "x ray",
                "face wave",
                "high wave",
                "shake hand",
            ),
        )
        self.assertEqual(
            get_allowed_gestures("safe_only"),
            (
                "release arm",
                "left kiss",
                "right kiss",
                "clap",
                "high five",
                "reject",
                "right hand up",
                "x ray",
                "face wave",
                "shake hand",
            ),
        )

    def test_normalization_accepts_names_separators_and_numeric_ids(self) -> None:
        self.assertEqual(normalize_gesture("face_wave"), "face wave")
        self.assertEqual(normalize_gesture("face-wave"), "face wave")
        self.assertEqual(normalize_gesture("25"), "face wave")
        self.assertEqual(normalize_gesture("24"), "x ray")
        self.assertIsNone(normalize_gesture("unknown"))

    def test_helpers_can_target_explicit_catalog(self) -> None:
        catalog = get_catalog("unitree_g1_edu")

        self.assertEqual(catalog.id, "unitree_g1_edu")
        self.assertEqual(catalog.normalize_gesture("high-wave"), "high wave")
        self.assertIn("face wave", build_available_gesture_text("safe_only"))

    def test_a2_policy_lists_real_safe_gestures_without_foreign_examples(self) -> None:
        prompt = build_gesture_policy_prompt("safe_only", "agibot_a2_ultra")

        self.assertIn("presentation point", prompt)
        self.assertIn("panel explanation", prompt)
        self.assertIn("panel explanation extended", prompt)
        self.assertIn("panel explanation long", prompt)
        self.assertIn("point left", prompt)
        self.assertIn("point right", prompt)
        self.assertNotIn("clap", prompt)
        self.assertEqual(
            get_motion_duration_caps_ms("agibot_a2_ultra"),
            {18: 23_000, 19: 26_000, 20: 30_000},
        )


if __name__ == "__main__":
    unittest.main()
