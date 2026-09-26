#!/usr/bin/env python3
"""
Activate Damp mode on the G1 robot.

Damp (FSM ID 1): motors apply passive damping — the robot becomes compliant
and slowly sinks under gravity. Safe to use at any time as an emergency stop.

Usage: python3 set_damp_mode.py [network_interface]
  network_interface: DDS interface (default: eth0)
"""
import sys

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from common import (
    ensure_ai_motion_mode,
    error_name,
    fail,
    loco_client,
    motion_switcher_client,
    set_fsm_and_wait,
)

NETWORK_INTERFACE = "eth0"


def main():
    interface = sys.argv[1] if len(sys.argv) > 1 else NETWORK_INTERFACE
    print(f"Initializing DDS on interface: {interface}")
    ChannelFactoryInitialize(0, interface)

    try:
        ensure_ai_motion_mode(motion_switcher_client())
    except RuntimeError as exc:
        fail(str(exc))

    client = loco_client()

    code, current = set_fsm_and_wait(client, 1, "Damp", timeout_seconds=5.0)

    if code == 0:
        print("Damp mode verified.")
        sys.exit(0)
    else:
        fail(f"Damp mode did not reach FSM 1: {error_name(code)}; last observed FSM ID={current}")


if __name__ == "__main__":
    main()
