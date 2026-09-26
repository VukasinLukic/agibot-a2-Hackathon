"""BlurBall locator. MIT weights from cogsys-tuebingen/blurball stay outside git.

The checkpoint is trained on real table-tennis blur, not on people.
https://cloud.cs.uni-tuebingen.de/index.php/s/6Z8TpM3sXRKHzGC

Their runner takes three frames, one step, and a score of at least 0.7.
The position it returns is the middle of the blur. That runner lives in
their checkout (``TT_BLURBALL_ROOT``) and requires CUDA; the published
detector refuses CPU. This class holds the three-frame buffer and the
weight path. ``locate`` stays quiet until that runner is attached, and the
OpenCV tracker keeps the frame. Torch is not imported here.
"""

from __future__ import annotations

from pathlib import Path

WEIGHTS_PAGE = "https://cloud.cs.uni-tuebingen.de/index.php/s/6Z8TpM3sXRKHzGC"
SCORE_THRESHOLD = 0.7
FRAMES_IN = 3


class BlurBallDetector:
    """Preferred ball locator. Missing weights fail at startup, not per frame."""

    def __init__(self, weights: str) -> None:
        path = Path(weights)
        if not path.is_file():
            raise ValueError(f"BlurBall weights not found: {path}")
        self.weights = path
        self._frames: list[tuple[bytes, int, int]] = []

    def locate(self, pixels: bytes, width: int, height: int) -> tuple[float, float, float] | None:
        self._frames.append((pixels, width, height))
        del self._frames[:-FRAMES_IN]
        if len(self._frames) < FRAMES_IN:
            return None
        return None
