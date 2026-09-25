#!/usr/bin/env python3
"""
a2_arm_stretch.py — raise BOTH arms to horizontal-forward ("stretched out", 90 deg at the
shoulder when viewed from the side), hold, then return to the natural hanging pose.

WHAT IT ACTUALLY DOES
    joint1 (shoulder flexion): home ~0.00 rad  ->  +1.5708 rad (90 deg forward)  -> back
    joint4 (elbow)           : home -0.10/+0.10 -> 0.00 (straight)               -> back
    everything else stays at the measured home pose.

Verified from the URDF forward kinematics: with joint1=+1.5708 the elbow sits +0.235 m
in front of the shoulder at the same height (z ~= 0.000), i.e. horizontal. Both arms use
the SAME sign for joint1; only joint2/joint4 are mirrored left/right.

USAGE
    python3 a2_arm_stretch.py --status            # read-only: state + joint angles
    python3 a2_arm_stretch.py                     # DRY RUN (prints, sends nothing)
    python3 a2_arm_stretch.py --execute           # MOVES THE ROBOT
    python3 a2_arm_stretch.py --execute --arm left        # one arm only
    python3 a2_arm_stretch.py --execute --api joint       # use JointControl instead of the planner
    python3 a2_arm_stretch.py --execute --angle-deg 45    # smaller motion first

SAFETY
    * Dry-run is the default; --execute is required to move.
    * Uses the planner (PlanningMove) by default => collision-aware, smooth.
    * velocity_scale defaults to 0.12 (slow). Raise it only once you trust the motion.
    * Ctrl-C sends SafeStop, then returns the arms home.
    * Keep the E-stop in hand. Arms sweep ~0.5 m forward - clear the space in front.
"""
from __future__ import annotations

import argparse
import math
import sys
import time

from a2_motion import (
    A2Motion,
    ARM_ACTION_WHILE_STANDING,
    ARM_CAPABLE_ACTIONS,
    ELBOW_SAFE_ABS,
    recommended_arm_action,
    check_bounds,
    elbow_straight,
    DEFAULT_ARM_ACTION,
    HOME_LEFT,
    HOME_RIGHT,
    LEFT_ARM,
    RIGHT_ARM,
    LEGS_PASSIVE_ACTIONS,
    MotionError,
)

FORWARD_90 = math.pi / 2  # 1.5708 rad -> arm horizontal, pointing forward


GROUPS = {
    "left": ("McPlanningGroup_LEFT_ARM", LEFT_ARM),
    "right": ("McPlanningGroup_RIGHT_ARM", RIGHT_ARM),
    "both": ("McPlanningGroup_DUAL_ARM", LEFT_ARM + RIGHT_ARM),
}


def capture_base(mc: A2Motion, arm: str) -> tuple[list[float], list[float]]:
    """Read the CURRENT pose of both arms and use it as the baseline.

    We deliberately do NOT trust the hardcoded HOME_* table as the return target:
    the resting pose drifts a little between sessions (joint2 was 1.26 one day and
    1.34 the next). Basing the move on live angles means the 'return' step puts the
    arm back exactly where it started and no joint moves that we did not intend.
    """
    left = mc.positions(LEFT_ARM)
    right = mc.positions(RIGHT_ARM)
    for tag, meas, table in (("left", left, HOME_LEFT), ("right", right, HOME_RIGHT)):
        drift = max(abs(m - t) for m, t in zip(meas, table))
        if drift > 0.35:
            print(f"  [warn] {tag} arm is {drift:.2f} rad from the expected home pose — "
                  f"is it already posed? Review before continuing.")
    return left, right


def build_target(arm: str, angle: float, straighten_elbow: bool,
                 base_left: list[float], base_right: list[float]
                 ) -> tuple[str, list[float], list[str], dict[str, float]]:
    """Return (planning_group, joint_vector, joint_names, {name: expected_angle}).

    Only joint1 (and optionally joint4) differ from the captured baseline.
    """
    left = list(base_left)
    right = list(base_right)

    if arm in ("left", "both"):
        left[0] = angle                          # joint1 shoulder flexion
        if straighten_elbow:
            left[3] = elbow_straight("left")     # NEVER 0.0 -- planner rejects |q|<0.03
    if arm in ("right", "both"):
        right[0] = angle
        if straighten_elbow:
            right[3] = elbow_straight("right")

    group, names = GROUPS[arm]
    vec = {"left": left, "right": right, "both": left + right}[arm]
    return group, vec, names, dict(zip(names, vec))


def home_target(arm: str, base_left: list[float], base_right: list[float]):
    """Return target == the exact pose captured at startup."""
    group, names = GROUPS[arm]
    vec = {"left": base_left, "right": base_right,
           "both": base_left + base_right}[arm]
    return group, list(vec), names, dict(zip(names, vec))


def move_planner(mc: A2Motion, group: str, vec: list[float], vel: float, label: str,
                 names: list[str], expected: dict[str, float]):
    print(f"\n>>> {label}")
    mc.planning_move_joint(group, vec, velocity_scale=vel, acceleration_scale=vel,
                           names=names)
    if mc.dry_run:
        print("    [dry-run] would now wait for joint convergence")
        return
    mc.wait_convergence(expected)


def move_joint_api(mc: A2Motion, arm: str, angle: float, straighten_elbow: bool,
                   steps: int, dwell: float, label: str,
                   restore: dict[str, float] | None = None):
    """Stepwise JointControl — no planner. Interpolates from the CURRENT angles.

    `restore` (name -> angle) overrides the computed targets; used for the return leg
    so every joint we touched goes back to its captured start value.
    """
    print(f"\n>>> {label} (JointControl, {steps} steps)")
    group = {"left": "LEFT_ARM", "right": "RIGHT_ARM", "both": "DUAL_ARM"}[arm]
    targets: dict[str, float] = {}
    if restore is not None:
        # only re-drive the joints this demo actually moves (joint1 / joint4)
        for chain in (LEFT_ARM, RIGHT_ARM):
            for j in (chain[0], chain[3]):
                if j in restore:
                    targets[j] = restore[j]
    else:
        if arm in ("left", "both"):
            targets[LEFT_ARM[0]] = angle
            if straighten_elbow:
                targets[LEFT_ARM[3]] = 0.0
        if arm in ("right", "both"):
            targets[RIGHT_ARM[0]] = angle
            if straighten_elbow:
                targets[RIGHT_ARM[3]] = 0.0

    names = list(targets)
    start = dict(zip(names, mc.positions(names)))
    for i in range(1, steps + 1):
        cmds = [(n, start[n] + (targets[n] - start[n]) * i / steps) for n in names]
        mc.joint_control(group, cmds)
        if not mc.dry_run:
            time.sleep(dwell)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", choices=["left", "right", "both"], default="both")
    ap.add_argument("--angle-deg", type=float, default=90.0,
                    help="shoulder flexion target in degrees (default 90 = horizontal forward)")
    ap.add_argument("--api", choices=["planner", "joint"], default="planner",
                    help="planner = PlanningMove (collision-aware, recommended); "
                         "joint = raw JointControl stepping")
    ap.add_argument("--vel", type=float, default=0.12, help="velocity/accel scale 0..1")
    ap.add_argument("--steps", type=int, default=10, help="--api joint: interpolation steps")
    ap.add_argument("--dwell", type=float, default=0.15, help="--api joint: seconds per step")
    ap.add_argument("--hold", type=float, default=3.0, help="seconds to hold the pose")
    ap.add_argument("--straighten-elbow", action="store_true",
                    help="also move joint4 to the straightest LEGAL angle "
                         f"(+/-{ELBOW_SAFE_ABS} rad). Off by default: it is only a ~2 deg "
                         "change and the elbow cannot go to exactly 0.")
    ap.add_argument("--stay", action="store_true", help="do NOT return home at the end")
    ap.add_argument("--status", action="store_true", help="read-only status dump, then exit")
    ap.add_argument("--control-source", default=None,
                    help="override control_source (default: auto-detect from GetWorkMode)")
    ap.add_argument("--enter-mode", action="store_true",
                    help="call SetAction to enter an arm-capable MC action mode. REQUIRED "
                         "for motion when the MC is in McAction_DEFAULT. Note the default "
                         "target mode leaves the LEG MOTORS UNPOWERED (gantry/stand only).")
    ap.add_argument("--action", default=None,
                    help="action mode to enter with --enter-mode. Default: chosen from the "
                         "robot's current leg state — "
                         f"{ARM_ACTION_WHILE_STANDING} while standing, the PASSIVE "
                         "equivalent when hung/supported.")
    ap.add_argument("--force-passive", action="store_true",
                    help="allow entering a legs-UNPOWERED action even though the robot is "
                         "standing. The robot WILL drop onto its support. Rarely correct.")
    ap.add_argument("--restore-action", action="store_true",
                    help="switch back to the previous action mode when finished")
    ap.add_argument("--execute", action="store_true", help="actually move the robot")
    a = ap.parse_args()

    mc = A2Motion(dry_run=not a.execute, control_source=a.control_source)

    # ---- status / preflight -------------------------------------------------
    if a.status:
        mc.preflight()
        cur, status = mc.get_action()
        capable = cur in ARM_CAPABLE_ACTIONS
        kind = ARM_CAPABLE_ACTIONS.get(cur)
        print(f"\nMC action        : {cur} ({status})")
        print(f"arm interface    : {kind or 'none'}")
        print(f"PlanningMove ok? : {'YES' if kind == 'planner' else 'NO'}")
        if kind != "planner":
            print("  -> PlanningMove needs the 'planner' interface; this mode "
                  f"{'offers only ' + kind if kind else 'accepts no external arm control'}.")
            print("  -> add --enter-mode to switch into a planner action.")
        rec = recommended_arm_action(mc.get_state())
        print("\nArm-capable actions on this robot:")
        avail = set(mc.available_actions())
        for act, kind in ARM_CAPABLE_ACTIONS.items():
            legs = "legs UNPOWERED" if act in LEGS_PASSIVE_ACTIONS else "legs active"
            mark = "available" if act in avail else "NOT AVAIL"
            star = " <- recommended now" if act == rec else ""
            print(f"  {mark}  {act:46s} {kind:8s} {legs}{star}")
        print(f"\ncontrol_source that WOULD be sent: {mc.resolve_control_source()}\n")
        js = mc.joint_state()
        for n in LEFT_ARM + RIGHT_ARM:
            s = js[n]
            print(f"  {n:24s} pos={s['position']:+.4f} rad ({math.degrees(s['position']):+7.2f} deg) "
                  f"vel={s['velocity']:+.3f} eff={s['effort']:+.2f}")
        return 0

    angle = math.radians(a.angle_deg)
    straighten = a.straighten_elbow

    print("=" * 74)
    print(f"A2 dual-arm stretch   arm={a.arm}  target joint1={angle:+.4f} rad "
          f"({a.angle_deg:+.1f} deg)  api={a.api}  vel={a.vel}")
    print("=" * 74)
    mc.preflight()
    if mc.dry_run:
        print("\n*** DRY RUN — nothing will be sent. Add --execute to move the robot. ***")
    else:
        print("\n*** LIVE — the robot WILL move. Keep the E-stop in hand. ***")

    # ---- action mode: the step that makes the motion actually execute --------
    try:
        previous_action = mc.ensure_arm_action(
            a.action, allow_switch=a.enter_mode,
            require_interface="planner", force_passive=a.force_passive)
    except MotionError as e:
        print(f"\nCANNOT MOVE — {e}", file=sys.stderr)
        return 2

    base_left, base_right = capture_base(mc, a.arm)
    group, vec, names, expected = build_target(a.arm, angle, straighten,
                                               base_left, base_right)
    before = mc.positions(names)
    print("\nCaptured start pose (this is also the return target):")
    print("  " + ", ".join(f"{n.split('_')[-1]}={v:+.3f}" for n, v in zip(names, before)))
    deltas = [(n, expected[n] - v) for n, v in zip(names, before) if abs(expected[n] - v) > 1e-3]
    violations = check_bounds(names, [expected[n] for n in names])
    if violations:
        print("\nGOAL OUT OF PLANNER BOUNDS — would be silently discarded:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 3
    print("Joints that will move:")
    for n, d in deltas:
        print(f"  {n:24s} delta={d:+.3f} rad ({math.degrees(d):+6.1f} deg)")

    try:
        # ---- out ----
        if a.api == "planner":
            move_planner(mc, group, vec, a.vel,
                         f"Stretch arms to {a.angle_deg:.0f} deg forward", names, expected)
        else:
            move_joint_api(mc, a.arm, angle, straighten, a.steps, a.dwell,
                           f"Stretch arms to {a.angle_deg:.0f} deg forward")

        if not mc.dry_run:
            time.sleep(0.6)
            print("\n  verify reached pose:")
            mc.verify(expected)
            print(f"\n  holding {a.hold}s ...")
            time.sleep(a.hold)

        # ---- back ----
        if a.stay:
            print("\n--stay given: leaving arms extended.")
            return 0

        hgroup, hvec, hnames, hexpected = home_target(a.arm, base_left, base_right)
        if a.api == "planner":
            move_planner(mc, hgroup, hvec, a.vel, "Return to captured start pose",
                         hnames, hexpected)
        else:
            move_joint_api(mc, a.arm, base_left[0], False, a.steps, a.dwell,
                           "Return to captured start pose", restore=hexpected)

        if not mc.dry_run:
            time.sleep(0.6)
            print("\n  verify returned home:")
            mc.verify(hexpected)

        if a.restore_action and previous_action not in (None, "DRY_RUN"):
            cur, _ = mc.get_action()
            if cur != previous_action:
                print(f"\n>>> Restoring previous action mode: {previous_action}")
                mc.set_action(previous_action)
        print("\nDone.")
        return 0

    except KeyboardInterrupt:
        print("\n!! interrupted -> stopping", file=sys.stderr)
        print(f"   stop result: {mc.safe_stop(hold=expected, group=group)}", file=sys.stderr)
        return 130
    except MotionError as e:
        print(f"\nMOTION ERROR: {e}", file=sys.stderr)
        mc.safe_stop(hold=expected, group=group)
        return 1


if __name__ == "__main__":
    sys.exit(main())
