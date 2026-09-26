#!/usr/bin/env python3
"""
a2_motion.py — minimal, dependency-light client for AgiBot A2 Ultra free-form motion.

ARCHITECTURE
------------
Dev/brain PC (this Orin, 192.168.100.110)  --eth_to_x86-->  Motion Controller x86 (192.168.100.100)

All motor commands are proto3-JSON over HTTP-RPC to the MC:

    http://192.168.100.100:56322/rpc/aimdk.protocol.<Service>/<Method>

Services used here:
  McBaseService    GetWorkMode / GetState        -> is the robot enabled? which control source?
  McDataService    GetJointState / GetTaskState  -> feedback (position, velocity, effort)
  McMotionService  JointControl / PlanningMove / JointMove / SafeStop / GoHomePose

WHY A COMMAND CAN BE SILENTLY IGNORED
-------------------------------------
`ControlSource` is d
escribed in control_source.proto as being set by the safety module
"used by MC to filter mismatched control source inputs". If the control_source in your
request header does not match the MC's current work mode, the MC accepts the HTTP call
(you get code 0) but DROPS the motion. That is the #1 cause of "the RPC succeeded but
the robot didn't move".

=> This client calls GetWorkMode() and uses the reported mode by default. Do not
   hardcode ControlSource_MANUAL.

JOINT MODEL (26 controllable joints, verified live on this robot)
----------------------------------------------------------------
Legs  6/leg : idx01..idx06 left, idx07..idx12 right
              (hip_roll, hip_yaw, hip_pitch, tarsus, toe_pitch, toe_roll)
Arms  7/arm : idx13..idx19 left, idx20..idx26 right
Head        : fixed on the `with_casing` body variant

Arm joint meanings (derived from URDF forward kinematics, base_link = x fwd, y left, z up):
  joint1  shoulder FLEXION   0 = arm hangs down, +1.5708 = arm horizontal FORWARD
                             (same sign for BOTH arms)
  joint2  shoulder ABDUCTION 0 = arm straight out to the side; home ~ +1.26 (L) / -1.26 (R)
  joint3  upper-arm roll
  joint4  ELBOW              0 = straight; limits [-2.094, 0] (L) / [0, 2.094] (R)
  joint5..7  wrist (parallel linkage; not modelled in the URDF serial chain)

LEGS ARE DIFFERENT — READ THIS
------------------------------
Do NOT drive leg joints directly for walking. Locomotion runs through AgiBot's own
balance/RL controller. You command it with LocomotionVelocity {forward, lateral,
angular} (m/s) on topic /motion/control/locomotion_velocity plus SetLocomotionGait.
`/body_drive/leg_joint_command` exists but bypasses balance and will drop the robot.
"""
from __future__ import annotations
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence
import requests


MC_IP = "192.168.100.100" # The Motor Control (and WiFi) IP Address
MC_PORT = 56322 # Motor Control Port for moving the robot.
BASE = f"http://{MC_IP}:{MC_PORT}/rpc/aimdk.protocol"

MOTION = f"{BASE}/McMotionService"
DATA = f"{BASE}/McDataService"
MCBASE = f"{BASE}/McBaseService"
KINE = f"{BASE}/McKinematicsService"
ACTION = f"{BASE}/McActionService"

# MC RPC error codes seen in practice
RPC_NOT_IMPLEMENTED = "1002"  # HTTP 500 "Handle rpc failed, code: 1002"

# ---------------------------------------------------------------- action modes
#
# THE MC IS AN ACTION STATE MACHINE. This is the single most important thing to
# understand about commanding this robot.
#
# After boot the MC sits in McAction_DEFAULT, in which external arm commands are
# ACCEPTED AT THE HTTP LAYER (you get HTTP 200, code 0) but the resulting task is
# never scheduled -- GetTaskState reports CommonState_UNKNOWN forever and the robot
# does not move. There is no error telling you why. You must first SetAction into a
# mode that permits external upper-body control.
#
# Mode families (verified against this robot via GetAvailableCommands):
#
#   PASSIVE_UPPER_BODY_*        lower body NOT powered, arms accept external commands.
#                               Correct choice when the robot is on a gantry/stand.
#   RL_LOCOMOTION_ARM_EXT_*     legs under the RL locomotion controller (standing /
#                               walking) while the arms take external commands.
#   RL_WHOLE_BODY_EXT_*         whole-body external control.
#
# Within each family the suffix selects the control interface:
#
#   PLANNING_MOVE    point-to-point, collision-aware      -> PlanningMove RPC
#   JOINT_SERVO      direct joint targets, high rate      -> RL / teleoperation
#   ONLINE_PLANNING  streamed trajectory point queue      -> RL / teleoperation
#
ARM_CAPABLE_ACTIONS: dict[str, str] = {
    # legs unpowered -- safe while hung on a gantry or stand
    "McAction_PASSIVE_UPPER_BODY_PLANNING_MOVE": "planner",
    "McAction_PASSIVE_UPPER_BODY_JOINT_SERVO": "servo",
    "McAction_PASSIVE_UPPER_BODY_ONLINE_PLANNING": "online",
    # legs actively balancing / walking
    "McAction_RL_LOCOMOTION_ARM_EXT_PLANNING_MOVE": "planner",
    "McAction_RL_LOCOMOTION_ARM_EXT_JOINT_SERVO": "servo",
    "McAction_RL_LOCOMOTION_EXT_ONLINE_PLANNING": "online",
    "McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO": "servo",
    "McAction_RL_WHOLE_BODY_EXT_ONLINE_PLANNING": "online",
}

# Modes in which the LEG MOTORS ARE NOT POWERED. The robot's weight must already be
# supported by a gantry or stand before entering one of these. Entering one of these
# WHILE THE ROBOT IS STANDING will drop it.
LEGS_PASSIVE_ACTIONS = {a for a in ARM_CAPABLE_ACTIONS if "PASSIVE_UPPER_BODY" in a}

# THE INTERFACE MUST MATCH THE API YOU CALL. This is not interchangeable.
# PlanningMove requires a *_PLANNING_MOVE action. Calling it from a *_JOINT_SERVO action
# gets refused by the arm planner with, verbatim:
#     [Warn][PncArmModule][pnc_arm_service.cc:85] current_action not right: <ACTION>
# and the goal is never even bounds-checked.
PLANNER_ACTIONS = {a for a, k in ARM_CAPABLE_ACTIONS.items() if k == "planner"}
SERVO_ACTIONS = {a for a, k in ARM_CAPABLE_ACTIONS.items() if k == "servo"}
ONLINE_ACTIONS = {a for a, k in ARM_CAPABLE_ACTIONS.items() if k == "online"}

# Planner-capable action for each leg situation.
ARM_ACTION_WHILE_STANDING = "McAction_RL_LOCOMOTION_ARM_EXT_PLANNING_MOVE"
ARM_ACTION_WHILE_PASSIVE = "McAction_PASSIVE_UPPER_BODY_PLANNING_MOVE"

# Kept for backwards compatibility; prefer recommended_arm_action(state).
DEFAULT_ARM_ACTION = ARM_ACTION_WHILE_PASSIVE

# robot_init_state values that mean the legs are carrying the robot.
STANDING_INIT_STATES = {
    "McRobotInitState_STAND_READY",
    "McRobotInitState_STANDING",
    "McRobotInitState_WALK_READY",
}


def is_standing(state: dict) -> bool:
    #True if the robot is up on its legs (so unpowering them would drop it).
    if state.get("robot_init_state") in STANDING_INIT_STATES:
        return True
    if state.get("is_walking"):
        return True
    act = state.get("action_info", {}).get("current_action", "")
    return any(t in act for t in ("LOCOMOTION", "STAND", "WHOLE_BODY", "NAVIGATION"))


def recommended_arm_action(state: dict) -> str:
    """Planner-capable action appropriate to the robot's current leg situation."""
    return ARM_ACTION_WHILE_STANDING if is_standing(state) else ARM_ACTION_WHILE_PASSIVE

TERMINAL_TASK_STATES = {
    "SUCCESS": True, "SUCCEEDED": True, "FINISHED": True, "DONE": True,
    "FAILURE": False, "FAILED": False, "ABORTED": False,
    "TIMEOUT": False, "INVALID": False,
}
PENDING_TASK_STATES = {"PENDING", "CREATED", "RUNNING", "PREPARE", "IN_MANUAL"}

# ---------------------------------------------------------------- joint tables

LEFT_ARM = [f"idx{n:02d}_left_arm_joint{i}" for i, n in enumerate(range(13, 20), start=1)]
RIGHT_ARM = [f"idx{n:02d}_right_arm_joint{i}" for i, n in enumerate(range(20, 27), start=1)]

# Home / natural-hang pose, 7 values per arm.
# Matches AgiBot's own jnt_home in config/calibration/tool/record_data/arm_control.py
# and the live GetJointState readings on this robot.
HOME_LEFT = [0.00, 1.20, 0.02, -0.10, 1.60, 0.0, 0.0]
HOME_RIGHT = [0.00, -1.20, 0.04, 0.10, 1.60, 0.0, 0.0]

# PLANNER limits -- what pnc_arm's BoundsChecker actually enforces. These are NOT the
# URDF limits. A goal with ANY joint outside these is rejected WHOLESALE: the entire
# trajectory is discarded, nothing moves, the RPC still returns code 0, and the only
# trace is a [Warn] in /agibot/log/pnc_arm/pnc_arm.log on the MC:
#
#   BoundsChecker: Invalid joint[3]: range: 0.03 2  q: 0
#   check goal:  BoundsChecker is invalid
#
# Values below were read off that checker by sending deliberately out-of-range goals
# (rejected without motion, so probing is safe).
#
# NOTE THE ELBOW: |joint4| >= 0.03 ALWAYS. The elbow can never be commanded perfectly
# straight -- there is a ~1.7 deg minimum bend. Asking for exactly 0.0 is the single
# easiest way to silently break an otherwise valid move.
ARM_LIMITS = {
    "joint1": (-2.91, 2.91),            # verified: shoulder flexion, both arms
    "joint2_left": (-0.5236, 1.6581),   # URDF (planner bound not probed)
    "joint2_right": (-1.6581, 0.5236),  # URDF (planner bound not probed)
    "joint3": (-2.91, 2.91),
    "joint4_left": (-2.0, -0.03),       # verified
    "joint4_right": (0.03, 2.0),        # verified
    "wrist": (-2.0, 2.0),
}

ELBOW_MIN_ABS = 0.03    # hard planner minimum
ELBOW_SAFE_ABS = 0.05   # what we use, with margin for float noise


def limits_for(joint_name: str) -> tuple[float, float]:
    side = "left" if "_left_" in joint_name else "right"
    idx = int(joint_name.rsplit("joint", 1)[1])
    if idx == 1:
        return ARM_LIMITS["joint1"]
    if idx == 2:
        return ARM_LIMITS[f"joint2_{side}"]
    if idx == 3:
        return ARM_LIMITS["joint3"]
    if idx == 4:
        return ARM_LIMITS[f"joint4_{side}"]
    return ARM_LIMITS["wrist"]


def elbow_straight(side: str) -> float:
    """Straightest LEGAL elbow angle for the given side."""
    return ELBOW_SAFE_ABS if side == "right" else -ELBOW_SAFE_ABS


def check_bounds(names: Sequence[str], values: Sequence[float]) -> list[str]:
    """Return human-readable violations; empty means the goal is acceptable."""
    bad = []
    for n, v in zip(names, values):
        lo, hi = limits_for(n)
        if not (lo <= v <= hi):
            extra = ("  <-- elbow cannot be straighter than "
                     f"{ELBOW_MIN_ABS} rad" if n.endswith("joint4")
                     and abs(v) < ELBOW_MIN_ABS else "")
            bad.append(f"{n} = {v:+.4f} outside planner range [{lo}, {hi}]{extra}")
    return bad


class MotionError(RuntimeError):
    pass


class NotImplementedOnRobot(MotionError):
    """The RPC exists in the proto but this MC firmware does not implement it."""


class WrongActionMode(MotionError):
    """The MC action mode does not permit external arm commands."""


# ---------------------------------------------------------------- the client


@dataclass
class A2Motion:
    """Thin wrapper over the MC HTTP-RPC surface.

    dry_run=True (default) prints requests and sends nothing.
    """

    dry_run: bool = True
    timeout: float = 6.0
    control_source: str | None = None  # None => auto-detect from GetWorkMode
    verbose: bool = True

    _session: requests.Session = field(default_factory=requests.Session, repr=False)

    # ---- plumbing ----------------------------------------------------------

    def _post(self, url: str, payload: dict) -> dict:
        r = self._session.post(
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        if r.status_code != 200:
            method = url.rsplit("/", 1)[-1]
            if RPC_NOT_IMPLEMENTED in r.text:
                raise NotImplementedOnRobot(
                    f"{method} is declared in the aimdk proto but is NOT implemented on "
                    f"this MC build (RPC code {RPC_NOT_IMPLEMENTED}). Use a different API."
                )
            raise MotionError(f"{url} -> HTTP {r.status_code}: {r.text[:300]}")
        out = r.json()
        code = str(out.get("header", {}).get("code", "0"))
        if code not in ("0", "None"):
            raise MotionError(f"{url} -> RPC code {code}: {out.get('header', {}).get('msg')}")
        return out

    def _log(self, *a):
        if self.verbose:
            print(*a)

    def header(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "timestamp": {
                "seconds": int(now.timestamp()),
                "nanos": now.microsecond * 1000,
                "ms_since_epoch": int(now.timestamp() * 1000),
            },
            "control_source": self.resolve_control_source(),
        }

    # ---- read-only ---------------------------------------------------------

    def get_work_mode(self) -> str:
        return self._post(f"{MCBASE}/GetWorkMode", {})["mode"]

    def get_state(self) -> dict:
        return self._post(f"{MCBASE}/GetState", {})["state"]

    def resolve_control_source(self) -> str:
        if self.control_source is None:
            self.control_source = self.get_work_mode()
            self._log(f"[a2] auto-detected control_source = {self.control_source}")
        return self.control_source

    def joint_state(self) -> dict[str, dict]:
        states = self._post(f"{DATA}/GetJointState", {})["states"]
        return {s["name"]: s for s in states}

    def positions(self, names: Sequence[str]) -> list[float]:
        js = self.joint_state()
        return [float(js[n]["position"]) for n in names]

    def task_state(self, task_id: int | str) -> str:
        out = self._post(f"{DATA}/GetTaskState", {"task_id": str(task_id)})
        return out.get("state", "UNKNOWN")

    # ---- action state machine ---------------------------------------------

    def get_action(self) -> tuple[str, str]:
        """Return (current_action, status)."""
        info = self._post(f"{ACTION}/GetAction", {})["info"]
        return info.get("current_action", "UNKNOWN"), info.get("status", "UNKNOWN")

    def available_actions(self) -> list[str]:
        out = self._post(f"{ACTION}/GetAvailableCommands", {})
        return [c.get("action", "") for c in out.get("commands", [])]

    def set_action(self, action: str, wait: float = 10.0, poll: float = 0.4) -> str:
        """Transition the MC into `action` and wait for it to become current.

        This is the step that makes external arm commands actually execute.
        """
        if action in LEGS_PASSIVE_ACTIONS:
            self._log(f"  [!] {action} leaves the LEG MOTORS UNPOWERED - the robot's "
                      f"weight must be carried by a gantry or stand.")
        payload = {"header": self.header(), "command": {"action": action, "ext_action": ""}}
        self._log(f"  SetAction -> {action}")
        if self.dry_run:
            self._log("   [dry-run] " + json.dumps(payload))
            return "DRY_RUN"
        self._post(f"{ACTION}/SetAction", payload)
        deadline = time.monotonic() + wait
        cur = status = "UNKNOWN"
        while time.monotonic() < deadline:
            cur, status = self.get_action()
            if cur == action and status in ("McActionStatus_RUNNING", "McActionStatus_DONE"):
                self._log(f"   action now {cur} ({status})")
                return cur
            time.sleep(poll)
        raise WrongActionMode(
            f"SetAction({action}) did not take effect (still {cur}/{status} after {wait}s)")

    def ensure_arm_action(self, action: str | None = None,
                          allow_switch: bool = False,
                          require_interface: str = "planner",
                          force_passive: bool = False) -> str:
        """Make sure the MC is in a mode whose INTERFACE matches the API we will call.

        `require_interface` is 'planner' for PlanningMove/JointMove, 'servo' for
        streamed joint targets, 'online' for streamed trajectory queues. An
        arm-capable action with the WRONG interface is still a failure: the arm
        planner refuses it with "current_action not right: <ACTION>".

        Returns the action current BEFORE any switch, so the caller can restore it.
        """
        st = self.get_state()
        standing = is_standing(st)
        target = action or recommended_arm_action(st)

        cur, status = self.get_action()
        kind = ARM_CAPABLE_ACTIONS.get(cur)
        self._log(f"[a2] current action = {cur} ({status})"
                  + (f" [{kind} interface]" if kind else " [no external arm control]"))
        self._log(f"[a2] legs are {'CARRYING the robot (standing)' if standing else 'passive/supported'}")

        if kind == require_interface:
            self._log(f"[a2] interface matches ({kind}) — no switch needed")
            return cur

        # Build a precise explanation of the mismatch.
        if kind is None:
            problem = (f"MC is in {cur}, which does not accept external arm commands "
                       f"at all.")
        else:
            problem = (f"MC is in {cur}, which accepts external arm commands but only "
                       f"through the '{kind}' interface. This call needs the "
                       f"'{require_interface}' interface, so the arm planner will refuse "
                       f"it with \"current_action not right: {cur.replace('McAction_','')}\".")

        if not allow_switch:
            opts = [a for a, k in ARM_CAPABLE_ACTIONS.items() if k == require_interface]
            raise WrongActionMode(
                problem
                + f"\n    Actions providing the '{require_interface}' interface:\n      "
                + "\n      ".join(
                    f"{a}  ({'legs UNPOWERED' if a in LEGS_PASSIVE_ACTIONS else 'legs active'})"
                    for a in opts)
                + f"\n    Recommended for the CURRENT leg state: {target}"
                + "\n    Re-run with --enter-mode to switch automatically."
            )

        if ARM_CAPABLE_ACTIONS.get(target) != require_interface:
            raise WrongActionMode(
                f"requested action {target} provides the "
                f"'{ARM_CAPABLE_ACTIONS.get(target)}' interface, not "
                f"'{require_interface}'.")

        # HARD SAFETY GATE: never unpower the legs while the robot is standing.
        if standing and target in LEGS_PASSIVE_ACTIONS and not force_passive:
            raise WrongActionMode(
                f"REFUSING to enter {target} while the robot is STANDING "
                f"(robot_init_state={st.get('robot_init_state')}).\n"
                f"    That action UNPOWERS THE LEG MOTORS — the robot would drop onto "
                f"its gantry/stand.\n"
                f"    Use {ARM_ACTION_WHILE_STANDING} instead (keeps the legs under the "
                f"RL locomotion controller).\n"
                f"    Pass --force-passive only if the robot is fully supported and you "
                f"intend the legs to go limp."
            )

        avail = self.available_actions()
        if avail and target not in avail:
            raise WrongActionMode(f"{target} not in MC's available commands: {avail}")
        self.set_action(target)
        return cur

    # ---- preflight ---------------------------------------------------------

    def preflight(self) -> dict:
        """Assert the robot is in a state that can accept arm commands."""
        st = self.get_state()
        mode = st.get("control_info", {}).get("mode")
        work = st.get("work_state")
        self._log(f"[a2] work_state={work} control_mode={mode} "
                  f"arm_motion_state={st.get('arm_motion_state')} "
                  f"init_state={st.get('robot_init_state')} "
                  f"walking={st.get('is_walking')} collided={st.get('is_collisioned')}")
        if work != "McWorkState_ENABLED":
            raise MotionError(f"robot not enabled (work_state={work}); call EnableRobot first")
        if st.get("is_collisioned"):
            raise MotionError("MC reports is_collisioned=true; clear the collision first")
        return st

    # ---- motion ------------------------------------------------------------

    def joint_control(self, group: str, cmds: list[tuple[str, float]],
                      mode: str = "ABSOLUTE") -> dict | None:
        """Direct per-joint command.

        NOTE: verified NOT IMPLEMENTED on this robot's MC build -- it returns
        HTTP 500 / RPC code 1002. Kept for other builds; raises NotImplementedOnRobot
        here. Use planning_move_joint(), or a JOINT_SERVO / ONLINE_PLANNING action mode
        with the streaming channel, instead.
        """
        for name, ang in cmds:
            lo, hi = limits_for(name)
            if mode == "ABSOLUTE" and not (lo <= ang <= hi):
                raise MotionError(f"{name}={ang:.4f} outside URDF limit [{lo},{hi}]")
        payload = {
            "header": self.header(),
            "group": group,
            "cmds": [{"name": n, "mode": mode, "angle": float(a)} for n, a in cmds],
        }
        pretty = ", ".join(f"{n}={a:+.3f}" for n, a in cmds)
        self._log(f"  JointControl[{group}] {pretty}")
        if self.dry_run:
            self._log("   [dry-run] " + json.dumps(payload))
            return None
        return self._post(f"{MOTION}/JointControl", payload)

    def planning_move_joint(self, group: str, joints: Sequence[float],
                            velocity_scale: float = 0.15,
                            acceleration_scale: float = 0.15,
                            mode: str = "McPlanningMode_DEFAULT",
                            names: Sequence[str] | None = None) -> int | None:
        """Collision-aware planned move to a joint vector.

        This is the path AgiBot's own calibration tool uses. `joints` length must match
        the group: 7 for LEFT_ARM/RIGHT_ARM, 14 for DUAL_ARM (left then right).
        Returns task_id (poll with task_state()).
        """
        if names is not None:
            violations = check_bounds(names, joints)
            if violations:
                raise MotionError(
                    "goal REJECTED locally before sending (the planner would discard it "
                    "wholesale and report success anyway):\n    " + "\n    ".join(violations))
        payload = {
            "header": self.header(),
            "group": group,
            "mode": mode,
            "target": {"type": "JOINT", "joints": [float(v) for v in joints]},
            "param": {"velocity_scale": velocity_scale,
                      "acceleration_scale": acceleration_scale},
        }
        self._log(f"  PlanningMove[{group}] vel={velocity_scale} "
                  f"joints=[{', '.join(f'{v:+.3f}' for v in joints)}]")
        if self.dry_run:
            self._log("   [dry-run] " + json.dumps(payload))
            return None
        out = self._post(f"{MOTION}/PlanningMove", payload)
        # The MC proxies arm planning to PncArmMotionService on the MC's own
        # 127.0.0.1:56321 and does NOT propagate that service's task_id back, so this is
        # always "0". Do not try to poll it -- verify by watching joint positions.
        return int(out.get("task_id", 0))

    def wait_convergence(self, expected: dict[str, float], timeout: float = 25.0,
                         tol: float = 0.05, poll: float = 0.25,
                         settle: float = 0.5) -> bool:
        """Wait until the measured joints reach `expected`.

        This is the real completion signal for arm moves, because PlanningMove through
        the MC returns task_id 0 and GetTaskState(0) is meaningless.
        """
        names = list(expected)
        start = dict(zip(names, self.positions(names)))
        deadline = time.monotonic() + timeout
        moved = False
        last_err = None
        stable_since = None
        while time.monotonic() < deadline:
            js = self.joint_state()
            err = max(abs(float(js[n]["position"]) - expected[n]) for n in names)
            # Judge "did it move" by DISPLACEMENT from the start pose, not velocity.
            # A standing robot is constantly balancing, so joint velocities are never
            # truly zero and a velocity test reports motion that never happened.
            disp = max(abs(float(js[n]["position"]) - start[n]) for n in names)
            if disp > 0.05:
                moved = True
            last_err = err
            if err <= tol:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= settle:
                    self._log(f"    converged (max err {err:.4f} rad)")
                    return True
            else:
                stable_since = None
            time.sleep(poll)
        cur, status = self.get_action()
        kind = ARM_CAPABLE_ACTIONS.get(cur)
        if not moved:
            reason = (f"No joint actually moved (max displacement stayed under 0.05 rad).\n"
                   f"    Action = {cur} ({status})"
                   + (f", which provides the '{kind}' interface." if kind else
                      ", which does not accept external arm commands.")
                   + "\n    If that interface is not 'planner', PlanningMove is refused "
                     "outright.\n    Diagnose on the MC with:\n"
                     "      ssh -i ~/.ssh/agibot_rsa agi@192.168.100.100 "
                     "\"grep -a 'not right\\|BoundsChecker' "
                     "/agibot/log/pnc_arm/pnc_arm.log | tail\"")
        else:
            reason = ("The arm moved but stopped short; the planner may have truncated the "
                   "trajectory (collision, or a joint hitting a bound).")
        raise MotionError(
            f"joints did not reach the goal within {timeout}s "
            f"(max error {last_err:.4f} rad, tol {tol}).\n    " + reason)

    def wait_task(self, task_id: int | None, timeout: float = 25.0,
                  poll: float = 0.3) -> str:
        """Poll GetTaskState until terminal.

        A task that stays CommonState_UNKNOWN means the MC never scheduled it -- almost
        always the action mode. We surface that explicitly instead of a bare timeout.
        """
        if task_id is None:  # dry-run
            return "DRY_RUN"
        deadline = time.monotonic() + timeout
        short = "UNKNOWN"
        ever_seen = False
        while time.monotonic() < deadline:
            short = self.task_state(task_id).replace("CommonState_", "")
            if short in TERMINAL_TASK_STATES:
                if TERMINAL_TASK_STATES[short]:
                    return short
                raise MotionError(f"task {task_id} ended as {short}")
            if short in PENDING_TASK_STATES:
                ever_seen = True
            time.sleep(poll)
        if not ever_seen and short == "UNKNOWN":
            cur, status = self.get_action()
            raise WrongActionMode(
                f"task {task_id} was never scheduled (stayed CommonState_UNKNOWN for "
                f"{timeout}s).\n    The MC accepted the RPC but discarded the motion. "
                f"Current action = {cur} ({status}).\n    "
                + ("This action does not permit external arm control -- "
                   "re-run with --enter-mode."
                   if cur not in ARM_CAPABLE_ACTIONS else
                   "Action looks correct; check joint limits and collision state.")
            )
        return short + " (timeout)"

    def safe_stop(self, hold: dict[str, float] | None = None,
                  group: str | None = None) -> str:
        """Best-effort stop.

        SafeStop's handler is registered on this MC but returns NOT_IMPLEMENTED (1002),
        so it cannot be relied on. The effective way to stop a planned arm move is to
        issue a new PlanningMove to where the arm is RIGHT NOW, which supersedes the
        running trajectory. Never raises.
        """
        if self.dry_run:
            self._log("  [dry-run] safe_stop")
            return "DRY_RUN"
        try:
            self._post(f"{MOTION}/SafeStop", {"header": self.header()})
            self._log("  SafeStop accepted")
            return "SAFESTOP"
        except NotImplementedOnRobot:
            self._log("  SafeStop unavailable on this build (1002) — "
                      "superseding with a hold-in-place PlanningMove")
        except MotionError as e:
            self._log(f"  SafeStop failed ({e}) — trying hold-in-place")

        try:
            if group is None or hold is None:
                return "NO_STOP_PATH"
            names = list(hold)
            here = dict(zip(names, self.positions(names)))
            # clamp so the hold pose itself can't be rejected by BoundsChecker
            vals = []
            for n in names:
                lo, hi = limits_for(n)
                vals.append(min(max(here[n], lo), hi))
            self._post(f"{MOTION}/PlanningMove", {
                "header": self.header(), "group": group,
                "mode": "McPlanningMode_DEFAULT",
                "target": {"type": "JOINT", "joints": vals},
                "param": {"velocity_scale": 0.05, "acceleration_scale": 0.05}})
            self._log("  hold-in-place issued")
            return "HOLD"
        except Exception as e:  # never let a stop path raise
            self._log(f"  hold-in-place also failed: {e}")
            return "FAILED"

    def go_home_pose(self) -> dict | None:
        self._log("  GoHomePose")
        if self.dry_run:
            return None
        return self._post(f"{MOTION}/GoHomePose", {"header": self.header()})

    # ---- verification ------------------------------------------------------

    def verify(self, expected: dict[str, float], tol: float = 0.12) -> bool:
        """Read back joints and report whether they reached the expected angles."""
        js = self.joint_state()
        ok = True
        for name, want in expected.items():
            got = float(js[name]["position"])
            good = abs(got - want) <= tol
            ok &= good
            self._log(f"    {'OK ' if good else 'MISS'} {name}: want {want:+.3f} got {got:+.3f} "
                      f"(d={got - want:+.3f})")
        return ok


def deg(x: float) -> float:
    return math.degrees(x)


def rad(x: float) -> float:
    return math.radians(x)
