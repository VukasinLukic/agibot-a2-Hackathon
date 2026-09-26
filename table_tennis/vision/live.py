"""Send this process's vision commands to one match.

``TT_VISION_TOKEN`` is the vision actor. The body never names the actor.
Nothing here writes the score. Sound stays off: the judge is not given a
microphone, so a missing conclusion cannot block a proposal.

    python -m table_tennis.vision.live --match-id latest --calibration table.json --device CHEST_LEFT_FISHEYE
    python -m table_tennis.vision.live --grab still.jpg --device CHEST_LEFT_FISHEYE
    python -m table_tennis.vision.live --match-id <uuid> --calibration table.json --clip clip.ttclip --dry-run
    python -m table_tennis.vision.live --match-id latest --calibration table.json --clip snimak.mov --ballnet ballnet.onnx --show

A .mov/.mp4 plays at its own speed so the operator can press serve in time.
``--show`` opens a preview window (space pauses, q stops); the robot runs without it.

A dead backend does not kill the process. The last snapshot is kept, a proposal
is posted once more with the same ``command_id``, and exit sends
``camera.ready.set false``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Sequence

from table_tennis.contracts import MatchSnapshot
from table_tennis.vision.events import MatchVisionProducer, RallyJudge

_API = "/api/table-tennis"
_LOG = logging.getLogger(__name__)
_GET_EVERY_S = 0.25
_STREAM_SILENT_S = 20.0


class BackendUnavailable(Exception):
    """The match API could not be reached. The process keeps the last snapshot."""


def fetch_snapshot(base_url: str, token: str, match_id: str) -> MatchSnapshot:
    status, body = _request("GET", _match_url(base_url, match_id), token)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"snapshot request failed ({status})")
    return MatchSnapshot.model_validate(body)


def latest_match_id(base_url: str, token: str) -> str:
    """The last match in creation order. ``GET /matches`` is oldest first."""
    status, body = _request("GET", base_url.rstrip("/") + _API + "/matches", token)
    if status != 200 or not isinstance(body, list) or not body:
        raise RuntimeError(f"match list failed ({status})")
    return str(body[-1])


def post_command(base_url: str, token: str, match_id: str, command: dict[str, Any]) -> dict[str, Any]:
    """POST one command. The returned ``status`` is the HTTP status, so 409 stays a conflict."""
    status, body = _request("POST", _match_url(base_url, match_id) + "/commands", token, command)
    if isinstance(body, dict):
        return {"status": status, "body": body}
    return {"status": status, "body": {}}


def deliver_command(send: Callable[[dict[str, Any]], dict[str, Any]], command: dict[str, Any]) -> dict[str, Any]:
    """Post a proposal twice at most, both times with the same ``command_id``. Other commands are tried once."""
    try:
        reply = send(command)
    except BackendUnavailable:
        if command.get("type") != "point.propose":
            _LOG.warning("command %s was not delivered", command.get("type"))
            return {"status": 0, "body": {}}
        try:
            reply = send(command)
        except BackendUnavailable:
            _LOG.warning("proposal %s was not delivered", command.get("command_id"))
            return {"status": 0, "body": {}}
        _log_reply(command, reply)
        return reply
    _log_reply(command, reply)
    return reply


def _log_reply(command: dict[str, Any], reply: dict[str, Any]) -> None:
    status = reply.get("status")
    if status in (200, 409):
        return
    _LOG.warning("command %s rejected (%s): %s", command.get("type"), status, reply.get("body"))


def run_match(
    match_id: str,
    base_url: str,
    token: str,
    frames: Iterator[Any],
    tracker: Any,
    judge: RallyJudge,
    capture: Any = None,
    sound: Callable[[], dict[str, Any] | None] | None = None,
    *,
    dry_run: bool = False,
    on_frame: Callable[[Any, Any], None] | None = None,
    follow_latest: bool = False,
) -> MatchVisionProducer:
    """Read frames and post ``camera.ready.set`` and at most one proposal."""
    producer = MatchVisionProducer(frames, tracker, judge, capture, sound)
    held: dict[str, Any] = {"snapshot": None, "feed": _SnapshotFeed(base_url, token, match_id), "match": match_id, "checked": time.monotonic()}

    def sink(command: dict[str, Any]) -> dict[str, Any]:
        if dry_run:
            _LOG.info("dry-run would send %s", command.get("type"))
            return {"status": 200, "body": {}}
        return deliver_command(lambda body: post_command(base_url, token, held["match"], body), command)

    def context() -> MatchSnapshot | None:
        _follow(held, base_url, token, follow_latest)
        feed = held["feed"]
        current = feed.latest
        if current is not None and feed.alive():
            held["snapshot"] = current
            return _for_judge(current, dry_run)
        last = held["snapshot"]
        now = time.monotonic()
        if now - held.get("fetched", 0.0) < _GET_EVERY_S:
            return _for_judge(last, dry_run) if last is not None else None
        held["fetched"] = now
        try:
            snapshot = fetch_snapshot(base_url, token, held["match"])
        except (BackendUnavailable, RuntimeError) as error:
            _LOG.warning("snapshot unavailable (%s); keeping the last one", error)
            return _for_judge(last, dry_run) if last is not None else None
        held["snapshot"] = snapshot
        return _for_judge(snapshot, dry_run)

    try:
        producer.run(sink, context, on_frame)
    finally:
        held["feed"].close()
    return producer


def _follow(held: dict[str, Any], base_url: str, token: str, follow_latest: bool) -> None:
    if not follow_latest or time.monotonic() - held["checked"] < 5.0:
        return
    held["checked"] = time.monotonic()
    try:
        match_id = latest_match_id(base_url, token)
    except (BackendUnavailable, RuntimeError):
        return
    if match_id == held["match"]:
        return
    current = held["snapshot"]
    if current is not None and current.status in ("rally", "pending_decision"):
        return
    _LOG.info("latest match is now %s", match_id)
    held["feed"].close()
    held["match"] = match_id
    held["feed"] = _SnapshotFeed(base_url, token, match_id)
    held["snapshot"] = None


def _for_judge(snapshot: MatchSnapshot, dry_run: bool) -> MatchSnapshot:
    """Dry-run never posts ready, so the judge must not wait for the backend to say the camera is up."""
    if not dry_run:
        return snapshot
    return snapshot.model_copy(update={"ready": snapshot.ready.model_copy(update={"camera_ready": True})})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--match-id", help="match uuid, or 'latest'")
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--token", default=os.environ.get("TT_VISION_TOKEN", ""))
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--clip", type=Path, help="TTCLIP or a .mov/.mp4 recording; mutually exclusive with --device")
    parser.add_argument("--start", type=float, default=0.0, help="start a .mov/.mp4 this many seconds in")
    parser.add_argument("--ballnet", help="BallNet .onnx or .npz (overrides ballnet_path from the config)")
    parser.add_argument("--show", action="store_true", help="preview window: ball and table; space pauses, q stops")
    parser.add_argument("--device", help="raw chest fisheye alias, for example CHEST_LEFT_FISHEYE")
    parser.add_argument("--dry-run", action="store_true", help="log commands instead of posting them")
    parser.add_argument("--record", nargs="?", const="auto", help="write a TTCLIP under table_tennis/var")
    parser.add_argument("--grab", type=Path, help="write the first frame as JPEG and exit")
    args = parser.parse_args(argv)
    if (args.clip is None) == (args.device is None):
        parser.error("pass either --clip or --device")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _limit_vision_threads()
    _yield_a_core()

    from table_tennis.vision.config import load_config, load_example_config

    config = load_config(args.config) if args.config else load_example_config()
    if args.ballnet:
        config = dataclasses.replace(config, ballnet_path=args.ballnet, model_path=None)
    capture = _open_capture(args, config.camera_id)
    with capture:
        if args.grab is not None:
            frame = next(iter(capture), None)
            if frame is None:
                raise SystemExit("camera produced no frame")
            _write_jpeg(args.grab, frame)
            _LOG.info("wrote %s", args.grab)
            return 0
        if not args.token:
            raise SystemExit("set TT_VISION_TOKEN or pass --token")
        if args.match_id is None or args.calibration is None:
            parser.error("--match-id and --calibration are required")
        from table_tennis.vision.calibration import read_calibration
        from table_tennis.vision.track import BallTracker

        match_id = args.match_id
        if match_id == "latest":
            match_id = latest_match_id(args.base_url, args.token)
            _LOG.info("latest match is %s", match_id)
        calibration = read_calibration(args.calibration)
        tracker = BallTracker(config, calibration)
        judge = RallyJudge(calibration)
        record = _ClipSink(args.record) if args.record else None
        pace = _Pace(capture, tracker)
        window = None
        if args.show:
            from table_tennis.vision.preview import PreviewWindow

            window = PreviewWindow(calibration)
        try:
            run_match(
                match_id,
                args.base_url,
                args.token,
                iter(capture),
                tracker,
                judge,
                capture,
                dry_run=args.dry_run,
                follow_latest=args.match_id == "latest",
                on_frame=_observe(pace, record, calibration, window),
            )
        except KeyboardInterrupt:
            _LOG.info("stopped")
        finally:
            if record is not None:
                record.close()
            if window is not None:
                window.close()
    return 0


def _observe(pace: _Pace, record: _ClipSink | None, calibration: Any, window: Any = None) -> Callable[[Any, Any], None]:
    checked = False

    def on_frame(frame: Any, sample: Any) -> None:
        nonlocal checked
        if not checked:
            checked = True
            if frame.width != calibration.width or frame.height != calibration.height:
                _LOG.warning(
                    "calibration is %sx%s but the camera frame is %sx%s",
                    calibration.width,
                    calibration.height,
                    frame.width,
                    frame.height,
                )
        if record is not None:
            record.write(frame)
        if window is not None:
            window.show(frame, sample)
        pace.note(sample)

    return on_frame


def _open_capture(args: argparse.Namespace, camera_id: str) -> Any:
    if args.clip is not None and is_ttclip(args.clip):
        from table_tennis.vision.capture import FileCapture

        return FileCapture(args.clip, camera_id)
    if args.clip is not None:
        from table_tennis.vision.video import VideoFileCapture

        return VideoFileCapture(args.clip, camera_id, start_s=args.start)
    from table_tennis.vision.a2 import A2FisheyeCapture

    return A2FisheyeCapture(args.device)


def is_ttclip(path: Path) -> bool:
    from table_tennis.vision.capture import MAGIC

    with Path(path).open("rb") as handle:
        return handle.read(len(MAGIC)) == MAGIC


class _SnapshotFeed:
    """Latest match snapshot from the existing SSE stream. ``read`` stays a GET until the first event."""

    def __init__(self, base_url: str, token: str, match_id: str) -> None:
        self.latest: MatchSnapshot | None = None
        self._seen = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(base_url, token, match_id),
            name="vision-snapshot",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        """The server sends a heartbeat every 15 s, so silence past that means the stream stalled."""
        return time.monotonic() - self._seen < _STREAM_SILENT_S

    def _run(self, base_url: str, token: str, match_id: str) -> None:
        url = _match_url(base_url, match_id) + "/events"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream", "X-TT-Actor": "vision"},
        )
        while not self._stop.is_set():
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    self._read(response)
            except (BackendUnavailable, urllib.error.URLError, TimeoutError, OSError) as error:
                _LOG.warning("snapshot stream dropped (%s)", error)
                if self._stop.wait(1.0):
                    return

    def _read(self, response: Any) -> None:
        event = ""
        data: list[str] = []
        while not self._stop.is_set():
            raw = response.readline()
            if not raw:
                return
            self._seen = time.monotonic()
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                self._take(event, "\n".join(data))
                event = ""
                data = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data.append(line.split(":", 1)[1].lstrip())

    def _take(self, event: str, data: str) -> None:
        if event != "snapshot" or not data:
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
        if isinstance(snapshot, dict):
            self.latest = MatchSnapshot.model_validate(snapshot)


class _ClipSink:
    """Writes frames on a side thread. A full queue drops the frame instead of stalling the camera."""

    def __init__(self, target: str) -> None:
        self._target = target
        self._queue: queue.Queue[tuple[int, Any] | None] = queue.Queue(maxsize=8)
        self._thread = threading.Thread(target=self._loop, name="vision-record", daemon=True)
        self._thread.start()

    def write(self, frame: Any) -> None:
        try:
            self._queue.put_nowait((frame.capture_monotonic_ns, _as_bgr(frame)))
        except queue.Full:
            return

    def close(self) -> None:
        while self._thread.is_alive():
            try:
                self._queue.put(None, timeout=0.5)
                break
            except queue.Full:
                continue
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        from table_tennis.vision.capture import ClipWriter

        first = self._queue.get()
        if first is None:
            return
        second = self._queue.get()
        period = 33_333_333
        if second is not None and second[0] > first[0]:
            period = second[0] - first[0]
        writer: Any = None
        try:
            path = _record_path(self._target)
            path.parent.mkdir(parents=True, exist_ok=True)
            writer = ClipWriter(path, first[1].width, first[1].height, period)
            writer.__enter__()
            _LOG.info("recording %s", path)
            writer.write_frame(first[1])
            item = second
            while item is not None:
                writer.write_frame(item[1])
                item = self._queue.get()
        except (OSError, ValueError) as error:
            _LOG.warning("recording stopped: %s", error)
        finally:
            if writer is not None:
                writer.__exit__(None, None, None)


class _Pace:
    def __init__(self, capture: Any, tracker: Any) -> None:
        self._capture = capture
        self._tracker = tracker
        self._last = time.monotonic()
        self._frames = 0
        self._totals: list[float] = []
        self._emitted = int(getattr(getattr(capture, "stats", None), "frames_emitted", 0))
        self._dupes = int(getattr(getattr(capture, "stats", None), "duplicate_frames", 0))

    def note(self, _sample: Any) -> None:
        self._frames += 1
        pipeline = getattr(self._tracker, "pipeline", None)
        timings = getattr(pipeline, "last_ms", None)
        if isinstance(timings, dict) and timings:
            self._totals.append(sum(float(value) for value in timings.values()))
            del self._totals[:-300]
        now = time.monotonic()
        if now - self._last < 5.0:
            return
        elapsed = now - self._last
        emitted = int(getattr(getattr(self._capture, "stats", None), "frames_emitted", self._frames))
        dupes = int(getattr(getattr(self._capture, "stats", None), "duplicate_frames", 0))
        candidates = int(getattr(pipeline, "last_candidates", 0))
        _LOG.info(
            "fps camera %.1f process %.1f p50 %.1fms p95 %.1fms candidates %s duplicates %s",
            (emitted - self._emitted) / elapsed,
            self._frames / elapsed,
            _percentile(self._totals, 50),
            _percentile(self._totals, 95),
            candidates,
            dupes - self._dupes,
        )
        self._last = now
        self._frames = 0
        self._emitted = emitted
        self._dupes = dupes


def _percentile(values: list[float], percent: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((percent / 100) * (len(ordered) - 1)))
    return ordered[index]


def _record_path(target: str) -> Path:
    if target != "auto":
        return Path(target)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(__file__).resolve().parents[1] / "var" / f"vision-{stamp}.ttclip"


def _as_bgr(frame: Any) -> Any:
    from table_tennis.vision.image import BgrImage

    image = frame.image
    if isinstance(image, BgrImage):
        return image
    import numpy as np

    array = np.ascontiguousarray(image)
    return BgrImage(frame.width, frame.height, array.tobytes())


def _write_jpeg(path: Path, frame: Any) -> None:
    import cv2
    import numpy as np

    from table_tennis.vision.image import BgrImage

    image = frame.image
    if isinstance(image, BgrImage):
        array = np.frombuffer(image.data, dtype=np.uint8).reshape(image.height, image.width, 3)
    else:
        array = np.asarray(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), array):
        raise RuntimeError(f"could not write {path}")


def _limit_vision_threads() -> None:
    """Leave cores for the Supervisor. Two OpenCV threads is enough for this net."""
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    try:
        import cv2

        cv2.setNumThreads(2)
    except ImportError:
        return


def _yield_a_core() -> None:
    """Ask the OS to prefer other processes. ``taskset`` still belongs to the launch command."""
    nice = getattr(os, "nice", None)
    if nice is None:
        return
    try:
        nice(5)
    except OSError:
        return


def _match_url(base_url: str, match_id: str) -> str:
    return base_url.rstrip("/") + _API + "/matches/" + match_id


def _request(
    method: str, url: str, token: str, payload: dict[str, Any] | None = None
) -> tuple[int, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "X-TT-Actor": "vision"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, _json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _json(error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise BackendUnavailable(str(error)) from error


def _json(raw: bytes) -> object:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
