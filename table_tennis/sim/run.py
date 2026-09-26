"""Simulator CLI: run a scenario against a live API (mock backend).

    python -m table_tennis.sim.run --scenario manual-game --api http://127.0.0.1:8099
    python -m table_tennis.sim.run --scenario disputed-point --api http://127.0.0.1:8099
    python -m table_tennis.sim.run --list

Every request is clearly marked as coming from the simulator (actor "sim" for
CV proposals). Exit code 0 = all scenario assertions passed.
"""

from __future__ import annotations

import argparse
import os
import sys

from .driver import HttpDriver, ScenarioFailed
from .scenarios import SCENARIOS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A2 table tennis simulator")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="scenario to run")
    parser.add_argument("--api", default="http://127.0.0.1:8099", help="backend base URL")
    parser.add_argument("--token", default=os.environ.get("TT_OPERATOR_TOKEN"), help="bearer token (token auth mode)")
    parser.add_argument("--list", action="store_true", help="list scenarios")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.list or not args.scenario:
        for name in sorted(SCENARIOS):
            doc = (SCENARIOS[name].__doc__ or "").strip().splitlines()
            print(f"{name:20} {doc[0] if doc else ''}")
        return 0 if args.list else 2

    import httpx

    driver = HttpDriver(args.api, token=args.token, verbose=not args.quiet)
    try:
        health = driver.health()
    except httpx.HTTPError as exc:
        print(f"ERROR: backend not reachable at {args.api} ({exc}). Start: python -m table_tennis.run_demo --mode mock")
        return 2
    mode = health.get("mode") if isinstance(health, dict) else "?"
    print(f"== scenario {args.scenario} against {args.api} (mode={mode}, simulated={health.get('simulated')})")
    try:
        SCENARIOS[args.scenario](driver)
    except ScenarioFailed as exc:
        print(f"FAILED: {exc}")
        return 1
    snap = driver.snapshot() if driver.match_id else None
    if snap:
        s = snap["score_by_player"]
        print(f"== match {snap['match_id']} rev={snap['revision']} status={snap['status']} score {s['p1']}:{s['p2']}")
        driver.settle()
        out = driver.outputs()
        if isinstance(out, dict) and out.get("display"):
            print(f"== fake screen: {out['display']['text']}")
            for line in out.get("speech", [])[-3:]:
                print(f"== fake speech: {line['text']}")
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
