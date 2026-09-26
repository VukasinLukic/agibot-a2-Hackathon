"""Offline prolaz kroz mp4/mov: BallTracker, CSV, opciono overlay i vreme po koraku.

    python -m table_tennis.vision.run_video snimak.mov --ballnet C:/tezine/ballnet.onnx

CSV (``write_track_csv``) ide u ``table_tennis/var/vision/`` ako se ne zada
``--csv``; taj folder je van gita. Snimci i težine se ne commituju. Ovo nije
živi put: vreme kadra je ``CAP_PROP_POS_MSEC`` iz fajla.
"""

from __future__ import annotations

import argparse
import dataclasses
import time
from pathlib import Path
from typing import Any, Sequence

from table_tennis.vision.config import load_config, load_example_config
from table_tennis.vision.frame import ORIGIN_FILE, Frame
from table_tennis.vision.rally_events import RallyEvent, RallyEventDetector
from table_tennis.vision.track import BallTracker, TrackSample, write_track_csv

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "var" / "vision"
_COLORS = {"observed": (255, 0, 255), "predicted": (0, 220, 255)}
_EVENT_COLORS = {"bounce": (0, 140, 255), "hit": (255, 200, 0)}
_TRAIL_FRAMES = 15
_EVENT_FRAMES = 20


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("--config", type=Path, help="vision yaml; default config.example.yaml")
    parser.add_argument("--ballnet", help="BallNet .onnx or .npz (overrides ballnet_path)")
    parser.add_argument("--work-width", type=int, help="overrides ballnet.work_width_px")
    parser.add_argument("--no-compensate", action="store_true", help="skip camera-motion compensation")
    parser.add_argument("--csv", type=Path, help="default: table_tennis/var/vision/<video>.track.csv")
    parser.add_argument("--overlay", type=Path, help="write an mp4 with the ball marked")
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args(argv)

    import cv2
    import numpy as np

    config = load_config(args.config) if args.config else load_example_config()
    changes: dict[str, object] = {"origin": ORIGIN_FILE}
    if args.ballnet:
        changes.update(ballnet_path=args.ballnet, model_path=None)
    if args.work_width:
        changes["work_width_px"] = args.work_width
    if args.no_compensate:
        changes["compensate_motion"] = False
    config = dataclasses.replace(config, **changes)
    tracker = BallTracker(config)

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    writer = None
    overlay: _Overlay | None = None
    csv_path = args.csv or DEFAULT_OUT / f"{args.video.stem}.track.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    samples: list[TrackSample] = []
    stages: dict[str, list[float]] = {}
    totals: list[float] = []
    decode: list[float] = []
    candidates: list[int] = []
    last_ns = -1
    index = 0
    try:
        while not args.max_frames or index < args.max_frames:
            started = time.perf_counter()
            ok, image = capture.read()
            if not ok:
                break
            decode.append((time.perf_counter() - started) * 1000.0)
            position_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
            stamp = int(position_ms * 1_000_000) if position_ms > 0 else int(index * 1e9 / fps)
            stamp = max(stamp, last_ns + 1)
            last_ns = stamp
            height, width = image.shape[:2]
            frame = Frame(index, stamp, width, height, image, config.camera_id, ORIGIN_FILE)
            started = time.perf_counter()
            sample = tracker.update(frame)
            totals.append((time.perf_counter() - started) * 1000.0)
            samples.append(sample)
            pipeline = tracker.pipeline
            if pipeline is not None:
                for name, value in pipeline.last_ms.items():
                    stages.setdefault(name, []).append(value)
                candidates.append(pipeline.last_candidates)
            if args.overlay is not None:
                if writer is None:
                    args.overlay.parent.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(str(args.overlay), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
                    overlay = _Overlay(width)
                writer.write(overlay.draw(cv2, image, sample, totals[-1]))
            index += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()

    write_track_csv(csv_path, samples)
    kinds = {kind: sum(1 for s in samples if s.observation_kind == kind) for kind in ("observed", "predicted", "missing")}
    print(f"frames {len(samples)}  {kinds}")
    print(f"csv {csv_path}" + (f"  overlay {args.overlay}" if args.overlay else ""))
    if not totals:
        return 0
    print(f"{'stage':<11}{'mean ms':>9}{'p95 ms':>9}")
    for name, values in [*stages.items(), ("total", totals), ("decode", decode)]:
        array = np.asarray(values)
        print(f"{name:<11}{array.mean():9.2f}{np.percentile(array, 95):9.2f}")
    if candidates:
        print(f"candidates/frame mean {np.mean(candidates):.1f}  max {max(candidates)}")
    print(f"tracker fps {1000.0 / float(np.mean(totals)):.1f}")
    return 0


class _Overlay:
    """Ball ring, a short trail, bounce/hit marks and a status line. Sizes follow the frame width."""

    def __init__(self, width: int) -> None:
        self._unit = max(1.0, width / 960.0)
        self._events = RallyEventDetector(width)
        self._trail: list[tuple[int, int, int]] = []
        self._shown: list[tuple[int, RallyEvent]] = []
        self._counts = {"bounce": 0, "hit": 0}

    def draw(self, cv2: Any, image: Any, sample: TrackSample, ms: float) -> Any:
        marked = image.copy()
        unit = self._unit
        seq = sample.frame_seq
        for event in self._events.add(sample):
            self._shown.append((seq, event))
            self._counts[event.kind] += 1
        if sample.observation_kind == "observed" and sample.x_px is not None and sample.y_px is not None:
            self._trail.append((seq, int(round(sample.x_px)), int(round(sample.y_px))))
        self._trail = [point for point in self._trail if seq - point[0] <= _TRAIL_FRAMES]
        self._shown = [item for item in self._shown if seq - item[0] <= _EVENT_FRAMES]

        for (_, x0, y0), (age_seq, x1, y1) in zip(self._trail, self._trail[1:]):
            fade = 1.0 - (seq - age_seq) / (_TRAIL_FRAMES + 1)
            cv2.line(marked, (x0, y0), (x1, y1), (255, 0, 255), max(1, int(round(4 * unit * fade))), cv2.LINE_AA)
        for shown_seq, event in self._shown:
            color = _EVENT_COLORS[event.kind]
            center = (int(round(event.x_px)), int(round(event.y_px)))
            grow = int(round((14 + 2 * (seq - shown_seq)) * unit))
            cv2.circle(marked, center, grow, color, max(2, int(round(3 * unit))), cv2.LINE_AA)
            label = "ODSKOK" if event.kind == "bounce" else "UDARAC"
            _text(cv2, marked, label, (center[0] + grow + 4, center[1] + int(8 * unit)), 0.9 * unit, color)

        color = _COLORS.get(sample.observation_kind)
        if color is not None and sample.x_px is not None and sample.y_px is not None:
            center = (int(round(sample.x_px)), int(round(sample.y_px)))
            radius = int(round(26 * unit))
            if sample.observation_kind == "observed":
                cv2.circle(marked, center, radius, color, max(2, int(round(4 * unit))), cv2.LINE_AA)
                _text(cv2, marked, f"{sample.confidence:.2f}", (center[0] + radius + 4, center[1] - radius), 0.7 * unit, color)
            else:
                for angle in range(0, 360, 45):
                    cv2.ellipse(marked, center, (radius, radius), 0, angle, angle + 25, color, max(2, int(round(3 * unit))), cv2.LINE_AA)

        status = {"observed": "LOPTICA", "predicted": "PREDVIDJENO", "missing": "-"}[sample.observation_kind]
        line = f"kadar {seq}   {status}   odskoka {self._counts['bounce']}   udaraca {self._counts['hit']}   {ms:.0f} ms"
        _text(cv2, marked, line, (int(14 * unit), int(34 * unit)), 0.8 * unit, (255, 255, 255))
        return marked


def _text(cv2: Any, image: Any, text: str, origin: tuple[int, int], scale: float, color: tuple[int, int, int]) -> None:
    thickness = max(1, int(round(2 * scale)))
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 3, cv2.LINE_AA)
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


if __name__ == "__main__":
    raise SystemExit(main())
