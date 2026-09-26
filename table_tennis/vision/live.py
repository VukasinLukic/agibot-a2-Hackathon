"""Send this process's vision commands to one match.

``TT_VISION_TOKEN`` is the vision actor. The body never names the actor.
Nothing here writes the score. Sound is optional: pass a callable that returns
one ``missed_return`` conclusion, or nothing, and the judge waits for it.

    python -m table_tennis.vision.live --match-id <uuid> --calibration table.json --device CHEST_LEFT_FISHEYE
    python -m table_tennis.vision.live --match-id <uuid> --calibration table.json --clip clip.ttclip
    python -m table_tennis.vision.live --match-id <uuid> --calibration table.json --clip snimak.mov --ballnet ballnet.onnx --show

A .mov/.mp4 plays at its own speed so the operator can press serve in time.
When the backend does not answer, the last good snapshot is used and the read
is retried after a pause; a proposal gets one retry with the same command_id.
On exit the camera is reported not ready.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Sequence

from table_tennis.contracts import MatchSnapshot
from table_tennis.vision.events import MatchVisionProducer, RallyJudge

_API = "/api/table-tennis"
# After a failed snapshot read, keep the last good one this long before asking again.
_RETRY_S = 1.0
_WAIT_LOG_S = 2.0


class BackendUnavailable(RuntimeError):
    """No HTTP answer at all: refused connection, timeout, or a dropped network."""


def fetch_snapshot(base_url: str, token: str, match_id: str) -> MatchSnapshot:
    status, body = _request("GET", _match_url(base_url, match_id), token)
    if status == 0:
        raise BackendUnavailable(str(body.get("error", "")) if isinstance(body, dict) else "")
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"snapshot request failed ({status})")
    return MatchSnapshot.model_validate(body)


def post_command(base_url: str, token: str, match_id: str, command: dict[str, Any]) -> dict[str, Any]:
    """POST one command. The returned ``status`` is the HTTP status, so 409 stays a conflict; 0 is no answer."""
    status, body = _request("POST", _match_url(base_url, match_id) + "/commands", token, command)
    if isinstance(body, dict):
        return {"status": status, "body": body}
    return {"status": status, "body": {}}


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
    initial: MatchSnapshot | None = None,
    now: Callable[[], float] = time.monotonic,
) -> MatchVisionProducer:
    """Read frames and post ``camera.ready.set`` and at most one proposal per rally."""
    producer = MatchVisionProducer(frames, tracker, judge, capture, sound)
    producer.run(
        command_sender(base_url, token, match_id),
        snapshot_reader(base_url, token, match_id, initial=initial, now=now),
    )
    return producer


def command_sender(base_url: str, token: str, match_id: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """POST a command; with no answer, one retry with the same bytes (same command_id)."""

    def sink(command: dict[str, Any]) -> dict[str, Any]:
        reply = post_command(base_url, token, match_id, command)
        if reply["status"] == 0:
            reply = post_command(base_url, token, match_id, command)
        _log_command(command, reply)
        return reply

    return sink


def snapshot_reader(
    base_url: str,
    token: str,
    match_id: str,
    *,
    initial: MatchSnapshot | None = None,
    now: Callable[[], float] = time.monotonic,
) -> Callable[[], MatchSnapshot]:
    """Snapshot per frame. With no answer, the last good one, and no new read for ``_RETRY_S``."""
    last = initial
    retry_at = 0.0
    down = False

    def context() -> MatchSnapshot:
        nonlocal last, retry_at, down
        if down and last is not None and now() < retry_at:
            return last
        try:
            snapshot = fetch_snapshot(base_url, token, match_id)
        except BackendUnavailable as exc:
            if last is None:
                raise
            if not down:
                _log(f"backend ne odgovara ({exc}); nastavljam sa poslednjim stanjem meca")
            down = True
            retry_at = now() + _RETRY_S
            return last
        if down:
            _log("backend ponovo odgovara")
        down = False
        last = snapshot
        return snapshot

    return context


def latest_match_id(base_url: str, token: str) -> str:
    """The newest match, the same one the app's "Pridruzi se" opens."""
    status, body = _request("GET", base_url.rstrip("/") + _API + "/matches", token)
    if status == 0:
        raise BackendUnavailable(str(body.get("error", "")) if isinstance(body, dict) else "")
    if status != 200 or not isinstance(body, list) or not body:
        raise SystemExit(f"nema meca na {base_url} ({status}); napravi ga u aplikaciji")
    return str(body[-1])


def wait_for_match(
    base_url: str,
    token: str,
    match_id: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> MatchSnapshot:
    """Block until the backend answers for this match. A wrong token or match id stops the process."""
    attempt = 0
    while True:
        try:
            return fetch_snapshot(base_url, token, match_id)
        except BackendUnavailable:
            if attempt % int(_WAIT_LOG_S / 0.5) == 0:
                _log(f"cekam backend na {base_url} ...")
        except RuntimeError as exc:
            raise SystemExit(f"mec {match_id} nije dostupan: {exc} (proveri --match-id, --token i --base-url)") from exc
        sleep(0.5)
        attempt += 1


def camera_not_ready(base_url: str, token: str, match_id: str, reason: str) -> None:
    """Best effort: the app must not keep showing a ready camera after this process stops."""
    from table_tennis.contracts import parse_command

    command = {
        "command_id": str(uuid.uuid4()),
        "expected_revision": None,
        "type": "camera.ready.set",
        "payload": {"ready": False, "reason": reason},
    }
    parse_command(command)
    reply = post_command(base_url, token, match_id, command)
    _log_command(command, reply)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--match-id", required=True, help="match uuid, or latest for the newest match")
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--token", default=os.environ.get("TT_VISION_TOKEN", ""))
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--ballnet", help="BallNet .onnx or .npz (overrides ballnet_path from the config)")
    parser.add_argument("--clip", type=Path, help="TTCLIP or a .mov/.mp4 recording; mutually exclusive with --device")
    parser.add_argument("--start", type=float, default=0.0, help="start a .mov/.mp4 this many seconds in")
    parser.add_argument("--device", help="raw chest fisheye alias, for example CHEST_LEFT_FISHEYE")
    parser.add_argument("--show", action="store_true", help="preview window: ball and table; space pauses, q stops")
    args = parser.parse_args(argv)
    if not args.token:
        raise SystemExit("set TT_VISION_TOKEN or pass --token")
    if (args.clip is None) == (args.device is None):
        parser.error("pass either --clip or --device")

    from table_tennis.vision.calibration import read_calibration
    from table_tennis.vision.config import load_config, load_example_config
    from table_tennis.vision.track import BallTracker

    calibration = read_calibration(args.calibration)
    config = load_config(args.config) if args.config else load_example_config()
    if args.ballnet:
        config = dataclasses.replace(config, ballnet_path=args.ballnet, model_path=None)
    tracker: Any = BallTracker(config, calibration)
    judge = RallyJudge(calibration)
    if args.show:
        from table_tennis.vision.preview import PreviewTracker

        tracker = PreviewTracker(tracker, calibration)

    if args.clip is not None and is_ttclip(args.clip):
        from table_tennis.vision.capture import FileCapture

        capture: Any = FileCapture(args.clip, config.camera_id)
    elif args.clip is not None:
        from table_tennis.vision.video import VideoFileCapture

        capture = VideoFileCapture(args.clip, config.camera_id, start_s=args.start)
    else:
        from table_tennis.vision.a2 import A2FisheyeCapture

        capture = A2FisheyeCapture(args.device)

    if args.match_id == "latest":
        try:
            args.match_id = latest_match_id(args.base_url, args.token)
        except BackendUnavailable as exc:
            raise SystemExit(f"backend ne odgovara na {args.base_url} ({exc})") from exc
    snapshot = wait_for_match(args.base_url, args.token, args.match_id)
    _explain(snapshot, calibration.calibration_id)
    reason = "vision stopped"
    try:
        with capture:
            run_match(
                args.match_id, args.base_url, args.token, iter(capture), tracker, judge, capture, initial=snapshot
            )
        reason = "camera_missing" if getattr(capture, "camera_missing", False) else "clip ended"
    except KeyboardInterrupt:
        _log("zaustavljeno")
    finally:
        camera_not_ready(args.base_url, args.token, args.match_id, reason)
        if args.show:
            tracker.close()
    return 0


def is_ttclip(path: Path) -> bool:
    from table_tennis.vision.capture import MAGIC

    with Path(path).open("rb") as handle:
        return handle.read(len(MAGIC)) == MAGIC


def _explain(snapshot: MatchSnapshot, calibration_id: str | None) -> None:
    """Say up front why no proposal would come, instead of staying silent."""
    _log(f"mec {snapshot.match_id}: {snapshot.status}, bodovanje {snapshot.scoring_mode}")
    if snapshot.scoring_mode != "assisted":
        _log("mec nije u assisted rezimu ('Kamera, uz potvrdu'): vizija nece predlagati poene")
    if snapshot.calibration_id != calibration_id:
        _log(f"u aplikaciji postavi calibration_id = {calibration_id} (sada je {snapshot.calibration_id})")


def _log_command(command: dict[str, Any], reply: dict[str, Any]) -> None:
    status = reply.get("status")
    text = f"{command.get('type')} -> {status or 'bez odgovora'}"
    if command.get("type") == "point.propose":
        text += f" (predlog: poen {command.get('payload', {}).get('winner_id')})"
        if status == 0:
            text += "; predlog nije stigao, dodeli poen rucno"
    elif status not in (200, 0) and isinstance(reply.get("body"), dict):
        text += f" {reply['body'].get('code', '')}"
    _log(text)


def _log(text: str) -> None:
    print(f"[vision] {text}", file=sys.stderr, flush=True)


def _match_url(base_url: str, match_id: str) -> str:
    return base_url.rstrip("/") + _API + "/matches/" + match_id


def _request(
    method: str, url: str, token: str, payload: dict[str, Any] | None = None
) -> tuple[int, object]:
    """HTTP status and JSON body. Status 0 means no answer (connection refused, timeout)."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, _json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _json(error.read())
    except OSError as error:
        return 0, {"error": str(getattr(error, "reason", error))}


def _json(raw: bytes) -> object:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
