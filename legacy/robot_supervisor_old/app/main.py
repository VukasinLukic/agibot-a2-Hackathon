from __future__ import annotations

import logging
import os
import time
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .config import load_config
from .conversations import ConversationManager, load_conversation_settings
from .conversation_orchestrator import ConversationOrchestrator
from .controllers.logs import LogController, LogProviderError
from .controllers.services import ServiceController
from .engagement import EngagementController, EngagementError, load_engagement_settings
from .models import (
    AuditEntry,
    ConversationState,
    EngagementStatus,
    ServiceConfigResponse,
    ServiceConfigUpdateRequest,
    ServiceStatus,
    SupervisorStatus,
)
from .service_manager import ServiceManagerError, UnknownServiceError

APP_VERSION = "0.1.0"
logger = logging.getLogger("robot_supervisor")

config = load_config()
controller = ServiceController(config)
log_controller = LogController(config)
conversation_manager = ConversationManager(load_conversation_settings())
engagement_controller = EngagementController(controller, load_engagement_settings())
def _parse_service_list(raw: Optional[str], default: str = "") -> List[str]:
    source = raw if raw is not None else default
    return [item.strip() for item in source.split(",") if item.strip()]


conversation_required_services = _parse_service_list(
    os.getenv("ROBOT_SUPERVISOR_CONVERSATION_SERVICES"),
    default="livekit,voice-agent",
)
conversation_trigger_services = _parse_service_list(
    os.getenv("ROBOT_SUPERVISOR_CONVERSATION_TRIGGER_SERVICES"),
    default="audio-bridge",
)
conversation_agent_service = os.getenv("ROBOT_SUPERVISOR_CONVERSATION_AGENT_SERVICE", "voice-agent")
conversation_orchestrator = ConversationOrchestrator(
    controller,
    conversation_manager,
    conversation_required_services,
    trigger_services=conversation_trigger_services,
    agent_service=conversation_agent_service,
)
controller.set_before_stop_hook(conversation_orchestrator.notify_service_stopping)
_auth_token = os.getenv("ROBOT_SUPERVISOR_TOKEN")
if not _auth_token:
    raise RuntimeError("ROBOT_SUPERVISOR_TOKEN must be set (e.g., in .env or the process environment)")

_log_stream_tasks: Dict[asyncio.Task, WebSocket] = {}


async def _close_log_streams() -> None:
    pending = [(task, ws) for task, ws in _log_stream_tasks.items() if not task.done()]
    if not pending:
        return
    print(f"[log-stream] closing {len(pending)} active stream(s)", flush=True)
    close_tasks = []
    for task, websocket in pending:
        task.cancel()
        close_tasks.append(
            asyncio.create_task(
                _close_websocket(websocket, code=1012, reason="Supervisor shutting down")
            )
        )
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    await asyncio.gather(*(task for task, _ in pending), return_exceptions=True)


async def _close_websocket(websocket: WebSocket, code: int, reason: str) -> None:
    with suppress(Exception):
        await websocket.close(code=code, reason=reason)


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    try:
        await websocket.receive()
    except WebSocketDisconnect:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    await conversation_orchestrator.start()
    try:
        yield
    finally:
        logger.info("Supervisor shutting down; stopping managed services")
        await _close_log_streams()
        await conversation_orchestrator.stop()
        controller.stop_all()


app = FastAPI(title="Robot Supervisor", version=APP_VERSION, lifespan=lifespan)
AUDIT: List[AuditEntry] = []
AUDIT_LIMIT = 500
UI_TEMPLATE = (Path(__file__).resolve().parent / "ui" / "index.html").read_text(encoding="utf-8")


class ServiceModeRequest(BaseModel):
    mode: str


class EngagementDispatchRequest(BaseModel):
    room: Optional[str] = None
    restart_livekit: bool = False


class EngagementWrapRequest(BaseModel):
    reset_voice_agent: bool = True


@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
def ui_root() -> HTMLResponse:
    return HTMLResponse(content=UI_TEMPLATE)


@app.exception_handler(ServiceManagerError)
async def service_error_handler(_, exc: ServiceManagerError):
    status_code = status.HTTP_404_NOT_FOUND if isinstance(exc, UnknownServiceError) else status.HTTP_400_BAD_REQUEST
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.exception_handler(LogProviderError)
async def log_error_handler(_, exc: LogProviderError):
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


def require_bearer_token(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    if token != _auth_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    return "token-user"


def _validate_token_value(token: Optional[str]) -> str:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    if token != _auth_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    return "token-user"


def _record_audit(actor: str, action: str, target: str, ok: bool, detail: Optional[str] = None) -> None:
    AUDIT.append(
        AuditEntry(
            timestamp=time.time(),
            actor=actor,
            action=action,
            target=target,
            ok=ok,
            detail=detail,
        )
    )
    if len(AUDIT) > AUDIT_LIMIT:
        del AUDIT[:-AUDIT_LIMIT]


@app.get("/api/v1/status", response_model=SupervisorStatus)
def api_status(actor: str = Depends(require_bearer_token)) -> SupervisorStatus:
    _ = actor
    services = controller.list_statuses()
    return SupervisorStatus(version=APP_VERSION, now=time.time(), services=services)


@app.get("/api/v1/services", response_model=List[ServiceStatus])
def api_list_services(actor: str = Depends(require_bearer_token)) -> List[ServiceStatus]:
    _ = actor
    return controller.list_statuses()


@app.get("/api/v1/services/{service}", response_model=ServiceStatus)
def api_get_service(service: str, actor: str = Depends(require_bearer_token)) -> ServiceStatus:
    _ = actor
    return controller.status(service)


@app.post("/api/v1/services/{service}/start", response_model=ServiceStatus)
def api_start_service(service: str, actor: str = Depends(require_bearer_token)) -> ServiceStatus:
    return _perform_service_action(actor, "start", service, lambda: controller.start(service))


@app.post("/api/v1/services/{service}/stop", response_model=ServiceStatus)
def api_stop_service(service: str, actor: str = Depends(require_bearer_token)) -> ServiceStatus:
    return _perform_service_action(actor, "stop", service, lambda: controller.stop(service))


@app.post("/api/v1/services/{service}/restart", response_model=ServiceStatus)
def api_restart_service(service: str, actor: str = Depends(require_bearer_token)) -> ServiceStatus:
    return _perform_service_action(actor, "restart", service, lambda: controller.restart(service))


@app.get("/api/v1/engagement", response_model=EngagementStatus)
def api_engagement_status(actor: str = Depends(require_bearer_token)) -> EngagementStatus:
    _ = actor
    return engagement_controller.status()


@app.post("/api/v1/engagement/dispatch", response_model=EngagementStatus)
def api_engagement_dispatch(
    req: EngagementDispatchRequest = EngagementDispatchRequest(),
    actor: str = Depends(require_bearer_token),
) -> EngagementStatus:
    detail = f"room={req.room or 'default'}"
    try:
        result = engagement_controller.dispatch(room=req.room, restart_livekit=req.restart_livekit)
    except EngagementError as exc:
        _record_audit(actor, "engagement_dispatch", "voice-agent", False, detail)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    else:
        _record_audit(actor, "engagement_dispatch", "voice-agent", True, detail)
        return result


@app.post("/api/v1/engagement/wrap", response_model=EngagementStatus)
def api_engagement_wrap(
    req: EngagementWrapRequest = EngagementWrapRequest(),
    actor: str = Depends(require_bearer_token),
) -> EngagementStatus:
    detail = f"reset_voice_agent={req.reset_voice_agent}"
    try:
        result = engagement_controller.wrap(reset_voice_agent=req.reset_voice_agent)
    except EngagementError as exc:
        _record_audit(actor, "engagement_wrap", "voice-agent", False, detail)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    else:
        _record_audit(actor, "engagement_wrap", "voice-agent", True, detail)
        return result


@app.post("/api/v1/services/{service}/mode", response_model=ServiceStatus)
def api_set_service_mode(
    service: str,
    req: ServiceModeRequest,
    actor: str = Depends(require_bearer_token),
) -> ServiceStatus:
    detail = f"mode={req.mode}"
    return _perform_service_action(actor, "mode", service, lambda: controller.set_mode(service, req.mode), detail=detail)


@app.get("/api/v1/services/{service}/config", response_model=ServiceConfigResponse)
def api_service_config(service: str, actor: str = Depends(require_bearer_token)) -> ServiceConfigResponse:
    _ = actor
    return controller.config(service)


@app.put("/api/v1/services/{service}/config", response_model=ServiceStatus)
def api_update_service_config(
    service: str,
    req: ServiceConfigUpdateRequest,
    actor: str = Depends(require_bearer_token),
) -> ServiceStatus:
    detail = f"parameters={len(req.parameters)}"
    return _perform_service_action(
        actor,
        "config",
        service,
        lambda: controller.update_config(service, req.parameters),
        detail=detail,
    )


@app.get("/api/v1/services/{service}/logs")
def api_service_logs(
    service: str,
    lines: int = Query(default=200, ge=1, le=5000),
    actor: str = Depends(require_bearer_token),
) -> dict:
    try:
        text = log_controller.tail(service, lines)
    except LogProviderError:
        _record_audit(actor, "logs_tail", service, False, f"lines={lines}")
        raise
    else:
        _record_audit(actor, "logs_tail", service, True, f"lines={lines}")
        return {"service": service, "lines": lines, "text": text}


@app.get("/api/v1/audit", response_model=List[AuditEntry])
def api_audit(actor: str = Depends(require_bearer_token)) -> List[AuditEntry]:
    _ = actor
    return AUDIT[-100:]


@app.get("/api/v1/conversations", response_model=ConversationState)
async def api_conversations(actor: str = Depends(require_bearer_token)) -> ConversationState:
    _ = actor
    return conversation_manager.state()


@app.websocket("/api/v1/services/{service}/logs/stream")
async def ws_service_logs(websocket: WebSocket, service: str, token: Optional[str] = Query(default=None)) -> None:
    try:
        actor = _validate_token_value(token)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=exc.detail)
        return

    await websocket.accept()
    current_task = asyncio.current_task()
    if current_task:
        _log_stream_tasks[current_task] = websocket
    _record_audit(actor, "logs_stream", service, True, None)
    disconnect_task = asyncio.create_task(_wait_for_disconnect(websocket))
    stream_iter = log_controller.stream(service)
    line_task: Optional[asyncio.Task[str]] = None
    try:
        while True:
            if disconnect_task.done():
                break
            if line_task is None:
                line_task = asyncio.create_task(stream_iter.__anext__())
            done, _ = await asyncio.wait(
                [line_task, disconnect_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                line_task.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await line_task
                break
            if line_task in done:
                try:
                    line = line_task.result()
                except StopAsyncIteration:
                    break
                line_task = None
                await websocket.send_text(line)
    except LogProviderError as exc:
        await websocket.send_text(f"[log-error] {exc}")
        await websocket.close(code=1011, reason=str(exc))
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        await _close_websocket(websocket, code=1012, reason="Supervisor shutting down")
        raise
    finally:
        disconnect_task.cancel()
        with suppress(Exception):
            await disconnect_task
        if line_task:
            line_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await line_task
        with suppress(Exception):
            await stream_iter.aclose()
        if current_task:
            _log_stream_tasks.pop(current_task, None)

def _perform_service_action(
    actor: str,
    action: str,
    target: str,
    func: Callable[[], ServiceStatus],
    detail: Optional[str] = None,
) -> ServiceStatus:
    try:
        result = func()
    except ServiceManagerError:
        _record_audit(actor, action, target, False, detail or f"{action} failed")
        raise
    else:
        _record_audit(actor, action, target, True, detail)
        return result


__all__ = ["app"]
