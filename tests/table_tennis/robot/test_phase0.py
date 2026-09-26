"""Phase 0: one confirmed point through fake adapters, without a robot."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from table_tennis.api.runtime import build_runtime
from table_tennis.config import load_settings
from table_tennis.sim.driver import InProcessDriver
from table_tennis.sim.scenarios import manual_point


def _driver(tmpdir: str) -> tuple[InProcessDriver, object]:
    settings = load_settings(
        env={},
        storage__db_path=str(Path(tmpdir) / "phase0.sqlite"),
        outputs__fake_log_path="",
    )
    runtime = build_runtime(settings, background=False)
    runtime.start()
    return InProcessDriver(runtime), runtime


def test_confirmed_point_log_shows_match_winner_side_and_score() -> None:
    before = set(sys.modules)
    with tempfile.TemporaryDirectory() as tmp:
        driver, runtime = _driver(tmp)
        try:
            manual_point(driver)
            driver.settle()
            gesture = driver.outputs()["gestures"][-1]
        finally:
            runtime.stop()
    assert gesture["match_id"] == driver.match_id
    assert gesture["event_id"]
    assert gesture["revision"] is not None
    assert "winner=p1" in gesture["text"]
    assert "robot_side=left" in gesture["text"]
    assert "score=1:0" in gesture["text"]
    loaded = set(sys.modules) - before
    assert not any(name == "robot_services" or name.startswith("robot_services.") for name in loaded)


def test_call_passes_moving_then_ready_and_a_second_call_is_busy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        driver, runtime = _driver(tmp)
        try:
            driver.create(scoring_mode="manual")
            call = driver.robot_call()
            busy = driver.robot_call()
            robot_busy_status = busy.status
            states = [call.body["state"]]
            while states[-1] not in {"ready", "failed", "cancelled"}:
                runtime.robot.tick()
                states.append(driver.robot_get(call.body["call_id"])["state"])
        finally:
            runtime.stop()
    assert states == ["requested", "validating", "moving", "arrived", "ready"]
    assert robot_busy_status == 409


def test_cancel_and_failed_waypoint_do_not_become_ready() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        driver, runtime = _driver(tmp)
        try:
            driver.create(scoring_mode="manual")
            call = driver.robot_call()
            assert call.body["state"] == "requested"
            cancelled = driver.robot_cancel(call.body["call_id"])
            assert cancelled.body["state"] == "cancel_requested"
            done = driver.wait_robot(call.body["call_id"], {"cancelled"})
            assert done["state"] == "cancelled"

            runtime.robot.waypoints["table-1"].append("broken-spot")
            failed = driver.robot_call(named_waypoint_id="broken-spot")
            assert failed.status == 202
            end = driver.wait_robot(failed.body["call_id"], {"failed", "ready"})
            assert end["state"] == "failed"
        finally:
            runtime.stop()
