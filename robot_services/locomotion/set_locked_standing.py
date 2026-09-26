#!/usr/bin/env python3
"""
Activate Locked Standing mode on the G1 robot (FSM ID 4).

Locked Standing:
  - Limbs become extended, robot supports its own weight
  - Does NOT use active balance — will fall if unsupported
  - Designed as an intermediary state (e.g. on the hanging rack)
  - Balancing only becomes active after switching to Walk/Run mode

PRECONDITION: The robot must be physically upright/supported when this
command is sent. It will not work from a lying or floor-damp state.
From a lying position, use Lie2StandUp (FSM 702) instead.

Usage: python3 set_locked_standing.py [network_interface]
  network_interface: DDS interface (default: eth0)
"""
import sys

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from common import LOCK_STANDING_FSM_ID, ensure_ai_motion_mode, error_name, fail, loco_client, motion_switcher_client, set_fsm_and_wait

NETWORK_INTERFACE = "eth0"


def main():
    interface = sys.argv[1] if len(sys.argv) > 1 else NETWORK_INTERFACE
    print(f"Initializing DDS on interface: {interface}")
    ChannelFactoryInitialize(0, interface)

    try:
        ensure_ai_motion_mode(motion_switcher_client())
    except RuntimeError as exc:
        fail(str(exc))

    loco = loco_client()

    code, current = set_fsm_and_wait(loco, LOCK_STANDING_FSM_ID, "Lock Standing")

    if code == 0:
        print(f"Locked Standing (FSM {LOCK_STANDING_FSM_ID}) verified.")
        sys.exit(0)
    else:
        fail(
            f"Lock Standing did not reach FSM {LOCK_STANDING_FSM_ID}: "
            f"{error_name(code)}; last observed FSM ID={current}"
        )


if __name__ == "__main__":
    main()
