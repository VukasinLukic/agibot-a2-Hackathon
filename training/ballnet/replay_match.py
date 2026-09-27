"""Replay a robot TTCLIP through the whole chain against a local mock backend.

Laptop only, no robot. Start the backend first:

    python -m table_tennis.run_demo --mode mock --port 8099 --db table_tennis/var/replay.sqlite

then:

    python training/ballnet/replay_match.py CLIP.ttclip table.json --ballnet models/ballnet/ballnet_robot.onnx

Creates an assisted match with the clip's calibration, marks a manual arrival,
arms a rally and runs ``table_tennis.vision.live --clip`` exactly as on the
robot. A stand-in operator re-arms after every decision and confirms each
proposal (the clip has no ground truth for who won). Prints what vision did.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def call(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + "/api/table-tennis" + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-TT-Actor": "operator",
                                          "Authorization": "Bearer operator_secret"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read() or b"{}")


def command(base: str, match: str, kind: str, payload: dict | None = None, revision: int | None = None) -> dict:
    body = {"schema_version": "1.0", "command_id": str(uuid.uuid4()), "type": kind, "payload": payload or {}}
    body["expected_revision"] = revision if revision is not None else call(base, "GET", f"/matches/{match}")["revision"]
    return call(base, "POST", f"/matches/{match}/commands", body)["snapshot"]


def operator(base: str, match: str, stop: threading.Event, log: list) -> None:
    """Re-arm after each decision; confirm a proposal as soon as it shows up."""
    while not stop.is_set():
        try:
            snap = call(base, "GET", f"/matches/{match}")
            proposal = snap.get("active_proposal")
            if proposal:
                log.append((time.monotonic(), "predlog", proposal.get("winner_id"), proposal.get("reason")))
                command(base, match, "point.confirm", {"proposal_id": proposal["proposal_id"]}, snap["revision"])
            elif snap["status"] == "between_rallies":
                command(base, match, "rally.arm", {}, snap["revision"])
        except Exception as error:  # noqa: BLE001 - keep replaying, report at the end
            log.append((time.monotonic(), "greska", str(error)[:80], None))
        time.sleep(0.2)


def realtime_clip(path: Path) -> Path:
    """TTCLIP replays as fast as it reads, far ahead of the backend; a lossless AVI plays at 30 fps."""
    if path.read_bytes()[:8] != b"TTCLIP01":
        return path
    out = path.with_suffix(".avi")
    if not out.exists():
        import cv2

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from robot_clip import read_clip

        frames = read_clip(str(path))
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"FFV1"), 30.0, (frames.shape[2], frames.shape[1]))
        for frame in frames:
            writer.write(frame)
        writer.release()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("calibration")
    ap.add_argument("--ballnet", default="models/ballnet/ballnet_robot.onnx")
    ap.add_argument("--base-url", default="http://127.0.0.1:8099")
    args = ap.parse_args()
    base = args.base_url
    clip = realtime_clip(Path(args.clip))
    cal_id = json.loads(Path(args.calibration).read_text())["calibration_id"]
    snap = call(base, "POST", "/matches", {
        "schema_version": "1.0", "command_id": str(uuid.uuid4()),
        "players": [{"id": "p1", "display_name": "A"}, {"id": "p2", "display_name": "B"}],
        "config": {"scoring_mode": "assisted"}, "calibration_id": cal_id,
    })
    match = snap["match_id"]
    command(base, match, "robot.ready.set", {"ready": True, "reason": "manual_arrival"})
    command(base, match, "match.start")
    stop, log = threading.Event(), []
    worker = threading.Thread(target=operator, args=(base, match, stop, log), daemon=True)
    worker.start()
    live = subprocess.run(
        [sys.executable, "-m", "table_tennis.vision.live", "--clip", str(clip), "--match-id", match,
         "--base-url", base, "--token", "vision_secret", "--calibration", args.calibration, "--ballnet", args.ballnet],
        cwd=ROOT, capture_output=True, text=True,
    )
    time.sleep(1.0)
    stop.set()
    worker.join()
    final = call(base, "GET", f"/matches/{match}")
    lines = [l for l in live.stderr.splitlines() if "fps camera" not in l]
    print("\n".join(lines[-25:]))
    print(f"mec {match[:8]}: predloga {sum(1 for e in log if e[1] == 'predlog')}, rezultat {final['score_by_player']}")
    for _t, kind, a, b in log:
        if kind != "greska":
            print(f"  {kind}: pobednik {a} ({b})")
    errors = [e for e in log if e[1] == "greska"]
    if errors:
        print(f"  greske operatora: {len(errors)}, prva: {errors[0][2]}")


if __name__ == "__main__":
    main()
