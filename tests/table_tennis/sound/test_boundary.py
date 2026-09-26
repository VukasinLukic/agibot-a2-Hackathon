"""Phase 0: the sound package is a boundary, not a detector."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SOUND_ROOT = REPO_ROOT / "table_tennis" / "sound"
FORBIDDEN = {
    "torch",
    "sounddevice",
    "rclpy",
    "cv2",
    "socket",
    "requests",
    "urllib",
    "robot_services",
}


class SoundBoundaryTests(unittest.TestCase):
    def test_import_does_not_open_a_microphone_or_a_model(self) -> None:
        import table_tennis.sound as sound

        self.assertFalse(hasattr(sound, "propose"))
        self.assertFalse(hasattr(sound, "detect"))
        self.assertNotIn("propose", sound.__all__)
        self.assertNotIn("detect", sound.__all__)
        for path in SOUND_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    self.assertNotIn(name, FORBIDDEN, f"{path.name} imports {name}")
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("sounddevice", sys.modules)
        self.assertNotIn("rclpy", sys.modules)


if __name__ == "__main__":
    unittest.main()
