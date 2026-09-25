#!/usr/bin/env python3
"""
A2 Ultra free-form arm move — raise one arm to ~90deg (forward horizontal), hold, return.

Uses the McMotionService HTTP-RPC on the x86 motion controller (MC_IP=192.168.100.100:56322).
This is the SAME service AgiBot's own stack calls; it goes through the whole-body safety layer.

SAFETY:
  * Defaults to --dry-run (prints the request, sends nothing).
  * Moves ONE joint, slowly (low velocity_scale), and returns to the recorded start angle.
  * You MUST confirm which joint is the shoulder-flexion joint for your body variant first
    (run with --show-state and watch which joint changes when you pose the arm by hand / teleop).
"""
import argparse, json, sys, time, math
from datetime import datetime, timezone
import requests

MC_URL = "http://192.168.100.100:56322"
BASE_ = f"{MC_URL}/rpc/aimdk.protocol.McBaseService"
SVC = f"{MC_URL}/rpc/aimdk.protocol.McMotionService"
DATA = f"{MC_URL}/rpc/aimdk.protocol.McDataService"


def get_work_mode():
    """The MC filters commands whose control_source != its current work mode."""
    return rpc(f"{BASE_}/GetWorkMode", {})["mode"]


def header(source=None):
    if source is None:
        source = get_work_mode()
    now = datetime.now(timezone.utc)
    return {
        "timestamp": {
            "seconds": int(now.timestamp()),
            "nanos": now.microsecond * 1000,
            "ms_since_epoch": int(now.timestamp() * 1000),
        },
        "control_source": source,
    }


def rpc(url, payload, timeout=5):
    r = requests.post(url, headers={"Content-Type": "application/json"},
                      data=json.dumps(payload), timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_joint_state():
    return rpc(f"{DATA}/GetJointState", {})["states"]


def joint_pos(states, name):
    for s in states:
        if s["name"] == name:
            return float(s["position"])
    raise KeyError(name)


def joint_control(group, name, angle, dry_run=True):
    """Single-joint absolute move (radians)."""
    payload = {
        "header": header(),
        "group": group,                       # JointControlRequest.JointGroup: LEFT_ARM / RIGHT_ARM / DUAL_ARM ...
        "cmds": [{"name": name, "mode": "ABSOLUTE", "angle": angle}],
    }
    print(f"  JointControl -> {name} = {angle:+.3f} rad ({math.degrees(angle):+.1f} deg)")
    if dry_run:
        print("  [dry-run] " + json.dumps(payload))
        return None
    return rpc(f"{SVC}/JointControl", payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["left", "right"], default="right")
    # Shoulder-flexion joint (raises the arm forward). VERIFY this for your body variant.
    ap.add_argument("--joint", default=None,
                    help="joint name; default idxNN_<arm>_arm_joint1")
    ap.add_argument("--target-deg", type=float, default=90.0)
    ap.add_argument("--hold", type=float, default=2.0)
    ap.add_argument("--show-state", action="store_true", help="print joint state and exit")
    ap.add_argument("--execute", action="store_true", help="actually send motion (default is dry-run)")
    a = ap.parse_args()

    states = get_joint_state()
    if a.show_state:
        for s in states:
            print(f"{s['name']:26s} pos={s['position']:+.4f} vel={s['velocity']:+.3f} eff={s['effort']:+.2f}")
        return

    grp = "RIGHT_ARM" if a.arm == "right" else "LEFT_ARM"
    joint = a.joint or (("idx20_right_arm_joint1") if a.arm == "right" else "idx13_left_arm_joint1")
    start = joint_pos(states, joint)
    target = math.radians(a.target_deg)
    dry = not a.execute

    print(f"Arm={a.arm} group={grp} joint={joint}")
    print(f"Start angle = {start:+.3f} rad ({math.degrees(start):+.1f} deg); target = {math.degrees(target):+.1f} deg")
    if dry:
        print("*** DRY-RUN (no motion). Re-run with --execute to move the robot. ***")

    # ramp up in steps so nothing is sudden
    steps = 8
    for i in range(1, steps + 1):
        ang = start + (target - start) * i / steps
        joint_control(grp, joint, ang, dry_run=dry)
        if not dry:
            time.sleep(0.25)
    print(f"Holding {a.hold}s ...")
    if not dry:
        time.sleep(a.hold)
    # return to start
    for i in range(1, steps + 1):
        ang = target + (start - target) * i / steps
        joint_control(grp, joint, ang, dry_run=dry)
        if not dry:
            time.sleep(0.25)
    print("Done.")


if __name__ == "__main__":
    main()
