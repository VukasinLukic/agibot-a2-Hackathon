#!/usr/bin/env python3
"""
Query the current robot locomotion state: FSM ID, FSM mode, balance mode,
and the active MotionSwitcher mode. Run this first to understand what state
the robot is in before sending locomotion commands.

Usage: python3 query_robot_state.py [network_interface]
"""
import sys
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from common import error_name, loco_client, motion_switcher_client, parse_json_data
from unitree_sdk2py.g1.loco.g1_loco_api import (
    ROBOT_API_ID_LOCO_GET_FSM_ID,
    ROBOT_API_ID_LOCO_GET_FSM_MODE,
    ROBOT_API_ID_LOCO_GET_BALANCE_MODE,
    ROBOT_API_ID_LOCO_GET_STAND_HEIGHT,
)

NETWORK_INTERFACE = "eth0"


def main():
    interface = sys.argv[1] if len(sys.argv) > 1 else NETWORK_INTERFACE
    print(f"Initializing DDS on interface: {interface}")
    ChannelFactoryInitialize(0, interface)
    time.sleep(0.5)

    # --- MotionSwitcher state ---
    print("\n=== MotionSwitcher ===")
    msc = motion_switcher_client()
    code, result = msc.CheckMode()
    if code == 0:
        name = result.get("name", "(empty)") if result else "(no data)"
        print(f"  CheckMode {error_name(code)}  active_mode='{name}'")
        if name == "":
            print("  NOTE: Empty active_mode means debug/development mode; high-level sport RPCs may not answer.")
    else:
        print(f"  CheckMode FAILED {error_name(code)}")

    # --- LocoClient state ---
    print("\n=== LocoClient (sport service) ===")
    loco = loco_client(timeout=5.0)

    for api_id, label in [
        (ROBOT_API_ID_LOCO_GET_FSM_ID,       "FSM ID       "),
        (ROBOT_API_ID_LOCO_GET_FSM_MODE,     "FSM Mode     "),
        (ROBOT_API_ID_LOCO_GET_BALANCE_MODE, "Balance Mode "),
        (ROBOT_API_ID_LOCO_GET_STAND_HEIGHT, "Stand Height "),
    ]:
        try:
            code, data = loco._Call(api_id, "{}")
            if data:
                parsed = parse_json_data(data)
                print(f"  {label} {error_name(code)}  value={parsed}")
            else:
                print(f"  {label} {error_name(code)}  data={data!r}")
        except Exception as e:
            print(f"  {label} ERROR: {e}")

    print()


if __name__ == "__main__":
    main()
