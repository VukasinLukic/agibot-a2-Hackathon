import time
import requests

MC_URL = "http://192.168.100.100:56322/rpc/aimdk.protocol"
CONTROL_SOURCE = "ControlSource_SAFE"
TARGET_ACTION = "McAction_RL_LOCOMOTION_ARM_EXT_PLANNING_MOVE"


ROUTE_TO_TARGET = {
    "McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO": ["McAction_RL_LOCOMOTION_DEFAULT", TARGET_ACTION],
    "McAction_RL_WHOLE_BODY_EXT_ONLINE_PLANNING": ["McAction_RL_LOCOMOTION_DEFAULT", TARGET_ACTION],
    "McAction_RL_LOCOMOTION_ARM_EXT_JOINT_SERVO": ["McAction_RL_LOCOMOTION_DEFAULT", TARGET_ACTION],
    "McAction_RL_LOCOMOTION_EXT_ONLINE_PLANNING": ["McAction_RL_LOCOMOTION_DEFAULT", TARGET_ACTION],
    "McAction_RL_LOCOMOTION_DEFAULT": [TARGET_ACTION],
    "McAction_RL_JOINT_DEFAULT": [TARGET_ACTION],
}
HOP_PAUSE = 3.0  # tablet ceka ~3 s izmedju koraka

VEL = 1.0
ACC = 1.0

SHOULDER_90 = 1.5708
ELBOW_UP = 1.50
ELBOW_DOWN = 0.40
RIGHT_WRIST_ROLL = 1.6
SHAKES = 3
STEP_WAIT = 1.5

LEFT_HOME = [0.0, 1.26, 0.0, -0.03, 0.0, 0.0, 0.0]
RIGHT_HOME = [0.0, -1.26, 0.0, 0.03, 0.0, 0.0, 0.0]

BOUNDS = {
    "j1": (-2.91, 2.91),
    "right_j4": (0.03, 2.00),
    "left_j4": (-2.00, -0.03),
    "wrist": (-2.00, 2.00),
}


def header():
    now = time.time()
    return {
        "timestamp": {"seconds": int(now), "nanos": int((now % 1) * 1e9)},
        "control_source": CONTROL_SOURCE,
    }


def rpc(service, method, body):
    r = requests.post(f"{MC_URL}.{service}/{method}", json=body, timeout=5)
    r.raise_for_status()
    return r.json()


def get_action():
    info = rpc("McActionService", "GetAction", {"header": header()}).get("info", {})
    return info.get("current_action", ""), info.get("status", "")


def set_action(action, wait=15.0):
    # SetAction vraca SUCCESS i kad MC odbije prelaz, pa proveravamo sa GetAction.
    print("SetAction ->", action)
    rpc("McActionService", "SetAction",
        {"header": header(), "command": {"action": action, "ext_action": ""}})
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        cur, status = get_action()
        if cur == action and status in ("McActionStatus_RUNNING", "McActionStatus_DONE"):
            return
        time.sleep(0.5)
    raise SystemExit(f"SetAction({action}) nije prosao, robot je i dalje u {cur}/{status}.")


def ensure_action():
    cur, status = get_action()
    print("GetAction:", cur, status)
    if cur == TARGET_ACTION:
        return
    if cur not in ROUTE_TO_TARGET:
        raise SystemExit(f"Nepoznat put iz {cur} do {TARGET_ACTION}, ne menjam mod.")
    hops = ROUTE_TO_TARGET[cur]
    for i, hop in enumerate(hops):
        set_action(hop)
        if i < len(hops) - 1:
            time.sleep(HOP_PAUSE)


def validate(left, right):
    for arm, j in (("left", left), ("right", right)):
        lo, hi = BOUNDS["j1"]
        if not lo <= j[0] <= hi:
            raise ValueError(f"{arm} j1={j[0]} van opsega")
        lo, hi = BOUNDS[f"{arm}_j4"]
        if not lo <= j[3] <= hi:
            raise ValueError(f"{arm} j4={j[3]} van opsega")
        lo, hi = BOUNDS["wrist"]
        for k in (4, 5, 6):
            if not lo <= j[k] <= hi:
                raise ValueError(f"{arm} j{k + 1}={j[k]} van opsega")


def move(left, right, wait=STEP_WAIT):
    validate(left, right)
    # skillpilot idle animacija na ~60-80 s vraca robota u WHOLE_BODY_EXT_JOINT_SERVO
    ensure_action()
    body = {
        "header": header(),
        "group": "McPlanningGroup_DUAL_ARM",
        "mode": "McPlanningMode_DEFAULT",
        "target": {"type": "JOINT", "joints": left + right},
        "param": {"velocity_scale": VEL, "acceleration_scale": ACC},
    }
    resp = rpc("McMotionService", "PlanningMove", body)
    print("PlanningMove:", resp)
    time.sleep(wait)


def right_arm(shoulder, elbow):
    j = RIGHT_HOME.copy()
    j[0] = shoulder
    j[3] = elbow
    j[4] = RIGHT_WRIST_ROLL
    return j


SIGN_HOLD = 3.0


def show_sign(sign):
    print(f"[TODO saka] znak: {sign}")


def pump_forearm():
    for i in range(SHAKES):
        print(f"{i + 1}...")
        move(LEFT_HOME, right_arm(SHOULDER_90, ELBOW_UP))
        move(LEFT_HOME, right_arm(SHOULDER_90, ELBOW_DOWN))


def main(sign="kamen"):
    ensure_action()
    move(LEFT_HOME, right_arm(SHOULDER_90, ELBOW_DOWN), wait=4.0)
    pump_forearm()
    show_sign(sign)
    time.sleep(SIGN_HOLD)
    move(LEFT_HOME, RIGHT_HOME, wait=4.0)
    print("Gotovo.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Prekinuto - vracam ruku u home.")
        move(LEFT_HOME, RIGHT_HOME, wait=4.0)
        