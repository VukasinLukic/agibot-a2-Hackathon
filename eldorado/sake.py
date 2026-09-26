import argparse, time, requests

URL = "http://192.168.100.100:56322/rpc/aimdk.protocol.McMotionService/SetHandCommand"
FINGERS = ["thumb_0", "thumb_1", "index", "middle", "ring", "pinky"]
POS_KEYS = ["thumb_pos_0", "thumb_pos_1", "index_pos", "middle_pos", "ring_pos", "pinky_pos"]
TOQ_KEYS = ["thumb_toq_0", "thumb_toq_1", "index_toq", "middle_toq", "ring_toq", "pinky_toq"]
MAX_POS = 2000  # highest value seen in Agibot's own tools

POSES = {
    "open":  ([0, 0, 0, 0, 0, 0],                   [4000] * 6),
    "close": ([2000, 2000, 1200, 1200, 1200, 1200], [8000, 2000, 8000, 2000, 2000, 2000]),
    "point": ([2000, 2000, 0, 1200, 1200, 1200],    [8000, 2000, 4000, 2000, 2000, 2000]),
}

def send_hand(side, pos, toq):
    pos = [float(max(0, min(MAX_POS, p))) for p in pos]
    now = time.time()
    payload = {
        "header": {
            "timestamp": {"seconds": int(now), "nanos": int((now % 1) * 1e9),
                          "ms_since_epoch": int(now * 1000)},
            "control_source": "ControlSource_SAFE",
        },
        "data": {side: {"agi_hand": {"finger": {
            "pos": dict(zip(POS_KEYS, pos)),
            "toq": dict(zip(TOQ_KEYS, map(float, toq))),
        }}}},
    }
    r = requests.post(URL, json=payload, timeout=5)
    print(side, r.status_code, r.text[:200])
    return r.ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=["left", "right", "both"], default="right")
    ap.add_argument("--pose", choices=list(POSES), help="preset pose")
    ap.add_argument("--finger", choices=FINGERS, help="move one finger (others open)")
    ap.add_argument("--value", type=int, default=1200, help="0=open .. 2000=closed")
    ap.add_argument("--demo", action="store_true", help="close each finger in turn, then open")
    a = ap.parse_args()
    sides = ["left", "right"] if a.side == "both" else [a.side]

    if a.demo:
        pos, toq = [0] * 6, [4000] * 6
        for i, name in enumerate(FINGERS):
            pos[i] = 1200
            print("closing", name)
            for s in sides: send_hand(s, pos, toq)
            time.sleep(1.0)
        for s in sides: send_hand(s, [0] * 6, toq)
        return

    if a.finger:
        pos = [0] * 6
        pos[FINGERS.index(a.finger)] = a.value
        toq = [4000] * 6
    else:
        pos, toq = POSES[a.pose or "open"]
    for s in sides:
        send_hand(s, pos, toq)

if __name__ == "__main__":
    main()