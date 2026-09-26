from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Local weights stay outside git (table_tennis/var/ is ignored); without them this skips.
WEIGHTS = REPO_ROOT / "table_tennis" / "var" / "vision"
HAS_DEPS = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
HAS_WEIGHTS = (WEIGHTS / "ballnet.onnx").is_file() and (WEIGHTS / "ballnet.npz").is_file()


@unittest.skipUnless(HAS_DEPS and HAS_WEIGHTS, "needs cv2 + numpy and local ballnet.onnx/.npz")
class OnnxBallNetTests(unittest.TestCase):
    def test_growing_batches_match_numpy(self) -> None:
        import numpy as np

        from table_tennis.vision.ballnet import BallNet, OnnxBallNet, load_ballnet

        onnx = load_ballnet(WEIGHTS / "ballnet.onnx")
        reference = load_ballnet(WEIGHTS / "ballnet.npz")
        self.assertIsInstance(onnx, OnnxBallNet)
        self.assertIsInstance(reference, BallNet)
        rng = np.random.default_rng(3)
        # OpenCV 5.0 DNN used to kill the process on 10 then 50.
        for count in (10, 50, 0, 1, 200, 3):
            patches = rng.random((count, 32, 32, 4), dtype=np.float32)
            got = onnx.logits(patches)
            self.assertEqual(got.shape, (count,))
            if count:
                self.assertLess(float(np.abs(got - reference.logits(patches)).max()), 1e-4)


if __name__ == "__main__":
    unittest.main()
