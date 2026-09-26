"""Send this process's vision commands to one match.

``TT_VISION_TOKEN`` is the vision actor. The body never names the actor.
Nothing here writes the score. Sound is optional: pass a callable that returns
one ``missed_return`` conclusion, or nothing, and the judge waits for it.

    python -m table_tennis.vision.live --match-id <uuid> --calibration table.json --clip clip.ttclip
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Sequence

from table_tennis.contracts import MatchSnapshot
from table_tennis.vision.events import MatchVisionProducer, RallyJudge

_API = "/api/table-tennis"


def fetch_snapshot(base_url: str, token: str, match_id: str) -> MatchSnapshot:
    status, body = _request("GET", _match_url(base_url, match_id), token)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"snapshot request failed ({status})")
    return MatchSnapshot.model_validate(body)


def post_command(base_url: str, token: str, match_id: str, command: dict[str, Any]) -> dict[str, Any]:
    """POST one command. The returned ``status`` is the HTTP status, so 409 stays a conflict."""
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
) -> MatchVisionProducer:
    """Read frames and post ``camera.ready.set`` and at most one proposal."""
    producer = MatchVisionProducer(frames, tracker, judge, capture, sound)

    def sink(command: dict[str, Any]) -> dict[str, Any]:
        return post_command(base_url, token, match_id, command)

    def context() -> MatchSnapshot:
        return fetch_snapshot(base_url, token, match_id)

    producer.run(sink, context)
    return producer


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--token", default=os.environ.get("TT_VISION_TOKEN", ""))
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--clip", type=Path, help="local TTCLIP; mutually exclusive with --device")
    parser.add_argument("--device", help="raw chest fisheye alias, for example CHEST_LEFT_FISHEYE")
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
    tracker = BallTracker(config, calibration)
    judge = RallyJudge(calibration)
    if args.clip is not None:
        from table_tennis.vision.capture import FileCapture

        capture: Any = FileCapture(args.clip, config.camera_id)
    else:
        from table_tennis.vision.a2 import A2FisheyeCapture

        capture = A2FisheyeCapture(args.device)
    with capture:
        run_match(args.match_id, args.base_url, args.token, iter(capture), tracker, judge, capture)
    return 0


def _match_url(base_url: str, match_id: str) -> str:
    return base_url.rstrip("/") + _API + "/matches/" + match_id


def _request(
    method: str, url: str, token: str, payload: dict[str, Any] | None = None
) -> tuple[int, object]:
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


def _json(raw: bytes) -> object:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
