"""Navigation missions: map view, waypoint routes, per-waypoint actions.

WHAT THIS TAB IS FOR
--------------------
Build an ordered route out of a map's waypoints, say what the robot should do when it
arrives at each one, run it, and watch the real planned path while it drives.

THE THREE DATA SOURCES, AND WHY EACH IS FETCHED THE WAY IT IS
--------------------------------------------------------------
1. MAP + WAYPOINTS -> read straight off disk, in-process.
   `a2_map.py` already decodes the A2's non-standard occupancy format (RGB 3-colour,
   TOP-LEFT origin with v pointing DOWN, and a yaml whose free/obstacle triples are
   BGR). The supervisor's own venv has numpy/PIL/yaml, so no subprocess is needed.
   We reuse a2_map rather than re-deriving the format, because getting it wrong puts
   waypoints hundreds of pixels off and makes white unknown space look free.

2. PLANNED PATH + REPLAN EVENTS -> `a2_nav_stream.py` sidecar over NDJSON.
   These only exist as AimRT/ROS2 topics, and the supervisor venv (python3.12) has no
   rclpy. Same sidecar pattern as the LiDAR costmap tab. See a2_nav_stream.py for the
   four traps involved (mangled topic names, generic wrapper message, list-of-bytes
   payload, BEST_EFFORT-only QoS).

3. POSE -> also the sidecar, off ROS 2 `/tf`.
   CORRECTED 2026-09-09. This originally called
   `TransFormService/GetTransFormation{map, base_link}` over HTTP-RPC. That RPC is
   registered on the gateway but is BROKEN on this build: it returns an empty body
   and blocks until the caller's timeout — verified with the robot standing,
   localized and freshly relocalized from the tablet. It was the sole reason the tab
   permanently reported "no live pose" and never drew the robot.
   slam publishes the pose natively on `/tf` (map->base_link) and on
   `/slam/localization/odometry`, so the sidecar reads it there. Consequence: pose
   exists only while the live stream is up, which is exactly when someone is looking
   at the map. The relay caches the newest one and drops it after 5 s, because a
   frozen dot would misreport where the robot is.

   Beware `SLAMGetCurrentLocalizationState`: isRunning=true means a localization
   SESSION is running, not that it converged. It can sit in an init retry loop
   ("localization init failed, confidence: 1.67", loc state 4) indefinitely, with no
   pose. Never use isRunning alone as a readiness gate.

4. TASK STATE -> HTTP-RPC to the on-Orin gateway, in-process. These calls are fast
   and reliable, unlike the TF one.

WHY MISSIONS RUN SERVER-SIDE
----------------------------
A mission walks a two-legged robot around a room. If the runner lived in the browser,
closing the tab or losing wifi mid-leg would leave the robot walking with nobody
sequencing or cancelling it. So the runner is an asyncio task in this process, the UI
only starts/cancels it and observes, and there is at most ONE runner because there is
one robot.

SAFETY
------
Starting a mission is gated on the same check that `a2_nav.py doctor` performs: if the
MC action gate is closed (McAction_DEFAULT, e.g. after an E-stop) the robot cannot
step, and pnc would report RUNNING forever without moving. We refuse up front with an
explanation instead of launching a mission that silently does nothing.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import math
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nav", tags=["navigation"])

# robot_supervisor_v2/app/api/nav_missions.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
NAV_DIR = REPO_ROOT / "robot_services" / "autonomous_navigation" / "testing_controls"
STREAM_SCRIPT = NAV_DIR / "a2_nav_stream.py"
MISSION_FILE = REPO_ROOT / "robot_supervisor_v2" / "state" / "nav_missions.json"

# a2_nav.py / a2_map.py are the tested clients for this robot. Import them rather
# than re-implementing their hard-won details (the map format traps, the action-gate
# preflight, the "ActionCancel needs the real task_id" rule).
if str(NAV_DIR) not in sys.path:
    sys.path.insert(0, str(NAV_DIR))

IDLE_GRACE_S = 5.0     # keep the sidecar this long after the last viewer leaves
QUEUE_MAX = 8          # per-client backlog; a slow tab drops frames, never stalls
STDERR_KEEP = 20

COSTMAP_SCRIPT = NAV_DIR / "a2_costmap.py"
# --------------------------------------------------------------- glass survey
# The detector needs the robot STATIONARY, and it needs a few multi-second
# windows to settle. Measured 2026-09-23: from about 0.05 m/s -- a crawl -- it
# finds nothing at all, so "drive slower and look as you go" is not a thing that
# works. Surveying therefore happens only during a deliberate stop.
GLASS_SURVEY_MIN_S = 12.0        # below this the detector never leaves `settling`
GLASS_SURVEY_DEFAULT_S = 20.0
GLASS_SURVEY_SETTLE_ALLOWANCE_S = 30.0   # extra grace while waiting to see
GLASS_SURVEY_RADIUS_M = 8.0
STATE_PERIOD_S = 1.0
# The supervisor already curates which gestures this robot may perform;
# reuse that rather than inventing a second list that could drift.
GESTURE_CATALOG_ID = "agibot_a2_ultra"
GESTURE_SAFETY_POOL = "safe_only"


def _import_nav():
    """Import a2_nav lazily so a broken robot script cannot stop the supervisor."""
    import a2_nav
    return a2_nav


def _import_map():
    import a2_map
    return a2_map


# --------------------------------------------------------------------------- #
# maps
# --------------------------------------------------------------------------- #
_map_cache: dict[str, Any] = {}


def _load_map(map_id: str):
    """Load and cache an A2Map. Maps are immutable once recorded, so caching is safe."""
    if map_id in _map_cache:
        return _map_cache[map_id]
    a2_map = _import_map()
    store = a2_map.MapStore()
    m = store.load(map_id)
    _map_cache[map_id] = m
    return m


@router.get("/maps")
async def list_maps() -> dict:
    """Every recorded map, newest first, plus which one pnc is currently working on.

    `map_id` is the creation time in epoch MILLISECONDS, so ordering by it is
    reliable; `map_version` is not (some maps report a save time before creation).
    """
    def work():
        a2_map, a2_nav = _import_map(), _import_nav()
        store = a2_map.MapStore()
        try:
            current = a2_nav.current_map()
        except Exception as exc:
            logger.warning("could not read current working map: %s", exc)
            current = None
        maps = []
        for m in store.maps():
            maps.append({
                "map_id": m.map_id,
                "name": m.name,
                "index": m.index,
                "exists": m.exists,
                "recorded": m.recorded.isoformat() if m.recorded else None,
                "waypoint_count": len(m.waypoints),
                "is_current": str(m.map_id) == str(current),
            })
        return {"maps": maps, "current_map_id": str(current) if current else None}

    return await asyncio.to_thread(work)


@router.get("/maps/{map_id}/meta")
async def map_meta(map_id: str) -> dict:
    """Grid geometry + waypoints, in BOTH world metres and image pixels.

    The frontend needs the pixel coordinates to place markers over the PNG, and the
    world coordinates to send goals. Doing the conversion here (via a2_map's
    GridInfo) means the browser never re-implements the top-left-origin rule.
    """
    def work():
        m = _load_map(map_id)
        g = m.grid
        wps = []
        for w in m.meta.waypoints:
            u, v = g.world_to_pixel(w.x, w.y)
            wps.append({"id": w.id, "name": w.name,
                        "x": w.x, "y": w.y, "theta": w.theta,
                        "u": round(u, 2), "v": round(v, 2),
                        "in_bounds": g.contains_px(u, v)})
        return {
            "map_id": str(m.meta.map_id),
            "name": m.meta.name,
            "grid": {"resolution": g.resolution, "width": g.width, "height": g.height,
                     "origin_x": g.origin_x, "origin_y": g.origin_y},
            # Same formula the frontend must use: u=(x-ox)/res, v=(oy-y)/res
            "waypoints": wps,
        }

    try:
        return await asyncio.to_thread(work)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@router.get("/maps/{map_id}/image")
async def map_image(map_id: str) -> Response:
    """The occupancy grid as a PNG the browser can drawImage() directly.

    We re-colour rather than serving occupancy_map.png as-is, because that file uses
    WHITE for UNKNOWN and a pale blue for FREE — which reads backwards to a human and
    makes unmapped space look like open floor. Here: white = free, dark = obstacle,
    mid-grey = never mapped.
    """
    def work() -> bytes:
        import numpy as np
        from PIL import Image
        m = _load_map(map_id)
        occ = m.occupancy                     # int8: -1 unknown, 0 free, 100 occupied
        h, w = occ.shape
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        rgb[occ == 0] = (255, 255, 255)       # free
        rgb[occ < 0] = (170, 174, 184)        # unknown / never mapped
        rgb[occ > 0] = (33, 37, 46)           # occupied
        buf = io.BytesIO()
        Image.fromarray(rgb, "RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    try:
        png = await asyncio.to_thread(work)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    # Maps never change after recording, so let the browser keep it.
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


# --------------------------------------------------------------------------- #
# live robot state
# --------------------------------------------------------------------------- #
def _read_pose() -> Optional[dict]:
    """Latest map->base_link pose, or None when it is not available.

    POSE COMES FROM THE SIDECAR, NOT FROM AN RPC (changed 2026-09-09).
    `TransFormService/GetTransFormation` is registered on the gateway but is broken
    on this build: it returns an empty body and blocks until the caller's timeout,
    even with the robot standing, localized and freshly relocalized. Calling it at
    all was the reason this tab always said "no live pose".

    slam publishes the pose to ROS 2 natively on `/tf` (map->base_link), so
    a2_nav_stream.py reads it there and the relay caches the newest one. That means
    pose is only available while the live stream is running — which is exactly when
    somebody is looking at the map — and costs nothing when it is not.
    """
    return relay.last_pose()


def _read_nav_state() -> dict:
    """pnc task state + the MC action gate, in one blob for the UI header."""
    a2_nav = _import_nav()
    out: dict[str, Any] = {}
    try:
        a = a2_nav.action_state()
        out["pnc_state"] = a.get("state")
        out["pnc_info"] = a.get("info")
        out["pnc_task_id"] = str(a.get("task_id") or "")
    except Exception as exc:
        out["pnc_error"] = str(exc)
    try:
        act, status = a2_nav.mc_action()
        out["mc_action"] = act
        out["mc_action_status"] = status
        out["can_walk"] = a2_nav.can_walk(act)
    except Exception as exc:
        out["mc_error"] = str(exc)
    try:
        out["localization_running"] = a2_nav.localization_running()
    except Exception:
        out["localization_running"] = None
    return out


@router.get("/pose")
async def get_pose() -> dict:
    pose = _read_pose()
    running = relay.status()["running"]
    if pose:
        note = None
    elif not running:
        note = ("Pose is carried on the live stream. Open /api/nav/live/stream (the "
                "Navigation tab does this automatically) and it will appear.")
    else:
        note = ("Stream is up but /tf has no map->base_link transform: localization "
                "is started but has not converged. Relocalize from the tablet.")
    return {"pose": pose, "stream_running": running, "note": note}


@router.get("/glass")
async def get_glass() -> dict:
    """Glass planes found during the current/most recent run, in map metres.

    ADVISORY. These do not enter vectorflux's costmap, do not change routing and
    will not stop the robot. `waypoints_incomplete` counts stops where a clean
    look was never achieved — those are places nothing is known about, which is
    NOT the same as places known to be clear.
    """
    return runner.glass()


@router.get("/status")
async def nav_status() -> dict:
    """Everything the UI needs to decide whether a mission can run at all.

    Mirrors `a2_nav.py doctor`: the MC action gate is the check that matters, because
    a closed gate means pnc accepts and reports RUNNING while the robot stands still.
    """
    state = await asyncio.to_thread(_read_nav_state)
    pose = await asyncio.to_thread(_read_pose)
    blockers: list[str] = []
    if state.get("can_walk") is False:
        blockers.append(
            f"MC action is {state.get('mc_action')} — the legs cannot step. "
            f"Run `a2_nav.py arm --execute` (it powers the legs) and re-check.")
    if state.get("localization_running") is False:
        blockers.append("Localization is not running — waypoint navigation is "
                        "impossible until you relocalize.")
    # Only treat a missing pose as a blocker when the stream that carries it is
    # actually up. Otherwise this endpoint would always report "no pose" simply
    # because nobody has the map open, which is what it used to do.
    if pose is None and relay.status()["running"]:
        blockers.append(
            "No live pose: the sidecar is running but /tf is not carrying a "
            "map->base_link transform. Localization is started but has not "
            "converged — check `tail /agibot/log/slam/running_slam.log` for "
            "'localization init failed', and relocalize from the tablet.")
    return {**state, "pose": pose, "blockers": blockers, "hold": hold.status(),
            "pose_source": "live-stream (/tf via a2_nav_stream)",
            "stream_running": relay.status()["running"],
            "runner": runner.status()}


# --------------------------------------------------------------------------- #
# the live SSE stream: sidecar frames + pose + task state, one connection
# --------------------------------------------------------------------------- #
class NavRelay:
    """Owns at most one a2_nav_stream sidecar and fans its frames out to viewers.

    Same lifecycle discipline as the costmap relay: started on the first subscriber,
    stopped IDLE_GRACE_S after the last one leaves, so nobody watching => no process.
    Pose and task state are polled here too, so a viewer opens ONE EventSource and
    gets path, replan events, pose and pnc state interleaved.
    """

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._tasks: list[asyncio.Task] = []
        self._stop_timer: Optional[asyncio.Task] = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._hello: Optional[dict] = None
        self._last_path: Optional[dict] = None
        self._last_pose: Optional[dict] = None
        self._last_pose_at: float = 0.0
        self._stderr: list[str] = []
        self._frames = 0

    def last_pose(self, max_age_s: float = 5.0) -> Optional[dict]:
        """Newest pose from the sidecar, or None if stale/absent.

        Staleness matters: if localization drops out the sidecar simply stops
        emitting, and a frozen dot on the map would be a lie about where the robot
        is. Better to show nothing.
        """
        if self._last_pose is None:
            return None
        if time.time() - self._last_pose_at > max_age_s:
            return None
        return self._last_pose

    def status(self) -> dict:
        running = self._proc is not None and self._proc.returncode is None
        return {"running": running,
                "pid": self._proc.pid if running and self._proc else None,
                "subscribers": len(self._subscribers),
                "frames_relayed": self._frames,
                "script": str(STREAM_SCRIPT),
                "script_exists": STREAM_SCRIPT.is_file(),
                "last_path_points": (self._last_path or {}).get("n"),
                "stderr": self._stderr[-STDERR_KEEP:]}

    def _broadcast(self, obj: dict) -> None:
        payload = json.dumps(obj, separators=(",", ":"))
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Live view: prefer the newest frame over a complete history.
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def _spawn(self) -> None:
        if not STREAM_SCRIPT.is_file():
            raise FileNotFoundError(f"nav stream script not found: {STREAM_SCRIPT}")
        argv = [sys.executable, str(STREAM_SCRIPT), "--min-path-interval", "0.2"]
        # The sidecar re-execs through `bash -lc "source setup.bash"` when rclpy is
        # missing, so launching it with the supervisor's python3.12 is fine. Drop any
        # inherited ROS_DOMAIN_ID so the child's own forcing logic is unambiguous.
        env = {k: v for k, v in os.environ.items() if k != "ROS_DOMAIN_ID"}
        logger.info("starting nav stream sidecar: %s", " ".join(argv))
        self._proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=str(NAV_DIR))
        self._hello = None
        self._stderr = []
        self._tasks = [asyncio.create_task(self._pump_stdout()),
                       asyncio.create_task(self._pump_stderr()),
                       asyncio.create_task(self._poll_state())]

    async def _pump_stdout(self) -> None:
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
                    logger.debug("nav stream: non-JSON line %r", line[:200])
                    continue
                kind = obj.get("type")
                if kind == "hello":
                    self._hello = obj
                elif kind == "path":
                    self._last_path = obj
                elif kind == "pose":
                    self._last_pose = obj.get("pose")
                    self._last_pose_at = time.time()
                self._frames += 1
                self._broadcast(obj)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("nav stream stdout pump failed")

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
                    logger.info("nav stream sidecar: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("nav stream stderr pump failed")

    # NOTE: there is no _poll_pose task any more. Pose used to be polled here over
    # HTTP-RPC; that RPC is broken on this build (empty body, blocks until timeout),
    # which is why the map never showed the robot. The sidecar now reads pose from
    # /tf and emits it directly, so _pump_stdout relays and caches it.

    async def _poll_state(self) -> None:
        try:
            while True:
                st = await asyncio.to_thread(_read_nav_state)
                st.update({"type": "state", "stamp": time.time(),
                           "runner": runner.status(), "hold": hold.status()})
                self._broadcast(st)
                await asyncio.sleep(STATE_PERIOD_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("state poll failed")

    async def _stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            logger.info("stopping nav stream sidecar (pid %s)", proc.pid)
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        self._hello = None
        self._last_path = None

    async def _stop_after_grace(self) -> None:
        try:
            await asyncio.sleep(IDLE_GRACE_S)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if not self._subscribers:
                await self._stop()

    @contextlib.asynccontextmanager
    async def hold_open(self):
        """Keep the sidecar alive without being a browser client.

        Pose only exists while the sidecar runs, and the sidecar only runs while
        something is subscribed. A glass survey needs pose to place its findings
        on the map, so the mission runner holds a subscription of its own for the
        duration — otherwise a survey would work only when somebody happened to
        have the Navigation tab open.

        The queue is drained and discarded: this is a lifetime handle, not a
        reader. It still must be drained, or a full queue would make _broadcast
        do pointless evict-and-retry work on every frame.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        async with self._lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
            if self._proc is None or self._proc.returncode is not None:
                await self._spawn()
            self._subscribers.add(queue)

        async def drain() -> None:
            while True:
                await queue.get()

        drainer = asyncio.create_task(drain())
        try:
            yield
        finally:
            drainer.cancel()
            async with self._lock:
                self._subscribers.discard(queue)
                if not self._subscribers and self._stop_timer is None:
                    self._stop_timer = asyncio.create_task(self._stop_after_grace())

    async def subscribe(self) -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        async with self._lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
            if self._proc is None or self._proc.returncode is not None:
                await self._spawn()
            self._subscribers.add(queue)
            hello, last_path = self._hello, self._last_path
        try:
            for replay in (hello, last_path):
                if replay is not None:
                    yield f"data: {json.dumps(replay, separators=(',', ':'))}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {payload}\n\n"
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
                if not self._subscribers and self._stop_timer is None:
                    self._stop_timer = asyncio.create_task(self._stop_after_grace())


relay = NavRelay()


@router.get("/live/stream")
async def live_stream():
    """One SSE stream carrying `path`, `avoid`, `pose`, `state` and `mission` events."""
    async def gen() -> AsyncIterator[str]:
        try:
            async for chunk in relay.subscribe():
                yield chunk
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("nav live stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


@router.get("/live/status")
async def live_status() -> dict:
    return relay.status()


# --------------------------------------------------------------------------- #
# waypoints: create one from a canvas click
# --------------------------------------------------------------------------- #
class NewWaypoint(BaseModel):
    """A waypoint to add to a map.

    Coordinates are IMAGE PIXELS, because that is what `LocalizationService/
    SetNaviPoint` takes and what a canvas click naturally produces. The frontend
    sends the pixel it clicked; no world<->pixel round trip is needed.
    """
    u: int = Field(..., description="pixel column in occupancy_map.png")
    v: int = Field(..., description="pixel row (0 = TOP of the image)")
    name: str = Field(..., min_length=1, max_length=64)
    theta: float = Field(0.0, description="desired world heading, radians CCW from +x")


@router.post("/maps/{map_id}/waypoints")
async def add_waypoint(map_id: str, body: NewWaypoint) -> dict:
    """Add a navigation point to a stored map.

    Two details that are easy to get wrong:

    * `point_id = 0` means "create"; a positive id EDITS that waypoint. We always
      send 0 here, so this endpoint can only add.
    * `PixelPose.angle` is measured from the +u axis rotating TOWARD +v. Because v
      points DOWN the image, that is the clockwise direction, so it is the NEGATIVE
      of a normal world yaw. We convert, otherwise every saved heading is mirrored.
    """
    def work():
        a2_nav = _import_nav()
        payload = {
            "header": {},
            "command": "TopoCommand_SET_NAVI_POINT",
            "map_id": int(map_id),
            "point": {
                "point_id": 0,                     # 0 = add
                "name": body.name,
                "point_type": "NaviPointType_NAVI_POINT",
                "pixel_pose": {"position": {"u": int(body.u), "v": int(body.v)},
                               "angle": -float(body.theta)},   # see docstring
            },
        }
        out = a2_nav.post(f"{a2_nav.LOC}/SetNaviPoint", payload)
        return {"point_id": (out.get("data") or {}).get("point_id"), "raw": out}

    try:
        res = await asyncio.to_thread(work)
    except Exception as exc:
        raise HTTPException(502, f"SetNaviPoint failed: {exc}")
    # The map's waypoint list lives in map.db, which we cache — drop it so the next
    # /meta read shows the new point.
    _map_cache.pop(str(map_id), None)
    return res


# --------------------------------------------------------------------------- #
# missions
# --------------------------------------------------------------------------- #
ACTION_TYPES = ("dwell", "turn", "speak", "gesture", "script")

# WHICH ACTIONS MAY RUN AT THE SAME TIME
# ---------------------------------------
# A per-type allow-list rather than one global "concurrent_available" flag, because
# the interesting constraint is PHYSICAL and differs per pair. The matrix is
# symmetric and is validated as such at import time.
#
# The rule that actually matters here: a GESTURE is played by motion_player, which
# streams whole-body joint targets and therefore forces the MC into
# McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO. A TURN is SpinTurn, which needs a
# LOCOMOTION action. Those two cannot both own the body, so gesture+turn is refused
# — that is exactly the class of "physically impossible" combination this table
# exists to catch, and it is the same action-gate conflict that makes the idle
# animation break navigation.
#
# speak is audio-only, so it composes with anything. dwell is just a timer.
# script is deliberately isolated: it can do anything at all, so we cannot reason
# about what it would collide with.
CONCURRENCY_RULES: dict[str, tuple[str, ...]] = {
    "dwell":   ("speak", "gesture", "turn"),
    "turn":    ("speak", "dwell"),
    "speak":   ("gesture", "dwell", "turn"),
    "gesture": ("speak", "dwell"),
    "script":  (),
}

# Fail loudly at import if the table is ever edited into an asymmetric state,
# rather than letting the UI offer a pairing the validator would then reject.
for _a, _peers in CONCURRENCY_RULES.items():
    for _b in _peers:
        if _a not in CONCURRENCY_RULES.get(_b, ()):
            raise RuntimeError(
                f"CONCURRENCY_RULES is asymmetric: {_a} allows {_b} but not vice versa")


class Action(BaseModel):
    """Something to do on arrival at a step.

    `concurrent` holds actions that run AT THE SAME TIME as this one; the group
    finishes when its slowest member does. Nesting is one level deep on purpose —
    a tree of concurrency would be much harder to reason about on a real robot than
    the flat "this, plus these alongside it" the UI presents.
    """
    type: str = Field(..., description=" | ".join(ACTION_TYPES))
    seconds: float = Field(0.0, ge=0, le=600, description="dwell: how long to wait")
    radians: float = Field(0.0, ge=-6.284, le=6.284, description="turn: spin angle")
    script: str = Field("", description="script: file name inside testing_controls/")
    args: list[str] = Field(default_factory=list, description="script: extra argv")
    text: str = Field("", max_length=1000, description="speak: what to say")
    gesture: str = Field("", description="gesture: a name from GET /api/nav/gestures")
    concurrent: list["Action"] = Field(
        default_factory=list,
        description="actions performed simultaneously with this one")


Action.model_rebuild()      # resolve the self-reference in `concurrent`


class Step(BaseModel):
    """Go to one waypoint, then perform its actions (in order)."""
    waypoint_id: int = Field(..., description="target_id within the mission's map")
    label: str = ""
    actions: list[Action] = Field(default_factory=list)


class Mission(BaseModel):
    id: str = ""
    name: str = Field(..., min_length=1, max_length=120)
    map_id: str
    steps: list[Step] = Field(default_factory=list)
    loop: bool = Field(False, description="repeat until cancelled")
    glass_survey: bool = Field(
        False,
        description="stand still at each waypoint and look for glass. ADVISORY: "
                    "findings are drawn on the map, they do not change where the "
                    "robot will go or stop it hitting anything")
    glass_survey_seconds: float = Field(
        GLASS_SURVEY_DEFAULT_S, ge=GLASS_SURVEY_MIN_S, le=120,
        description="how long to stand still at each waypoint, once the detector "
                    f"reports it can see (minimum {GLASS_SURVEY_MIN_S:.0f} s)")


class MissionStore:
    """Missions on disk as one JSON file.

    A JSON file (not the faces sqlite db) because missions are a handful of small
    documents a human edits, and being able to read/diff/hand-edit them is worth
    more here than query power.
    """

    def __init__(self, path: Path = MISSION_FILE) -> None:
        self.path = path

    def _read(self) -> dict[str, dict]:
        try:
            with open(self.path) as fh:
                data = json.load(fh)
            return {m["id"]: m for m in data.get("missions", [])}
        except (OSError, ValueError):
            return {}

    def _write(self, missions: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w") as fh:
            json.dump({"missions": list(missions.values())}, fh, indent=2)
        os.replace(tmp, self.path)      # atomic: never leave a half-written file

    def list(self) -> list[dict]:
        return sorted(self._read().values(), key=lambda m: m.get("name", ""))

    def get(self, mission_id: str) -> Optional[dict]:
        return self._read().get(mission_id)

    def save(self, mission: Mission) -> dict:
        missions = self._read()
        doc = mission.model_dump()
        if not doc["id"]:
            doc["id"] = uuid.uuid4().hex[:12]
        missions[doc["id"]] = doc
        self._write(missions)
        return doc

    def delete(self, mission_id: str) -> bool:
        missions = self._read()
        if mission_id not in missions:
            return False
        del missions[mission_id]
        self._write(missions)
        return True


store = MissionStore()


@router.get("/missions")
async def list_missions() -> dict:
    return {"missions": await asyncio.to_thread(store.list)}


def _validate_action(a: Action, *, nested: bool = False) -> None:
    """Reject an action the runner could not honour, at SAVE time not mid-mission.

    Validating here matters more than usual: the alternative is discovering the
    problem with the robot already standing at a waypoint halfway through a route.
    """
    if a.type not in ACTION_TYPES:
        raise HTTPException(400, f"unknown action type {a.type!r}")
    if a.type == "script":
        _resolve_script(a.script)
    if a.type == "speak" and not a.text.strip():
        raise HTTPException(400, "a 'speak' action needs some text")
    if a.type == "gesture":
        if a.gesture.startswith(MOTION_REF_PREFIX):
            if _parse_motion_ref(a.gesture) is None:
                raise HTTPException(
                    400, f"unknown preset {a.gesture!r} — see GET /api/nav/gestures "
                         f"for the ids this robot actually has")
        elif a.gesture not in _allowed_gestures():
            raise HTTPException(
                400, f"unknown gesture {a.gesture!r}. Use a curated name "
                     f"({', '.join(_allowed_gestures())}) or 'motion:<id>' from "
                     f"the full library at GET /api/nav/gestures")
    if nested and a.concurrent:
        # One level only — see the Action docstring.
        raise HTTPException(400, "concurrent actions cannot themselves have "
                                 "concurrent actions")


def _validate_group(primary: Action) -> None:
    """Check a primary action and everything meant to run alongside it."""
    _validate_action(primary)
    if not primary.concurrent:
        return

    seen = {primary.type}
    for peer in primary.concurrent:
        _validate_action(peer, nested=True)
        # Same type twice is always refused: the robot has one voice, one body,
        # one pair of legs. Two speeches would talk over each other and two
        # gestures would fight for the same joints.
        if peer.type in seen:
            raise HTTPException(
                400, f"cannot run two '{peer.type}' actions concurrently — "
                     f"the robot only has one of whatever that uses")
        allowed = CONCURRENCY_RULES.get(primary.type, ())
        if peer.type not in allowed:
            reason = (" A gesture drives the whole body through motion_player while a"
                      " turn needs a locomotion action, so they cannot both run."
                      if {primary.type, peer.type} == {"gesture", "turn"} else "")
            raise HTTPException(
                400,
                f"'{peer.type}' cannot run concurrently with '{primary.type}'."
                f"{reason} Allowed alongside '{primary.type}': "
                f"{', '.join(allowed) or 'nothing'}")
        seen.add(peer.type)


@router.get("/action-types")
async def action_types() -> dict:
    """What actions exist and which may be combined — drives the UI's menus.

    The frontend uses `concurrency` to offer only legal pairings, so an impossible
    combination is never presented rather than being rejected after the fact.
    """
    return {
        "types": list(ACTION_TYPES),
        "concurrency": {k: list(v) for k, v in CONCURRENCY_RULES.items()},
        "notes": {
            "gesture+turn": "refused: a gesture forces the MC into whole-body servo, "
                            "a turn needs a locomotion action",
            "same-type": "refused: one voice, one body, one pair of legs",
            "script": "no concurrency — a script can do anything, so we cannot "
                      "reason about what it would collide with",
        },
    }


@router.put("/missions")
async def save_mission(mission: Mission) -> dict:
    for s in mission.steps:
        for a in s.actions:
            _validate_group(a)
    return await asyncio.to_thread(store.save, mission)


@router.delete("/missions/{mission_id}")
async def delete_mission(mission_id: str) -> dict:
    if not await asyncio.to_thread(store.delete, mission_id):
        raise HTTPException(404, "no such mission")
    return {"deleted": mission_id}


# --------------------------------------------------------------------------- #
# speech (TTS) and gestures
# --------------------------------------------------------------------------- #
# SPEECH: the TEAM'S OWN TTS (ElevenLabs), not AgiBot's built-in TTSService.
# -------------------------------------------------------------------------
# AgiBot's `TTSService/PlayTTS` is hosted by the `agent` app. The supervisor's audio
# bridge deliberately STOPS agent so that this stack owns the microphone and
# speakers (that is how Soniox STT + ElevenLabs TTS work at all). So on this robot
# PlayTTS is permanently unavailable while the supervisor runs — it is accepted by
# the gateway and simply never answers. Using it here was wrong; this now goes
# through the same provider the voice agent uses.
#
# The path, and why each hop:
#   1. ElevenLabs REST with output_format=pcm_48000 -> raw 48 kHz mono s16le.
#      That is EXACTLY the audio bridge's internal format (SAMPLE_RATE 48000,
#      NUM_CHANNELS 1, int16), so there is no resampling anywhere.
#   2. POST the PCM to the audio bridge's control API (127.0.0.1:8766 /play-pcm),
#      which appends it to the speaker buffers.
#      NOT `aplay`: the bridge holds the output device, and its output path is also
#      the AEC render reference — going through it stops the microphone
#      transcribing the robot's own voice.
#   3. Duration is EXACT: bytes / (48000 * 2). No guessing how long speech takes,
#      which is what makes a concurrent speak+gesture group timed correctly.
#
# Credentials come from the repo .env, the same file the voice agent reads, so
# changing the voice in one place changes it everywhere.
ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech"
AUDIO_BRIDGE_CONTROL = "http://127.0.0.1:8766"
TTS_HTTP_TIMEOUT_S = 45.0
SPEECH_MAX_S = 120.0


def _env_file() -> dict[str, str]:
    """Read the repo .env. Cached by the caller; tiny file, parsed simply."""
    out: dict[str, str] = {}
    try:
        for line in (REPO_ROOT / ".env").read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_tts_cfg_cache: dict[str, str] | None = None


def _tts_config() -> dict[str, str]:
    """Voice settings, preferring the real environment over the .env file."""
    global _tts_cfg_cache
    if _tts_cfg_cache is None:
        env = _env_file()

        def pick(name: str, default: str = "") -> str:
            return os.environ.get(name) or env.get(name) or default

        _tts_cfg_cache = {
            "provider": (pick("TTS_PROVIDER", "elevenlabs")).lower(),
            "api_key": pick("ELEVENLABS_API_KEY"),
            "voice_id": pick("TTS_VOICE_ID"),
            "model": pick("TTS_MODEL") or pick("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
            "language": pick("TTS_LANGUAGE", ""),
        }
    return _tts_cfg_cache


def _synthesize(text: str) -> bytes:
    """Text -> 48 kHz mono s16le PCM via ElevenLabs. Raises on any failure."""
    import requests
    cfg = _tts_config()
    if cfg["provider"] != "elevenlabs":
        raise RuntimeError(
            f"TTS_PROVIDER is {cfg['provider']!r}; the mission runner only "
            f"implements elevenlabs so far")
    if not cfg["api_key"]:
        raise RuntimeError("ELEVENLABS_API_KEY is not set (checked env and .env)")
    if not cfg["voice_id"]:
        raise RuntimeError("TTS_VOICE_ID is not set (checked env and .env)")
    body: dict[str, Any] = {"text": text, "model_id": cfg["model"]}
    if cfg["language"]:
        body["language_code"] = cfg["language"]
    r = requests.post(
        f"{ELEVEN_URL}/{cfg['voice_id']}?output_format=pcm_48000",
        json=body,
        headers={"xi-api-key": cfg["api_key"], "Content-Type": "application/json"},
        timeout=TTS_HTTP_TIMEOUT_S,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs returned {r.status_code}: {r.text[:300]}")
    if not r.content:
        raise RuntimeError("ElevenLabs returned no audio")
    return r.content


def _play_pcm(pcm: bytes) -> float:
    """Hand PCM to the audio bridge for playback. Returns its length in seconds."""
    import base64
    import requests
    r = requests.post(f"{AUDIO_BRIDGE_CONTROL}/play-pcm",
                      json={"pcm_base64": base64.b64encode(pcm).decode("ascii")},
                      timeout=20.0)
    if r.status_code == 404:
        raise RuntimeError(
            "the audio bridge does not expose /play-pcm — it is running an older "
            "build; restart the audio-bridge service to pick up the new endpoint")
    r.raise_for_status()
    return float((r.json() or {}).get("seconds") or 0.0)


def _stop_speech() -> None:
    """Cut off speech already queued in the bridge's speaker buffers."""
    import requests
    requests.post(f"{AUDIO_BRIDGE_CONTROL}/play-pcm/stop", json={}, timeout=10.0)


def _speech_available() -> tuple[bool, str]:
    """Is everything speech needs in place? Configuration + a live audio bridge."""
    import requests
    cfg = _tts_config()
    missing = [n for n, k in (("ELEVENLABS_API_KEY", "api_key"),
                              ("TTS_VOICE_ID", "voice_id")) if not cfg[k]]
    if missing:
        return False, f"missing {', '.join(missing)} in the environment or .env"
    try:
        requests.get(f"{AUDIO_BRIDGE_CONTROL}/status", timeout=3.0).raise_for_status()
    except Exception as exc:
        return False, (
            f"the audio bridge is not reachable on {AUDIO_BRIDGE_CONTROL} "
            f"({type(exc).__name__}) — speech plays through it, so start the "
            f"audio-bridge service")
    return True, (f"ElevenLabs voice {cfg['voice_id']} ({cfg['model']}) "
                  f"via the audio bridge")


@router.get("/speech/status")
async def speech_status() -> dict:
    ok, detail = await asyncio.to_thread(_speech_available)
    cfg = _tts_config()
    return {"available": ok, "detail": detail, "provider": cfg["provider"],
            "voice_id": cfg["voice_id"], "model": cfg["model"]}


# GESTURES: reuse the curated catalog the supervisor already ships in
# robot_services/gestures — the same 20 safe gestures the robot uses when speaking.
# We drive AgibotMotionPlayerController in-process rather than calling the gesture
# bridge on :8090, because that bridge is an optional service and is frequently not
# running; the controller itself is just httpx calls to the MC, which the supervisor
# venv can make directly.
_gesture_ctl: Any = None
_gesture_ctl_error: str = ""


def _gesture_controller():
    """Build (once) the controller that maps a curated gesture to a preset motion."""
    global _gesture_ctl, _gesture_ctl_error
    if _gesture_ctl is not None or _gesture_ctl_error:
        return _gesture_ctl
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from robot_services.gestures.catalogs import (
            get_catalog, get_motion_duration_caps_ms, get_motion_hints)
        from robot_services.gestures.motion_player import (
            AgibotMotionPlayerConfig, AgibotMotionPlayerController)
        cat = get_catalog(GESTURE_CATALOG_ID)
        _gesture_ctl = AgibotMotionPlayerController(AgibotMotionPlayerConfig(
            action_by_gesture=cat.mapping,
            motion_hints=get_motion_hints(cat.id),
            motion_duration_caps_ms=get_motion_duration_caps_ms(cat.id),
        ))
    except Exception as exc:
        logger.exception("gesture controller init failed")
        _gesture_ctl_error = str(exc)
    return _gesture_ctl


# The controller sends duration_ms=6000 to bound long routines, so a motion is
# truncated at ~6 s however long its file is. Waiting is backstopped against that,
# not against the file's full length.
_MOTION_SEND_CAP_S = 6.0
# Absolute ceiling on how long we will wait for a motion. The library's longest
# presets really are ~72 s ("Dance", "Handshake_right hand_Long"), so a 30 s
# ceiling would abandon them mid-motion and let the mission walk off — the very
# bug the idle-polling wait exists to prevent. This is a backstop against a wedged
# player, not a policy on motion length.
_MOTION_WAIT_MAX_S = 120.0
_MOTION_WAIT_MARGIN_S = 6.0
RESOURCE_SVC = "http://192.168.100.110:51049/rpc/aimdk.protocol.ResourceService"
MOTION_CMD_SVC = "http://127.0.0.1:51056/rpc/aimdk.protocol.MotionCommandService"


def _motion_status() -> dict:
    """motion_player's live state: IDLE / OPERATING / PAUSE / STOP."""
    import requests
    r = requests.post(f"{MOTION_CMD_SVC}/GetMotionStatus",
                      json={"header": {}}, timeout=5.0)
    r.raise_for_status()
    return r.json() or {}


# THE FULL PRESET LIBRARY vs THE CURATED CATALOG
# ----------------------------------------------
# robot_services/gestures ships a curated 20-gesture `safe_only` catalog. That list
# is NOT just a convenience: livekit-client/agent_main.py feeds it to the LLM as the
# gesture policy prompt (`ALLOWED_GESTURES` / `build_gesture_policy_prompt`), so
# adding entries there changes what the conversational agent believes it may do.
#
# A mission is different: a human explicitly picks the gesture for a specific
# waypoint, so the reason for constraining the LLM does not apply. Missions may
# therefore also address ANY of this robot's ~133 factory presets — the same ones
# the tablet offers — by id, written as "motion:<id>".
#
# Only the curated 20 are vetted as safe_only; the rest are factory motions of every
# size (tai chi is in there). The listing marks which is which and reports each
# motion's real duration so the choice is informed.
_motion_library_cache: list[dict] | None = None


def _motion_library() -> list[dict]:
    """Every preset on this robot: id, English/Chinese name, duration, path.

    From ResourceService/GetMotion on the Orin (:51049). Cached — the preset
    library does not change while we run.
    """
    global _motion_library_cache
    if _motion_library_cache is None:
        import requests
        try:
            r = requests.post(f"{RESOURCE_SVC}/GetMotion",
                              json={"header": {}}, timeout=10.0)
            r.raise_for_status()
            out = []
            for m in (r.json() or {}).get("motions", []):
                out.append({
                    "motion_id": int(m.get("motion_id") or 0),
                    "name_en": m.get("display_name_en") or "",
                    "name_zh": m.get("display_name_zh") or "",
                    "path": m.get("motion_path") or "",
                    "duration_s": round(float(m.get("duration") or 0.0), 2),
                })
            _motion_library_cache = sorted(out, key=lambda x: x["name_en"].lower())
        except Exception:
            logger.warning("could not read the motion library", exc_info=True)
            _motion_library_cache = []
    return _motion_library_cache


def _motion_duration_table() -> dict[str, float]:
    """motion file path -> advertised duration in seconds."""
    return {m["path"]: m["duration_s"] for m in _motion_library() if m["path"]}


MOTION_REF_PREFIX = "motion:"


def _parse_motion_ref(value: str) -> Optional[dict]:
    """Resolve a "motion:<id>" gesture value to its library entry, else None."""
    if not value.startswith(MOTION_REF_PREFIX):
        return None
    raw = value[len(MOTION_REF_PREFIX):].strip()
    try:
        wanted = int(raw)
    except ValueError:
        return None
    for m in _motion_library():
        if m["motion_id"] == wanted:
            return m
    return None


def _send_motion(path: str, duration_s: float) -> dict:
    """Play a preset by file path, straight through MotionCommandService.

    `duration_ms` is the player's "maximum run time". We pass the motion's own
    length (plus a second) rather than the gesture controller's 6 s cap, so a long
    preset such as "Direction_point to the right" (18.3 s) is not bounded to a
    length it was never designed for.
    """
    import requests
    body = {"header": {}, "motion_id": path,
            "duration_ms": int(max(1.0, duration_s + 1.0) * 1000),
            "cmd_end": True, "cmd_pause": False, "cmd_reset": False}
    r = requests.post(f"{MOTION_CMD_SVC}/SendMotionCommand", json=body, timeout=10.0)
    r.raise_for_status()
    return r.json() or {}


def _gesture_duration_s(name: str) -> float:
    """Advertised length of a curated gesture, or 0 if it cannot be resolved."""
    ctl = _gesture_controller()
    if ctl is None:
        return 0.0
    try:
        code = ctl.cfg.action_by_gesture.get(name)
        ctl._ensure_motion_table()
        resolved = (ctl._motion_table or {}).get(code)
        if not resolved:
            return 0.0
        return _motion_duration_table().get(resolved[0], 0.0)
    except Exception:
        return 0.0


def _allowed_gestures() -> tuple[str, ...]:
    """The safe-pool gesture names, i.e. what the UI may offer."""
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from robot_services.gestures import get_allowed_gestures
        return get_allowed_gestures(GESTURE_SAFETY_POOL, GESTURE_CATALOG_ID)
    except Exception:
        logger.exception("could not load gesture catalog")
        return ()


def _refresh_motion_caches() -> None:
    """Forget the cached preset library so a newly added animation shows up.

    Both caches are per-process and would otherwise persist until the supervisor
    restarts: ours (`_motion_library_cache`) and the gesture controller's internal
    `_motion_table`, which maps curated names to real motion files.
    """
    global _motion_library_cache
    _motion_library_cache = None
    ctl = _gesture_controller()
    if ctl is not None:
        ctl._motion_table = None
        ctl._motion_table_failed = False


@router.get("/gestures")
async def list_gestures(
    refresh: bool = Query(False,
                          description="re-read the preset library from the robot "
                                      "instead of using the cached copy"),
) -> dict:
    """Gestures a `gesture` action may use.

    These come from the supervisor's curated A2 catalog, resolved against the
    robot's live 133-entry preset library via ResourceService/GetMotion. A curated
    gesture whose keyword matches nothing on this unit simply will not fire, and the
    runner reports that rather than pretending it played.
    """
    if refresh:
        await asyncio.to_thread(_refresh_motion_caches)
    names = await asyncio.to_thread(_allowed_gestures)

    def durations() -> dict[str, float]:
        # The motion's ADVERTISED length from ResourceService/GetMotion.
        #
        # Deliberately NOT min(full, send_cap): the controller passes
        # duration_ms=6000 as a "maximum run time", but that does not actually
        # truncate playback — `wave` advertises 7.16 s and was measured taking
        # 7.46 s end to end (animation + the reset back to neutral). Showing 6 s
        # would under-report and is exactly the sort of thing that makes someone
        # size a dwell too short and have the robot walk off mid-gesture.
        return {n: round(_gesture_duration_s(n), 2) for n in names}

    eff = await asyncio.to_thread(durations)
    library = await asyncio.to_thread(_motion_library)
    curated_paths = set()
    ctl = _gesture_controller()
    if ctl is not None:
        try:
            ctl._ensure_motion_table()
            curated_paths = {v[0] for v in (ctl._motion_table or {}).values()}
        except Exception:
            pass
    motions = [{**m, "ref": f"{MOTION_REF_PREFIX}{m['motion_id']}",
                "curated": m["path"] in curated_paths} for m in library]
    return {"gestures": list(names), "motions": motions,
            "catalog": GESTURE_CATALOG_ID,
            "safety_pool": GESTURE_SAFETY_POOL,
            "durations_s": eff,
            "durations_note": "advertised motion length in seconds; a mission waits "
                              "for motion_player to actually go idle, which also "
                              "covers the reset back to the neutral pose",
            "controller_error": _gesture_ctl_error or None}


def _resolve_script(name: str) -> Path:
    """Resolve a `script` action to a real file inside testing_controls/.

    Missions are stored server-side and executed by this process, so the script name
    is an input that must not be able to escape the intended directory. Reject any
    path separators outright and require the resolved path to stay inside NAV_DIR,
    so neither "../.." nor a symlink can reach elsewhere.
    """
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "script must be a bare file name in testing_controls/")
    target = (NAV_DIR / name).resolve()
    if target.parent != NAV_DIR.resolve() or not target.is_file():
        raise HTTPException(400, f"no such script in testing_controls/: {name}")
    if target.suffix != ".py":
        raise HTTPException(400, "only .py scripts may be run as a waypoint action")
    return target


@router.get("/scripts")
async def list_scripts() -> dict:
    """Scripts a `script` action may run — so the UI offers a picker, not free text."""
    def work():
        return sorted(p.name for p in NAV_DIR.glob("a2_*.py"))
    return {"scripts": await asyncio.to_thread(work)}


# --------------------------------------------------------------------------- #
# the mission runner
# --------------------------------------------------------------------------- #
async def _scan_for_glass(seconds: float) -> dict:
    """Stand still and look for glass. Returns findings in BASE_LINK metres.

    Runs a throwaway `a2_costmap.py` for the duration and takes its last frame.
    A fresh process per waypoint is deliberate: the detector's evidence grid is
    anchored to base_link, so anything learned at the previous waypoint is
    positionally meaningless here. Starting clean is a guaranteed reset, with no
    stdin control protocol and no heuristic about when to forget.

    Waits for `glass_stats.observing` before starting the clock. That flag means
    "the last window was usable" — it is false while settling AND false whenever
    the scene is moving, so this will not count a dwell that the robot spent
    drifting or that a passer-by disturbed.
    """
    if not COSTMAP_SCRIPT.is_file():
        return {"ok": False, "error": f"missing {COSTMAP_SCRIPT}", "lines": []}

    argv = [sys.executable, str(COSTMAP_SCRIPT), "--ndjson",
            "--radius", str(GLASS_SURVEY_RADIUS_M), "--max-hz", "2", "--glass"]
    env = {k: v for k, v in os.environ.items() if k != "ROS_DOMAIN_ID"}
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=env, cwd=str(NAV_DIR))

    deadline = time.time() + seconds + GLASS_SURVEY_SETTLE_ALLOWANCE_S
    observing_since: Optional[float] = None
    last: Optional[dict] = None
    saw_any_frame = False
    try:
        while time.time() < deadline:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") != "frame":
                continue
            saw_any_frame = True
            last = obj
            stats = obj.get("glass_stats") or {}
            if stats.get("observing"):
                if observing_since is None:
                    observing_since = time.time()
                elif time.time() - observing_since >= seconds:
                    break
            else:
                # Lost the ability to see (someone walked past, the robot
                # shifted). Restart the clock rather than banking a partial look.
                observing_since = None
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    if not saw_any_frame:
        return {"ok": False, "error": "no LiDAR frames from the costmap sidecar",
                "lines": []}
    stats = (last or {}).get("glass_stats") or {}
    complete = observing_since is not None and (time.time() - observing_since) >= seconds
    return {
        "ok": True,
        # `complete` false means the dwell timed out before a clean look was
        # achieved. The caller MUST surface that: "no glass found" and "never
        # managed to look" have to stay distinguishable.
        "complete": complete,
        "observing": bool(stats.get("observing")),
        "windows": stats.get("windows"),
        "windows_discarded": stats.get("windows_discarded"),
        "lines": (last or {}).get("glass_lines") or [],
    }


def _lines_to_map(lines: list[dict], pose: dict) -> list[dict]:
    """base_link segments -> map frame, using the robot's pose at scan time.

    base_link is +x forward, +y left, so this is the standard 2-D rigid
    transform: rotate by yaw, translate by the robot's position.
    """
    cos_y, sin_y = math.cos(pose["yaw"]), math.sin(pose["yaw"])

    def to_map(x: float, y: float) -> tuple[float, float]:
        return (pose["x"] + x * cos_y - y * sin_y,
                pose["y"] + x * sin_y + y * cos_y)

    out = []
    for ln in lines:
        x1, y1 = to_map(ln["x1"], ln["y1"])
        x2, y2 = to_map(ln["x2"], ln["y2"])
        out.append({"x1": round(x1, 3), "y1": round(y1, 3),
                    "x2": round(x2, 3), "y2": round(y2, 3),
                    "length": ln.get("length"), "inliers": ln.get("inliers"),
                    "density": ln.get("density"), "rms": ln.get("rms")})
    return out


class MissionRunner:
    """Executes one mission, server-side, one step at a time.

    Exactly one runner may be active, because there is exactly one robot. Every
    state change is broadcast on the live SSE stream as a `mission` event, so any
    number of tabs can watch and none of them owns the run.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._state: dict[str, Any] = {"active": False}
        self._nav_task_id: Optional[int] = None
        self._speaking_until: Optional[float] = None
        self._schedule_task: Optional[asyncio.Task] = None
        # Glass findings for the CURRENT run only, in map metres. Deliberately
        # not persisted: a pane's detectability depends on the angle it was seen
        # from, so a finding from a previous run is not evidence about this one.
        self._glass: list[dict] = []
        self._glass_surveys: list[dict] = []

    def glass(self) -> dict:
        surveys = list(self._glass_surveys)
        return {
            "lines": list(self._glass),
            "surveys": surveys,
            "waypoints_surveyed": len(surveys),
            "waypoints_incomplete": sum(1 for s in surveys if not s.get("complete")),
            "advisory": True,
        }

    # ------------------------------------------------------------- reporting
    def status(self) -> dict:
        return dict(self._state)

    def _set(self, **kw) -> None:
        self._state.update(kw)
        relay._broadcast({"type": "mission", "stamp": time.time(),
                          **self._state})

    # ------------------------------------------------------------- lifecycle
    async def start(self, mission: dict) -> dict:
        if self._task is not None and not self._task.done():
            raise HTTPException(409, "a mission is already running — cancel it first")

        # Same gate as `a2_nav.py doctor`. Refusing here is the whole point: with the
        # gate closed pnc would accept every goal and report RUNNING forever while the
        # robot stood still, which looks like a mission that is "working but slow".
        # Engage the hold rather than merely checking the gate. A one-shot arm is
        # undone by skillpilot within a minute (see ActionHold), so a mission that
        # only checked "can it walk right now?" could be stranded a minute later.
        try:
            await hold.engage()
        except Exception as exc:
            raise HTTPException(502, f"could not arm the robot: {exc}")
        state = await asyncio.to_thread(_read_nav_state)
        if state.get("can_walk") is False:
            await hold.release()
            raise HTTPException(
                409, f"MC action is {state.get('mc_action')} and arming did not take. "
                     f"Check `a2_nav.py doctor` — an asserted E-stop will do this.")
        if state.get("localization_running") is False:
            raise HTTPException(
                409, "Localization is not running, so waypoint navigation cannot work. "
                     "Relocalize from the tablet first.")
        if not mission.get("steps"):
            raise HTTPException(400, "mission has no steps")

        # Findings belong to one run. Whether a pane is detectable depends on the
        # angle it was seen from, so carrying results across runs would show
        # stale lines the robot has no current evidence for.
        self._glass = []
        self._glass_surveys = []

        self._state = {"active": True, "mission_id": mission["id"],
                       "mission_name": mission["name"], "map_id": mission["map_id"],
                       "step_index": 0, "total_steps": len(mission["steps"]),
                       "phase": "starting", "message": "", "started_at": time.time(),
                       "glass_survey": bool(mission.get("glass_survey"))}
        self._task = asyncio.create_task(self._run(mission))
        return self.status()

    async def cancel(self) -> dict:
        """Stop the mission AND the nav task it left running.

        Cancelling the asyncio task alone is not enough — pnc would keep walking to
        the current goal. ActionCancel must carry the REAL task_id (sending 0 is
        rejected and the robot keeps going), which a2_nav.cancel_task handles.
        """
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        # Cut off any speech still playing — a cancelled mission that keeps
        # narrating is alarming and looks like the cancel did not work. The audio
        # is already queued in the bridge's speaker buffers, so dropping those
        # buffers is what actually stops it.
        if self._speaking_until:
            try:
                await asyncio.to_thread(_stop_speech)
            except Exception:
                logger.warning("could not stop speech on cancel", exc_info=True)
            self._speaking_until = None
        cancelled = "nothing running"
        try:
            a2_nav = _import_nav()
            cancelled = await asyncio.to_thread(a2_nav.cancel_task, self._nav_task_id)
        except Exception as exc:
            logger.exception("nav cancel failed")
            cancelled = f"cancel failed: {exc}"
        self._nav_task_id = None
        self._set(active=False, phase="cancelled", message=str(cancelled))
        return self.status()

    # ------------------------------------------------------------- scheduling
    async def schedule(self, mission: dict, at: str, prepare: bool) -> dict:
        """Arm a mission to start at a wall-clock time.

        Deliberately does NOT run the pre-flight gate now: "can the robot walk?"
        is a question about the start moment, not about when the button was
        pressed, and the whole point of `prepare` is to make the answer yes by
        then. It is checked in start() as usual when the time comes.
        """
        if self._task is not None and not self._task.done():
            raise HTTPException(409, "a mission is already running")
        if self._schedule_task is not None and not self._schedule_task.done():
            raise HTTPException(409, "a mission is already scheduled — cancel first")
        if not mission.get("steps"):
            raise HTTPException(400, "mission has no steps")

        target = _next_occurrence(at)
        wait_s = target - time.time()
        if wait_s > SCHEDULE_MAX_WAIT_S:
            raise HTTPException(400, "that start time is more than a day away")

        self._state = {
            "active": False, "scheduled": True,
            "mission_id": mission["id"], "mission_name": mission["name"],
            "map_id": mission["map_id"], "total_steps": len(mission["steps"]),
            "phase": "scheduled", "scheduled_for": round(target, 3),
            "scheduled_local": time.strftime("%Y-%m-%d %H:%M:%S",
                                             time.localtime(target)),
            "message": f"waiting until {at} ({int(wait_s)}s)",
        }
        self._schedule_task = asyncio.create_task(
            self._wait_and_run(mission, target, prepare))
        self._set(**self._state)
        return self.status()

    async def cancel_schedule(self) -> dict:
        task, self._schedule_task = self._schedule_task, None
        if task is not None and not task.done():
            task.cancel()
        self._set(active=False, scheduled=False, phase="idle",
                  message="schedule cancelled", scheduled_for=None)
        return self.status()

    async def _wait_and_run(self, mission: dict, target: float,
                            prepare: bool) -> None:
        """Sleep until `target`, optionally make the robot ready, then run."""
        try:
            prepared = False
            last_announced = -1
            while True:
                remaining = target - time.time()
                if remaining <= 0:
                    break
                # Get the robot ready shortly before the start so it is not sitting
                # with powered legs for the whole wait. 60 s is enough for the
                # two-hop action transition plus settling.
                if prepare and not prepared and remaining <= 60:
                    prepared = True
                    self._set(phase="preparing",
                              message="stopping idle motion and enabling walking")
                    try:
                        # Engage the HOLD, not a one-shot prepare: between here and
                        # the start time skillpilot would otherwise steal the action
                        # back, which is exactly how timed starts were failing.
                        await hold.engage()
                    except Exception as exc:
                        logger.exception("prepare failed")
                        self._set(scheduled=False, phase="failed",
                                  message=f"could not prepare the robot: {exc}")
                        return
                # Announce once per 10 s. Guarded on the value actually changing,
                # because the 0.5 s tick would otherwise emit each countdown twice
                # and double the traffic on the live SSE stream for no reason.
                whole = int(remaining)
                if whole % 10 == 0 and whole != last_announced:
                    last_announced = whole
                    self._set(message=f"starting in {whole}s")
                await asyncio.sleep(min(SCHEDULE_TICK_S, max(0.05, remaining)))

            self._schedule_task = None
            self._set(scheduled=False, phase="starting",
                      message="scheduled time reached")
            await self.start(mission)
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            # start()'s pre-flight refused — say why instead of failing silently.
            self._set(active=False, scheduled=False, phase="failed",
                      message=str(exc.detail))
        except Exception as exc:
            logger.exception("scheduled start failed")
            self._set(active=False, scheduled=False, phase="failed",
                      message=str(exc))

    # ------------------------------------------------------------- execution
    async def _survey_here(self, mission: dict, step: dict) -> None:
        """Stand still at the current waypoint and look for glass."""
        seconds = float(mission.get("glass_survey_seconds") or GLASS_SURVEY_DEFAULT_S)
        wp = step.get("waypoint_id")
        self._set(phase="surveying",
                  message=f"looking for glass at waypoint {wp} (~{seconds:.0f}s)")

        result = await _scan_for_glass(seconds)
        pose = _read_pose()
        entry = {
            "waypoint_id": wp,
            "stamp": round(time.time(), 3),
            "complete": bool(result.get("complete")),
            "found": len(result.get("lines") or []),
            "pose": pose,
            "error": result.get("error"),
        }

        if not result.get("ok"):
            entry["placed"] = False
            self._set(message=f"glass survey failed at waypoint {wp}: "
                              f"{result.get('error')}")
        elif pose is None:
            # Without pose the finding cannot be put on the map. Say so rather
            # than dropping it silently or guessing a position.
            entry["placed"] = False
            self._set(message=f"glass survey at waypoint {wp} found "
                              f"{entry['found']} plane(s) but there is no live "
                              f"pose, so they cannot be placed on the map")
        else:
            placed = _lines_to_map(result["lines"], pose)
            self._glass.extend(placed)
            entry["placed"] = True
            note = "" if entry["complete"] else " (could not get a clean look)"
            self._set(message=f"glass survey at waypoint {wp}: "
                              f"{len(placed)} plane(s){note}")

        self._glass_surveys.append(entry)
        relay._broadcast({"type": "glass", "stamp": time.time(), **self.glass()})

    async def _run_steps(self, a2_nav, mission: dict, survey: bool) -> None:
        """Walk the step list, once or forever, driving the robot."""
        while True:
            for i, step in enumerate(mission["steps"]):
                self._set(step_index=i, phase="navigating",
                          message=f"to waypoint {step['waypoint_id']}")
                await self._goto(a2_nav, int(mission["map_id"]),
                                 int(step["waypoint_id"]))
                # Survey BEFORE the waypoint's own actions: a gesture or a turn
                # moves the robot, and the detector needs it still. Doing it
                # first also means the dwell happens even if an action fails.
                if survey:
                    await self._survey_here(mission, step)
                for j, action in enumerate(step.get("actions") or []):
                    await self._act_group(a2_nav, int(mission["map_id"]),
                                          action, j + 1)
            if not mission.get("loop"):
                return
            self._set(phase="looping", message="restarting mission")

    async def _run(self, mission: dict) -> None:
        a2_nav = _import_nav()
        survey = bool(mission.get("glass_survey"))
        try:
            # Hold the nav sidecar open for the whole run when surveying: pose is
            # only published while it is alive, and without pose the findings
            # cannot be placed on the map.
            async with (relay.hold_open() if survey else contextlib.nullcontext()):
                await self._run_steps(a2_nav, mission, survey)
            self._set(active=False, phase="finished", message="mission complete")
            asyncio.create_task(self._release_hold_later())
        except asyncio.CancelledError:
            # cancel() already reported and stopped the robot.
            raise
        except Exception as exc:
            logger.exception("mission failed")
            self._set(active=False, phase="failed", message=str(exc))
            asyncio.create_task(self._release_hold_later())
            # A failed leg leaves pnc walking toward a goal it will never reach.
            try:
                await asyncio.to_thread(_import_nav().cancel_task, self._nav_task_id)
            except Exception:
                logger.exception("post-failure cancel failed")

    async def _release_hold_later(self) -> None:
        """Hand the robot back, but not instantly.

        HOLD_RELEASE_AFTER_S exists so back-to-back missions do not each pay the
        two-hop arming transition, and so an operator who starts another run right
        away finds the robot still ready.
        """
        try:
            await asyncio.sleep(HOLD_RELEASE_AFTER_S)
            if self._task is not None and not self._task.done():
                return              # another mission started; keep holding
            await hold.release()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("delayed hold release failed")

    async def _goto(self, a2_nav, map_id: int, target_id: int) -> None:
        task_id = a2_nav.new_task_id()
        self._nav_task_id = task_id
        body = {"header": {}, "task_id": task_id, "map_id": map_id,
                "target_id": target_id, "guide_line_id": 0}
        await asyncio.to_thread(a2_nav.post, f"{a2_nav.PNC}/PlanningNaviToGoal", body)
        await self._wait_terminal(a2_nav)
        self._nav_task_id = None

    async def _wait_terminal(self, a2_nav, poll: float = 0.5) -> None:
        """Poll ActionGetState until the task ends.

        No timeout on purpose: how long a leg takes depends on distance and on how
        often the planner has to route around people. The operator cancels; we do not
        guess a deadline and abandon a robot mid-stride.
        """
        while True:
            a = await asyncio.to_thread(a2_nav.action_state)
            st = a.get("state", "?")
            if st in a2_nav.TERMINAL:
                if not a2_nav.TERMINAL[st]:
                    raise RuntimeError(f"navigation ended as {st}: {a.get('info')}")
                return
            await asyncio.sleep(poll)

    async def _act_group(self, a2_nav, map_id: int, action: dict, ordinal: int) -> None:
        """Run one action plus anything attached to it concurrently.

        The group finishes when its SLOWEST member does, so a 2 s gesture paired
        with an 8 s sentence takes 8 s — the robot does not walk off mid-sentence.

        A failure in any member aborts the whole group: `asyncio.gather` without
        return_exceptions cancels the siblings and re-raises, which is what we want
        here because the mission runner treats an action failure as mission failure
        and stops the robot. Silently continuing after, say, a failed gesture would
        leave the operator with a mission that "succeeded" but did not do the job.
        """
        peers = action.get("concurrent") or []
        if not peers:
            self._set(phase="acting", message=f"action {ordinal}: {action['type']}")
            await self._act(a2_nav, map_id, action)
            return

        names = ", ".join([action["type"], *[p["type"] for p in peers]])
        self._set(phase="acting", message=f"action {ordinal}: {names} (concurrent)")
        await asyncio.gather(
            self._act(a2_nav, map_id, action),
            *[self._act(a2_nav, map_id, p) for p in peers],
        )

    async def _act(self, a2_nav, map_id: int, action: dict) -> None:
        kind = action["type"]
        if kind == "speak":
            await self._speak(action.get("text") or "")
            return
        if kind == "gesture":
            await self._gesture(action.get("gesture") or "")
            return
        if kind == "dwell":
            await asyncio.sleep(float(action.get("seconds") or 0))
            return
        if kind == "turn":
            task_id = a2_nav.new_task_id()
            self._nav_task_id = task_id
            body = {"header": {}, "task_id": task_id, "map_id": map_id,
                    "angle": float(action.get("radians") or 0)}
            await asyncio.to_thread(a2_nav.post, f"{a2_nav.PNC}/SpinTurn", body)
            await self._wait_terminal(a2_nav)
            self._nav_task_id = None
            return
        if kind == "script":
            target = _resolve_script(action.get("script") or "")
            argv = [sys.executable, str(target), *[str(a) for a in
                                                   (action.get("args") or [])]]
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=str(NAV_DIR))
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError(f"script {target.name} timed out after 180s")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"script {target.name} exited {proc.returncode}: "
                    f"{err.decode('utf-8', 'replace')[-400:]}")
            self._set(phase="acting",
                      message=f"{target.name} ok: "
                              f"{out.decode('utf-8', 'replace').strip()[-200:]}")
            return
        raise RuntimeError(f"unknown action type {kind!r}")

    async def _speak(self, text: str) -> None:
        """Say `text` through the team's TTS, and block until it has been said.

        Blocking for the real audio length is what makes a concurrent speak+gesture
        group correct: the robot does not walk off mid-sentence. The length is
        EXACT here (PCM bytes / 48000 / 2), not an estimate.
        """
        text = text.strip()
        if not text:
            return
        try:
            pcm = await asyncio.to_thread(_synthesize, text)
            seconds = await asyncio.to_thread(_play_pcm, pcm)
        except Exception as exc:
            raise RuntimeError(f"speech failed: {exc}") from exc

        # Remember what is playing so cancel() can cut it short.
        self._speaking_until = time.monotonic() + seconds
        try:
            await asyncio.sleep(min(SPEECH_MAX_S, seconds))
        finally:
            self._speaking_until = None

    async def _gesture(self, name: str) -> None:
        """Play a curated gesture and wait for the robot to finish it.

        WAITING PROPERLY MATTERS (fixed 2026-09-10): the first version slept for
        the controller's `duration_ms`, which is the 6 s CAP it sends to bound long
        routines — not how long the motion actually takes. So the mission walked on
        while the arms were still moving.

        Two better sources exist, and we use both:
          * ResourceService/GetMotion reports a real `duration` per motion, which
            the /gestures endpoint surfaces so you can see it while planning.
          * motion_player's own GetMotionStatus is authoritative at RUN time, so we
            poll that. It only returns to IDLE after the animation AND the reset
            transition back to the neutral pose ("复位完成，进入空闲状态"), which is
            precisely the moment it is safe to walk again.

        `execute_gesture` returns a status rather than raising: a gesture whose
        keyword matches nothing in this unit's preset library reports "skipped".
        We escalate that, because a step that silently does nothing is worse than
        one that stops and says why.
        """
        # A gesture needs TWO things the armed state deliberately takes away:
        #
        #   1. whole-body servo — the hold would otherwise see "not walk-capable"
        #      and yank the action out from under the animation mid-play;
        #   2. an ENABLED motion_player — arming disables it to stop skillpilot's
        #      idle animation, and a disabled player silently ignores our gesture
        #      too. Measured 2026-09-11: while armed, `wave` returned in 0.78 s
        #      (it takes ~7.5 s) with the player stuck at STOP and no error.
        #
        # So bracket the gesture: lend it the player and stop holding, then put
        # both back exactly as they were.
        # Condition on the PLAYER'S ACTUAL STATE, not on whether we happen to be
        # holding. The two can disagree: if the supervisor is restarted while armed,
        # nothing ever re-enables the player, and every later gesture would fail even
        # though nothing is armed any more. Asking the robot is self-healing.
        was_disabled = False
        try:
            was_disabled = (await asyncio.to_thread(_motion_status)).get(
                "status") == "MotionCommandStatus_STOP"
        except Exception:
            logger.warning("could not read motion player state", exc_info=True)

        hold.suppress()
        try:
            if was_disabled:
                await asyncio.to_thread(_set_motion_player, True)
            await self._gesture_inner(name)
        finally:
            # Put the player back the way we found it, so a gesture never silently
            # turns AgiBot's idle animation back on mid-mission.
            if was_disabled:
                try:
                    await asyncio.to_thread(_set_motion_player, False)
                except Exception:
                    logger.warning("could not re-disable the motion player",
                                   exc_info=True)
            hold.unsuppress()

    async def _gesture_inner(self, name: str) -> None:
        preset = _parse_motion_ref(name)
        if preset is not None:
            # A raw library preset, picked explicitly by the operator.
            await asyncio.to_thread(_send_motion, preset["path"], preset["duration_s"])
            await self._await_motion_idle(name, advertised=preset["duration_s"])
            return

        ctl = _gesture_controller()
        if ctl is None:
            raise RuntimeError(f"gesture controller unavailable: {_gesture_ctl_error}")
        res = await asyncio.to_thread(ctl.execute_gesture, name)
        status = res.get("status")
        if status != "ok":
            raise RuntimeError(
                f"gesture {name!r} did not play ({status}: {res.get('reason')})")
        await self._await_motion_idle(name)

    async def _await_motion_idle(self, name: str, poll: float = 0.25,
                                 advertised: float | None = None) -> None:
        """Block until motion_player reports it is no longer playing.

        The deadline is a backstop only: it comes from the motion's own advertised
        duration plus the send cap and a margin for the reset, so a wedged player
        cannot park a mission forever. Hitting it is logged, not raised — the
        gesture did play, we just stopped watching.
        """
        cap_s = _MOTION_SEND_CAP_S + 4.0
        if advertised is None:
            advertised = _gesture_duration_s(name)
        deadline = time.monotonic() + min(
            _MOTION_WAIT_MAX_S, max(cap_s, advertised + _MOTION_WAIT_MARGIN_S))
        # Wait for the player to actually START. Without this we sample the
        # pre-existing IDLE/STOP and return immediately — which is precisely how a
        # gesture against a disabled player looked like success.
        started = False
        start_deadline = time.monotonic() + 3.0
        while time.monotonic() < start_deadline:
            await asyncio.sleep(0.2)
            try:
                st = await asyncio.to_thread(_motion_status)
            except Exception:
                break
            if st.get("status") == "MotionCommandStatus_OPERATING":
                started = True
                break
        if not started:
            raise RuntimeError(
                f"gesture {name!r} was accepted but motion_player never started it "
                f"(status stayed {st.get('status') if 'st' in dir() else 'unknown'}). "
                f"The player is disabled — that happens while the robot is armed, "
                f"and the gesture should have re-enabled it.")

        while time.monotonic() < deadline:
            try:
                st = await asyncio.to_thread(_motion_status)
            except Exception:
                logger.warning("GetMotionStatus failed while waiting for %s", name,
                               exc_info=True)
                return
            if st.get("status") != "MotionCommandStatus_OPERATING":
                return
            await asyncio.sleep(poll)
        logger.info("gesture %s: stopped waiting at the backstop deadline", name)


runner = MissionRunner()


# --------------------------------------------------------------------------- #
# scheduled start
# --------------------------------------------------------------------------- #
# COST: negligible. The scheduler is one asyncio task that sleeps. It wakes at most
# once a second to compare wall-clock time, does no subprocess work and no I/O, and
# there is at most one of them because there is one robot. On a box already at load
# ~29 this is not measurable.
#
# WHY IT RE-READS THE CLOCK EACH TICK instead of computing one long sleep: the
# robot's clock is NOT NTP-synced (`System clock synchronized: no`), so it can be
# stepped — by an operator, or by NTP being switched on later. A single long
# `asyncio.sleep(delta)` computed up front would then fire at the wrong moment. A
# short poll against the current wall clock survives the clock moving underneath it.
# How long to keep holding the walking action after a mission ends, so back-to-back
# runs do not each pay the two-hop arming transition. After this the robot is handed
# back to AgiBot's idle behaviour.
HOLD_RELEASE_AFTER_S = 15.0
SCHEDULE_TICK_S = 0.5
# Refuse to sit on a schedule for longer than this. A mission armed for "08:00"
# that was actually meant for yesterday should not lurk for 23 hours.
SCHEDULE_MAX_WAIT_S = 24 * 3600


def _set_motion_player(enabled: bool) -> None:
    """Enable or disable BOTH motion players.

    Order matters when disabling: body first, then neck, or a body animation is
    left mid-play with its head frozen.
    """
    a2_nav = _import_nav()
    methods = (("EnableMotionPlayer", "EnableNeckMotionPlayer") if enabled
               else ("DisableMotionPlayer", "DisableNeckMotionPlayer"))
    for m in methods:
        a2_nav.post(_motion_url(m), {"header": {}})


def _prepare_for_walking() -> dict:
    """Make the robot able to walk, without a human running `a2_nav.py arm`.

    Two things stop a scheduled mission dead, both established earlier in this
    robot's history:
      1. The MC sitting in McAction_DEFAULT (an E-stop demotes it there and
         releasing the E-stop does NOT restore it), where pnc accepts goals and
         reports RUNNING forever while the robot stands still.
      2. skillpilot's idle "liveliness" animation, which drags the MC into
         McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO roughly every 80 s — so even a
         correctly armed robot can be back in the wrong action by the time the
         scheduled moment arrives.

    So: stop the idle animation first (otherwise it can undo the next step), then
    walk a2_nav's action graph to a locomotion action. That graph walk is the same
    `a2_nav.py arm --execute` logic — DEFAULT cannot jump straight to a walking
    action, it must go via RL_JOINT_DEFAULT, and SetAction lies about success so
    every hop is verified.

    THIS POWERS THE LEG MOTORS. It only runs when the operator ticked "prepare",
    and only in the last minute before a start they chose.
    """
    a2_nav = _import_nav()
    result: dict[str, Any] = {}

    try:
        _set_motion_player(False)
        result["idle_motion"] = "disabled"
    except Exception as exc:
        # Not fatal: the mission can still run, it is just more likely to be
        # interrupted by an animation.
        logger.warning("could not disable idle motion: %s", exc)
        result["idle_motion"] = f"could not disable: {exc}"

    action, _status = a2_nav.mc_action()
    if a2_nav.can_walk(action):
        result["action"] = f"already {action}"
        return result
    hops = a2_nav.plan_action_path(action, a2_nav.WALK_ACTION_DEFAULT)
    done = []
    for hop in hops:
        done.append(a2_nav.set_action(hop))
        if hop != hops[-1]:
            time.sleep(3.0)     # the tablet leaves ~3 s between hops
    result["action"] = f"{action} -> " + " -> ".join(done)
    return result


# --------------------------------------------------------------------------- #
# the action hold ("arm" that actually sticks)
# --------------------------------------------------------------------------- #
# WHY A ONE-SHOT ARM DOES NOT STICK — root-caused 2026-09-11
# ----------------------------------------------------------
# skillpilot (x86 MC) runs a "standby" feature with `standby_mode_: true` and
# `standby_time_: 60`. Every ~60 s idle it sends an idle animation, and its
# mc_action_service.cc does this FIRST:
#
#   CheckAndSetMcAction begin for protocol action: 405,
#       target_action: McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO
#   Current action: 401 (RL_LOCOMOTION_DEFAULT) Target action: 405
#   Attempting transition, retry_count: 1
#   -> Current Action: RL_WHOLE_BODY_EXT_JOINT_SERVO, Last Action: RL_LOCOMOTION_DEFAULT
#
# and then never puts it back. Measured: armed at 15:48:21, gone by 15:48:31.
#
# CRUCIALLY, DisableMotionPlayer DOES NOT PREVENT THIS. That stops motion_player
# PLAYING; skillpilot still SENDS, and the action switch happens on the send path
# before the player is even consulted. Verified: with both players disabled,
# skillpilot kept sending every ~81 s and kept stealing the action.
#
# There is no exposed switch for skillpilot's standby (`ConfigControl` is a
# key/value store but the key is not discoverable without reading the binary,
# which runs as root), and stopping the whole skillpilot app would also lose
# battery warnings and the rest-point countdown. So instead of trying to win the
# argument, we simply re-assert: a small loop notices the action drift and puts it
# back. skillpilot switches at most once a minute; we check every couple of
# seconds, so the robot is walk-capable essentially all the time.
HOLD_POLL_S = 2.0
HOLD_REARM_GRACE_S = 5.0     # don't thrash if a re-arm is already in flight


class ActionHold:
    """Keeps the MC in a walking action for as long as navigation needs it.

    This is the difference between `a2_nav.py arm` (a one-shot, which skillpilot
    undoes within a minute) and "the robot is armed until I say otherwise".

    It is NOT always-on by design. Holding means the legs stay powered and the
    robot never settles into AgiBot's idle behaviour, so it is engaged around
    navigation and released afterwards, which is what `auto_release_s` is for.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._suppressed = 0          # >0 while a gesture legitimately needs servo
        self._rearms = 0
        self._last_rearm: float = 0.0
        self._last_error: str = ""
        self._engaged_at: float = 0.0

    def is_held(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        return {"held": self.is_held(),
                "rearms": self._rearms,
                "suppressed": self._suppressed > 0,
                "engaged_for_s": round(time.time() - self._engaged_at, 1)
                                 if self._engaged_at else 0,
                "last_error": self._last_error or None}

    # A gesture NEEDS whole-body servo — it is played by motion_player. Holding
    # through one would yank the action out from under it mid-animation, so the
    # runner brackets gestures with these.
    def suppress(self) -> None:
        self._suppressed += 1

    def unsuppress(self) -> None:
        self._suppressed = max(0, self._suppressed - 1)

    async def engage(self) -> dict:
        if self._task is not None and not self._task.done():
            return {"already_held": True, **self.status()}
        result = await asyncio.to_thread(_prepare_for_walking)
        self._engaged_at = time.time()
        self._rearms = 0
        self._last_error = ""
        self._task = asyncio.create_task(self._loop())
        relay._broadcast({"type": "hold", "stamp": time.time(), **self.status()})
        return {**result, **self.status()}

    async def release(self, restore_idle: bool = True) -> dict:
        """Stop holding and hand the robot back to AgiBot's normal behaviour."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        self._engaged_at = 0.0
        restored = None
        if restore_idle:
            # Give skillpilot its idle animation back, otherwise the robot stays
            # unnaturally still and we have silently changed how it behaves.
            try:
                await asyncio.to_thread(_set_motion_player, True)
                restored = "idle motion re-enabled"
            except Exception as exc:
                restored = f"could not re-enable idle motion: {exc}"
        relay._broadcast({"type": "hold", "stamp": time.time(), **self.status()})
        return {"released": True, "idle_motion": restored, **self.status()}

    async def _loop(self) -> None:
        a2_nav = _import_nav()
        try:
            while True:
                await asyncio.sleep(HOLD_POLL_S)
                if self._suppressed:
                    continue
                try:
                    action, _st = await asyncio.to_thread(a2_nav.mc_action)
                except Exception as exc:
                    self._last_error = str(exc)
                    continue
                if a2_nav.can_walk(action):
                    continue
                if time.time() - self._last_rearm < HOLD_REARM_GRACE_S:
                    continue
                self._last_rearm = time.time()
                self._rearms += 1
                logger.info("action hold: %s is not walk-capable, re-arming (#%d)",
                            action, self._rearms)
                try:
                    await asyncio.to_thread(_prepare_for_walking)
                    self._last_error = ""
                except Exception as exc:
                    self._last_error = str(exc)
                    logger.warning("action hold re-arm failed: %s", exc)
                relay._broadcast({"type": "hold", "stamp": time.time(),
                                  **self.status()})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("action hold loop died")


hold = ActionHold()


class ScheduledStart(BaseModel):
    """When to start, in the robot's own local time."""
    at: str = Field(...,
                    description="HH:MM or HH:MM:SS, 24-hour, in the robot's "
                                "local timezone. The next occurrence is used, so "
                                "a time already past today means tomorrow.")
    prepare: bool = Field(
        True,
        description="Before the start time, put the MC into a walking action and "
                    "stop the idle animation, so the robot is not stuck in "
                    "McAction_DEFAULT or being dragged into whole-body servo when "
                    "the moment arrives.")


def _next_occurrence(hhmmss: str) -> float:
    """Parse HH:MM[:SS] and return the epoch time of its next occurrence."""
    import datetime as _dt
    parts = hhmmss.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise HTTPException(400, "time must be HH:MM or HH:MM:SS, 24-hour")
    hh, mm = int(parts[0]), int(parts[1])
    ss = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
        raise HTTPException(400, f"{hhmmss!r} is not a valid 24-hour time")
    now = _dt.datetime.now()
    target = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)      # already gone today -> tomorrow
    return target.timestamp()


@router.post("/missions/{mission_id}/run")
async def run_mission(mission_id: str) -> dict:
    mission = await asyncio.to_thread(store.get, mission_id)
    if mission is None:
        raise HTTPException(404, "no such mission")
    return await runner.start(mission)


@router.post("/missions/{mission_id}/schedule")
async def schedule_mission(mission_id: str, body: ScheduledStart) -> dict:
    """Arm a mission to start at a wall-clock time in the robot's local zone."""
    mission = await asyncio.to_thread(store.get, mission_id)
    if mission is None:
        raise HTTPException(404, "no such mission")
    return await runner.schedule(mission, body.at, body.prepare)


@router.post("/run/unschedule")
async def unschedule() -> dict:
    """Drop a pending scheduled start without touching a running mission."""
    return await runner.cancel_schedule()


class PlayGesture(BaseModel):
    gesture: str = Field(..., description="a curated name or 'motion:<id>'")


@router.post("/gestures/play")
async def play_gesture(body: PlayGesture) -> dict:
    """Play one gesture now, for previewing it while building a mission.

    Runs the exact same path a mission step does — including lending the motion
    player back if the robot is currently armed — so what you preview is what you
    get. Refused while a mission is running, because the robot is busy.
    """
    if runner.status().get("active"):
        raise HTTPException(409, "a mission is running — the robot is busy")
    _validate_action(Action(type="gesture", gesture=body.gesture))
    started = time.time()
    try:
        await runner._gesture(body.gesture)
    except Exception as exc:
        raise HTTPException(502, str(exc))
    return {"gesture": body.gesture, "seconds": round(time.time() - started, 2),
            "hold": hold.status()}


@router.post("/arm")
async def arm_robot() -> dict:
    """ARM: hold the robot in a walking action until disarmed.

    Unlike a one-shot `a2_nav.py arm --execute`, this keeps holding: skillpilot's
    standby feature steals the action back within ~60 s otherwise. Powers the legs
    and stops the idle animation for the duration.
    """
    try:
        return await hold.engage()
    except Exception as exc:
        raise HTTPException(502, f"arm failed: {exc}")


@router.post("/disarm")
async def disarm_robot(restore_idle: bool = Query(True)) -> dict:
    """DISARM: stop holding and give the robot back to AgiBot's idle behaviour."""
    return await hold.release(restore_idle=restore_idle)


@router.get("/arm")
async def arm_status() -> dict:
    return hold.status()


@router.post("/prepare")
async def prepare_now() -> dict:
    """Stop the idle animation and put the MC into a walking action, now.

    The same thing the scheduler does just before a timed start, exposed so it can
    be done by hand instead of shelling into `a2_nav.py arm --execute`.
    POWERS THE LEG MOTORS.
    """
    try:
        return await asyncio.to_thread(_prepare_for_walking)
    except Exception as exc:
        raise HTTPException(502, f"prepare failed: {exc}")


@router.post("/run/cancel")
async def cancel_run() -> dict:
    """Cancel the mission and stop the robot."""
    return await runner.cancel()


@router.get("/run")
async def run_status() -> dict:
    return runner.status()


# --------------------------------------------------------------------------- #
# idle "liveliness" motion (灵动待机)
# --------------------------------------------------------------------------- #
# WHAT THIS IS
# ------------
# Left alone, the robot plays a looping idle animation: it turns its waist, moves its
# head and generally tries to look alive. Traced 2026-09-09:
#
#   skillpilot (on the x86 MC, status_control.cc) notices everything is idle and
#     -> SendMotionCommand to motion_player (MC, port 56444) with one of
#        /agibot/data/resources/default/motion/灵动环顾{1,2,3,4}/*.mcap
#        ("灵动环顾" = "nimble look-around"; the business scenario is called
#         "灵动待机" = "nimble standby" in skillpilot/capability_business.yaml)
#     -> motion_player streams joints, which forces the MC into
#        McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO.
#
# Measured cadence: animation ~19.7 s, then a reset, then ~62 s idle, i.e. a new one
# roughly every 80 s. That is why the robot keeps leaving a locomotion action.
#
# CAN THE DELAY BE MADE LONGER? No — searched motion_player_t2d0_cfg.yaml,
# skillpilot.yaml and capability_business.yaml on the MC: the interval is not exposed
# as a config key, it is inside skillpilot's compiled status_control.cc. The only
# config-level switch is `SkillPilotModule.status.enable`, which would also disable
# battery warnings and the rest-point countdown, and needs a service restart. So the
# runtime RPC below is both the finer and the safer lever.
#
# WHY A TOGGLE IS SAFE HERE
# -------------------------
# Disabling only stops an animation being *started*; it powers nothing down and moves
# nothing. It takes effect immediately, needs no restart, and is not persisted by the
# robot — so a reboot brings the idle motion back.


def _motion_url(method: str) -> str:
    """MotionCommandService is a DOT-form service on the Orin gateway.

    Note the dot: `/rpc/aimdk.protocol.MotionCommandService/<Method>`. a2_nav's PNC
    constant uses the slash form (`/rpc/aimdk.protocol/PncService`) — both forms
    exist on this gateway depending on the service, and picking the wrong one gives
    a 404 that looks like "the method does not exist".
    """
    return f"http://127.0.0.1:51056/rpc/aimdk.protocol.MotionCommandService/{method}"


@router.get("/idle-motion")
async def idle_motion_status() -> dict:
    """Whether the idle animation is currently allowed to play.

    `is_neck_enable` is the head/neck player's live flag. `status` is the player's
    own state machine (IDLE = waiting for the next animation, OPERATING = playing,
    STOP = player disabled).
    """
    def work():
        a2_nav = _import_nav()
        out = a2_nav.post(_motion_url("GetMotionStatus"), {"header": {}})
        motion = out.get("motion_id") or ""
        return {
            "neck_enabled": bool(out.get("is_neck_enable")),
            "player_status": out.get("status"),
            "current_motion": motion.rsplit("/", 1)[-1] if motion else None,
            "time_to_end_ms": out.get("time_to_end_ms"),
        }

    try:
        return await asyncio.to_thread(work)
    except Exception as exc:
        raise HTTPException(502, f"GetMotionStatus failed: {exc}")


class IdleMotionToggle(BaseModel):
    enabled: bool = Field(..., description="False stops the idle animation playing")
    include_body: bool = Field(
        True,
        description="Also toggle the whole-body player, not just the head/neck. "
                    "The body player is what drags the MC into "
                    "RL_WHOLE_BODY_EXT_JOINT_SERVO, so leave this on to keep the "
                    "robot in a locomotion action.")


@router.post("/idle-motion")
async def set_idle_motion(body: IdleMotionToggle) -> dict:
    """Turn the idle "liveliness" animation off or on, at runtime.

    Order matters when disabling: stop the body player first, then the neck. Doing it
    the other way round leaves a body animation mid-play with its head frozen.
    """
    def work():
        a2_nav = _import_nav()
        calls = []
        if body.enabled:
            if body.include_body:
                calls.append("EnableMotionPlayer")
            calls.append("EnableNeckMotionPlayer")
        else:
            if body.include_body:
                calls.append("DisableMotionPlayer")
            calls.append("DisableNeckMotionPlayer")
        results = {}
        for m in calls:
            out = a2_nav.post(_motion_url(m), {"header": {}})
            results[m] = out.get("state", out)
        return results

    try:
        results = await asyncio.to_thread(work)
    except Exception as exc:
        raise HTTPException(502, f"idle motion toggle failed: {exc}")
    status = await idle_motion_status()
    return {"applied": results, **status,
            "note": "Not persisted by the robot — a reboot re-enables idle motion."}
