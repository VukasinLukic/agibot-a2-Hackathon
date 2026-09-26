"""Robot call outcome -> match event (readiness.changed) -> speech outbox."""


def _events(rt, match_id, type_="readiness.changed"):
    return [e for e in rt.store.events_for_match(match_id) if e.type == type_]


def _speech_rows(rt, match_id):
    return [r for r in rt.store.outbox_rows(match_id) if r["kind"] == "speech"]


def _call(d, waypoint="referee-spot"):
    r = d.robot_call(named_waypoint_id=waypoint)
    assert r.status == 202
    return r.body["call_id"]


def test_arrival_emits_readiness_event_and_speech_job(d, rt):
    d.create(scoring_mode="manual")
    cid = _call(d)
    d.wait_robot(cid, {"ready"})
    ev = _events(rt, d.match_id)
    assert len(ev) == 1
    p = ev[0].payload
    assert p.component == "robot_ready" and p.value is True and p.reason == "robot_arrived"
    assert d.snapshot()["ready"]["robot_ready"] is True
    assert any(r["event_id"] == ev[0].event_id for r in _speech_rows(rt, d.match_id))


def test_failed_call_emits_event_even_if_robot_was_not_ready(d, rt):
    d.create(scoring_mode="manual")
    cid = _call(d, waypoint="broken-spot")
    d.wait_robot(cid, {"failed"})
    ev = _events(rt, d.match_id)
    assert len(ev) == 1
    p = ev[0].payload
    assert p.value is False and p.reason == "robot_call_failed"
    assert d.snapshot()["ready"]["robot_ready"] is False
    assert any(r["event_id"] == ev[0].event_id for r in _speech_rows(rt, d.match_id))


def test_cancelled_call_emits_event(d, rt):
    d.create(scoring_mode="manual")
    cid = _call(d)
    assert d.robot_cancel(cid).status == 202
    d.wait_robot(cid, {"cancelled"})
    ev = _events(rt, d.match_id)
    assert [e.payload.reason for e in ev] == ["robot_call_cancelled"]


def test_repeated_tick_does_not_duplicate_report(d, rt):
    d.create(scoring_mode="manual")
    cid = _call(d)
    d.wait_robot(cid, {"ready"})
    call = rt.robot.get(cid)
    rt.robot._report_to_match(call)  # same outcome reported again -> idempotent
    assert len(_events(rt, d.match_id)) == 1


def test_operator_confirms_arrival_when_telemetry_is_missing(d, rt):
    from table_tennis.robot.arrival import ArrivalFacts

    rt.robot.navigator.arrival = ArrivalFacts()
    d.create(scoring_mode="manual")
    cid = _call(d)
    parked = d.wait_robot(cid, {"arrived"})
    assert parked["reason"] == "goal accepted; arrival not confirmed"
    rt.robot.tick()
    assert rt.robot.get(cid).reason == "need_operator_confirmation"
    done = rt.robot.confirm_arrival(cid, "operator")
    assert done.state == "ready"
    ev = _events(rt, d.match_id)
    assert [e.payload.reason for e in ev] == ["manual_arrival"]
    assert d.snapshot()["ready"]["robot_ready"] is True
    # The call is finished, so a second call is not blocked as busy.
    assert rt.robot._active_call() is None


def test_restart_asks_the_navigator_to_stop_the_unfinished_call(d, rt):
    d.create(scoring_mode="manual")
    cid = _call(d)
    stopped: list[str] = []
    rt.robot.navigator.abandon = lambda call: stopped.append(call.call_id)
    assert rt.robot.startup() == 1
    assert stopped == [cid]
    assert rt.robot.store.robot_call(cid) is not None
    assert rt.robot._active_call() is None


def test_route_after_the_navigator_forgets_the_call_returns_the_stored_one(d, rt):
    from table_tennis.robot.a2_adapters import A2RobotNavigator
    from table_tennis.robot.readiness import NavFacts

    d.create(scoring_mode="manual")
    cid = _call(d)
    stored = rt.robot.get(cid)
    fresh = A2RobotNavigator(dry_run=False, transport=lambda action, args: {"task_id": 5}, facts=NavFacts())
    rt.robot.navigator = fresh
    again = rt.robot.confirm_route(cid, "operator")
    assert again.call_id == stored.call_id
    assert again.state == stored.state
    assert fresh.sent == []


def test_gesture_skipped_while_walking_stays_healthy(d, rt):
    d.create(scoring_mode="manual")
    d.ok("match.start")
    d.ok("robot.ready.set", {"ready": True, "reason": "manual_arrival"}, expected_revision=None)
    _call(d)
    d.point("p1")
    rt.dispatcher.drain()
    gesture = rt.dispatcher.output_status()["gesture"]
    assert gesture["failed"] == 0
    assert gesture["healthy"] is True
    assert gesture["skipped"] >= 1


def test_a_call_that_fails_before_it_moves_still_tells_the_match(d, rt):
    from table_tennis.robot.readiness import NavFacts

    d.create(scoring_mode="manual")
    rt.robot.navigator.facts = NavFacts(localization_running=False)
    cid = _call(d)
    assert rt.robot.get(cid).state == "failed"
    assert rt.robot.tick() == []
    ev = _events(rt, d.match_id)
    assert [e.payload.reason for e in ev] == ["robot_call_failed"]


def test_operator_unchanged_ready_is_still_noop(d, rt):
    d.create(scoring_mode="manual")
    d.ok("operator.ready.set", {"ready": False}, expected_revision=None)
    assert _events(rt, d.match_id) == []
