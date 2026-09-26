"""Make table.json for ``live``: four table corners and the net, on one frame.

Click order (the window shows which point is next):

1. ``end_a_0`` - a corner of end A
2. ``end_a_1`` - the other corner of end A
3. ``end_b_0`` - the end B corner on the same long side as ``end_a_1``
4. ``end_b_1`` - the last corner
5. and 6. where the net meets the two long sides

So the corners go around the table. End A is the end the app asks about
("Na kraju stola end_a stoji"). The frame must come from the same camera and
resolution that ``live`` will read.

    # laptop, clicks on a frame of the recording
    python -m table_tennis.vision.calibrate --clip snimak.mov --at 3 --out table.json
    # no screen (robot): save one fisheye frame, read pixels elsewhere, then pass them
    python -m table_tennis.vision.calibrate --device CHEST_LEFT_FISHEYE --save-frame kadar.png
    python -m table_tennis.vision.calibrate --device CHEST_LEFT_FISHEYE --points "x,y x,y x,y x,y x,y x,y" --out table.json

Same result as ``mark_table`` on a ``live --grab`` still, but straight from a
recording, with a scaled window, typed points and a picture to check.

Next to table.json goes table.png with ends A/B and the net drawn. Then set the
printed calibration_id in the app (setup, or "calibration" in a pause).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from table_tennis.vision.frame import Frame

POINT_NAMES = ("end_a_0", "end_a_1", "end_b_0", "end_b_1", "mreza_1", "mreza_2")
_REASONS = {
    "need_four_corners_and_net": "treba tacno 4 ugla i 2 tacke mreze",
    "out_of_frame": "neka tacka je na manje od 8 px od ivice kadra",
    "self_intersection": "uglovi se seku: idi redom oko stola (end_a_0, end_a_1, pa end_b_0 uz end_a_1)",
    "degenerate_area": "sto je premali u kadru (ispod 2% slike)",
    "homography_failed": "od ovih uglova ne moze da se izracuna sto",
    "net_not_midline": "mreza nije na sredini izmedju krajeva A i B (ili su krajevi zamenjeni)",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--clip", type=Path, help=".mov/.mp4 or TTCLIP; the same file you pass to live")
    source.add_argument("--image", type=Path, help="a saved frame (png/jpg) from the camera live will read")
    source.add_argument("--device", help="raw chest fisheye alias on the robot, for example CHEST_LEFT_FISHEYE")
    parser.add_argument("--at", type=float, default=0.0, help="seconds into --clip (pick a frame with the whole table)")
    parser.add_argument("--points", help='six points "x,y x,y x,y x,y x,y x,y" instead of clicking')
    parser.add_argument("--camera-id", help="default: camera_id from the vision config")
    parser.add_argument("--config", type=Path, help="vision yaml; default config.example.yaml")
    parser.add_argument("--save-frame", type=Path, help="write the frame as png and stop (to read pixels elsewhere)")
    parser.add_argument("--out", type=Path, default=Path("table.json"))
    args = parser.parse_args(argv)

    import cv2

    from table_tennis.vision.config import load_config, load_example_config

    config = load_config(args.config) if args.config else load_example_config()
    frame = grab_frame(args, args.camera_id or config.camera_id)
    image = _array(frame)
    print(f"kadar {frame.width}x{frame.height}, camera_id {frame.camera_id}")

    if args.save_frame is not None:
        if not cv2.imwrite(str(args.save_frame), image):
            raise SystemExit(f"ne mogu da upisem {args.save_frame}")
        print(f"sacuvan {args.save_frame}. Ocitaj piksele redom: {', '.join(POINT_NAMES)}")
        return 0

    points = parse_points(args.points) if args.points else click_points(cv2, image)
    if points is None:
        print("prekinuto, nista nije upisano")
        return 1
    return save(cv2, frame, image, points, args.out)


def grab_frame(args: argparse.Namespace, camera_id: str) -> Frame:
    if args.image is not None:
        import cv2

        from table_tennis.vision.frame import ORIGIN_FILE

        image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
        if image is None:
            raise SystemExit(f"ne mogu da otvorim sliku {args.image}")
        height, width = image.shape[:2]
        return Frame(0, 0, width, height, image, camera_id, ORIGIN_FILE)
    if args.device is not None:
        from table_tennis.vision.a2 import A2FisheyeCapture

        capture: Any = A2FisheyeCapture(args.device)
    else:
        from table_tennis.vision.live import is_ttclip

        if is_ttclip(args.clip):
            from table_tennis.vision.capture import FileCapture

            capture = FileCapture(args.clip, camera_id)
        else:
            from table_tennis.vision.video import VideoFileCapture

            capture = VideoFileCapture(args.clip, camera_id, realtime=False, start_s=args.at)
    with capture:
        for frame in capture:
            return frame
    raise SystemExit("izvor nije dao nijedan kadar")


def parse_points(text: str) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for part in text.replace(";", " ").split():
        try:
            x, y = part.split(",")
            points.append((int(round(float(x))), int(round(float(y)))))
        except ValueError:
            raise SystemExit(f"tacka {part!r} nije u obliku x,y") from None
    if len(points) != len(POINT_NAMES):
        raise SystemExit(f"treba {len(POINT_NAMES)} tacaka ({', '.join(POINT_NAMES)}), dobila sam {len(points)}")
    return points


def click_points(cv2: Any, image: Any) -> list[tuple[int, int]] | None:
    """Six clicks in a window. u = undo, Enter = done, Esc = cancel."""
    from table_tennis.vision.preview import WINDOW, draw_table, fit, put_text

    _, scale = fit(cv2, image)
    points: list[tuple[int, int]] = []

    def on_mouse(event: int, x: int, y: int, flags: int, param: object) -> None:
        del flags, param
        if event == cv2.EVENT_LBUTTONDOWN and len(points) < len(POINT_NAMES):
            points.append((int(round(x / scale)), int(round(y / scale))))

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)
    try:
        while True:
            canvas = image.copy()
            unit = max(1.0, image.shape[1] / 960.0)
            draw_table(cv2, canvas, points[:4], points[4:6], unit)
            for index, point in enumerate(points):
                cv2.circle(canvas, point, int(round(6 * unit)), (0, 255, 255), -1, cv2.LINE_AA)
                put_text(cv2, canvas, POINT_NAMES[index], (point[0] + int(8 * unit), point[1] - int(8 * unit)), 0.6 * unit, (0, 255, 255))
            if len(points) < len(POINT_NAMES):
                hint = f"klikni {POINT_NAMES[len(points)]}   (u = vrati, Esc = odustani)"
            else:
                hint = "Enter = sacuvaj, u = vrati poslednju"
            put_text(cv2, canvas, hint, (int(14 * unit), int(34 * unit)), 0.8 * unit, (255, 255, 255))
            shown, _ = fit(cv2, canvas)
            cv2.imshow(WINDOW, shown)
            key = cv2.waitKey(30) & 0xFF
            if key == 27:
                return None
            if key == ord("u") and points:
                points.pop()
            if key in (13, 10) and len(points) == len(POINT_NAMES):
                return points
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                return None
    finally:
        cv2.destroyAllWindows()


def save(cv2: Any, frame: Frame, image: Any, points: Sequence[tuple[int, int]], out: Path) -> int:
    from table_tennis.vision.calibration import calibration_from_clicks, write_calibration
    from table_tennis.vision.preview import draw_table

    calibration = calibration_from_clicks(frame, list(points))
    print("tacke: " + " ".join(f"{x},{y}" for x, y in points))
    if not calibration.ready:
        print(f"kalibracija NIJE prihvacena: {_REASONS.get(calibration.reason, calibration.reason)}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    write_calibration(out, calibration)
    marked = image.copy()
    draw_table(cv2, marked, calibration.corners_px, calibration.net_px, max(1.0, frame.width / 960.0))
    preview = out.with_suffix(".png")
    cv2.imwrite(str(preview), marked)
    print(f"upisano {out} i {preview}")
    print(f"calibration_id: {calibration.calibration_id}")
    print("U aplikaciji upisi ovaj calibration_id i izaberi ko stoji na kraju A (vidi A/B na slici).")
    return 0


def _array(frame: Frame) -> Any:
    from table_tennis.vision.preview import as_array

    return as_array(frame.image)


if __name__ == "__main__":
    raise SystemExit(main())
