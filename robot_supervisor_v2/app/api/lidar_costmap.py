"""Live LiDAR local-costmap stream for the supervisor UI.

WHAT THIS DOES
--------------
Exposes the A2's MID-360 LiDAR as a browser-consumable Server-Sent Events stream
of robot-centred occupancy grids, for the "LiDAR" tab.

WHY A SUBPROCESS
----------------
The supervisor runs on the repo `.venv` (python3.12), which has **no rclpy** —
rclpy only exists on system python3.10 with /opt/ros/humble sourced. So we do not
subscribe to ROS in-process. Instead we spawn
`robot_services/autonomous_navigation/testing_controls/a2_costmap.py --ndjson`,
which bootstraps its own ROS environment (re-execing through a login shell that
sources setup.bash and pins ROS_DOMAIN_ID=232), and relay its stdout.

That also keeps ROS/DDS env vars out of the supervisor's own process, where they
could disturb unrelated services.

COMPUTE DISCIPLINE
------------------
The LiDAR itself is always on and always publishing — this adds no sensor load.
But the rasterising sidecar is only worth running while somebody is watching, so:
one sidecar is shared by all viewers, started on the first subscriber and stopped
`IDLE_GRACE_S` after the last one disconnects. Nobody watching => no process.

If a viewer asks for different grid parameters than the running sidecar was
started with, the sidecar is restarted with the new ones and every current viewer
follows along — this is a single-team debugging tool, not a multi-tenant service.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lidar", tags=["lidar"])

# robot_supervisor_v2/app/api/lidar_costmap.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
COSTMAP_SCRIPT = (REPO_ROOT / "robot_services" / "autonomous_navigation"
                  / "testing_controls" / "a2_costmap.py")

IDLE_GRACE_S = 5.0        # keep the sidecar this long after the last viewer leaves
QUEUE_MAX = 4             # per-client backlog; a slow tab drops frames, never stalls
STDERR_KEEP = 20          # lines of sidecar stderr retained for /status


class CostmapRelay:
    """Owns at most one sidecar process and fans its frames out to subscribers."""

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._pump: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._stop_timer: Optional[asyncio.Task] = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._params: dict[str, Any] = {}
        self._hello: Optional[dict] = None
        self._last_frame: Optional[dict] = None
        self._stderr: list[str] = []
        self._frames = 0

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        running = self._proc is not None and self._proc.returncode is None
        return {
            "running": running,
            "pid": self._proc.pid if running and self._proc else None,
            "subscribers": len(self._subscribers),
            "frames_relayed": self._frames,
            "params": self._params,
            "hello": self._hello,
            "script": str(COSTMAP_SCRIPT),
            "script_exists": COSTMAP_SCRIPT.is_file(),
            "last_seq": (self._last_frame or {}).get("seq"),
            "last_hz": (self._last_frame or {}).get("hz"),
            "last_cells": (self._last_frame or {}).get("n_cells"),
            "last_glass": (self._last_frame or {}).get("n_glass"),
            "glass_stats": (self._last_frame or {}).get("glass_stats"),
            "stderr": self._stderr[-STDERR_KEEP:],
        }

    # ---------------------------------------------------------------- process
    async def _spawn(self, params: dict[str, Any]) -> None:
        """Start the sidecar. Caller must hold the lock."""
        if not COSTMAP_SCRIPT.is_file():
            raise FileNotFoundError(f"costmap script not found: {COSTMAP_SCRIPT}")

        argv = [
            sys.executable, str(COSTMAP_SCRIPT), "--ndjson",
            "--radius", str(params["radius"]),
            "--resolution", str(params["resolution"]),
            "--decay", str(params["decay"]),
            "--min-range", str(params["min_range"]),
            "--z-min", str(params["z_min"]),
            "--z-max", str(params["z_max"]),
            "--max-hz", str(params["max_hz"]),
        ]
        # Glass detection is advisory and on by default in the sidecar; the UI
        # can turn it off or retune it without a code change.
        if params.get("glass", True):
            argv += [
                "--glass",
                "--glass-threshold", str(params["glass_threshold"]),
                "--glass-min-flicker", str(params["glass_min_flicker"]),
                "--glass-max-range", str(params["glass_max_range"]),
                "--glass-line-min-density", str(params["glass_min_density"]),
            ]
        else:
            argv += ["--no-glass"]
        # The sidecar re-execs itself through `bash -lc "source setup.bash"` when
        # rclpy is missing, so spawning it with the supervisor's own python3.12 is
        # fine — it lands on system python3.10 by itself. Drop any inherited
        # ROS_DOMAIN_ID so the child's own forcing logic is unambiguous.
        env = {k: v for k, v in os.environ.items() if k != "ROS_DOMAIN_ID"}

        logger.info("starting lidar costmap sidecar: %s", " ".join(argv))
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(COSTMAP_SCRIPT.parent),
        )
        self._params = dict(params)
        self._hello = None
        self._stderr = []
        self._pump = asyncio.create_task(self._pump_stdout())
        self._stderr_task = asyncio.create_task(self._pump_stderr())

    async def _pump_stdout(self) -> None:
        """Read NDJSON lines and push them to every subscriber."""
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    logger.debug("costmap: non-JSON line ignored: %r", line[:200])
                    continue
                if obj.get("type") == "hello":
                    self._hello = obj
                else:
                    self._last_frame = obj
                    self._frames += 1
                payload = line.decode("utf-8", "replace").rstrip("\n")
                for q in list(self._subscribers):
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        # Slow consumer: drop the OLDEST frame and keep the newest.
                        # A live view wants freshness, not a complete history.
                        try:
                            q.get_nowait()
                            q.put_nowait(payload)
                        except (asyncio.QueueEmpty, asyncio.QueueFull):
                            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("costmap stdout pump failed")

    async def _pump_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    self._stderr.append(text)
                    del self._stderr[:-STDERR_KEEP]
                    logger.info("costmap sidecar: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("costmap stderr pump failed")

    async def _stop(self) -> None:
        """Terminate the sidecar. Caller must hold the lock."""
        for task in (self._pump, self._stderr_task):
            if task is not None:
                task.cancel()
        self._pump = self._stderr_task = None

        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            logger.info("stopping lidar costmap sidecar (pid %s)", proc.pid)
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        self._hello = None
        self._last_frame = None

    async def _stop_after_grace(self) -> None:
        try:
            await asyncio.sleep(IDLE_GRACE_S)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if not self._subscribers:
                await self._stop()

    # ---------------------------------------------------------------- stream
    async def subscribe(self, params: dict[str, Any]) -> AsyncIterator[str]:
        """Yield SSE-formatted frames for one browser client."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)

        async with self._lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None

            running = self._proc is not None and self._proc.returncode is None
            if running and params != self._params:
                logger.info("costmap params changed, restarting sidecar")
                await self._stop()
                running = False
            if not running:
                await self._spawn(params)

            self._subscribers.add(queue)
            hello = self._hello

        try:
            # Replay the descriptor so a late joiner can size its canvas at once.
            if hello is not None:
                yield f"data: {json.dumps(hello, separators=(',', ':'))}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    # Comment frame: keeps proxies from closing an idle connection
                    # and lets the client tell "no data yet" from "disconnected".
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {payload}\n\n"
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
                if not self._subscribers and self._stop_timer is None:
                    self._stop_timer = asyncio.create_task(self._stop_after_grace())


relay = CostmapRelay()


@router.get("/costmap/stream")
async def costmap_stream(
    radius: float = Query(10.0, ge=1.0, le=50.0,
                          description="half-width of the window, metres"),
    resolution: float = Query(0.10, ge=0.02, le=1.0, description="cell size, metres"),
    decay: float = Query(0.80, ge=0.0, le=0.99,
                         description="per-frame confidence decay; 0 = single frame"),
    min_range: float = Query(0.40, ge=0.0, le=5.0,
                             description="discard returns closer than this (self-hits)"),
    z_min: float = Query(-0.30, ge=-2.0, le=3.0),
    z_max: float = Query(1.70, ge=-2.0, le=5.0),
    max_hz: float = Query(10.0, ge=0.0, le=30.0, description="output rate cap"),
    glass: bool = Query(True, description="flag suspected glass (ADVISORY — a "
                                          "view only, it does not affect the robot)"),
    glass_threshold: float = Query(0.45, ge=0.05, le=0.95,
                                   description="evidence needed to report a cell"),
    glass_min_flicker: float = Query(0.35, ge=0.05, le=0.9,
                                     description="minimum hit/miss flips per frame"),
    glass_max_range: float = Query(8.0, ge=1.0, le=30.0,
                                   description="ignore suspected glass beyond this"),
    glass_min_density: float = Query(0.55, ge=0.1, le=1.0,
                                     description="inlier density a segment must "
                                                 "reach to be accepted"),
):
    """SSE stream of local costmap frames. See a2_costmap.py for the frame format."""
    params = {
        "radius": radius, "resolution": resolution, "decay": decay,
        "min_range": min_range, "z_min": z_min, "z_max": z_max, "max_hz": max_hz,
        "glass": glass, "glass_threshold": glass_threshold,
        "glass_min_flicker": glass_min_flicker, "glass_max_range": glass_max_range,
        "glass_min_density": glass_min_density,
    }

    async def gen() -> AsyncIterator[str]:
        try:
            async for chunk in relay.subscribe(params):
                yield chunk
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface startup failures in the UI
            logger.exception("costmap stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # don't let a reverse proxy buffer SSE
        },
    )


@router.get("/costmap/status")
async def costmap_status() -> dict:
    """Whether the sidecar is up, how many viewers, and its recent stderr."""
    return relay.status()
