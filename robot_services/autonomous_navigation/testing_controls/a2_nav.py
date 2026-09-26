#!/usr/bin/env python3
"""
a2_nav.py — drive AgiBot A2 Ultra navigation from YOUR code (no tablet, no TaskMaster).

WHY THIS WORKS WITHOUT THE `agent` APP
--------------------------------------
The thing that actually moves the robot is `pnc` (planning & control, aka vectorflux),
NOT TaskMaster. TaskMaster is only the *mission orchestrator* the tablet uses.

Look at the SM state definition (config/sm/human_T2_SFSM_cfg.yaml):

    "Manual":
      active_FG_list: [ ..., "Motion", ... ]        # "Motion" = [agent, mc, pnc]
      action_list:
        - [ deactivate task_engine TaskMaster cancel, DIFF ]   # only TaskMaster dies
        - [ activate rc ]

Manual kills the MISSION layer, not the NAVIGATION layer. `pnc` stays up, and
PncService answers on the gateway right now with TaskMaster deactivated and `agent`
stopped. That is the whole trick: the Supervisor can own the audio devices (agent
stopped) AND drive navigation at the same time, because they touch different things.

VERIFIED (2026-09-07, agent stopped, TaskMaster deactivated, state=Manual):
    PncService/ActionGetState   -> {"state":"PncServiceState_SUCCESS","info":"task_success"}
    PncService/CalculateNaviTime-> responded
NOT YET VERIFIED: that a Navi*/MoveForward call actually produces motion. Test that
before relying on it (use `forward --execute` with a small distance, E-stop in hand).

TWO CLASSES OF COMMAND — this distinction decides what you can demo
-------------------------------------------------------------------
RELATIVE (needs a valid map_id, but no waypoint):
    MoveForward{angle, distance}, SpinTurn{angle}, SpinTurnAndMoveForward
    -> CORRECTION (measured 2026-09-07): these are NOT map-free. Sending map_id:0
       returns PncServiceState_FAILED / info="get_map_fail" with no motion. They
       resolve against the current working map, so pass a real map_id. Whether they
       also need localization RUNNING is still unverified — test with the robot
       hung on the gantry first.

MAP / WAYPOINT (REQUIRES localization to be RUNNING):
    PlanningNaviToGoal{map_id, target_id}, PreciseNaviToGoal, MobileNaviToPose2D...
    -> as of 2026-09-07 SLAMGetCurrentLocalizationState reports isRunning=false,
       so these CANNOT work until localization is started (see `status`).

THE E-STOP TRAP (root-caused 2026-09-08 -- run `a2_nav.py doctor` first, always)
--------------------------------------------------------------------------------
Pressing the E-stop leaves navigation permanently dead until you fix it BY HAND,
and nothing in the pnc API tells you so. The chain, from the robot's own logs:

  16:31:08.81  hal_ethercat  "abnormal emergency status ... [wireless emergency stop
               signal: true]"                                  <- button pressed, bus cut
  16:31:10.32  mc/security_guard.cpp:144  "HAL data is lost, last publish time is
               1508 ms ago" -> "Disable Work Mode" -> "Call SetAction DEFAULT!"
  16:31:10.46  mc/core.cc:200  "Current Action: DEFAULT, Last Action: RL_LOCOMOTION_DEFAULT"
  16:31:22.84  hal_ethercat  "Emergency stop: False! resume ethercat"   <- button released
  16:31:38.41  mc/security_guard.cpp:151-152  "HAL data is normal" -> "Set Work Mode to Safe"

Read that last line carefully: releasing the E-stop restores the WORK MODE but NOT
the ACTION. The MC sits in McAction_DEFAULT forever. In DEFAULT the legs cannot step,
so every subsequent MoveForward:
  - is accepted (CommonState_SUCCESS),
  - is fully planned (planner_server, BehaviorTree, controller_server all run),
  - emits velocity that the MC discards -- "SecurityGuard: User command
    [x_vel_yaw_frame] is expired" on the MC side,
  - never changes the robot pose (pnc logs the SAME "robot pose: (0.072, 0.012,
    0.429)" every second), so the odometry loop never closes,
  - and therefore reports PncServiceState_RUNNING until you Ctrl-C.

Two traps in the state you see while this is happening:
  - work_state stays McWorkState_ENABLED, so an enable check passes. Check the ACTION.
  - is_walking stays True and walking_speed_estimate keeps a bit-identical value,
    because in DEFAULT no leg controller runs, so the estimator never updates. It is
    a stale latch, not moving legs. `doctor` samples twice to tell the two apart.
    (This is NOT evidence of a HAL/EtherCAT problem -- being in DEFAULT is enough.)

RECOVERY IS TWO HOPS, NOT ONE -- and a single hop FAILS SILENTLY
----------------------------------------------------------------
`SetAction(McAction_RL_LOCOMOTION_DEFAULT)` from DEFAULT returns
CommonState_SUCCESS and does nothing. The MC gates transitions on the graph in
config/motion_control/configuration/robot/raise_a2_t2d0_flagship/action_manager/
default.yaml, which sets `allow_action_indirect_transition: false`; DEFAULT's legal
successors do NOT include any locomotion mode. The rejection is log-only, and the
MC then retries the illegal request every 3 s forever:

  action_ruler.cc:60 [指令非法] 不允许间接切换，请先完成从 DEFAULT 到 RL_JOINT_DEFAULT 的状态迁移

The only route to walking is  DEFAULT -> RL_JOINT_DEFAULT -> RL_LOCOMOTION_DEFAULT,
which is exactly what the tablet sends (two SetActions ~3 s apart, seen in the MC log
from com.agibot.aimmaster.control). `a2_nav.py arm --execute` does this and VERIFIES
each hop with GetAction, because the reply cannot be trusted.

Gateway: http://127.0.0.1:51056/rpc/aimdk.protocol.PncService/<Method>
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

import requests

GW = "http://127.0.0.1:51056/rpc/aimdk.protocol"
PNC = f"{GW}/PncService"
SLAM_LOC = f"{GW}/SLAMLocalizationService"
MAPPING = f"{GW}/MappingService"
LOC = f"{GW}/LocalizationService"
MC = "http://192.168.100.100:56322/rpc/aimdk.protocol"
MC_ACTION = f"{MC}/McActionService"
# The emergency service lives on the MC's HAL port. Do NOT go through the gateway:
# gateway_config.yaml routes ".*aimdk.protocol.Hal.*" to MC_IP:56422, which answers
# with an empty body. 56421 is the port that actually serves EmergencyState.
MC_EMERGENCY = "http://192.168.100.100:56421/rpc/aimdk.protocol.HalEmergencyService"

# Only these MC actions make the legs take steps. In any other action -- above all
# McAction_DEFAULT -- the MC accepts pnc's velocity commands and silently drops them
# (they show up on the MC as "SecurityGuard: User command [x_vel_yaw_frame] is expired").
WALK_ACTIONS = ("LOCOMOTION", "NAVIGATION")
# What the tablet/task_engine uses for navigation on this robot, and what the MC was in
# before the 2026-09-08 E-stop demoted it to DEFAULT.
WALK_ACTION_DEFAULT = "McAction_RL_LOCOMOTION_DEFAULT"


# YOU CANNOT JUMP STRAIGHT FROM DEFAULT TO A WALKING ACTION.
# The MC runs an ActionRuler over this graph with `allow_action_indirect_transition:
# false` (MC: config/motion_control/configuration/robot/raise_a2_t2d0_flagship/
# action_manager/default.yaml), so every SetAction must name a DIRECT neighbour of the
# current action. Ask for a non-neighbour and SetAction still answers
# CommonState_SUCCESS, but the MC only logs, every 3 s, forever:
#   action_ruler.cc:60 [指令非法] 不允许间接切换，请先完成从 DEFAULT 到 RL_JOINT_DEFAULT 的状态迁移
#   ("illegal command: indirect switching not allowed, first complete the
#     DEFAULT -> RL_JOINT_DEFAULT transition")
# The tablet does it in two hops 3 s apart, which is what this mirrors. Only the subset
# needed to get from a post-E-stop DEFAULT back to walking is transcribed here.
ACTION_GRAPH = {
    "McAction_DEFAULT": ["McAction_RL_JOINT_DEFAULT",
                         "McAction_PASSIVE_UPPER_BODY_JOINT_SERVO",
                         "McAction_PASSIVE_UPPER_BODY_PLANNING_MOVE",
                         "McAction_CURLED_UP", "McAction_STAND_UP",
                         "McAction_PACKAGE_SIT"],
    "McAction_RL_JOINT_DEFAULT": ["McAction_RL_LOCOMOTION_DEFAULT",
                                  "McAction_RL_LOCOMOTION_ARM_EXT_JOINT_SERVO",
                                  "McAction_RL_LOCOMOTION_ARM_EXT_PLANNING_MOVE",
                                  "McAction_RL_LOCOMOTION_EXT_ONLINE_PLANNING",
                                  "McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO",
                                  "McAction_RL_WHOLE_BODY_EXT_ONLINE_PLANNING"],
    "McAction_RL_LOCOMOTION_DEFAULT": ["McAction_RL_JOINT_DEFAULT",
                                       "McAction_RL_LOCOMOTION_ARM_EXT_JOINT_SERVO",
                                       "McAction_RL_LOCOMOTION_ARM_EXT_PLANNING_MOVE",
                                       "McAction_RL_LOCOMOTION_EXT_ONLINE_PLANNING",
                                       "McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO",
                                       "McAction_RL_WHOLE_BODY_EXT_ONLINE_PLANNING"],
    # The teleop/servo modes the RC and standing-mode drop the robot into. They reach
    # locomotion only back through RL_JOINT_DEFAULT.
    "McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO": ["McAction_RL_JOINT_DEFAULT",
                                               "McAction_RL_LOCOMOTION_DEFAULT"],
    "McAction_RL_WHOLE_BODY_EXT_ONLINE_PLANNING": ["McAction_RL_JOINT_DEFAULT",
                                                   "McAction_RL_LOCOMOTION_DEFAULT"],
    "McAction_RL_LOCOMOTION_ARM_EXT_JOINT_SERVO": ["McAction_RL_JOINT_DEFAULT",
                                                   "McAction_RL_LOCOMOTION_DEFAULT"],
    "McAction_RL_LOCOMOTION_ARM_EXT_PLANNING_MOVE": ["McAction_RL_JOINT_DEFAULT",
                                                     "McAction_RL_LOCOMOTION_DEFAULT"],
    "McAction_RL_LOCOMOTION_EXT_ONLINE_PLANNING": ["McAction_RL_JOINT_DEFAULT",
                                                   "McAction_RL_LOCOMOTION_DEFAULT"],
}

TERMINAL = {"PncServiceState_SUCCESS": True,
            "PncServiceState_FAILED": False,
            "PncServiceState_FAILURE": False,
            "PncServiceState_CANCELED": False,
            "PncServiceState_CANCELLED": False,
            "PncServiceState_TIMEOUT": False}


class NavError(RuntimeError):
    pass


def post(url: str, payload: dict, timeout: float = 10.0) -> dict:
    r = requests.post(url, headers={"Content-Type": "application/json"},
                      data=json.dumps(payload), timeout=timeout)
    if r.status_code != 200:
        raise NavError(f"{url.rsplit('/', 1)[-1]} -> HTTP {r.status_code}: {r.text[:200]}")
    out = r.json()
    code = str(out.get("header", {}).get("code", "0"))
    if code not in ("0", "None"):
        raise NavError(f"{url.rsplit('/', 1)[-1]} -> code {code}: "
                       f"{out.get('header', {}).get('msg')}")
    return out


def new_task_id() -> int:
    return random.getrandbits(63)


# ------------------------------------------------------------------ read-only

def localization_running() -> bool:
    return bool(post(f"{SLAM_LOC}/SLAMGetCurrentLocalizationState", {"header": {}})
                .get("isRunning", False))


def current_map() -> str | None:
    d = post(f"{MAPPING}/GetCurrentWorkingMap", {"header": {}}).get("data", {})
    return d.get("map_id")


def stored_maps() -> list[dict]:
    return post(f"{MAPPING}/GetStoredMapNames", {"header": {}}) \
        .get("data", {}).get("map_lists", [])


def waypoints(map_id: str | int) -> list[dict]:
    out = post(f"{LOC}/GetTopoMsgs", {"header": {}, "map_id": int(map_id)})
    return out.get("data", {}).get("points", [])


def action_state() -> dict:
    return post(f"{PNC}/ActionGetState", {"header": {}})


def mc_state() -> dict:
    return post(f"{MC}/McBaseService/GetState", {}).get("state", {})


def mc_action() -> tuple[str, str]:
    info = post(f"{MC_ACTION}/GetAction", {"header": {}}).get("info", {})
    return info.get("current_action", "UNKNOWN"), info.get("status", "UNKNOWN")


def mc_available_actions() -> list[str]:
    out = post(f"{MC_ACTION}/GetAvailableCommands", {"header": {}})
    return [c.get("action", "") for c in out.get("commands", [])]


def can_walk(action: str) -> bool:
    return any(k in action for k in WALK_ACTIONS)


def plan_action_path(start: str, goal: str) -> list[str]:
    """Shortest legal chain of SetAction hops from `start` to `goal`.

    Returns the hops to SEND (excluding `start`), or [] if start == goal.
    Raises if no route exists in the transcribed subset of the graph.
    """
    if start == goal:
        return []
    seen, queue = {start}, [(start, [])]
    while queue:
        node, path = queue.pop(0)
        for nxt in ACTION_GRAPH.get(node, []):
            if nxt in seen:
                continue
            if nxt == goal:
                return path + [nxt]
            seen.add(nxt)
            queue.append((nxt, path + [nxt]))
    raise NavError(
        f"no legal SetAction route from {start} to {goal} in the transition graph.\n"
        f"    Check the MC's own table: config/motion_control/configuration/robot/\n"
        f"    raise_a2_t2d0_flagship/action_manager/default.yaml")


def set_action(action: str, wait: float = 15.0, poll: float = 0.5) -> str:
    """SetAction and VERIFY it took effect.

    Never trust the response. SetAction returns CommonState_SUCCESS for a request the
    ActionRuler goes on to reject, so the only proof is GetAction reporting the new
    action as current. Measured 2026-09-08: an illegal request is also RETRIED by the
    MC every 3 s indefinitely, so a rejected request stays pending -- which is why
    this raises instead of shrugging.
    """
    post(f"{MC_ACTION}/SetAction",
         {"header": {"control_source": "ControlSource_AUTO"},
          "command": {"action": action, "ext_action": ""}})
    deadline = time.monotonic() + wait
    cur = status = "UNKNOWN"
    while time.monotonic() < deadline:
        cur, status = mc_action()
        if cur == action and status in ("McActionStatus_RUNNING", "McActionStatus_DONE"):
            return cur
        time.sleep(poll)
    raise NavError(
        f"SetAction({action}) returned success but the MC is still {cur}/{status} "
        f"after {wait}s.\n"
        f"    The ActionRuler is rejecting it. Check the MC log for 'action_ruler.cc' /\n"
        f"    '不允许间接切换' (illegal indirect transition) to see which hop it wants first.")


def emergency_state() -> dict:
    """Live E-stop state straight off the MC's HAL.

    NOTE on reading this: EmergencyState is proto3, so every false/zero field is
    omitted from the JSON. A response of {} therefore means "nothing is asserted"
    -- that is the healthy reading, not a broken service.
    """
    return post(f"{MC_EMERGENCY}/GetEmergencyState", {"header": {}}).get("data", {}) \
        or post(f"{MC_EMERGENCY}/GetEmergencyState", {"header": {}})


def mc_telemetry_frozen(gap: float = 1.5) -> tuple[bool, dict, dict]:
    """Sample GetState twice and see whether the leg estimator is still updating.

    A frozen estimator means NO LEG CONTROLLER IS RUNNING. Being in McAction_DEFAULT
    is enough to cause it on its own -- verified 2026-09-08, where the MC logged
    "HAL data is normal" at 17:43:13 and the estimator was still bit-frozen hours
    later, purely because the action was DEFAULT. So do NOT read this as "the
    EtherCAT bus is cut"; read it as "the legs are not being driven".

    Either way `is_walking` is stuck at whatever it was when the controller stopped,
    which is why is_walking=True persists on a robot whose legs are completely
    still. It is a stale latch, not live state.
    """
    a = mc_state()
    time.sleep(gap)
    b = mc_state()
    va, vb = a.get("walking_speed_estimate", {}), b.get("walking_speed_estimate", {})
    return (va == vb and va != {}), a, b


def mc_log_action_resets(limit: int = 6) -> list[str]:
    """Best-effort: pull the MC's own record of who last forced the action to DEFAULT.

    The MC log is the only place that says WHY the action changed. The line to look
    for is security_guard.cpp's "Call SetAction DEFAULT!", which the HAL-lost
    watchdog emits ~1.5 s after the E-stop cuts the bus.
    """
    import re
    import subprocess
    # -h so the filename does not eat the line, and one grep per file so that a
    # chatty log cannot crowd the other one out of the tail.
    cmd = (
        "grep -ahE 'Call SetAction DEFAULT|Current Action:' "
        f"/agibot/log/mc/motion_control.log 2>/dev/null | tail -{limit}; "
        "grep -ahE 'emergency quick stop|Emergency stop: ' "
        f"/agibot/log/hal_ethercat/hal_ethercat.log 2>/dev/null | tail -{limit}")
    try:
        out = subprocess.run(
            ["ssh", "-i", os.path.expanduser("~/.ssh/agibot_rsa"),
             "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5", "agi@192.168.100.100", cmd],
            capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    lines = []
    for l in out.stdout.splitlines():
        if not l.strip():
            continue
        # "[ts][Info][pid][Module][very/long/path.cc:123 @Fn]message" -> "ts  message"
        ts = l[1:24] if l.startswith("[") else ""
        msg = re.sub(r"^(\[[^]]*\])+", "", l).strip()
        msg = re.sub(r"^external/\S+?[)\]]\s*:?\s*", "", msg)
        lines.append(f"{ts}  {msg[:110]}")
    return sorted(lines)


def cmd_status(_args) -> int:
    print("=" * 72)
    loc = localization_running()
    mid = current_map()
    print(f"localization running : {loc}     <-- map/waypoint nav needs this True")
    print(f"current working map  : {mid}")
    try:
        st = mc_state()
        print(f"MC work_state        : {st.get('work_state')}")
        print(f"MC action            : {st.get('action_info', {}).get('current_action')}")
        print(f"walking / collided   : {st.get('is_walking')} / {st.get('is_collisioned')}")
    except Exception as e:
        print(f"MC state             : unavailable ({e})")
    try:
        a = action_state()
        print(f"pnc last task        : id={a.get('task_id')} state={a.get('state')} "
              f"info={a.get('info')}")
    except Exception as e:
        print(f"pnc action state     : unavailable ({e})")

    print("\nstored maps:")
    for m in stored_maps():
        mark = " <-- current" if str(m.get("map_id")) == str(mid) else ""
        print(f"  id={m.get('map_id')}  index={m.get('map_index')}  "
              f"name={m.get('map_name')!r}{mark}")

    if mid:
        wps = waypoints(mid)
        print(f"\nwaypoints in current map ({len(wps)}):")
        for w in wps:
            p = w.get("pose", {}).get("position", {})
            print(f"  target_id={w.get('point_id')}  name={w.get('name')!r}  "
                  f"type={w.get('point_type')}  "
                  f"xy=({p.get('x')},{p.get('y')})")
        if len(wps) < 2:
            print("  [!] fewer than 2 waypoints — record more (tablet, or "
                  "LocalizationService/SetNaviPoint) before a point-to-point demo.")
    if not loc:
        print("\n[!] Localization is NOT running. Relative moves (forward/turn) will "
              "work;\n    waypoint nav (goto) will not. Relocalize first — the tablet's "
              "\n    human-in-the-loop relocalization is the reliable path.")
    print("=" * 72)
    return 0


# ------------------------------------------------------------------ diagnosis

def cmd_doctor(_args) -> int:
    """Answer the question "pnc says RUNNING, so why is nothing moving?"

    Checks the whole command path in the order it can break, and stops guessing at
    the first FATAL. The failure this was written for (2026-09-08): pressing the
    E-stop makes the MC's HAL-lost watchdog force the action to McAction_DEFAULT,
    and RELEASING the E-stop restores the work mode but NOT the action. pnc then
    plans, controls and reports RUNNING into a void forever.
    """
    print("=" * 72)
    fatal: list[str] = []
    warn: list[str] = []

    # 1. E-stop. Outermost gate; everything below is meaningless if this is asserted.
    try:
        em = emergency_state()
        if em.get("active") or em.get("wired_emergency_stop") or \
                em.get("wireless_emergency_stop") or em.get("software_emergency_stop"):
            fatal.append("E-STOP IS ASSERTED RIGHT NOW: " + json.dumps(em))
            print(f"E-stop               : ASSERTED  {json.dumps(em)}")
        else:
            print("E-stop               : clear  (empty reply = no flags set)")
    except Exception as e:
        warn.append(f"could not read EmergencyState on :56421 ({e})")
        print(f"E-stop               : UNKNOWN ({e})")

    # 2. MC action gate. THIS is the one that bites after an E-stop.
    try:
        act, act_status = mc_action()
        print(f"MC action            : {act} ({act_status})")
        if not can_walk(act):
            fatal.append(
                f"MC action is {act} -- the legs CANNOT step in this action. pnc will\n"
                f"       still accept the task, plan a path, run the controller and report\n"
                f"       PncServiceState_RUNNING forever, because it closes the loop on\n"
                f"       odometry that never changes. This is the single most common cause\n"
                f"       of 'accepted but nothing moves', and an E-stop press is what puts\n"
                f"       the MC here (see the MC log section below).")
    except Exception as e:
        act = "UNKNOWN"
        fatal.append(f"could not read MC action ({e})")

    # 3. Is the MC's leg telemetry even live? A frozen estimator means the EtherCAT
    #    bus was cut (E-stop) and is_walking / walking_speed_estimate are stale.
    try:
        frozen, s_a, s_b = mc_telemetry_frozen()
        print(f"MC work_state        : {s_b.get('work_state')}")
        print(f"MC init_state        : {s_b.get('robot_init_state')}")
        print(f"is_walking           : {s_b.get('is_walking')}"
              f"   <-- STALE if telemetry is frozen, see next line")
        print(f"leg telemetry        : {'FROZEN' if frozen else 'live'}  "
              f"walking_speed_estimate={json.dumps(s_b.get('walking_speed_estimate'))}")
        if frozen:
            warn.append(
                "walking_speed_estimate is bit-identical across two samples 1.5 s apart,\n"
                "       so the MC's leg estimator is NOT updating. Any is_walking=True here\n"
                "       is a latched value from the instant the bus was cut, NOT moving legs.")
        if s_b.get("work_state") != "McWorkState_ENABLED":
            fatal.append(f"work_state is {s_b.get('work_state')}, not McWorkState_ENABLED")
        if s_b.get("is_collisioned"):
            fatal.append("MC reports is_collisioned=true")
        if s_b.get("robot_init_state") == "McRobotInitState_HANGING":
            warn.append("robot is HANGING on the gantry: it cannot translate, so any "
                        "distance/goal\n       move closes the loop on odometry that never "
                        "reaches the goal and steps forever. Use a short --timeout.")
    except Exception as e:
        fatal.append(f"could not read MC state ({e})")

    # 4. Nav-side prerequisites (these fail LOUDLY, unlike the action gate).
    try:
        loc, mid = localization_running(), current_map()
        print(f"localization running : {loc}")
        print(f"current working map  : {mid}")
        if not loc:
            warn.append("localization is not running: 'goto' is impossible, and relative "
                        "moves fail with info='update_map_failed'.")
        if not mid or int(mid) == 0:
            fatal.append("no current working map -- relative moves fail with "
                         "info='get_map_fail'.")
    except Exception as e:
        warn.append(f"could not read localization/map ({e})")

    try:
        a = action_state()
        print(f"pnc last task        : id={a.get('task_id')} state={a.get('state')} "
              f"info={a.get('info')}")
    except Exception as e:
        warn.append(f"could not read pnc ActionGetState ({e})")

    # 5. The MC's own account of why the action changed. This is the receipt.
    lines = mc_log_action_resets()
    if lines:
        print("\nMC log (E-stop presses and action changes, oldest first):")
        for l in lines:
            print(f"  {l}")
    else:
        print("\nMC log               : unavailable (ssh to 192.168.100.100 failed)")

    print()
    for w in warn:
        print(f"[warn]  {w}")
    for f in fatal:
        print(f"[FATAL] {f}")

    if not fatal:
        print("\nVERDICT: no blocking condition found. A move should produce motion.")
    elif not can_walk(act):
        print(f"\nVERDICT: the MC action gate is closed ({act}).")
        try:
            hops = plan_action_path(act, WALK_ACTION_DEFAULT)
            print(f"Legal route back: {act} -> " + " -> ".join(hops))
            if len(hops) > 1:
                print(f"NOTE: {len(hops)} hops. A single SetAction straight to "
                      f"{WALK_ACTION_DEFAULT} will\n"
                      f"      return CommonState_SUCCESS and then be silently rejected --"
                      f" the MC has\n"
                      f"      allow_action_indirect_transition: false, and only logs "
                      f"'action_ruler.cc:60\n"
                      f"      不允许间接切换' (illegal indirect transition) every 3 s.")
        except NavError as e:
            print(f"Legal route back: UNKNOWN -- {e}")
        print("\nRecover with (POWERS THE LEG MOTORS -- robot starts balancing; clear the")
        print("area / confirm the gantry takes the weight, and keep the E-stop in hand):")
        print("  python a2_nav.py arm            # dry-run, prints the hops")
        print("  python a2_nav.py arm --execute  # walks the graph, verifying each hop")
        print("Then re-run `a2_nav.py doctor`; MC action should show a LOCOMOTION mode")
        print("and leg telemetry should read 'live'.")
    else:
        print("\nVERDICT: blocked -- see the FATAL lines above.")
    print("=" * 72)
    return 1 if fatal else 0


def cmd_arm(args) -> int:
    """Walk the MC's action graph back into a walking mode, one legal hop at a time.

    This is the recovery for the E-stop trap. A single SetAction to a walking action
    CANNOT work from McAction_DEFAULT -- see ACTION_GRAPH. The tablet does the same
    two hops (RL_JOINT_DEFAULT, then RL_LOCOMOTION_DEFAULT, ~3 s apart); this just
    does it without the tablet, and verifies each hop instead of trusting the reply.
    """
    goal = args.target
    cur, status = mc_action()
    print(f"[arm] MC action now : {cur} ({status})")
    if cur == goal:
        print(f"[arm] already in {goal} -- nothing to do.")
        return 0

    hops = plan_action_path(cur, goal)
    print(f"[arm] legal route   : {cur} -> " + " -> ".join(hops))
    intermediate = [h for h in hops[:-1]]
    if intermediate:
        print(f"[arm] {len(hops)} hops required. A direct SetAction to {goal} would return\n"
              f"      CommonState_SUCCESS and then be silently rejected by the ActionRuler.")

    st = mc_state()
    hanging = st.get("robot_init_state") == "McRobotInitState_HANGING"
    print("\n*** THIS POWERS THE LEG MOTORS. The robot will begin actively balancing. ***")
    if hanging:
        print("    robot_init_state=McRobotInitState_HANGING: confirm the gantry is")
        print("    carrying the full weight before you run this.")
    else:
        print(f"    robot_init_state={st.get('robot_init_state')}: the robot is NOT hanging.")
        print("    Clear the area around it.")
    print("    Keep the E-stop in hand. Note that pressing it puts you right back in")
    print("    McAction_DEFAULT and you will have to run this again.\n")

    if not args.execute:
        for h in hops:
            print(f"    [dry-run] SetAction -> {h}")
        print("\n    Nothing sent. Add --execute to actually change the action.")
        return 0

    for h in hops:
        print(f"    SetAction -> {h} ...", flush=True)
        print(f"      confirmed: {set_action(h)}")
        # The tablet leaves ~3 s between hops; the RL controller needs to settle
        # before the next transition is legal in practice as well as on paper.
        if h != hops[-1]:
            time.sleep(3.0)

    frozen, _, s = mc_telemetry_frozen()
    print(f"\n[arm] MC action now : {mc_action()[0]}")
    print(f"[arm] leg telemetry : {'STILL FROZEN' if frozen else 'live'}  "
          f"is_walking={s.get('is_walking')}")
    if frozen:
        print("[arm] [!] the estimator is still not updating -- the legs are not being "
              "driven.\n      Do not send a move yet; re-run `doctor`.")
        return 1
    print("[arm] ready. `a2_nav.py doctor` should now be clean.")
    return 0


# ------------------------------------------------------------------ motion

def wait_done(timeout: float, poll: float = 0.5, verbose: bool = True) -> str:
    """Poll ActionGetState until terminal."""
    deadline = time.monotonic() + timeout
    last = "?"
    while time.monotonic() < deadline:
        a = action_state()
        last = a.get("state", "?")
        if verbose:
            print(f"    {last}  ({a.get('info')})")
        if last in TERMINAL:
            if TERMINAL[last]:
                return last
            raise NavError(f"navigation ended as {last}: {a.get('info')}")
        time.sleep(poll)
    return f"{last} (timeout after {timeout}s)"


RUNNING_TASK_ID: int | None = None      # tracked so Ctrl-C can cancel correctly


def running_task_id() -> int | None:
    """Ask pnc which task it is running. ActionGetState echoes the real task_id."""
    try:
        a = action_state()
        tid = int(a.get("task_id", 0) or 0)
        return tid or None
    except Exception:
        return None


def cancel_task(task_id: int | None = None, verify: float = 8.0) -> str:
    """Cancel the running nav task and VERIFY the robot actually stopped.

    CRITICAL: ActionCancel requires the real task_id. Sending 0 is rejected with
        onActionCancelRequest: task_id_ : <id> != req.task_id() : 0
    and the robot KEEPS WALKING. This cost a real E-stop on 2026-09-08.
    """
    tid = task_id or RUNNING_TASK_ID or running_task_id()
    if tid is None:
        return "nothing running"
    for attempt in range(3):
        try:
            post(f"{PNC}/ActionCancel", {"header": {}, "task_id": int(tid)})
        except NavError as e:
            print(f"    cancel attempt {attempt+1} error: {e}", file=sys.stderr)
        deadline = time.monotonic() + verify
        while time.monotonic() < deadline:
            a = action_state()
            st = a.get("state", "?")
            if st in ("PncServiceState_IDLE", "PncServiceState_SUCCESS",
                      "PncServiceState_FAILED", "PncServiceState_FAILURE"):
                walking = mc_state().get("is_walking")
                print(f"    cancelled: pnc={st} info={a.get('info')} "
                      f"mc.is_walking={walking}")
                if walking:
                    print("    [!] pnc stopped but MC still reports is_walking=True. "
                          "Watch the robot; use the E-stop if the legs keep moving.",
                          file=sys.stderr)
                return st
            time.sleep(0.3)
        print(f"    cancel not yet effective (state={st}), retrying...", file=sys.stderr)
    return "CANCEL FAILED — USE THE E-STOP"


def send(method: str, body: dict, args, label: str, wait: float) -> int:
    body = {"header": {}, "task_id": new_task_id(), **body}
    print(f"\n>>> {label}")
    print(f"    POST {PNC}/{method}")
    print(f"    {json.dumps(body)}")
    if not args.execute:
        print("    [dry-run] not sent. Add --execute to move the robot.")
        return 0
    global RUNNING_TASK_ID
    RUNNING_TASK_ID = int(body["task_id"])
    out = post(f"{PNC}/{method}", body)
    print(f"    accepted: task_id={out.get('task_id')} state={out.get('state')}")
    print(f"    waiting (up to {wait}s; Ctrl-C cancels):")
    try:
        res = wait_done(wait)
    except NavError:
        RUNNING_TASK_ID = None
        raise
    print(f"    -> {res}")
    # A timeout means the task is STILL RUNNING. Never leave it walking.
    if "timeout" in res:
        print("    timeout -> auto-cancelling so the robot does not keep walking", file=sys.stderr)
        print(f"    {cancel_task()}")
    RUNNING_TASK_ID = None
    return 0


def preflight(args, need_localization: bool) -> None:
    st = mc_state()
    if st.get("work_state") != "McWorkState_ENABLED":
        raise NavError(f"robot not enabled (work_state={st.get('work_state')})")
    if st.get("is_collisioned"):
        raise NavError("MC reports is_collisioned=true — clear it first")
    act = st.get("action_info", {}).get("current_action", "")
    print(f"[nav] MC action={act}  walking={st.get('is_walking')}  "
          f"init_state={st.get('robot_init_state')}")
    if st.get("robot_init_state") == "McRobotInitState_HANGING":
        print("[nav] [!] Robot is HANGING on the gantry. Distance/goal moves close the "
              "loop on\n      ODOMETRY, so the robot can never reach the goal and will "
              "step FOREVER.\n      Use a short --timeout (it now auto-cancels) and keep "
              "the E-stop in hand.")
    if st.get("is_walking"):
        print("[nav] [!] MC already reports is_walking=True before we send anything. "
              "This is often\n      a STALE latch rather than moving legs -- "
              "`a2_nav.py doctor` tells the two apart.")
    # Hard stop, not a warning. Measured 2026-09-08: in a non-walking action the MC
    # drops pnc's velocity commands, the robot never moves, and pnc reports
    # PncServiceState_RUNNING until you Ctrl-C. Sending the task is pure downside.
    if not can_walk(act) and not getattr(args, "force", False):
        raise NavError(
            f"MC action is {act} -- the legs cannot step in this action, so this move\n"
            f"    would report RUNNING forever without moving.\n"
            f"    Fix it:  python a2_nav.py arm --execute     (then re-run doctor)\n"
            f"    Diagnose: python a2_nav.py doctor\n"
            f"    To send the move anyway, pass the --force flag.")
    if need_localization and not localization_running():
        raise NavError(
            "localization is NOT running — waypoint navigation cannot work.\n"
            "    Relocalize first (tablet), or use relative moves: forward / turn.")


def resolve_map(args) -> int:
    """MoveForward/SpinTurn validate map_id — passing 0 returns "get_map_fail".
    Fall back to the MC's current working map."""
    mid = args.map_id or current_map()
    if not mid or int(mid) == 0:
        raise NavError("no usable map_id (pass --map-id, or set a current working map). "
                       "map_id=0 is rejected by pnc with 'get_map_fail'.")
    return int(mid)


def cmd_forward(args) -> int:
    preflight(args, need_localization=False)
    return send("MoveForward",
                {"map_id": resolve_map(args),
                 "angle": float(args.angle), "distance": float(args.distance)},
                args, f"MoveForward {args.distance} m (heading offset {args.angle} rad)",
                args.timeout)


def cmd_turn(args) -> int:
    preflight(args, need_localization=False)
    return send("SpinTurn",
                {"map_id": resolve_map(args), "angle": float(args.angle)},
                args, f"SpinTurn {args.angle} rad", args.timeout)


def cmd_goto(args) -> int:
    preflight(args, need_localization=True)
    mid = args.map_id or current_map()
    if not mid:
        raise NavError("no map_id given and no current working map")
    ids = [str(w.get("point_id")) for w in waypoints(mid)]
    if str(args.target_id) not in ids:
        raise NavError(f"target_id {args.target_id} not in map {mid}; available: {ids}")
    return send("PlanningNaviToGoal",
                {"map_id": int(mid), "target_id": int(args.target_id),
                 "guide_line_id": int(args.guide_line_id)},
                args, f"PlanningNaviToGoal map={mid} target={args.target_id}",
                args.timeout)


def cmd_cancel(args) -> int:
    tid = running_task_id()
    print(f">>> ActionCancel (running task_id={tid})")
    if tid is None:
        print("    nothing is running")
        return 0
    if not args.execute:
        print("    [dry-run] would cancel task", tid)
        return 0
    print(f"    {cancel_task(tid)}")
    return 0


def cmd_pause(args) -> int:
    m = "ActionResume" if args.resume else "ActionPause"
    print(f">>> {m}")
    if not args.execute:
        print("    [dry-run]")
        return 0
    print("   ", post(f"{PNC}/{m}", {"header": {}, "task_id": 0}))
    return 0


def main() -> int:
    # Global flags are attached BOTH to the top-level parser and (via `parents`) to
    # every subcommand, so "a2_nav.py --execute forward 0.3" and
    # "a2_nav.py forward 0.3 --execute" both work. The subcommand copies use
    # default=SUPPRESS so that omitting them does not clobber a value given
    # before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--execute", action="store_true", default=argparse.SUPPRESS,
                        help="actually move the robot (default: dry-run)")
    common.add_argument("--map-id", default=argparse.SUPPRESS)
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
    common.add_argument("--force", action="store_true", default=argparse.SUPPRESS,
                        help="send the move even though the MC action cannot walk")

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--execute", action="store_true", default=False,
                   help="actually move the robot (default: dry-run)")
    p.add_argument("--map-id", default=None)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--force", action="store_true", default=False,
                   help="send the move even though the MC action cannot walk")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", parents=[common],
                   help="read-only: localization, map, waypoints, pnc state") \
        .set_defaults(func=cmd_status)

    sub.add_parser("doctor", parents=[common],
                   help="read-only: WHY is nothing moving? checks E-stop, the MC "
                        "action gate, telemetry freshness, map/localization") \
        .set_defaults(func=cmd_doctor)

    ar = sub.add_parser("arm", parents=[common],
                        help="recover the MC action gate into a walking mode "
                             "(POWERS THE LEGS); dry-run unless --execute")
    ar.add_argument("--target", default=WALK_ACTION_DEFAULT,
                    help=f"action to end up in (default {WALK_ACTION_DEFAULT})")
    ar.set_defaults(func=cmd_arm)

    f = sub.add_parser("forward", parents=[common],
                       help="relative move (no map/localization needed)")
    f.add_argument("distance", type=float)
    f.add_argument("--angle", type=float, default=0.0, help="heading offset, rad")
    f.set_defaults(func=cmd_forward)

    t = sub.add_parser("turn", parents=[common],
                       help="spin in place (no map/localization needed)")
    t.add_argument("angle", type=float, help="radians, +ve = left/CCW")
    t.set_defaults(func=cmd_turn)

    g = sub.add_parser("goto", parents=[common],
                       help="navigate to a saved waypoint (NEEDS localization)")
    g.add_argument("target_id", type=int)
    g.add_argument("--guide-line-id", type=int, default=0)
    g.set_defaults(func=cmd_goto)

    sub.add_parser("cancel", parents=[common],
                   help="cancel the running nav action").set_defaults(func=cmd_cancel)
    pa = sub.add_parser("pause", parents=[common], help="pause / resume")
    pa.add_argument("--resume", action="store_true")
    pa.set_defaults(func=cmd_pause)

    a = p.parse_args()
    try:
        return a.func(a)
    except NavError as e:
        print(f"\nNAV ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n!! interrupted -> cancelling the running task", file=sys.stderr)
        try:
            print(f"   {cancel_task()}", file=sys.stderr)
        except Exception as e:
            print(f"   cancel failed: {e} -- USE THE E-STOP", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
