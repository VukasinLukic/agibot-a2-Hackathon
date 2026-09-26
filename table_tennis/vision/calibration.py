"""Table-plane calibration from four clicks and a net line.

Corner order is fixed and is not image-left or robot-left:

0. ``end_a_0`` — one corner of end A
1. ``end_a_1`` — the other corner of end A
2. ``end_b_0`` — the end B corner next to ``end_a_1``
3. ``end_b_1`` — the remaining end B corner

The net is two points across the middle. Homography maps the table plane
only. A projected image point is never a bounce.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

from table_tennis.vision.a2 import marks_inside
from table_tennis.vision.frame import Frame
from table_tennis.vision.image import BgrImage
from table_tennis.vision.table import TABLE_LENGTH_MM, TABLE_WIDTH_MM

CORNER_ORDER = ("end_a_0", "end_a_1", "end_b_0", "end_b_1")
PLANE_NOTE = "homography is the table plane; an airborne ball projected onto it is not a bounce"

class _Clock(Protocol):
    def now(self) -> datetime: ...


class _Ids(Protocol):
    def new_id(self) -> str: ...


_END_A = (255, 160, 32)
_END_B = (32, 160, 255)
_NET = (40, 220, 40)
_SIDE = (180, 180, 180)
_MIN_AREA_RATIO = 0.02
_NET_TOLERANCE = 0.12


@dataclass(frozen=True, slots=True)
class PlaneProjection:
    x_mm: float
    y_mm: float
    inside_table: bool
    proves_bounce: bool


@dataclass(frozen=True, slots=True)
class TableCalibration:
    ready: bool
    reason: str
    calibration_id: str | None
    camera_id: str
    width: int
    height: int
    frame_seq: int
    corner_order: tuple[str, str, str, str]
    corners_px: tuple[tuple[int, int], ...]
    net_px: tuple[tuple[int, int], tuple[int, int]]
    homography: tuple[tuple[float, float, float], ...] | None
    created_at: str

    def project_to_table_plane(self, x: float, y: float) -> PlaneProjection:
        if self.homography is None:
            raise ValueError("calibration has no homography")
        u, v = _apply(self.homography, x, y)
        inside = 0.0 <= u <= TABLE_WIDTH_MM and 0.0 <= v <= TABLE_LENGTH_MM
        return PlaneProjection(x_mm=u, y_mm=v, inside_table=inside, proves_bounce=False)

    def to_json(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "calibration_id": self.calibration_id,
            "camera_id": self.camera_id,
            "width": self.width,
            "height": self.height,
            "frame_seq": self.frame_seq,
            "corner_order": list(self.corner_order),
            "corners_px": [list(point) for point in self.corners_px],
            "net_px": [list(point) for point in self.net_px],
            "ends": {"end_a": [0, 1], "end_b": [2, 3]},
            "table_plane_mm": {"width": TABLE_WIDTH_MM, "length": TABLE_LENGTH_MM},
            "homography": None if self.homography is None else [list(row) for row in self.homography],
            "created_at": self.created_at,
            "plane_note": PLANE_NOTE,
        }

    def invalidated(self) -> TableCalibration:
        return TableCalibration(
            ready=False,
            reason="invalidated",
            calibration_id=self.calibration_id,
            camera_id=self.camera_id,
            width=self.width,
            height=self.height,
            frame_seq=self.frame_seq,
            corner_order=self.corner_order,
            corners_px=self.corners_px,
            net_px=self.net_px,
            homography=self.homography,
            created_at=self.created_at,
        )


class CalibrationGate:
    """Accept one still frame. A rejected click cannot be retried until the pose changes."""

    def __init__(
        self,
        *,
        ids: _Ids | None = None,
        clock: _Clock | None = None,
        margin_px: int = 8,
    ) -> None:
        self._ids = ids or _default_ids()
        self._clock = clock or _default_clock()
        self._margin_px = margin_px
        self._blocked = False
        self._rejected_seq: int | None = None
        self.current: TableCalibration | None = None

    def camera_moved(self) -> None:
        self._blocked = False
        if self.current is not None:
            self.current = self.current.invalidated()

    def observe_frame(self, frame: Frame) -> None:
        current = self.current
        if current is None or not current.ready:
            return
        if frame.camera_id != current.camera_id or frame.width != current.width or frame.height != current.height:
            self.current = current.invalidated()

    def submit(
        self,
        frame: Frame,
        corners: Sequence[tuple[int, int]],
        net: Sequence[tuple[int, int]],
    ) -> TableCalibration:
        if self._blocked or frame.frame_seq == self._rejected_seq:
            result = _blank(frame, corners, net, self._clock, reason="wait_for_new_pose")
            self.current = None
            return result
        result = _build(frame, corners, net, self._ids, self._clock, self._margin_px)
        if not result.ready:
            self._blocked = True
            self._rejected_seq = frame.frame_seq
            self.current = None
            return result
        self._rejected_seq = None
        self.current = result
        return result


def write_calibration(path: Path | str, calibration: TableCalibration) -> None:
    if not calibration.ready or calibration.calibration_id is None:
        raise ValueError("only a ready calibration is written")
    Path(path).write_text(json.dumps(calibration.to_json(), indent=2), encoding="utf-8")


def read_calibration(path: Path | str) -> TableCalibration:
    """Read a file written by ``write_calibration``. A calibration that is not ready is refused."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("ready") is not True or not data.get("calibration_id"):
        raise ValueError("calibration file is not ready")
    homography = data.get("homography")
    if not isinstance(homography, list) or len(homography) != 3:
        raise ValueError("calibration file has no homography")
    corners = _points(data.get("corners_px"), 4)
    net = _points(data.get("net_px"), 2)
    order = data.get("corner_order")
    if not isinstance(order, list) or tuple(order) != CORNER_ORDER:
        raise ValueError("calibration corner_order is not end_a_0, end_a_1, end_b_0, end_b_1")
    return TableCalibration(
        ready=True,
        reason=str(data.get("reason") or "ready"),
        calibration_id=str(data["calibration_id"]),
        camera_id=str(data["camera_id"]),
        width=_positive_int(data.get("width"), "width"),
        height=_positive_int(data.get("height"), "height"),
        frame_seq=_non_negative_int(data.get("frame_seq"), "frame_seq"),
        corner_order=CORNER_ORDER,
        corners_px=corners,
        net_px=(net[0], net[1]),
        homography=_homography(homography),
        created_at=str(data.get("created_at") or ""),
    )


def _points(value: object, count: int) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"expected {count} points")
    points: list[tuple[int, int]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"expected {count} points")
        points.append((_non_negative_int(point[0], "x"), _non_negative_int(point[1], "y")))
    return tuple(points)


def _homography(rows: list[object]) -> tuple[tuple[float, float, float], ...]:
    parsed: list[tuple[float, float, float]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError("calibration file has no homography")
        parsed.append((float(row[0]), float(row[1]), float(row[2])))
    return tuple(parsed)


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive int")
    return value


def _non_negative_int(value: object, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative int")
    return value


def mark_ends(image: BgrImage, calibration: TableCalibration) -> BgrImage:
    """Copy the frame and draw end A, end B, and the net. Does not judge a point."""
    marked = image.copy()
    corners = calibration.corners_px
    if len(corners) == 4:
        _line(marked, corners[0], corners[1], _END_A)
        _line(marked, corners[2], corners[3], _END_B)
        _line(marked, corners[1], corners[2], _SIDE)
        _line(marked, corners[3], corners[0], _SIDE)
        _label(marked, _mid(corners[0], corners[1]), "A")
        _label(marked, _mid(corners[2], corners[3]), "B")
    if len(calibration.net_px) == 2:
        _line(marked, calibration.net_px[0], calibration.net_px[1], _NET)
    return marked


def write_marked_ppm(path: Path | str, image: BgrImage) -> None:
    header = f"P6\n{image.width} {image.height}\n255\n".encode("ascii")
    rgb = bytearray(image.width * image.height * 3)
    for index in range(0, len(image.data), 3):
        rgb[index] = image.data[index + 2]
        rgb[index + 1] = image.data[index + 1]
        rgb[index + 2] = image.data[index]
    Path(path).write_bytes(header + rgb)


def _build(
    frame: Frame,
    corners: Sequence[tuple[int, int]],
    net: Sequence[tuple[int, int]],
    ids: _Ids,
    clock: _Clock,
    margin_px: int,
) -> TableCalibration:
    points = tuple((int(x), int(y)) for x, y in corners)
    net_points = tuple((int(x), int(y)) for x, y in net)
    if len(points) != 4 or len(net_points) != 2:
        return _blank(frame, points, net_points, clock, reason="need_four_corners_and_net")
    if not marks_inside(frame.width, frame.height, points + net_points, margin_px):
        return _blank(frame, points, net_points, clock, reason="out_of_frame")
    if _self_intersects(points):
        return _blank(frame, points, net_points, clock, reason="self_intersection")
    if abs(_area(points)) < _MIN_AREA_RATIO * frame.width * frame.height:
        return _blank(frame, points, net_points, clock, reason="degenerate_area")
    destination = (
        (0.0, 0.0),
        (TABLE_WIDTH_MM, 0.0),
        (TABLE_WIDTH_MM, TABLE_LENGTH_MM),
        (0.0, TABLE_LENGTH_MM),
    )
    try:
        homography = _solve(points, destination)
    except ValueError:
        return _blank(frame, points, net_points, clock, reason="homography_failed")
    if not _net_is_midline(homography, net_points):
        return _blank(frame, points, net_points, clock, reason="net_not_midline")
    return TableCalibration(
        ready=True,
        reason="ready",
        calibration_id=ids.new_id(),
        camera_id=frame.camera_id,
        width=frame.width,
        height=frame.height,
        frame_seq=frame.frame_seq,
        corner_order=CORNER_ORDER,
        corners_px=points,
        net_px=(net_points[0], net_points[1]),
        homography=homography,
        created_at=clock.now().isoformat(),
    )


def _blank(
    frame: Frame,
    corners: Sequence[tuple[int, int]],
    net: Sequence[tuple[int, int]],
    clock: _Clock,
    *,
    reason: str,
) -> TableCalibration:
    points = tuple((int(x), int(y)) for x, y in corners)
    net_points = tuple((int(x), int(y)) for x, y in net)
    pair = (net_points[0], net_points[1]) if len(net_points) == 2 else ((0, 0), (0, 0))
    return TableCalibration(
        ready=False,
        reason=reason,
        calibration_id=None,
        camera_id=frame.camera_id,
        width=frame.width,
        height=frame.height,
        frame_seq=frame.frame_seq,
        corner_order=CORNER_ORDER,
        corners_px=points,
        net_px=pair,
        homography=None,
        created_at=clock.now().isoformat(),
    )


def _default_ids() -> _Ids:
    from table_tennis.core.ports import UuidGenerator

    return UuidGenerator()


def _default_clock() -> _Clock:
    from table_tennis.core.ports import SystemClock

    return SystemClock()


def _solve(
    source: Sequence[tuple[int, int]],
    destination: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float, float], ...]:
    import numpy as np

    rows: list[list[float]] = []
    for (x, y), (u, v) in zip(source, destination, strict=True):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y, -u])
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y, -v])
    _, _, vh = np.linalg.svd(np.array(rows, dtype=float))
    flat = vh[-1, :]
    scale = float(flat[8])
    if abs(scale) < 1e-12:
        raise ValueError("homography is degenerate")
    values = [float(item) / scale for item in flat]
    return (
        (values[0], values[1], values[2]),
        (values[3], values[4], values[5]),
        (values[6], values[7], values[8]),
    )


def _apply(homography: tuple[tuple[float, float, float], ...], x: float, y: float) -> tuple[float, float]:
    weight = homography[2][0] * x + homography[2][1] * y + homography[2][2]
    if abs(weight) < 1e-9:
        raise ValueError("point is outside the homography")
    across = (homography[0][0] * x + homography[0][1] * y + homography[0][2]) / weight
    along = (homography[1][0] * x + homography[1][1] * y + homography[1][2]) / weight
    return across, along


def _net_is_midline(
    homography: tuple[tuple[float, float, float], ...],
    net: tuple[tuple[int, int], tuple[int, int]],
) -> bool:
    projected = [_apply(homography, x, y) for x, y in net]
    middle = TABLE_LENGTH_MM / 2.0
    span = TABLE_LENGTH_MM * _NET_TOLERANCE
    for across, along in projected:
        if not -1.0 <= across <= TABLE_WIDTH_MM + 1.0:
            return False
        if abs(along - middle) > span:
            return False
    return True


def _self_intersects(points: Sequence[tuple[int, int]]) -> bool:
    edges = ((0, 1), (1, 2), (2, 3), (3, 0))
    for index, (a, b) in enumerate(edges):
        for c, d in edges[index + 1 :]:
            if len({a, b, c, d}) < 4:
                continue
            if _segments_cross(points[a], points[b], points[c], points[d]):
                return True
    return False


def _segments_cross(
    a: tuple[int, int],
    b: tuple[int, int],
    c: tuple[int, int],
    d: tuple[int, int],
) -> bool:
    return _orient(a, b, c) * _orient(a, b, d) < 0 and _orient(c, d, a) * _orient(c, d, b) < 0


def _orient(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> int:
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _area(points: Sequence[tuple[int, int]]) -> float:
    total = 0.0
    for index, (x, y) in enumerate(points):
        nx, ny = points[(index + 1) % len(points)]
        total += x * ny - nx * y
    return total / 2.0


def _mid(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    return ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)


def _line(image: BgrImage, start: tuple[int, int], end: tuple[int, int], color: tuple[int, int, int]) -> None:
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        if 0 <= x0 < image.width and 0 <= y0 < image.height:
            image.set(x0, y0, color)
        if x0 == x1 and y0 == y1:
            return
        doubled = 2 * err
        if doubled > -dy:
            err -= dy
            x0 += sx
        if doubled < dx:
            err += dx
            y0 += sy


_GLYPHS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
}


def _label(image: BgrImage, origin: tuple[int, int], glyph: str) -> None:
    rows = _GLYPHS[glyph]
    x0, y0 = origin
    for row, bits in enumerate(rows):
        for col, bit in enumerate(bits):
            if bit != "1":
                continue
            x = x0 + col - 2
            y = y0 + row - 3
            if 0 <= x < image.width and 0 <= y < image.height:
                image.set(x, y, (255, 255, 255))
