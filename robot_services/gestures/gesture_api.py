import argparse
import asyncio
import contextlib
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

# Ensure repository root and livekit-client are on sys.path when running as a script
REPO_ROOT = Path(__file__).resolve().parents[2]
LIVEKIT_CLIENT = REPO_ROOT / "livekit-client"
for path in (REPO_ROOT, LIVEKIT_CLIENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from humanoid_platform import GestureBackend
from robot_services.gestures import (
    DEFAULT_GESTURE_SAFETY_POOL,
    get_catalog,
    get_motion_duration_caps_ms,
    get_motion_hints,
    normalize_gesture_safety_pool,
)
from robot_services.gestures.arm_controller import RobotArmConfig, RobotArmController
from robot_services.gestures.motion_player import (
    AgibotMotionPlayerConfig,
    AgibotMotionPlayerController,
)

logger = logging.getLogger("gesture_api")

ACTIVE_GESTURE_BACKEND = os.getenv(
    "GESTURE_BACKEND",
    GestureBackend.UNITREE_G1_ARM_ACTIONS.value,
)
ACTIVE_GESTURE_CATALOG = get_catalog(os.getenv("GESTURE_CATALOG_ID"))
ACTIVE_GESTURE_SAFETY_POOL = normalize_gesture_safety_pool(
    os.getenv("GESTURE_SAFETY_POOL", DEFAULT_GESTURE_SAFETY_POOL)
)
ROBOT_ARM: RobotArmController | AgibotMotionPlayerController | None = None
GESTURE_QUEUE: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
GESTURE_WORKER: asyncio.Task | None = None



def _initialize_robot_arm() -> None:
    global ROBOT_ARM
    if ROBOT_ARM is not None:
        return

    if ACTIVE_GESTURE_BACKEND == GestureBackend.UNITREE_G1_ARM_ACTIONS.value:
        _initialize_unitree_arm()
    elif ACTIVE_GESTURE_BACKEND == GestureBackend.AGIBOT_A2_MOTION_PLAYER.value:
        _initialize_agibot_motion_player()
    else:
        raise RuntimeError(f"Unsupported gesture backend: {ACTIVE_GESTURE_BACKEND}")


def _initialize_unitree_arm() -> None:
    global ROBOT_ARM

    iface = (
        os.getenv("GESTURE_INTERFACE")
        or os.getenv("ROBOT_INTERFACE")
        or os.getenv("UNITREE_NET_IF")
        or "eth0"
    )
    try:
        ROBOT_ARM = RobotArmController(
            RobotArmConfig(
                iface=iface,
                action_by_gesture=ACTIVE_GESTURE_CATALOG.mapping,
                release_action=ACTIVE_GESTURE_CATALOG.mapping.get("release arm", 99),
            )
        )
        logger.info(
            "Robot arm controller initialized (%s, backend=%s, catalog=%s)",
            iface,
            ACTIVE_GESTURE_BACKEND,
            ACTIVE_GESTURE_CATALOG.id,
        )
    except Exception as exc:
        logger.warning("Robot arm init failed: %s", exc)
        ROBOT_ARM = None


def _initialize_agibot_motion_player() -> None:
    global ROBOT_ARM

    motion_command_url = os.getenv("AGIBOT_MOTION_COMMAND_URL", "http://192.168.100.100:56444")
    resource_service_url = os.getenv(
        "AGIBOT_RESOURCE_SERVICE_URL", "http://192.168.100.110:51049"
    )
    try:
        ROBOT_ARM = AgibotMotionPlayerController(
            AgibotMotionPlayerConfig(
                action_by_gesture=ACTIVE_GESTURE_CATALOG.mapping,
                motion_hints=get_motion_hints(ACTIVE_GESTURE_CATALOG.id),
                motion_duration_caps_ms=get_motion_duration_caps_ms(
                    ACTIVE_GESTURE_CATALOG.id
                ),
                motion_command_url=motion_command_url,
                resource_service_url=resource_service_url,
            )
        )
        logger.info(
            "Agibot motion player initialized (command=%s, resource=%s, catalog=%s)",
            motion_command_url,
            resource_service_url,
            ACTIVE_GESTURE_CATALOG.id,
        )
    except Exception as exc:
        logger.warning("Agibot motion player init failed: %s", exc)
        ROBOT_ARM = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global GESTURE_WORKER
    _initialize_robot_arm()
    GESTURE_WORKER = asyncio.create_task(_gesture_worker(), name="gesture_api_worker")
    try:
        yield
    finally:
        if GESTURE_WORKER is not None:
            GESTURE_WORKER.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await GESTURE_WORKER
            GESTURE_WORKER = None



app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "backend": ACTIVE_GESTURE_BACKEND,
        "catalog_id": ACTIVE_GESTURE_CATALOG.id,
        "gesture_names": list(ACTIVE_GESTURE_CATALOG.names),
        "active_pool": ACTIVE_GESTURE_SAFETY_POOL,
        "allowed_gestures": list(
            ACTIVE_GESTURE_CATALOG.get_allowed_gestures(ACTIVE_GESTURE_SAFETY_POOL)
        ),
    }

@app.get("/gesture")
async def gesture_query(
    name: str = Query(..., alias="name"),
    requested: bool = Query(False, alias="requested"),
    force_gesture: bool = Query(False, alias="force_gesture"),
) -> JSONResponse:
    return await _execute_gesture(name, requested=requested, force_gesture=force_gesture)


@app.get("/gesture/{gesture_name}")
async def gesture_path(
    gesture_name: str,
    requested: bool = Query(False, alias="requested"),
    force_gesture: bool = Query(False, alias="force_gesture"),
) -> JSONResponse:
    return await _execute_gesture(
        gesture_name,
        requested=requested,
        force_gesture=force_gesture,
    )


async def _execute_gesture(
    gesture: str,
    *,
    requested: bool,
    force_gesture: bool,
) -> JSONResponse:
    normalized_gesture = ACTIVE_GESTURE_CATALOG.normalize_gesture(gesture)
    if normalized_gesture is None:
        raise HTTPException(status_code=400, detail=f"Unknown gesture: {gesture}")

    if not force_gesture and not ACTIVE_GESTURE_CATALOG.is_gesture_allowed(
        normalized_gesture,
        ACTIVE_GESTURE_SAFETY_POOL,
    ):
        return JSONResponse(
            {
                "gesture": normalized_gesture,
                "status": "skipped",
                "reason": "blocked_by_safety_pool",
                "active_pool": ACTIVE_GESTURE_SAFETY_POOL,
            },
            status_code=403,
        )

    if ROBOT_ARM is None:
        return JSONResponse(
            {"gesture": normalized_gesture, "status": "skipped", "reason": "robot_unavailable"},
            status_code=503,
        )

    if GESTURE_WORKER is None or GESTURE_WORKER.done():
        return JSONResponse(
            {"gesture": normalized_gesture, "status": "skipped", "reason": "worker_unavailable"},
            status_code=503,
        )

    try:
        GESTURE_QUEUE.put_nowait(normalized_gesture)
    except asyncio.QueueFull:
        return JSONResponse(
            {"gesture": normalized_gesture, "status": "skipped", "reason": "queue_full"},
            status_code=429,
        )

    return JSONResponse(
        {
            "gesture": normalized_gesture,
            "status": "accepted",
            "queue_depth": GESTURE_QUEUE.qsize(),
            "active_pool": ACTIVE_GESTURE_SAFETY_POOL,
            "force_gesture": force_gesture,
        }
    )


async def _gesture_worker() -> None:
    while True:
        gesture = await GESTURE_QUEUE.get()
        try:
            if ROBOT_ARM is None:
                logger.warning("Dropping queued gesture because robot arm is unavailable: %s", gesture)
                continue
            result = await asyncio.to_thread(ROBOT_ARM.execute_gesture, gesture)
            logger.info("Gesture execution result: %s", result)
        except Exception:
            logger.exception("Gesture execution failed: %s", gesture)
        finally:
            GESTURE_QUEUE.task_done()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gesture API for robot control.")
    parser.add_argument("--host", default=os.getenv("GESTURE_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("GESTURE_API_PORT", "8090")))
    parser.add_argument("--log-level", default=os.getenv("GESTURE_API_LOG_LEVEL", "info"))
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn

    args = parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
