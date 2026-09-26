"""Shared OpenCV debug overlays for local vision diagnostics."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    from .card_capture import STABILITY_FRAMES
except ImportError:  # pragma: no cover - direct script import fallback
    from card_capture import STABILITY_FRAMES  # type: ignore


def draw_card_capture_preview(
    frame: Any,
    candidate: dict[str, Any] | None,
    processor: Any,
    captured_count: int,
    debug_info: dict[str, Any] | None,
) -> Any:
    preview = frame.copy()
    if debug_info and debug_info["rejected"]:
        rejected = debug_info["rejected"][0]
        points = rejected.get("points")
        if points is not None:
            points = points.astype(int)
            cv2.polylines(preview, [points], True, (0, 165, 255), 2)

    if candidate is not None:
        points = candidate["points"].astype(int)
        cv2.polylines(preview, [points], True, (0, 255, 0), 2)
        for point in points:
            cv2.circle(preview, tuple(point), 4, (0, 255, 255), -1)

    lines = [
        card_capture_status_line(candidate, processor, debug_info),
        f"captures={captured_count} required_stable={STABILITY_FRAMES}",
        "green=accepted orange=best rejected",
    ]
    draw_text_box(preview, lines)
    return preview


def draw_portrait_debug_preview(frame: Any, debug_info: dict[str, Any]) -> Any:
    preview = frame.copy()
    roi_points = debug_info.get("roi_points")
    if roi_points is not None:
        cv2.polylines(preview, [roi_points.astype(int)], True, (255, 255, 0), 2)

    for line in debug_info.get("lines") or []:
        x1, y1, x2, y2 = line.get("frame", (0, 0, 0, 0))
        color = (190, 190, 190) if line.get("orientation") == "horizontal" else (140, 140, 140)
        cv2.line(preview, (int(x1), int(y1)), (int(x2), int(y2)), color, 1, cv2.LINE_AA)

    portrait_bbox = (debug_info.get("portrait") or {}).get("frame_bbox")
    for face in debug_info.get("faces") or []:
        x, y, width, height = face.get("frame_bbox", (0, 0, 0, 0))
        if face.get("frame_bbox") == portrait_bbox:
            color = (0, 255, 255)
            thickness = 3
        elif face.get("accepted"):
            color = (255, 0, 0)
            thickness = 2
        else:
            color = (0, 0, 255)
            thickness = 2
        cv2.rectangle(preview, (int(x), int(y)), (int(x + width), int(y + height)), color, thickness)

    for index, candidate in enumerate(debug_info.get("candidates") or []):
        points = candidate.get("points")
        if points is None:
            continue
        points = points.astype(int)
        color = (0, 255, 0) if candidate.get("accepted") else (0, 165, 255)
        thickness = 3 if index == 0 else 2
        cv2.polylines(preview, [points], True, color, thickness)
        label_anchor = tuple(points[np.argmin(points.sum(axis=1))])
        label = (
            f"#{index + 1} "
            f"a={candidate.get('area_ratio', 0.0):.2f} "
            f"r={candidate.get('aspect_ratio', 0.0):.2f} "
            f"l={candidate.get('line_coverage', 0.0):.2f}"
        )
        cv2.putText(
            preview,
            label,
            (int(label_anchor[0]), max(18, int(label_anchor[1]) - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            color,
            1,
            cv2.LINE_AA,
        )

    lines = [
        portrait_debug_status_line(debug_info),
        "yellow=selected portrait blue=small face red=rejected face gray=edge lines",
        "green=accepted card candidate orange=rejected",
    ]
    draw_text_box(preview, lines)
    return preview


def portrait_debug_status_line(debug_info: dict[str, Any]) -> str:
    candidates = debug_info.get("candidates") or []
    accepted_count = sum(1 for candidate in candidates if candidate.get("accepted"))
    portrait = debug_info.get("portrait")
    portrait_area = portrait.get("area_ratio", 0.0) if isinstance(portrait, dict) else 0.0
    if not candidates:
        return (
            f"portrait_debug faces={debug_info.get('face_count', 0)} "
            f"portrait_area={portrait_area:.3f} lines={debug_info.get('line_count', 0)} "
            f"candidates=0 reason={debug_info.get('reason', 'n/a')}"
        )

    best = candidates[0]
    return (
        f"portrait_debug faces={debug_info.get('face_count', 0)} "
        f"portrait_area={portrait_area:.3f} lines={debug_info.get('line_count', 0)} "
        f"candidates={len(candidates)} accepted={accepted_count} "
        f"best_ok={str(bool(best.get('accepted'))).lower()} "
        f"area={best.get('area_ratio', 0.0):.3f} "
        f"aspect={best.get('aspect_ratio', 0.0):.2f} "
        f"line={best.get('line_coverage', 0.0):.2f} "
        f"reason={best.get('reason', 'n/a')}"
    )


def card_capture_status_line(
    candidate: dict[str, Any] | None,
    processor: Any,
    debug_info: dict[str, Any] | None = None,
) -> str:
    if candidate is None:
        if debug_info and debug_info["rejected"]:
            rejected = debug_info["rejected"][0]
            return (
                f"candidate=none stable={processor.stable_count}/{STABILITY_FRAMES} "
                f"lines={debug_info.get('line_count', 0)} faces={debug_info.get('face_count', 0)} "
                f"rejected={rejected.get('reason')} "
                f"area={format_optional_float(rejected.get('area_ratio'), 3)} "
                f"aspect={format_optional_float(rejected.get('aspect_ratio'), 2)}"
            )
        if debug_info:
            return (
                f"candidate=none stable={processor.stable_count}/{STABILITY_FRAMES} "
                f"lines={debug_info.get('line_count', 0)} faces={debug_info.get('face_count', 0)} rejected=none"
            )
        return f"candidate=none stable={processor.stable_count}/{STABILITY_FRAMES}"
    quality = candidate["quality"]
    return (
        f"candidate=ok stable={processor.stable_count}/{STABILITY_FRAMES} "
        f"source={candidate.get('source', 'n/a')} "
        f"score={quality['score']:.2f} blur={quality['blur']:.0f} "
        f"area={quality['area_ratio']:.2f} aspect={quality['aspect_ratio']:.2f} "
        f"line={quality.get('line_coverage', 0.0):.2f} "
        f"portrait={quality.get('portrait_area_ratio', 0.0):.3f} "
        f"frame_area={quality.get('portrait_frame_area_ratio', 0.0):.2f} "
        f"glare={quality['glare_ratio']:.2f}"
    )


def format_optional_float(value: Any, digits: int) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (float, int)) else "n/a"


def draw_text_box(image: Any, lines: list[str]) -> None:
    line_height = 22
    width = max(420, min(image.shape[1] - 20, 920))
    height = 12 + line_height * len(lines)
    overlay = image.copy()
    cv2.rectangle(overlay, (10, 10), (10 + width, 10 + height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, image, 0.45, 0, image)
    for index, line in enumerate(lines):
        y = 33 + line_height * index
        cv2.putText(
            image,
            line,
            (20, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )


def save_portrait_debug_snapshot(frame: Any, debug_info: dict[str, Any], save_dir: Path) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    stem = f"portrait-debug-{timestamp}"
    cv2.imwrite(str(save_dir / f"{stem}-frame.jpg"), frame)

    roi = debug_info.get("roi")
    if isinstance(roi, np.ndarray) and roi.size:
        cv2.imwrite(str(save_dir / f"{stem}-roi.jpg"), roi)

    metadata_path = save_dir / f"{stem}.json"
    metadata_path.write_text(json.dumps(json_safe_debug_info(debug_info), indent=2, sort_keys=True))
    return metadata_path


def json_safe_debug_info(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, float)):
        return round(float(value), 4)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"roi", "mask"}:
                continue
            result[key] = json_safe_debug_info(item)
        return result
    if isinstance(value, (list, tuple)):
        return [json_safe_debug_info(item) for item in value]
    return value
