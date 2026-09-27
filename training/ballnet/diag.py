"""Run a recording through tracker + RallyJudge with an always-open rally; print what vision sees.

    python diag.py <video> <table.json> <start_s> [work_width]

Every rally that closes without a proposal is re-armed, as an operator pressing
Servis would. THR_NEW / THR_CONF env vars override the MHT output thresholds.
"""
import dataclasses
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from table_tennis.contracts import MatchSnapshot
from table_tennis.vision.calibration import read_calibration
from table_tennis.vision.config import load_example_config
from table_tennis.vision.events import RallyJudge
from table_tennis.vision.track import BallTracker
from table_tennis.vision.video import VideoFileCapture

VIDEO, CAL, START = sys.argv[1], sys.argv[2], float(sys.argv[3])
cal = read_calibration(CAL)
config = dataclasses.replace(load_example_config(), ballnet_path=os.environ.get("BALLNET") or str(ROOT / "models" / "ballnet" / "ballnet.onnx"), model_path=None, work_width_px=int(sys.argv[4]) if len(sys.argv) > 4 else 960)
tracker = BallTracker(config, cal)
import os
if os.environ.get("THR_NEW"):
    from table_tennis.vision.mht import TrackerParams
    tracker.pipeline._tracker_params = TrackerParams(thr_new=float(os.environ["THR_NEW"]), thr_conf=float(os.environ["THR_CONF"]))
judge = RallyJudge(cal)
# Watch the detector directly for bounce/hit events.
seen_events = []
orig_add = judge._detector.add
def spy(sample):
    out = orig_add(sample)
    seen_events.extend(out)
    return out
judge._detector.add = spy

score = {"p1": 0, "p2": 0}
def snap(rally_id, server):
    return MatchSnapshot.model_validate({
        "match_id": "00000000-0000-4000-8000-0000000000a1", "revision": 4, "status": "rally",
        "players": [{"id": "p1", "display_name": "A"}, {"id": "p2", "display_name": "B"}],
        "config": {}, "score_by_player": dict(score), "first_server_id": "p1", "server_id": server,
        "winner_id": None, "assignment_version": 1,
        "court_end_by_player": {"p1": "end_a", "p2": "end_b"},
        "robot_side_by_player": {"p1": "left", "p2": "right"},
        "calibration_id": cal.calibration_id, "active_rally_id": rally_id, "active_proposal_id": None,
        "persona": "regular", "scoring_mode": "assisted",
        "ready": {"calibration_ready": True, "camera_ready": True},
        "updated_at": datetime(2026, 9, 26, tzinfo=timezone.utc).isoformat(),
    })

rally = str(uuid.uuid4())
server = "p1"
counts = {"observed": 0, "predicted": 0, "missing": 0}
with VideoFileCapture(VIDEO, config.camera_id, realtime=False, start_s=START) as cap:
    for frame in cap:
        t = START + frame.capture_monotonic_ns / 1e9 - (START if frame.capture_monotonic_ns / 1e9 >= START else 0)
        t = frame.capture_monotonic_ns / 1e9
        sample = tracker.update(frame)
        counts[sample.observation_kind] += 1
        judge.add(sample, rally)
        for ev in seen_events:
            print(f"  {ev.frame_seq and t:6.2f}s  {ev.kind:6} side={getattr(ev, 'side', None)}")
        seen_events.clear()
        cmd = judge.proposal_command(snap(rally, server))
        if cmd:
            p = cmd["payload"]
            score[p["winner_id"]] += 1
            print(f"*** {t:6.2f}s PREDLOG: poen {p['winner_id']} ({p['reason']}), servirao {server} -> {score}")
            total = score["p1"] + score["p2"]
            server = "p1" if (total // 2) % 2 == 0 else "p2"
            rally = str(uuid.uuid4())
            continue
        ask = judge.unclear_command(snap(rally, server))
        if ask:
            print(f"??? {t:6.2f}s ROBOT PITA: Ko je dobio poen?")
            rally = str(uuid.uuid4())
            continue
        fold = judge._fold
        if fold is not None and (fold._terminal or fold._closed):
            why = "lopta otisla kod kraja stola, slika ne dokazuje poen" if fold._terminal else "lopta se vratila posle >2s pauze"
            print(f"--- {t:6.2f}s razmena zatvorena BEZ predloga ({why}); faza={fold._play.phase} -> novi Servis")
            rally = str(uuid.uuid4())
print("kadrovi:", counts)
