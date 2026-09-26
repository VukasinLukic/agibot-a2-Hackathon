"""Feature router: /api/table-tennis (contract v1, section 8).

The same router is used by the standalone mock app and, behind a disabled-by-
default flag, by the Supervisor. It never changes the Supervisor's global
/api/events semantics.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from fastapi import APIRouter, Body, FastAPI, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from table_tennis.contracts import (
    CommandEnvelope,
    CommandResult,
    CreateMatchRequest,
    DebugOutputs,
    ErrorResponse,
    HealthResponse,
    MatchSnapshot,
    RobotCall,
    RobotCallRequest,
    RobotCancelRequest,
    RobotStatus,
)
from table_tennis.contracts.models import CapabilityStatus
from table_tennis.core.errors import NotFoundError, RefereeError
from table_tennis.core.stream import AsyncSubscriber, StreamMessage

from .auth import resolve_actor

PREFIX = "/api/table-tennis"

ERRORS = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _sse(msg: StreamMessage) -> str:
    head = f"event: {msg.event}\n"
    if msg.id:
        head += f"id: {msg.id}\n"
    return head + f"data: {msg.data}\n\n"


def build_router(get_runtime: Callable[[], "object"]) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["table-tennis"], responses=ERRORS)

    def rt():
        return get_runtime()

    def actor_of(request: Request) -> str:
        return resolve_actor(request, rt().settings)

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request, match_id: Optional[str] = Query(default=None)) -> HealthResponse:
        runtime = rt()
        actor_of(request)
        sim = runtime.settings.simulated
        status = runtime.dispatcher.output_status()
        info = runtime.adapter_info

        def cap(name: str, kind: Optional[str]) -> CapabilityStatus:
            i = info.get(name, {})
            detail = f"adapter={i.get('adapter', '?')}" + (" (dry-run)" if i.get("dry_run") else "")
            available = True
            if kind:
                st = status[kind]
                detail += f"; done={st['done']} failed={st['failed']} skipped={st['skipped']}"
                if not st["healthy"]:
                    available = False
                    detail += f"; last_error={st['last_error']}"
            return CapabilityStatus(available=available, simulated=bool(i.get("simulated", sim)), detail=detail)

        # Vision availability is match state, not a claim that an OpenCV
        # process exists. Prefer an explicitly selected match; otherwise use
        # the latest persisted match so the standalone health check follows
        # the active demo. A missing match stays unavailable and is explicit.
        vision_match_id = match_id or runtime.service.latest_match_id()
        vision = CapabilityStatus(available=False, simulated=sim, detail="no match selected; camera is not ready")
        if vision_match_id:
            snapshot = runtime.service.get_snapshot(vision_match_id)
            ready = snapshot.ready.camera_ready
            calibration = snapshot.ready.calibration_ready
            vision = CapabilityStatus(
                available=ready,
                simulated=sim,
                detail=(
                    f"match={vision_match_id}; camera_ready={ready}; "
                    f"calibration_ready={calibration}"
                ),
            )

        return HealthResponse(
            mode=runtime.settings.mode,
            simulated=sim,
            automatic_scoring_enabled=runtime.settings.features.automatic_scoring,
            auth_mode=runtime.settings.auth.mode,
            capabilities={
                "engine": CapabilityStatus(available=True, simulated=False),
                "storage": CapabilityStatus(available=True, simulated=False, detail="sqlite, single writer"),
                "robot_navigation": cap("robot_navigation", None),
                "screen": cap("screen", "display"),
                "gesture": cap("gesture", "gesture"),
                "speech": cap("speech", "speech"),
                "vision": vision,
            },
        )

    @router.get("/matches", response_model=list[str])
    def list_matches(request: Request) -> list[str]:
        actor_of(request)
        return rt().store.list_matches()

    @router.post("/matches", response_model=MatchSnapshot, status_code=201)
    def create_match(request: Request, body: CreateMatchRequest):
        actor = actor_of(request)
        snap, duplicate = rt().service.create_match(body, actor)
        if duplicate:
            return JSONResponse(status_code=200, content=jsonable_encoder(snap))
        return snap

    @router.get("/matches/{match_id}", response_model=MatchSnapshot)
    def get_match(match_id: str, request: Request) -> MatchSnapshot:
        actor_of(request)
        return rt().service.get_snapshot(match_id)

    @router.post("/matches/{match_id}/commands", response_model=CommandResult)
    def post_command(match_id: str, request: Request, command: CommandEnvelope = Body(...)) -> CommandResult:
        actor = actor_of(request)
        return rt().service.handle(match_id, command, actor)

    @router.get(
        "/matches/{match_id}/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}, "description": "SSE: snapshot + match_event"}},
    )
    async def events(
        match_id: str,
        request: Request,
        last_event_id: Optional[str] = Query(default=None),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ):
        actor_of(request)
        runtime = rt()
        service = runtime.service
        heartbeat = runtime.settings.server.sse_heartbeat_s
        loop = asyncio.get_running_loop()
        sub = AsyncSubscriber(loop)
        initial = await run_in_threadpool(service.subscribe, match_id, sub, last_event_id or last_event_id_header)

        async def gen():
            nonlocal sub
            try:
                for m in initial:
                    yield _sse(m)
                while True:
                    if await request.is_disconnected():
                        return
                    if sub.lagging:
                        service.unsubscribe(match_id, sub)
                        sub = AsyncSubscriber(loop)
                        again = await run_in_threadpool(service.subscribe, match_id, sub, None, True)
                        for m in again:
                            yield _sse(m)
                        continue
                    try:
                        msg = await asyncio.wait_for(sub.queue.get(), timeout=heartbeat)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    yield _sse(msg)
            finally:
                service.unsubscribe(match_id, sub)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/robot/calls", response_model=RobotCall, status_code=202)
    def robot_call(request: Request, body: RobotCallRequest) -> RobotCall:
        actor = actor_of(request)
        call, _dup = rt().robot.request(body, actor)
        return call

    @router.get("/robot/calls/{call_id}", response_model=RobotCall)
    def robot_call_get(call_id: str, request: Request) -> RobotCall:
        actor_of(request)
        return rt().robot.get(call_id)

    @router.post("/robot/calls/{call_id}/route", response_model=RobotCall, status_code=202)
    def robot_call_route(call_id: str, request: Request) -> RobotCall:
        """Operator confirms the path is free. Until then a real navigator sends nothing."""
        actor = actor_of(request)
        return rt().robot.confirm_route(call_id, actor)

    @router.post("/robot/calls/{call_id}/cancel", response_model=RobotCall, status_code=202)
    def robot_call_cancel(call_id: str, request: Request, body: RobotCancelRequest) -> RobotCall:
        actor = actor_of(request)
        return rt().robot.cancel(call_id, body.command_id, actor)

    @router.get("/robot/status", response_model=RobotStatus)
    def robot_status(request: Request) -> RobotStatus:
        actor_of(request)
        return rt().robot.status()

    @router.get("/debug/outputs", response_model=DebugOutputs)
    def debug_outputs(request: Request, match_id: Optional[str] = None) -> DebugOutputs:
        """Mock only: what the fake screen / speech / gesture adapters did."""
        actor_of(request)
        runtime = rt()
        if not runtime.settings.simulated:
            raise NotFoundError("not_available", "debug outputs exist only in mock mode")
        return runtime.fake_log.view(match_id)

    return router


def install_error_handlers(app: FastAPI) -> None:
    """ErrorResponse for our errors; FastAPI's default 422 format elsewhere."""

    @app.exception_handler(RefereeError)
    async def _referee_error(request: Request, exc: RefereeError):
        return JSONResponse(status_code=exc.http_status, content=exc.to_response().model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        if not request.url.path.startswith(PREFIX):
            return await request_validation_exception_handler(request, exc)
        errors = [
            {"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg")), "type": str(e.get("type"))}
            for e in exc.errors()
        ]
        body = ErrorResponse(code="invalid_request", message="request does not match contract v1", details={"errors": errors})
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))
