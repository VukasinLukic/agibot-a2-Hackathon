#!/usr/bin/env python3
"""
Activate Run mode on the G1 robot.

Restores high-level "ai" motion control if needed, enters G1 Run mode
(FSM 801 on this G1 Edu 29DoF), selects a high speed profile, selects
balanced standing behavior, and sends zero velocity. This enables the
self-balancing run controller without commanding movement.

Usage: python3 set_run_mode.py [network_interface]
  network_interface: DDS interface (default: eth0)
"""
import sys

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from common import (
    ensure_ai_motion_mode,
    error_name,
    fail,
    LOCK_STANDING_FSM_ID,
    loco_client,
    motion_switcher_client,
    require_static_mode,
    require_supported_source_fsm,
    RUN_FSM_IDS,
    set_first_available_fsm_and_wait,
    set_speed_mode,
    WALK_MOTION_FSM_IDS,
)

NETWORK_INTERFACE = "eth0"
RUN_SPEED_MODE = 1


def main():
    interface = sys.argv[1] if len(sys.argv) > 1 else NETWORK_INTERFACE
    print(f"Initializing DDS on interface: {interface}")
    ChannelFactoryInitialize(0, interface)

    try:
        ensure_ai_motion_mode(motion_switcher_client())
    except RuntimeError as exc:
        fail(str(exc))

    client = loco_client()

    try:
        require_static_mode(client)
        require_supported_source_fsm(
            client,
            {LOCK_STANDING_FSM_ID, *WALK_MOTION_FSM_IDS, *RUN_FSM_IDS},
            "Run",
        )
    except RuntimeError as exc:
        fail(str(exc))

    code, current, target = set_first_available_fsm_and_wait(client, RUN_FSM_IDS, "Run")
    if code != 0:
        fail(
            f"Run did not reach FSM {RUN_FSM_IDS}: "
            f"{error_name(code)}; last target={target}; last observed FSM ID={current}"
        )

    print(f"Selecting high speed profile (SetSpeedMode {RUN_SPEED_MODE})...")
    code = set_speed_mode(client, RUN_SPEED_MODE)
    if code != 0:
        fail(f"SetSpeedMode({RUN_SPEED_MODE}) returned {error_name(code)}")

    print("Selecting balanced standing behavior (BalanceStand / SetBalanceMode 0)...")
    code = client.SetBalanceMode(0)
    if code != 0:
        fail(f"BalanceStand returned {error_name(code)}")

    print("Sending zero velocity to hold self-balancing stance...")
    code = client.SetVelocity(0.0, 0.0, 0.0, 1.0)
    if code != 0:
        fail(f"SetVelocity(0, 0, 0) returned {error_name(code)}")

    print("Run mode prepared successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
