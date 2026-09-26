"""OpenCV window for a laptop test: the calibrated table and the tracked ball.

Opt-in (``live --show``). It never decides anything; the robot runs without it.
Space pauses the picture, q stops the process.
"""

from __future__ import annotations

from typing import Any

from table_tennis.vision.calibration import TableCalibration
from table_tennis.vision.image import BgrImage

END_A = (255, 160, 32)
END_B = (32, 160, 255)
NET = (40, 220, 40)
SIDE = (180, 180, 180)
_BALL = {"observed": (255, 0, 255), "predicted": (0, 220, 255)}
MAX_WINDOW_WIDTH = 1280
WINDOW = "TT vision"


def as_array(image: Any) -> Any:
    import numpy as np

    if isinstance(image, BgrImage):
        return np.frombuffer(bytes(image.data), dtype=np.uint8).reshape(image.height, image.width, 3).copy()
    return np.asarray(image).copy()


def draw_table(cv2: Any, image: Any, corners: Any, net: Any, unit: float) -> None:
    """End A, end B, the long sides and the net, with A/B labels at each end."""
    thick = max(2, int(round(3 * unit)))
    points = [tuple(int(v) for v in point) for point in corners]
    if len(points) >= 2:
        cv2.line(image, points[0], points[1], END_A, thick, cv2.LINE_AA)
    if len(points) >= 3:
        cv2.line(image, points[1], points[2], SIDE, thick, cv2.LINE_AA)
    if len(points) == 4:
        cv2.line(image, points[2], points[3], END_B, thick, cv2.LINE_AA)
        cv2.line(image, points[3], points[0], SIDE, thick, cv2.LINE_AA)
        for label, a, b, color in (("A", points[0], points[1], END_A), ("B", points[2], points[3], END_B)):
            mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
            put_text(cv2, image, label, (mid[0] - int(10 * unit), mid[1] + int(10 * unit)), 1.2 * unit, color)
    net_points = [tuple(int(v) for v in point) for point in net]
    if len(net_points) == 2:
        cv2.line(image, net_points[0], net_points[1], NET, thick, cv2.LINE_AA)


def put_text(cv2: Any, image: Any, text: str, origin: tuple[int, int], scale: float, color: tuple[int, int, int]) -> None:
    thickness = max(1, int(round(2 * scale)))
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 3, cv2.LINE_AA)
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def fit(cv2: Any, image: Any) -> tuple[Any, float]:
    """Shrink to the window width. Returns the picture and the scale (shown / original)."""
    width = image.shape[1]
    if width <= MAX_WINDOW_WIDTH:
        return image, 1.0
    scale = MAX_WINDOW_WIDTH / width
    return cv2.resize(image, (MAX_WINDOW_WIDTH, int(round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA), scale


class PreviewWindow:
    """One window update per frame, from ``live``'s ``on_frame`` hook. Draws only."""

    def __init__(self, calibration: TableCalibration) -> None:
        import cv2

        self._cv2 = cv2
        self._calibration = calibration

    def close(self) -> None:
        self._cv2.destroyAllWindows()

    def show(self, frame: Any, sample: Any) -> None:
        cv2 = self._cv2
        image = as_array(frame.image)
        unit = max(1.0, frame.width / 960.0)
        draw_table(cv2, image, self._calibration.corners_px, self._calibration.net_px, unit)
        color = _BALL.get(sample.observation_kind)
        if color is not None and sample.x_px is not None and sample.y_px is not None:
            center = (int(round(sample.x_px)), int(round(sample.y_px)))
            cv2.circle(image, center, int(round(22 * unit)), color, max(2, int(round(4 * unit))), cv2.LINE_AA)
        status = {"observed": "LOPTICA", "predicted": "PREDVIDJENO", "missing": "-"}[sample.observation_kind]
        put_text(cv2, image, f"kadar {frame.frame_seq}   {status}   space=pauza  q=kraj", (int(14 * unit), int(34 * unit)), 0.8 * unit, (255, 255, 255))
        shown, _ = fit(cv2, image)
        cv2.imshow(WINDOW, shown)
        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            key = self._paused()
        if key == ord("q"):
            raise KeyboardInterrupt

    def _paused(self) -> int:
        while True:
            key = self._cv2.waitKey(50) & 0xFF
            if key in (ord(" "), ord("q")):
                return key
