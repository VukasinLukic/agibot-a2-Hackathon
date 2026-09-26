"""Wires config -> store, service, adapters, dispatcher. Mock is the default.

Adapters are chosen per port in ``settings.adapters`` (fake / a2 / livekit).
In mock mode the real classes run as dry-run. ``mode: real`` must be chosen
explicitly; it rejects fake adapters and refuses to start until persons 3/4
wire real transports. Mock never falls back to real and real never silently
falls back to mock.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from table_tennis.config import Settings
from table_tennis.core.fake_log import FakeOutputLog
from table_tennis.core.outputs import OutputDispatcher
from table_tennis.core.ports import Clock, IdGenerator, SystemClock, UuidGenerator
from table_tennis.core.service import RefereeService
from table_tennis.persona.speech import FakeSpeechOutput, LiveKitSpeechOutput
from table_tennis.robot.a2_adapters import A2GestureOutput, A2RobotNavigator, A2ScoreDisplay, RealTransportMissing
from table_tennis.robot.call_service import RobotCallService, TickerThread
from table_tennis.robot.fake import FakeGestureOutput, FakeRobotNavigator, FakeScoreDisplay
from table_tennis.robot.gesture_output import MotionCoordinator
from table_tennis.storage.sqlite_store import SqliteEventStore

log = logging.getLogger("table_tennis.runtime")


class RealModeNotAvailable(RuntimeError):
    pass


@dataclass
class Runtime:
    settings: Settings
    store: SqliteEventStore
    service: RefereeService
    dispatcher: OutputDispatcher
    robot: RobotCallService
    fake_log: FakeOutputLog
    display: Any
    speech: Any
    gesture: Any
    navigator: Any
    background: bool = True
    ticker: Optional[TickerThread] = None
    adapter_info: dict = field(default_factory=dict)
    started: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        with self._lock:
            if self.started:
                return
            failed_calls = self.robot.startup()
            counts = self.dispatcher.startup()
            if self.background:
                self.ticker = TickerThread(self.robot, self.settings.robot.sim_step_s)
                self.ticker.start()
            self.started = True
            log.info("table tennis runtime started (mode=%s, outbox recovery=%s, calls failed=%s)",
                     self.settings.mode, counts, failed_calls)

    def stop(self) -> None:
        with self._lock:
            if not self.started:
                return
            if self.ticker:
                self.ticker.stop()
            self.dispatcher.shutdown()
            self.store.close()
            self.started = False


def build_adapters(settings: Settings, fake_log: FakeOutputLog, clock: Clock) -> tuple[Any, Any, Any, Any, dict]:
    """Select output/navigation adapters from ``settings.adapters``.

    mock: fake adapters, or the real classes in dry-run (no hardware touched).
    real: non-fake adapters without dry-run; they refuse to start until a real
    transport is wired, and fake adapters are rejected (no silent fallback).
    """
    a = settings.adapters
    real = settings.mode == "real"
    if real:
        fakes = [name for name, kind in a.model_dump().items() if kind == "fake"]
        if fakes:
            raise RealModeNotAvailable(
                f"mode=real cannot use fake adapters ({', '.join(fakes)}); choose a2/livekit adapters explicitly"
            )
    dry_run = not real
    info: dict[str, dict] = {}
    # One body: the navigator tells the gesture session when the robot is walking.
    motion = MotionCoordinator()
    try:
        if a.display == "fake":
            display: Any = FakeScoreDisplay(fake_log)
        else:
            display = A2ScoreDisplay(dry_run=dry_run)
        info["screen"] = {"adapter": a.display, "dry_run": a.display != "fake" and dry_run, "simulated": not real}

        if a.gesture == "fake":
            gesture: Any = FakeGestureOutput(fake_log, coordinator=motion)
        else:
            gesture = A2GestureOutput(dry_run=dry_run, coordinator=motion)
        info["gesture"] = {"adapter": a.gesture, "dry_run": a.gesture != "fake" and dry_run, "simulated": not real}

        if a.speech == "fake":
            speech: Any = FakeSpeechOutput(fake_log)
        else:
            speech = LiveKitSpeechOutput(dry_run=dry_run)
        info["speech"] = {"adapter": a.speech, "dry_run": a.speech != "fake" and dry_run, "simulated": not real}

        if a.navigator == "fake":
            navigator: Any = FakeRobotNavigator(
                clock=clock, fail_waypoints=set(settings.robot.fail_waypoints), coordinator=motion
            )
        else:
            navigator = A2RobotNavigator(clock=clock, dry_run=dry_run, coordinator=motion)
        info["robot_navigation"] = {"adapter": a.navigator, "dry_run": a.navigator != "fake" and dry_run, "simulated": not real}
    except (RealTransportMissing, RuntimeError) as exc:
        raise RealModeNotAvailable(f"mode=real: {exc}") from exc
    return display, speech, gesture, navigator, info


def build_runtime(
    settings: Settings,
    *,
    clock: Optional[Clock] = None,
    ids: Optional[IdGenerator] = None,
    background: bool = True,
) -> Runtime:
    clock = clock or SystemClock()
    ids = ids or UuidGenerator()
    fake_log = FakeOutputLog(settings.outputs.fake_log_path, clock=SystemClock())
    # Build adapters first: an impossible real config fails before the DB is opened.
    display, speech, gesture, navigator, info = build_adapters(settings, fake_log, clock)
    store = SqliteEventStore(settings.storage.db_path)
    service = RefereeService(store, clock, ids, automatic_scoring_enabled=settings.features.automatic_scoring)
    dispatcher = OutputDispatcher(
        service, display, speech, gesture, ttl_s=settings.outputs.speech_ttl_s, background=background
    )
    waypoints = {table: list(wps) for table, wps in settings.robot.tables.items()}
    if settings.mode == "mock" and settings.adapters.navigator == "fake":
        # mock only: simulated-failure waypoints must be callable so the UI can test "failed"
        for table in waypoints:
            waypoints[table] += [w for w in settings.robot.fail_waypoints if w not in waypoints[table]]
    robot = RobotCallService(navigator, store, service, waypoints=waypoints, ids=ids, clock=clock)
    return Runtime(
        settings=settings,
        store=store,
        service=service,
        dispatcher=dispatcher,
        robot=robot,
        fake_log=fake_log,
        display=display,
        speech=speech,
        gesture=gesture,
        navigator=navigator,
        background=background,
        adapter_info=info,
    )
