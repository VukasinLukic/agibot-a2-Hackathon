"""RefereeEngine: pure, deterministic, event-sourced rules for one singles game.

    decide(state, command, actor, ctx) -> [events]      (validation + intent)
    apply_event(state, event)          -> state          (the only state transition)
    snapshot(state)                    -> MatchSnapshot   (derived view)

Replay of the stored event log through ``apply_event`` reconstructs exactly the
same state, which is how restart recovery works. The engine has no I/O, no
locks and no knowledge of robots, cameras, speech or HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from table_tennis.contracts import (
    COMMAND_PERMISSIONS,
    REVISIONLESS_COMMANDS,
    CourtEndByPlayer,
    CreateMatchRequest,
    GameRules,
    MatchSnapshot,
    Player,
    PointProposal,
    Readiness,
    RobotSideByPlayer,
    ScoreByPlayer,
)
from table_tennis.contracts.events import EVENT_ADAPTER

from .errors import ConflictError, ForbiddenError, RefereeError, UnsupportedError
from .rules import game_winner, next_server

SUPPORTED_TARGET_POINTS = 11
SUPPORTED_WIN_BY = 2
SUPPORTED_BEST_OF = 1


class PointRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    rally_id: str
    winner_id: str
    active: bool = True


class MatchState(BaseModel):
    """Internal authoritative state. Persisted as JSON; not part of the wire contract."""

    model_config = ConfigDict(extra="forbid")

    match_id: str
    revision: int
    status: str
    players: list[Player]
    rules: GameRules
    first_server_id: str
    scoring_mode: str
    persona: str
    court_end_by_player: CourtEndByPlayer
    robot_side_by_player: RobotSideByPlayer
    assignment_version: int = 1
    calibration_id: Optional[str] = None
    table_id: Optional[str] = None
    ready: Readiness = Field(default_factory=Readiness)
    active_rally_id: Optional[str] = None
    active_proposal: Optional[PointProposal] = None
    paused_from: Optional[str] = None
    points: list[PointRecord] = Field(default_factory=list)
    ended_by_operator: bool = False
    updated_at: datetime

    # ------------------------------------------------------------------ derived

    def score(self) -> ScoreByPlayer:
        p1 = sum(1 for p in self.points if p.active and p.winner_id == "p1")
        p2 = sum(1 for p in self.points if p.active and p.winner_id == "p2")
        return ScoreByPlayer(p1=p1, p2=p2)

    def natural_winner(self) -> Optional[str]:
        s = self.score()
        return game_winner(s.p1, s.p2, self.rules.target_points, self.rules.win_by)

    def last_active_point(self) -> Optional[PointRecord]:
        for p in reversed(self.points):
            if p.active:
                return p
        return None


@dataclass
class EngineContext:
    now: datetime
    new_id: Callable[[], str]


# --------------------------------------------------------------------------- snapshot


def snapshot(state: MatchState) -> MatchSnapshot:
    score = state.score()
    finished = state.status == "finished"
    if finished:
        if state.ended_by_operator:
            winner = state.natural_winner()
            if winner is None and score.p1 != score.p2:
                winner = "p1" if score.p1 > score.p2 else "p2"
        else:
            winner = state.natural_winner()
        server = None
    else:
        winner = None
        server = next_server(
            score.p1, score.p2, state.first_server_id, state.rules.target_points, state.rules.win_by
        )
    last = state.last_active_point()
    ready = state.ready.model_copy(update={"calibration_ready": state.calibration_id is not None})
    return MatchSnapshot(
        match_id=state.match_id,
        revision=state.revision,
        status=state.status,  # type: ignore[arg-type]
        players=state.players,
        config=state.rules,
        score_by_player=score,
        first_server_id=state.first_server_id,  # type: ignore[arg-type]
        server_id=server,  # type: ignore[arg-type]
        winner_id=winner,  # type: ignore[arg-type]
        assignment_version=state.assignment_version,
        court_end_by_player=state.court_end_by_player,
        robot_side_by_player=state.robot_side_by_player,
        calibration_id=state.calibration_id,
        active_rally_id=state.active_rally_id,
        active_proposal_id=state.active_proposal.proposal_id if state.active_proposal else None,
        persona=state.persona,  # type: ignore[arg-type]
        scoring_mode=state.scoring_mode,  # type: ignore[arg-type]
        ready=ready,
        updated_at=state.updated_at,
        active_proposal=state.active_proposal,
        paused_from=state.paused_from,  # type: ignore[arg-type]
        last_point_event_id=last.event_id if last else None,
        table_id=state.table_id,
    )


# --------------------------------------------------------------------------- apply


def _clear_rally(state: MatchState) -> None:
    state.active_rally_id = None
    state.active_proposal = None


def apply_event(state: Optional[MatchState], event: Any) -> MatchState:
    """The only place where match state changes. Used for commands and replay."""
    t = event.type
    p = event.payload
    if t == "match.created":
        new = MatchState(
            match_id=event.match_id,
            revision=event.revision,
            status="setup",
            players=p.players,
            rules=p.config,
            first_server_id=p.first_server_id,
            scoring_mode=p.scoring_mode,
            persona=p.persona,
            court_end_by_player=p.court_end_by_player,
            robot_side_by_player=p.robot_side_by_player,
            calibration_id=p.calibration_id,
            table_id=p.table_id,
            updated_at=event.occurred_at,
        )
        return new
    if state is None:
        raise ValueError(f"event {t} before match.created")
    s = state.model_copy(deep=True)
    if t == "match.started":
        s.status = "between_rallies"
    elif t == "rally.armed":
        s.active_rally_id = p.rally_id
        s.active_proposal = None
        s.status = "rally"
    elif t == "point.proposed":
        s.active_proposal = p.proposal
        s.status = "pending_decision"
    elif t == "point.confirmed":
        s.points.append(PointRecord(event_id=event.event_id, rally_id=p.rally_id, winner_id=p.winner_id))
        _clear_rally(s)
        s.status = "between_rallies"
    elif t == "rally.let":
        _clear_rally(s)
        s.status = "between_rallies"
    elif t == "score.corrected":
        for rec in s.points:
            if rec.event_id == p.target_event_id:
                rec.active = False
        if p.invalidated_rally_id:
            _clear_rally(s)
            if s.paused_from in ("rally", "pending_decision"):
                s.paused_from = "between_rallies"
        if s.status in ("finished", "rally", "pending_decision"):
            s.status = "between_rallies"
    elif t == "match.paused":
        if p.cancelled_rally_id:
            _clear_rally(s)
        s.paused_from = p.resume_to
        s.status = "paused"
    elif t == "match.resumed":
        s.status = p.status
        s.paused_from = None
    elif t == "match.finished":
        _clear_rally(s)
        s.paused_from = None
        s.ended_by_operator = p.ended_by_operator
        s.status = "finished"
    elif t == "persona.changed":
        s.persona = p.persona
    elif t in ("sides.changed", "calibration.changed"):
        if t == "sides.changed":
            s.court_end_by_player = p.court_end_by_player
            s.robot_side_by_player = p.robot_side_by_player
            s.assignment_version = p.assignment_version
        else:
            s.calibration_id = p.calibration_id
        if p.invalidated_rally_id or p.invalidated_proposal_id:
            _clear_rally(s)
            if s.paused_from in ("rally", "pending_decision"):
                s.paused_from = "between_rallies"
    elif t == "readiness.changed":
        if p.component != "calibration_ready":
            setattr(s.ready, p.component, p.value)
    else:
        raise ValueError(f"unknown event type {t}")
    s.revision = event.revision
    s.updated_at = event.occurred_at
    return s


def fold(state: Optional[MatchState], events: Iterable[Any]) -> MatchState:
    for e in events:
        state = apply_event(state, e)
    assert state is not None
    return state


# --------------------------------------------------------------------------- decide


class _EventFactory:
    def __init__(self, match_id: str, revision: int, ctx: EngineContext, causation_id: Optional[str]):
        self.match_id = match_id
        self.revision = revision
        self.ctx = ctx
        self.causation_id = causation_id

    def make(self, type_: str, payload: dict) -> Any:
        return EVENT_ADAPTER.validate_python(
            {
                "event_id": self.ctx.new_id(),
                "match_id": self.match_id,
                "revision": self.revision,
                "type": type_,
                "occurred_at": self.ctx.now,
                "received_at": self.ctx.now,
                "causation_id": self.causation_id,
                "payload": payload,
            }
        )


def _require(cond: bool, code: str, message: str, state: MatchState, **details: Any) -> None:
    if not cond:
        raise ConflictError(code, message, current_revision=state.revision, details=details or None)


def validate_create(request: CreateMatchRequest, automatic_scoring_enabled: bool = False) -> None:
    cfg = request.config
    if cfg.best_of != SUPPORTED_BEST_OF:
        raise UnsupportedError(
            "unsupported_best_of", "v1 supports exactly one game (best_of=1); more games are not supported yet"
        )
    if cfg.target_points != SUPPORTED_TARGET_POINTS or cfg.win_by != SUPPORTED_WIN_BY:
        raise UnsupportedError("unsupported_rules", "v1 supports one game to 11 points, win by 2")
    if cfg.scoring_mode == "automatic":
        # The bootstrap never enables automatic scoring. Person 2 adds it only after
        # person 1's benchmark (see 02_BACKEND.md, phase 5).
        raise UnsupportedError(
            "automatic_scoring_disabled",
            "automatic scoring is disabled in this bootstrap; use manual or assisted",
        )


def decide_create(request: CreateMatchRequest, match_id: str, ctx: EngineContext) -> list[Any]:
    f = _EventFactory(match_id, 1, ctx, request.command_id)
    return [
        f.make(
            "match.created",
            {
                "players": [p.model_dump() for p in sorted(request.players, key=lambda p: p.id)],
                "config": GameRules(
                    target_points=request.config.target_points,
                    win_by=request.config.win_by,
                    best_of=request.config.best_of,
                ).model_dump(),
                "first_server_id": request.config.first_server_id,
                "scoring_mode": request.config.scoring_mode,
                "persona": request.config.persona,
                "court_end_by_player": request.court_end_by_player.model_dump(),
                "robot_side_by_player": request.robot_side_by_player.model_dump(),
                "calibration_id": request.calibration_id,
                "table_id": request.table_id,
            },
        )
    ]


def check_permission(command: Any, actor: str) -> None:
    allowed = COMMAND_PERMISSIONS.get(command.type, frozenset())
    if actor not in allowed:
        raise ForbiddenError(
            "forbidden_actor",
            f"actor {actor!r} may not send {command.type}",
            details={"allowed": sorted(allowed)},
        )


def check_revision(state: MatchState, command: Any) -> None:
    if command.expected_revision is None:
        if command.type not in REVISIONLESS_COMMANDS:
            raise RefereeError(
                "expected_revision_required",
                f"{command.type} requires expected_revision",
                current_revision=state.revision,
                http_status=422,
            )
        return
    if command.expected_revision != state.revision:
        raise ConflictError(
            "stale_revision",
            f"expected_revision {command.expected_revision} != current {state.revision}; resync and decide again",
            current_revision=state.revision,
        )


def decide(state: MatchState, command: Any, actor: str, ctx: EngineContext) -> list[Any]:
    """Validate a command against state and return the events it produces.

    Permission and revision checks are done by the service (after idempotency).
    Returns [] for a valid no-op (e.g. setting persona to the current value).
    """
    t = command.type
    pl = command.payload
    f = _EventFactory(state.match_id, state.revision + 1, ctx, command.command_id)
    status = state.status
    snap = snapshot(state)

    if status == "finished" and t not in ("point.undo", "persona.set", "robot.ready.set", "camera.ready.set", "operator.ready.set"):
        _require(False, "match_finished", "match is finished", state)

    if t == "match.start":
        _require(status == "setup", "invalid_state", f"cannot start from {status}", state)
        return [f.make("match.started", {"server_id": snap.server_id})]

    if t == "rally.arm":
        _require(status == "between_rallies", "invalid_state", f"cannot arm a rally from {status}", state)
        _require(state.ready.robot_ready, "robot_not_ready", "robot is not ready (call robot or mark manual arrival)", state)
        return [f.make("rally.armed", {"rally_id": ctx.new_id(), "server_id": snap.server_id})]

    if t == "point.propose":
        prop: PointProposal = pl
        _require(state.scoring_mode == "assisted", "proposals_disabled", "CV proposals are only accepted in assisted mode", state)
        _require(state.ready.camera_ready, "camera_not_ready", "camera is not ready; proposals are paused", state)
        _require(state.calibration_id is not None, "calibration_not_ready", "no calibration set", state)
        _require(
            status in ("rally", "pending_decision"),
            "invalid_state",
            f"no active rally to propose for (status {status})",
            state,
        )
        _require(prop.rally_id == state.active_rally_id, "stale_rally", "proposal is for a rally that is not active", state)
        _require(
            state.active_proposal is None,
            "proposal_already_pending",
            "this rally already has a pending proposal; at most one decision per rally",
            state,
            active_proposal_id=state.active_proposal.proposal_id if state.active_proposal else None,
        )
        _require(prop.calibration_id == state.calibration_id, "stale_calibration", "proposal uses an old calibration", state)
        _require(
            prop.assignment_version == state.assignment_version,
            "stale_assignment",
            "proposal uses an old side assignment",
            state,
        )
        return [f.make("point.proposed", {"proposal": prop.model_dump()})]

    if t == "point.confirm":
        _require(status == "pending_decision", "invalid_state", f"nothing to confirm (status {status})", state)
        _require(
            state.active_proposal is not None and state.active_proposal.proposal_id == pl.proposal_id,
            "stale_proposal",
            "proposal is no longer pending",
            state,
        )
        assert state.active_proposal is not None
        return _point_events(f, state, state.active_proposal.rally_id, state.active_proposal.winner_id,
                             state.active_proposal.reason, "confirmed_proposal", state.active_proposal.proposal_id)

    if t == "point.award":
        _require(pl.rally_id == state.active_rally_id, "stale_rally", "rally is closed or not active", state)
        _require(status in ("rally", "pending_decision"), "invalid_state", f"no active rally (status {status})", state)
        return _point_events(f, state, pl.rally_id, pl.winner_id, pl.reason, "operator_award", None)

    if t == "rally.let":
        _require(pl.rally_id == state.active_rally_id, "stale_rally", "rally is closed or not active", state)
        _require(status in ("rally", "pending_decision"), "invalid_state", f"no active rally (status {status})", state)
        return [f.make("rally.let", {"rally_id": pl.rally_id, "reason": pl.reason})]

    if t == "point.undo":
        _require(status != "setup", "invalid_state", "nothing to undo before start", state)
        _require(not state.ended_by_operator, "match_ended", "match was ended by the operator", state)
        target = next((p for p in state.points if p.event_id == pl.target_event_id), None)
        _require(target is not None, "unknown_point", "target_event_id is not a confirmed point", state)
        assert target is not None
        _require(target.active, "already_undone", "this point was already undone", state)
        last = state.last_active_point()
        _require(
            last is not None and last.event_id == target.event_id,
            "not_last_point",
            "only the last active point can be undone in v1",
            state,
            last_point_event_id=last.event_id if last else None,
        )
        before = state.score()
        after = before.model_copy(update={target.winner_id: before.get(target.winner_id) - 1})
        invalidated = state.active_rally_id
        events = [
            f.make(
                "score.corrected",
                {
                    "target_event_id": target.event_id,
                    "rally_id": target.rally_id,
                    "winner_id": target.winner_id,
                    "reason": pl.reason,
                    "previous_score": before.model_dump(),
                    "new_score": after.model_dump(),
                    "invalidated_rally_id": invalidated,
                },
            )
        ]
        if invalidated and status in ("rally", "pending_decision"):
            # The armed rally is void; the match pauses and needs a fresh rally.arm.
            events.append(
                f.make(
                    "match.paused",
                    {
                        "reason": "undo_invalidated_rally",
                        "previous_status": status,
                        "resume_to": "between_rallies",
                        "cancelled_rally_id": None,
                    },
                )
            )
        return events

    if t == "match.pause":
        _require(
            status in ("between_rallies", "rally", "pending_decision"),
            "invalid_state",
            f"cannot pause from {status}",
            state,
        )
        cancelled = state.active_rally_id if status == "rally" else None
        resume_to = "pending_decision" if status == "pending_decision" else "between_rallies"
        return [
            f.make(
                "match.paused",
                {"reason": pl.reason, "previous_status": status, "resume_to": resume_to, "cancelled_rally_id": cancelled},
            )
        ]

    if t == "match.resume":
        _require(status == "paused", "invalid_state", "match is not paused", state)
        return [f.make("match.resumed", {"reason": pl.reason, "status": state.paused_from or "between_rallies"})]

    if t == "match.end":
        _require(status != "setup", "invalid_state", "match has not started", state)
        score = state.score()
        winner = state.natural_winner()
        if winner is None and score.p1 != score.p2:
            winner = "p1" if score.p1 > score.p2 else "p2"
        return [
            f.make(
                "match.finished",
                {"winner_id": winner, "final_score": score.model_dump(), "ended_by_operator": True, "reason": pl.reason},
            )
        ]

    if t == "persona.set":
        _require(
            status not in ("rally", "pending_decision") and state.active_rally_id is None,
            "invalid_state",
            "persona can only change between rallies (no active or pending rally)",
            state,
        )
        if pl.persona == state.persona:
            return []
        return [f.make("persona.changed", {"previous": state.persona, "persona": pl.persona})]

    if t == "sides.set":
        _require(status in ("setup", "paused"), "invalid_state", "sides can only change while paused (or in setup)", state)
        if (
            pl.court_end_by_player == state.court_end_by_player
            and pl.robot_side_by_player == state.robot_side_by_player
        ):
            return []
        return [
            f.make(
                "sides.changed",
                {
                    "assignment_version": state.assignment_version + 1,
                    "court_end_by_player": pl.court_end_by_player.model_dump(),
                    "robot_side_by_player": pl.robot_side_by_player.model_dump(),
                    "invalidated_proposal_id": state.active_proposal.proposal_id if state.active_proposal else None,
                    "invalidated_rally_id": state.active_rally_id,
                },
            )
        ]

    if t == "calibration.set":
        _require(status in ("setup", "paused"), "invalid_state", "calibration can only change while paused (or in setup)", state)
        if pl.calibration_id == state.calibration_id:
            return []
        return [
            f.make(
                "calibration.changed",
                {
                    "previous": state.calibration_id,
                    "calibration_id": pl.calibration_id,
                    "invalidated_proposal_id": state.active_proposal.proposal_id if state.active_proposal else None,
                    "invalidated_rally_id": state.active_rally_id,
                },
            )
        ]

    if t in ("robot.ready.set", "camera.ready.set", "operator.ready.set"):
        component = {
            "robot.ready.set": "robot_ready",
            "camera.ready.set": "camera_ready",
            "operator.ready.set": "operator_ready",
        }[t]
        reason = getattr(pl, "reason", None)
        if t == "robot.ready.set" and actor == "operator" and reason != "manual_arrival":
            raise ForbiddenError(
                "manual_arrival_required",
                "operator may set robot readiness only as an explicit manual arrival (reason='manual_arrival')",
            )
        # A robot adapter report (arrived / call failed / cancelled) is a fact worth
        # announcing even when the flag does not change (e.g. failed while not ready).
        robot_report = t == "robot.ready.set" and actor == "robot"
        if getattr(state.ready, component) == pl.ready and not robot_report:
            return []
        new_ready = state.ready.model_copy(update={component: pl.ready})
        new_ready = new_ready.model_copy(update={"calibration_ready": state.calibration_id is not None})
        return [
            f.make(
                "readiness.changed",
                {"component": component, "value": pl.ready, "reason": reason, "ready": new_ready.model_dump()},
            )
        ]

    raise UnsupportedError("unknown_command", f"unsupported command {t}")


def _point_events(
    f: _EventFactory,
    state: MatchState,
    rally_id: str,
    winner_id: str,
    reason: str,
    source: str,
    proposal_id: Optional[str],
) -> list[Any]:
    before = state.score()
    after = before.model_copy(update={winner_id: before.get(winner_id) + 1})
    events = [
        f.make(
            "point.confirmed",
            {
                "rally_id": rally_id,
                "winner_id": winner_id,
                "reason": reason,
                "source": source,
                "proposal_id": proposal_id,
                "previous_score": before.model_dump(),
                "new_score": after.model_dump(),
            },
        )
    ]
    game_over = game_winner(after.p1, after.p2, state.rules.target_points, state.rules.win_by)
    if game_over:
        events.append(
            f.make(
                "match.finished",
                {"winner_id": game_over, "final_score": after.model_dump(), "ended_by_operator": False, "reason": "game_won"},
            )
        )
    return events


def finalize(state: Optional[MatchState], events: list[Any]) -> tuple[MatchState, list[Any]]:
    """Fold events into state and attach the post-commit snapshot to score events."""
    new_state = fold(state, events)
    snap = snapshot(new_state)
    out = []
    for e in events:
        if e.type in ("point.confirmed", "score.corrected"):
            e = e.model_copy(update={"payload": e.payload.model_copy(update={"snapshot": snap})})
        out.append(e)
    return new_state, out


__all__ = [
    "EngineContext",
    "MatchState",
    "apply_event",
    "check_permission",
    "check_revision",
    "decide",
    "decide_create",
    "finalize",
    "fold",
    "snapshot",
    "validate_create",
]
