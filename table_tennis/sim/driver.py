"""Drivers let the same scenario run in-process (fixtures, tests) or over HTTP (CLI)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from table_tennis.contracts import CreateMatchRequest, RobotCallRequest, RobotCancelRequest, parse_command
from table_tennis.core.errors import RefereeError


class ScenarioFailed(AssertionError):
    pass


@dataclass
class Reply:
    status: int
    body: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def code(self) -> Optional[str]:
        return self.body.get("code") if isinstance(self.body, dict) else None


class Driver:
    """Common scenario helpers; subclasses implement the transport."""

    match_id: Optional[str] = None

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.steps: list[dict] = []

    # ---- transport (implemented by subclasses)
    def new_id(self) -> str:  # pragma: no cover
        raise NotImplementedError

    def _create(self, body: dict, actor: str) -> Reply:  # pragma: no cover
        raise NotImplementedError

    def _command(self, body: dict, actor: str) -> Reply:  # pragma: no cover
        raise NotImplementedError

    def snapshot(self) -> dict:  # pragma: no cover
        raise NotImplementedError

    def _robot_call(self, body: dict, actor: str) -> Reply:  # pragma: no cover
        raise NotImplementedError

    def robot_get(self, call_id: str) -> dict:  # pragma: no cover
        raise NotImplementedError

    def _robot_cancel(self, call_id: str, body: dict, actor: str) -> Reply:  # pragma: no cover
        raise NotImplementedError

    def wait_robot(self, call_id: str, states: set[str], timeout_s: float = 15.0) -> dict:  # pragma: no cover
        raise NotImplementedError

    def outputs(self) -> dict:  # pragma: no cover
        raise NotImplementedError

    def settle(self) -> None:
        """Let post-commit side effects (fake screen/speech/gesture) run."""

    # ---- helpers
    def _log(self, kind: str, actor: str, request: dict, reply: Reply) -> None:
        self.steps.append({"kind": kind, "actor": actor, "request": request, "status": reply.status, "response": reply.body})
        if self.verbose:
            summary = reply.code or ""
            if isinstance(reply.body, dict) and "snapshot" in reply.body:
                s = reply.body["snapshot"]
                summary = f"rev={s['revision']} {s['status']} {s['score_by_player']['p1']}:{s['score_by_player']['p2']} server={s['server_id']}"
                if reply.body.get("duplicate"):
                    summary += " (duplicate - stored answer, no second point)"
            label = request.get("type") or kind
            print(f"  [{actor:8}] {label:20} -> {reply.status} {summary}")

    def expect(self, cond: bool, message: str) -> None:
        if not cond:
            raise ScenarioFailed(message)
        if self.verbose:
            print(f"  OK  {message}")

    def create(self, *, players=None, actor: str = "operator", command_id: Optional[str] = None, **config) -> Reply:
        body = {
            "command_id": command_id or self.new_id(),
            "players": players
            or [
                {"id": "p1", "display_name": "Ana", "role_label": "direktorka", "role_rank": 3},
                {"id": "p2", "display_name": "Marko", "role_label": "inženjer", "role_rank": 2},
            ],
        }
        for key in ("court_end_by_player", "robot_side_by_player", "calibration_id", "table_id"):
            if key in config:
                body[key] = config.pop(key)
        if config:
            body["config"] = config
        CreateMatchRequest.model_validate(body)
        reply = self._create(body, actor)
        self._log("create", actor, body, reply)
        if reply.ok:
            self.match_id = reply.body["match_id"]
        return reply

    def cmd(
        self,
        type_: str,
        payload: Optional[dict] = None,
        *,
        actor: str = "operator",
        expected_revision: Any = "auto",
        command_id: Optional[str] = None,
        validate: bool = True,
    ) -> Reply:
        if expected_revision == "auto":
            expected_revision = self.snapshot()["revision"]
        body = {
            "command_id": command_id or self.new_id(),
            "expected_revision": expected_revision,
            "type": type_,
            "payload": payload or {},
        }
        if validate:
            parse_command(body)
        reply = self._command(body, actor)
        self._log("command", actor, body, reply)
        return reply

    def ok(self, type_: str, payload: Optional[dict] = None, **kw) -> dict:
        reply = self.cmd(type_, payload, **kw)
        if not reply.ok:
            raise ScenarioFailed(f"{type_} failed: {reply.status} {reply.body}")
        return reply.body

    def rejected(self, code: str, type_: str, payload: Optional[dict] = None, *, status: int = 409, **kw) -> Reply:
        reply = self.cmd(type_, payload, **kw)
        self.expect(reply.status == status and reply.code == code, f"{type_} rejected with {status} {code} (got {reply.status} {reply.code})")
        return reply

    def score(self) -> tuple[int, int]:
        s = self.snapshot()["score_by_player"]
        return s["p1"], s["p2"]

    def arm(self) -> str:
        return self.ok("rally.arm")["snapshot"]["active_rally_id"]

    def point(self, winner: str) -> dict:
        rally = self.arm()
        return self.ok("point.award", {"rally_id": rally, "winner_id": winner, "reason": "unknown"})

    def robot_call(self, *, table_id="table-1", named_waypoint_id="referee-spot", match: bool = True,
                   command_id: Optional[str] = None, actor: str = "operator") -> Reply:
        body = {"command_id": command_id or self.new_id(), "table_id": table_id, "named_waypoint_id": named_waypoint_id}
        if match and self.match_id:
            body["match_id"] = self.match_id
        RobotCallRequest.model_validate(body)
        reply = self._robot_call(body, actor)
        self._log("robot.call", actor, body, reply)
        return reply

    def robot_cancel(self, call_id: str, actor: str = "operator") -> Reply:
        body = {"command_id": self.new_id()}
        RobotCancelRequest.model_validate(body)
        reply = self._robot_cancel(call_id, body, actor)
        self._log("robot.cancel", actor, body, reply)
        return reply


# --------------------------------------------------------------------------- in-process


class InProcessDriver(Driver):
    def __init__(self, runtime, *, ids=None, verbose: bool = False):
        super().__init__(verbose)
        self.rt = runtime
        self._ids = ids

    def new_id(self) -> str:
        return self._ids.new_id() if self._ids else str(uuid.uuid4())

    @staticmethod
    def _call(fn) -> Reply:
        try:
            status, body = fn()
            return Reply(status, body)
        except RefereeError as exc:
            return Reply(exc.http_status, exc.to_response().model_dump(mode="json"))
        except ValueError as exc:  # pydantic validation
            return Reply(422, {"code": "invalid_request", "message": str(exc).splitlines()[0]})

    def _create(self, body: dict, actor: str) -> Reply:
        def go():
            snap, dup = self.rt.service.create_match(CreateMatchRequest.model_validate(body), actor)
            return (200 if dup else 201), snap.model_dump(mode="json")

        return self._call(go)

    def _command(self, body: dict, actor: str) -> Reply:
        def go():
            res = self.rt.service.handle(self.match_id, parse_command(body), actor)
            return 200, res.model_dump(mode="json")

        reply = self._call(go)
        self.rt.dispatcher.drain() if not self.rt.background else None
        return reply

    def snapshot(self) -> dict:
        return self.rt.service.get_snapshot(self.match_id).model_dump(mode="json")

    def _robot_call(self, body: dict, actor: str) -> Reply:
        def go():
            call, _ = self.rt.robot.request(RobotCallRequest.model_validate(body), actor)
            return 202, call.model_dump(mode="json")

        return self._call(go)

    def robot_get(self, call_id: str) -> dict:
        return self.rt.robot.get(call_id).model_dump(mode="json")

    def _robot_cancel(self, call_id: str, body: dict, actor: str) -> Reply:
        def go():
            return 202, self.rt.robot.cancel(call_id, body["command_id"], actor).model_dump(mode="json")

        return self._call(go)

    def wait_robot(self, call_id: str, states: set[str], timeout_s: float = 15.0) -> dict:
        for _ in range(20):
            call = self.robot_get(call_id)
            if call["state"] in states:
                return call
            self.rt.robot.tick()
            if not self.rt.background:
                self.rt.dispatcher.drain()
        raise ScenarioFailed(f"robot call {call_id} never reached {states}")

    def outputs(self) -> dict:
        return self.rt.fake_log.view(self.match_id).model_dump(mode="json")

    def settle(self) -> None:
        if not self.rt.background:
            self.rt.dispatcher.drain()


# --------------------------------------------------------------------------- HTTP


class HttpDriver(Driver):
    def __init__(self, base_url: str, *, token: Optional[str] = None, verbose: bool = True, timeout: float = 10.0):
        super().__init__(verbose)
        import httpx

        self.base = base_url.rstrip("/") + "/api/table-tennis"
        self.token = token
        self.client = httpx.Client(timeout=timeout)

    def new_id(self) -> str:
        return str(uuid.uuid4())

    def _headers(self, actor: str) -> dict:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {"X-TT-Actor": actor}

    def _req(self, method: str, path: str, actor: str = "operator", body: Optional[dict] = None) -> Reply:
        resp = self.client.request(method, self.base + path, headers=self._headers(actor), json=body)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            data = resp.text
        return Reply(resp.status_code, data)

    def health(self) -> dict:
        return self._req("GET", "/health").body

    def _create(self, body: dict, actor: str) -> Reply:
        return self._req("POST", "/matches", actor, body)

    def _command(self, body: dict, actor: str) -> Reply:
        return self._req("POST", f"/matches/{self.match_id}/commands", actor, body)

    def snapshot(self) -> dict:
        reply = self._req("GET", f"/matches/{self.match_id}")
        if not reply.ok:
            raise ScenarioFailed(f"snapshot failed: {reply.body}")
        return reply.body

    def _robot_call(self, body: dict, actor: str) -> Reply:
        return self._req("POST", "/robot/calls", actor, body)

    def robot_get(self, call_id: str) -> dict:
        return self._req("GET", f"/robot/calls/{call_id}").body

    def _robot_cancel(self, call_id: str, body: dict, actor: str) -> Reply:
        return self._req("POST", f"/robot/calls/{call_id}/cancel", actor, body)

    def wait_robot(self, call_id: str, states: set[str], timeout_s: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            call = self.robot_get(call_id)
            if call["state"] != last and self.verbose:
                print(f"  [robot   ] call {call_id[:8]} state={call['state']} ({call.get('reason')})")
                last = call["state"]
            if call["state"] in states:
                return call
            time.sleep(0.25)
        raise ScenarioFailed(f"robot call {call_id} never reached {states}")

    def settle(self) -> None:
        time.sleep(0.4)

    def outputs(self) -> dict:
        path = "/debug/outputs" + (f"?match_id={self.match_id}" if self.match_id else "")
        return self._req("GET", path).body
