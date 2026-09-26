#!/usr/bin/env python3
"""Shared helpers for G1 high-level locomotion scripts."""
import json
import sys
import time
from typing import Any

from unitree_sdk2py.comm.motion_switcher.motion_switcher_client import MotionSwitcherClient
from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient

AI_MOTION_MODE = "ai"
RPC_ERROR_NAMES = {
    0: "OK",
    3102: "RPC_ERR_CLIENT_SEND",
    3103: "RPC_ERR_CLIENT_API_NOT_REG",
    3104: "RPC_ERR_CLIENT_API_TIMEOUT",
    3105: "RPC_ERR_CLIENT_API_NOT_MATCH",
    3106: "RPC_ERR_CLIENT_API_DATA",
    3203: "RPC_ERR_SERVER_API_NOT_IMPL",
    3204: "RPC_ERR_SERVER_API_PARAMETER",
    7301: "UT_ROBOT_LOCO_ERR_LOCOSTATE_NOT_AVAILABLE",
    7302: "UT_ROBOT_LOCO_ERR_INVALID_FSM_ID",
    7303: "UT_ROBOT_LOCO_ERR_INVALID_TASK_ID",
    7304: "FSM_TRANSITION_NOT_OBSERVED",
}

ROBOT_API_ID_LOCO_SET_SPEED_MODE = 7107
LOCK_STANDING_FSM_ID = 4
WALK_MOTION_FSM_ID = 801
WALK_MOTION_FSM_IDS = (WALK_MOTION_FSM_ID,)
RUN_FSM_IDS = (WALK_MOTION_FSM_ID,)
INVALID_FSM_CODES = {7302, 3204}
FSM_RETRY_CODES = INVALID_FSM_CODES | {7304}


def error_name(code: int) -> str:
    return RPC_ERROR_NAMES.get(code, f"code {code}")


def parse_json_data(data: Any) -> Any:
    if not isinstance(data, str):
        return data
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def motion_switcher_client(timeout: float = 5.0) -> MotionSwitcherClient:
    client = MotionSwitcherClient()
    client.SetTimeout(timeout)
    client.Init()
    return client


def loco_client(timeout: float = 10.0) -> LocoClient:
    client = LocoClient()
    client.SetTimeout(timeout)
    client.Init()
    return client


def check_motion_mode(client: MotionSwitcherClient) -> tuple[int, str]:
    code, result = client.CheckMode()
    name = result.get("name", "") if (code == 0 and result) else ""
    return code, name


def ensure_ai_motion_mode(client: MotionSwitcherClient) -> None:
    """Keep high-level sport control online.

    Releasing the active MotionSwitcher mode puts the G1 into debug/development
    mode. In that state the sport service does not answer high-level locomotion
    RPCs, so SetFsmId/SetVelocity calls fail with client send/timeout errors.
    """
    code, current = check_motion_mode(client)
    print(f"Current MotionSwitcher mode: '{current}' (CheckMode {error_name(code)})")

    if code != 0:
        raise RuntimeError(f"CheckMode failed: {error_name(code)}")

    if current == AI_MOTION_MODE:
        return

    if current:
        print(
            f"WARNING: Active mode is '{current}', not '{AI_MOTION_MODE}'. "
            "Leaving it active and attempting sport command without ReleaseMode()."
        )
        return

    print("No active MotionSwitcher mode; selecting 'ai' to restore high-level sport control...")
    select_code, _ = client.SelectMode(AI_MOTION_MODE)
    if select_code != 0:
        raise RuntimeError(f"SelectMode('{AI_MOTION_MODE}') failed: {error_name(select_code)}")

    for _ in range(10):
        time.sleep(0.5)
        code, current = check_motion_mode(client)
        if code == 0 and current == AI_MOTION_MODE:
            print("MotionSwitcher 'ai' mode active.")
            return

    raise RuntimeError("Timed out waiting for MotionSwitcher 'ai' mode to become active.")


def set_speed_mode(client: LocoClient, speed_mode: int) -> int:
    """Call the newer G1 speed-mode API even if the installed Python SDK lacks it."""
    client._RegistApi(ROBOT_API_ID_LOCO_SET_SPEED_MODE, 0)
    parameter = json.dumps({"data": speed_mode})
    code, _ = client._Call(ROBOT_API_ID_LOCO_SET_SPEED_MODE, parameter)
    return code


def get_fsm_id(client: LocoClient) -> tuple[int, int | None]:
    code, data = client._Call(7001, "{}")
    parsed = parse_json_data(data)
    if code == 0 and isinstance(parsed, dict):
        try:
            return code, int(parsed["data"])
        except (KeyError, TypeError, ValueError):
            return code, None
    return code, None


def get_fsm_mode(client: LocoClient) -> tuple[int, int | None]:
    code, data = client._Call(7002, "{}")
    parsed = parse_json_data(data)
    if code == 0 and isinstance(parsed, dict):
        try:
            return code, int(parsed["data"])
        except (KeyError, TypeError, ValueError):
            return code, None
    return code, None


def require_static_mode(client: LocoClient) -> None:
    code, fsm_mode = get_fsm_mode(client)
    if code != 0:
        raise RuntimeError(f"GetFsmMode failed: {error_name(code)}")
    if fsm_mode != 0:
        raise RuntimeError(
            f"Robot is not static (FSM mode={fsm_mode}); refusing mode switch. "
            "Send Damp only, or wait until the robot is static."
        )


def require_supported_source_fsm(client: LocoClient, allowed: set[int], target: str) -> None:
    code, fsm_id = get_fsm_id(client)
    if code != 0:
        raise RuntimeError(f"GetFsmId failed: {error_name(code)}")
    if fsm_id not in allowed:
        allowed_text = ", ".join(str(value) for value in sorted(allowed))
        raise RuntimeError(
            f"{target} should be entered from FSM {allowed_text}; "
            f"current FSM is {fsm_id}. Use Locked Standing first."
        )


def set_fsm_and_wait(
    client: LocoClient,
    fsm_id: int,
    label: str,
    *,
    timeout_seconds: float = 8.0,
) -> tuple[int, int | None]:
    print(f"Sending {label} command (FSM ID {fsm_id})...")
    code = client.SetFsmId(fsm_id)
    if code != 0:
        return code, None

    deadline = time.monotonic() + timeout_seconds
    last_seen: int | None = None
    while time.monotonic() < deadline:
        time.sleep(0.5)
        state_code, current = get_fsm_id(client)
        if state_code == 0:
            last_seen = current
            print(f"  Current FSM ID: {current}")
            if current == fsm_id:
                return 0, current
        else:
            print(f"  FSM query returned {error_name(state_code)}")

    return 7304, last_seen


def set_first_available_fsm_and_wait(
    client: LocoClient,
    fsm_ids: tuple[int, ...],
    label: str,
    *,
    timeout_seconds: float = 8.0,
) -> tuple[int, int | None, int | None]:
    state_code, current = get_fsm_id(client)
    if state_code == 0 and current in fsm_ids:
        print(f"{label} already active (FSM ID {current}).")
        return 0, current, current

    last_code = 0
    last_seen: int | None = None
    last_target: int | None = None

    for index, fsm_id in enumerate(fsm_ids):
        last_target = fsm_id
        last_code, last_seen = set_fsm_and_wait(
            client,
            fsm_id,
            label,
            timeout_seconds=timeout_seconds,
        )
        if last_code == 0:
            return last_code, last_seen, fsm_id

        if index < len(fsm_ids) - 1 and last_code in FSM_RETRY_CODES:
            print(
                f"FSM ID {fsm_id} did not become active "
                f"({error_name(last_code)}; last observed {last_seen}); "
                f"trying FSM ID {fsm_ids[index + 1]}..."
            )
            continue

        break

    return last_code, last_seen, last_target


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)
