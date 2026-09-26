"""Click a still into ``table.json``.

Order is ``end_a_0``, ``end_a_1``, ``end_b_0``, ``end_b_1``, then the two net
points. ``u`` undoes the last click, ``r`` clears them, ``q`` quits.

    python -m table_tennis.vision.mark_table still.jpg -o table.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from table_tennis.vision.calibration import CORNER_ORDER, calibration_from_clicks, write_calibration
from table_tennis.vision.frame import ORIGIN_FILE, Frame
from table_tennis.vision.image import BgrImage

_LABELS = list(CORNER_ORDER) + ["net_0", "net_1"]


def frame_from_jpeg(path: Path, camera_id: str) -> tuple[Frame, object]:
    import cv2
    import numpy as np

    array = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if array is None:
        raise SystemExit(f"could not read {path}")
    array = np.ascontiguousarray(array)
    height, width = array.shape[:2]
    image = BgrImage(width, height, array.tobytes())
    frame = Frame(1, 1, width, height, image, camera_id, ORIGIN_FILE)
    return frame, array


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("table.json"))
    parser.add_argument("--camera-id", default="chest_left_fisheye")
    args = parser.parse_args(argv)
    import cv2

    frame, array = frame_from_jpeg(args.image, args.camera_id)
    clicks: list[tuple[int, int]] = []
    window = "table"

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and len(clicks) < 6:
            clicks.append((x, y))

    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)
    while True:
        view = array.copy()
        for index, (x, y) in enumerate(clicks):
            cv2.circle(view, (x, y), 6, (0, 220, 255), -1)
            cv2.putText(view, _LABELS[index], (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1)
        hint = _LABELS[len(clicks)] if len(clicks) < 6 else "enter accepts"
        cv2.putText(view, hint, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow(window, view)
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            cv2.destroyAllWindows()
            return 1
        if key == ord("u") and clicks:
            clicks.pop()
        if key == ord("r"):
            clicks.clear()
        if key in (13, 10) and len(clicks) == 6:
            result = calibration_from_clicks(frame, clicks)
            if not result.ready:
                print(result.reason)
                clicks.clear()
                continue
            write_calibration(args.output, result)
            print(f"{args.output} calibration_id={result.calibration_id} {result.width}x{result.height}")
            cv2.destroyAllWindows()
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
