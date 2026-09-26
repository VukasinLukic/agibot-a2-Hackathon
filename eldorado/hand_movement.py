import time
import requests

MC_URL = "http://192.168.100.100:56322/rpc/aimdk.protocol"
CONTROL_SOURCE = "ControlSource_SAFE"
REQUIRED_ACTION_SUFFIX = "PLANNING_MOVE"

VEL = 0.12
ACC = 0.12

SHOULDER_90 = 1.5708
ELBOW_UP = 1.50
ELBOW_DOWN = 0.40
SHAKES = 3
STEP_WAIT = 1.5

LEFT_HOME = [0.0, 1.26, 0.0, -0.03, 0.0, 0.0, 0.0]
RIGHT_HOME = [0.0, -1.26, 0.0, 0.03, 0.0, 0.0, 0.0]

BOUNDS = {
    "j1": (-2.91, 2.91),
    "right_j4": (0.03, 2.00),
    "left_j4": (-2.00, -0.03),
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


def check_action():
    resp = rpc("McActionService", "GetAction", {"header": header()})
    print("GetAction:", resp)
    if REQUIRED_ACTION_SUFFIX not in str(resp):
        raise SystemExit(f"Robot nije u *_{REQUIRED_ACTION_SUFFIX} modu, PlanningMove ce biti ignorisan.")


def validate(left, right):
    for arm, j in (("left", left), ("right", right)):
        lo, hi = BOUNDS["j1"]
        if not lo <= j[0] <= hi:
            raise ValueError(f"{arm} j1={j[0]} van opsega")
        lo, hi = BOUNDS[f"{arm}_j4"]
        if not lo <= j[3] <= hi:
            raise ValueError(f"{arm} j4={j[3]} van opsega")


def move(left, right, wait=STEP_WAIT):
    validate(left, right)
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
    check_action()
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