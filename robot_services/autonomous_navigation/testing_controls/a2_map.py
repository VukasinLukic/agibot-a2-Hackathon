#!/usr/bin/env python3
"""
a2_map.py — read the AgiBot A2 Ultra's LiDAR/SLAM maps, render them, and locate the robot.

WHERE THE MAPS LIVE
-------------------
Everything the tablet app records lands on THIS Orin (not the x86 motion controller):

    /agibot/data/var/MapManagerModule/
      map.db                 SQLite index: map_name -> map_id, grid info, topo/waypoints
      <map_id>/              one dir per map; <map_id> = creation time in epoch MILLIseconds
        occupancy_map.png    2D grid, RGB, 3 colours (see GOTCHA 2)
        occupancy_map.yaml   resolution / width / height / origin  (see GOTCHA 1)
        occupancy_map_prob.png   same grid, 8-bit probability instead of trinary
        map.pcd              full 3D LiDAR cloud (binary_compressed PCD)
        keyframes/*.pcd      per-keyframe clouds
        frame_poses.txt      "t x y z qx qy qz qw" per SLAM frame, WORLD metres
        trajectories.txt     "u v" per waypoint, PIXEL coords
        grid_map_info.txt    "px_per_m", "origin_u origin_v", "width height"
        <hash>.map           SLAM backend's own binary blob — opaque, ignore

GOTCHA 1 — THE ORIGIN IS TOP-LEFT, NOT ROS' BOTTOM-LEFT
-------------------------------------------------------
occupancy_map.yaml looks like a ROS map_server file, but `origin` is the world
coordinate of the TOP-LEFT pixel with v growing DOWNWARD. Real ROS map_server
treats origin as BOTTOM-left with v growing up. Using the ROS formula puts
waypoints hundreds of pixels off the image. Verified against grid_map_info.txt
(origin pixel 226,469 for test_big) and against frame_poses.txt starting at
world (0,0):

    u = (x - origin_x) / resolution           x = origin_x + u * resolution
    v = (origin_y - y) / resolution           y = origin_y - v * resolution

GOTCHA 2 — THE PNG IS NOT A ROS OCCUPANCY IMAGE, AND THE YAML COLOURS ARE BGR
-----------------------------------------------------------------------------
occupancy_map.png is RGB with exactly three colours:

    (224,229,241)  FREE        yaml says "free_space: [241,229,224]"  <- BGR!
    (157,166,189)  OCCUPIED    yaml says "obstacle:   [189,166,157]"  <- BGR!
    (255,255,255)  UNKNOWN     not listed in the yaml at all

ROS map_server would read white as FREE, so unknown space becomes drivable and
your planner happily routes through walls. `export` writes a proper greyscale
PGM (0=occupied, 205=unknown, 254=free) plus a bottom-left-origin YAML.

GOTCHA 3 — `image:` IN THE YAML IS AN ABSOLUTE PATH
---------------------------------------------------
It points at /agibot/data/var/MapManagerModule/<map_id>/occupancy_map.png. Copy
the folder anywhere else and the path still points home. We ignore the field and
resolve the PNG relative to the map dir.

GOTCHA 4 — `resolution` MEANS TWO DIFFERENT THINGS
--------------------------------------------------
map.db's map_info says `resolution: 20.0` (PIXELS PER METRE); the yaml says
`resolution: 0.05` (METRES PER PIXEL). They are reciprocal. This module always
uses metres-per-pixel internally and trusts the yaml.

GOTCHA 5 — SLAM WRITES TO map.db WHILE IT RUNS
----------------------------------------------
There is a live -wal/-shm pair. We snapshot db+wal+shm to a temp dir and read the
copy, so we never take a lock on the robot's live index. Never write to this tree
while slam/map_manager are up.

LOCATING THE ROBOT
------------------
There is no pose to read until localization is RUNNING. On a fresh boot slam sits
idle (mapping/reloc/localization all `isRunning: false`) and a TF lookup just
blocks until it times out. Once localization is up, the live pose is the
map -> base_link transform. Frames are "map", "odom", "base_link".

    A2MapClient().localization_state()   ->  is it running?
    A2MapClient().robot_pose()           ->  Pose(x, y, yaw) in map/world metres

`start_localization()` is provided but is NEVER called automatically — it changes
robot state and will fight the tablet app for control of slam.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Sequence
import numpy as np

MAP_ROOT = "/agibot/data/var/MapManagerModule" # This is where the map databse is stored... DO NOT CHANGE THIS PLEASE!!!
GATEWAY = "http://127.0.0.1:51056"

# occupancy_map.png colours, as RGB (the yaml lists these reversed — see GOTCHA 2)
RGB_FREE = (224, 229, 241)
RGB_OCCUPIED = (157, 166, 189)
RGB_UNKNOWN = (255, 255, 255)

# our internal grid encoding, matching ROS nav_msgs/OccupancyGrid
FREE, OCCUPIED, UNKNOWN = 0, 100, -1

# greyscale values ROS map_server expects on export
PGM_OCCUPIED, PGM_UNKNOWN, PGM_FREE = 0, 205, 254


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Pose:
    """A robot pose in the map frame. x/y in metres, yaw in radians."""

    x: float
    y: float
    yaw: float
    stamp: float | None = None

    def __str__(self) -> str:
        return f"x={self.x:+.3f} m  y={self.y:+.3f} m  yaw={math.degrees(self.yaw):+7.2f}deg"


def quat_to_yaw(qx: float, qy: float, qz: float, qw: float) -> float:
    """Z-axis rotation from a quaternion (the only component that matters on a floor)."""
    return math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))


@dataclass(frozen=True)
class GridInfo:
    """Occupancy-grid geometry. `origin` is the world coord of the TOP-LEFT pixel."""

    resolution: float  # metres per pixel
    width: int  # pixels
    height: int  # pixels
    origin_x: float  # metres
    origin_y: float  # metres

    def world_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.origin_x) / self.resolution, (self.origin_y - y) / self.resolution

    def pixel_to_world(self, u: float, v: float) -> tuple[float, float]:
        return self.origin_x + u * self.resolution, self.origin_y - v * self.resolution

    def extent_m(self) -> tuple[float, float, float, float]:
        """(left, right, bottom, top) in metres — ready for matplotlib imshow(extent=...)."""
        left, top = self.pixel_to_world(0, 0)
        right, bottom = self.pixel_to_world(self.width, self.height)
        return left, right, bottom, top

    def contains_px(self, u: float, v: float) -> bool:
        return 0 <= u < self.width and 0 <= v < self.height


@dataclass(frozen=True)
class Waypoint:
    """A navigation point placed in the tablet app."""

    id: int
    name: str
    x: float
    y: float
    theta: float


# --------------------------------------------------------------------------- #
# the map store
# --------------------------------------------------------------------------- #
@dataclass
class MapMeta:
    map_id: str
    name: str
    index: int
    directory: str
    recorded: datetime
    saved: datetime
    rotate_angle: float
    waypoints: list[Waypoint] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        return os.path.isdir(self.directory)


class MapStore:
    """Read-only view of map.db and the map directories beside it."""

    def __init__(self, root: str = MAP_ROOT) -> None:
        self.root = root
        self.db_path = os.path.join(root, "map.db")
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError(f"no map.db under {root}")

    # -- safe read of a live sqlite db (GOTCHA 5) ---------------------------- #
    def _snapshot(self) -> str:
        tmp = tempfile.mkdtemp(prefix="a2map-")
        for suffix in ("", "-wal", "-shm"):
            src = self.db_path + suffix
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp, "map.db" + suffix))
        return os.path.join(tmp, "map.db")

    def maps(self) -> list[MapMeta]:
        snap = self._snapshot()
        try:
            con = sqlite3.connect(snap)
            rows = con.execute("SELECT map_id, map_version, map_name, map_info, topo FROM map").fetchall()
            con.close()
        finally:
            shutil.rmtree(os.path.dirname(snap), ignore_errors=True)

        out: list[MapMeta] = []
        for map_id, map_version, name, info_json, topo_json in rows:
            info = json.loads(info_json) if info_json else {}
            directory = (info.get("map_path") or os.path.join(self.root, str(map_id))).rstrip("/")
            rotate, wps = _parse_topo(topo_json)
            out.append(
                MapMeta(
                    map_id=str(map_id),
                    name=name,
                    index=int(info.get("index", 0)),
                    directory=directory,
                    recorded=datetime.fromtimestamp(int(map_id) / 1000.0),
                    saved=datetime.fromtimestamp(int(map_version) / 1000.0),
                    rotate_angle=rotate,
                    waypoints=wps,
                )
            )
        # sort newest-first; map_version is unreliable (some maps report a save
        # time EARLIER than their creation time), so order by map_id
        out.sort(key=lambda m: int(m.map_id), reverse=True)
        return out

    def current_map_id(self) -> str | None:
        snap = self._snapshot()
        try:
            con = sqlite3.connect(snap)
            row = con.execute("SELECT map_id FROM current_map").fetchone()
            con.close()
            return str(row[0]) if row else None
        finally:
            shutil.rmtree(os.path.dirname(snap), ignore_errors=True)

    def find(self, ref: str) -> MapMeta:
        """Look a map up by exact name, then case-insensitive name, then map_id.

        Exact-first matters: this robot has both "power platform" and
        "Power platform v2", which differ only by case and suffix.
        """
        maps = self.maps()
        for m in maps:
            if m.name == ref or m.map_id == ref:
                return m
        hits = [m for m in maps if m.name.lower() == ref.lower()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise LookupError(f"{ref!r} is ambiguous: {[m.name for m in hits]}")
        hits = [m for m in maps if ref.lower() in m.name.lower()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise LookupError(f"{ref!r} matches several maps: {[m.name for m in hits]}")
        raise LookupError(f"no map named {ref!r}; have {[m.name for m in maps]}")

    def load(self, ref: str) -> "A2Map":
        return A2Map(self.find(ref))


def _parse_topo(topo_json: str | None) -> tuple[float, list[Waypoint]]:
    """Pull rotate_angle and navi_points out of map.db's `topo` column.

    Each navi_point's `pose` is itself a JSON *string* holding {x, y, theta}.
    """
    if not topo_json:
        return 0.0, []
    try:
        entries = json.loads(topo_json)
    except (ValueError, TypeError):
        return 0.0, []
    rotate, wps = 0.0, []
    for entry in entries or []:
        msg = entry.get("topo_msg") or {}
        rotate = float(msg.get("rotate_angle") or 0.0)
        for p in msg.get("navi_points") or []:
            try:
                pose = json.loads(p["pose"]) if isinstance(p.get("pose"), str) else (p.get("pose") or {})
            except (ValueError, TypeError):
                continue
            wps.append(
                Waypoint(
                    id=int(p.get("id", 0)),
                    name=str(p.get("name", "")),
                    x=float(pose.get("x", 0.0)),
                    y=float(pose.get("y", 0.0)),
                    theta=float(pose.get("theta", 0.0)),
                )
            )
    return rotate, wps


# --------------------------------------------------------------------------- #
# a single map
# --------------------------------------------------------------------------- #
class A2Map:
    """One recorded map: occupancy grid, waypoints, and the recorded trajectory.

    Grids and trajectories load lazily so `list` stays fast on the 384 MB map.
    """

    def __init__(self, meta: MapMeta) -> None:
        if not meta.exists:
            raise FileNotFoundError(f"map dir missing: {meta.directory}")
        self.meta = meta
        self.grid = self._load_grid_info()
        self._occ: np.ndarray | None = None
        self._traj: np.ndarray | None = None

    # -- geometry ----------------------------------------------------------- #
    def path(self, *parts: str) -> str:
        return os.path.join(self.meta.directory, *parts)

    def _load_grid_info(self) -> GridInfo:
        import yaml

        with open(self.path("occupancy_map.yaml")) as fh:
            y = yaml.safe_load(fh)
        origin = y.get("origin") or [0.0, 0.0, 0.0]
        return GridInfo(
            resolution=float(y["resolution"]),
            width=int(y["width"]),
            height=int(y["height"]),
            origin_x=float(origin[0]),
            origin_y=float(origin[1]),
        )

    def world_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        return self.grid.world_to_pixel(x, y)

    def pixel_to_world(self, u: float, v: float) -> tuple[float, float]:
        return self.grid.pixel_to_world(u, v)

    # -- occupancy ---------------------------------------------------------- #
    @property
    def occupancy(self) -> np.ndarray:
        """(height, width) int8 grid: -1 unknown, 0 free, 100 occupied.

        Row 0 is the TOP of the image, i.e. the +y end of the map (GOTCHA 1).
        """
        if self._occ is None:
            self._occ = self._decode_occupancy()
        return self._occ

    def _decode_occupancy(self) -> np.ndarray:
        from PIL import Image

        with Image.open(self.path("occupancy_map.png")) as im:
            im = im.convert("RGB")
            w, h = im.size
            # via tobytes(): PIL 9 + numpy 2 trip a __array__ copy-kwarg warning
            rgb = np.frombuffer(im.tobytes(), dtype=np.uint8).reshape(h, w, 3)

        occ = np.full((h, w), UNKNOWN, dtype=np.int8)
        occ[np.all(rgb == np.array(RGB_FREE, dtype=np.uint8), axis=-1)] = FREE
        occ[np.all(rgb == np.array(RGB_OCCUPIED, dtype=np.uint8), axis=-1)] = OCCUPIED
        return occ

    def stats(self) -> dict[str, Any]:
        occ = self.occupancy
        total = occ.size
        n_free = int((occ == FREE).sum())
        n_occ = int((occ == OCCUPIED).sum())
        res = self.grid.resolution
        return {
            "free_px": n_free,
            "occupied_px": n_occ,
            "unknown_px": total - n_free - n_occ,
            "free_m2": n_free * res * res,
            "occupied_m2": n_occ * res * res,
            "span_m": (self.grid.width * res, self.grid.height * res),
        }

    # -- trajectory --------------------------------------------------------- #
    @property
    def trajectory(self) -> np.ndarray:
        """(N, 4) float array of the recorded path: t, x, y, yaw. World metres.

        Prefers frame_poses.txt (full rate, world coords, with orientation) and
        falls back to trajectories.txt (sparse, pixel coords, no orientation).
        """
        if self._traj is None:
            self._traj = self._load_frame_poses()
            if self._traj.size == 0:
                self._traj = self._load_pixel_trajectory()
        return self._traj

    def _load_frame_poses(self) -> np.ndarray:
        p = self.path("frame_poses.txt")
        if not os.path.isfile(p):
            return np.empty((0, 4))
        rows = []
        with open(p) as fh:
            for line in fh:
                f = line.split()
                if len(f) < 8:
                    continue
                try:
                    t, x, y, _z, qx, qy, qz, qw = (float(v) for v in f[:8])
                except ValueError:
                    continue
                rows.append((t, x, y, quat_to_yaw(qx, qy, qz, qw)))
        return np.array(rows, dtype=float) if rows else np.empty((0, 4))

    def _load_pixel_trajectory(self) -> np.ndarray:
        p = self.path("trajectories.txt")
        if not os.path.isfile(p):
            return np.empty((0, 4))
        rows = []
        with open(p) as fh:
            for line in fh:
                f = line.split()
                if len(f) != 2:
                    continue
                try:
                    u, v = float(f[0]), float(f[1])
                except ValueError:
                    continue
                x, y = self.pixel_to_world(u, v)
                rows.append((float("nan"), x, y, float("nan")))
        return np.array(rows, dtype=float) if rows else np.empty((0, 4))

    # -- point cloud -------------------------------------------------------- #
    def cloud_path(self) -> str:
        return self.path("map.pcd")

    def cloud_point_count(self) -> int | None:
        """Read POINTS out of the PCD header without loading the cloud."""
        try:
            with open(self.cloud_path(), "rb") as fh:
                for _ in range(20):
                    line = fh.readline()
                    if not line:
                        break
                    if line.startswith(b"POINTS"):
                        return int(line.split()[1])
        except OSError:
            pass
        return None

    # -- export ------------------------------------------------------------- #
    def export(self, out_dir: str) -> str:
        """Write a portable, ROS-correct copy: greyscale PGM + bottom-left YAML.

        Fixes all four format traps at once — real occupancy greyscale instead of
        the 3-colour RGB, a relative `image:` path, ROS' bottom-left origin, and
        metres-per-pixel resolution. Waypoints and the trajectory go alongside as
        JSON/CSV so nothing is lost.
        """
        from PIL import Image

        os.makedirs(out_dir, exist_ok=True)
        occ = self.occupancy

        grey = np.full(occ.shape, PGM_UNKNOWN, dtype=np.uint8)
        grey[occ == FREE] = PGM_FREE
        grey[occ == OCCUPIED] = PGM_OCCUPIED
        Image.fromarray(grey, mode="L").save(os.path.join(out_dir, "map.pgm"))

        # ROS wants the world coord of the BOTTOM-left pixel
        g = self.grid
        origin_bl_x = g.origin_x
        origin_bl_y = g.origin_y - g.height * g.resolution
        with open(os.path.join(out_dir, "map.yaml"), "w") as fh:
            fh.write(
                "# exported by a2_map.py from AgiBot map "
                f"{self.meta.name!r} (map_id {self.meta.map_id})\n"
                "# origin is ROS convention here: world coord of the BOTTOM-left pixel\n"
                "image: map.pgm\n"
                "mode: trinary\n"
                f"resolution: {g.resolution!r}\n"
                f"origin: [{origin_bl_x!r}, {origin_bl_y!r}, 0.0]\n"
                "negate: 0\n"
                "occupied_thresh: 0.65\n"
                "free_thresh: 0.25\n"
            )

        with open(os.path.join(out_dir, "waypoints.json"), "w") as fh:
            json.dump(
                {
                    "map_name": self.meta.name,
                    "map_id": self.meta.map_id,
                    "frame": "map",
                    "waypoints": [
                        {"id": w.id, "name": w.name, "x": w.x, "y": w.y, "theta": w.theta}
                        for w in self.meta.waypoints
                    ],
                },
                fh,
                indent=2,
            )

        traj = self.trajectory
        if traj.size:
            with open(os.path.join(out_dir, "trajectory.csv"), "w") as fh:
                fh.write("t,x,y,yaw\n")
                for t, x, y, yaw in traj:
                    fh.write(f"{t!r},{x!r},{y!r},{yaw!r}\n")

        return out_dir

    # -- render ------------------------------------------------------------- #
    def render(
        self,
        out_path: str,
        waypoints: bool = True,
        trajectory: bool = True,
        pose: Pose | None = None,
        dpi: int = 150,
        title: str | None = None,
    ) -> str:
        """Draw the map to a PNG, in metres, with optional overlays."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        occ = self.occupancy
        # unknown -> light grey, free -> white, occupied -> near-black
        img = np.empty(occ.shape + (3,), dtype=np.uint8)
        img[occ == UNKNOWN] = (232, 232, 232)
        img[occ == FREE] = (255, 255, 255)
        img[occ == OCCUPIED] = (40, 44, 52)

        g = self.grid
        span_x = g.width * g.resolution
        span_y = g.height * g.resolution
        fig_w = 11.0
        fig_h = max(3.0, min(30.0, fig_w * span_y / span_x))
        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        ax.imshow(img, extent=g.extent_m(), origin="upper", interpolation="nearest")

        if trajectory:
            traj = self.trajectory
            if traj.size:
                ax.plot(
                    traj[:, 1], traj[:, 2],
                    color="#e8833a", lw=1.2, alpha=0.9, zorder=3,
                    label=f"recorded path ({len(traj)} poses)",
                )

        if waypoints and self.meta.waypoints:
            arrow = max(span_x, span_y) * 0.02
            for w in self.meta.waypoints:
                inside = g.contains_px(*g.world_to_pixel(w.x, w.y))
                ax.plot(
                    w.x, w.y, marker="o", ms=7, zorder=5,
                    mfc="#2f7ac9" if inside else "#c0392b", mec="white", mew=1.2,
                )
                ax.arrow(
                    w.x, w.y,
                    arrow * math.cos(w.theta), arrow * math.sin(w.theta),
                    head_width=arrow * 0.45, head_length=arrow * 0.45,
                    fc="#2f7ac9", ec="#2f7ac9", zorder=4, length_includes_head=True,
                )
                ax.annotate(
                    f"{w.id}. {w.name}", (w.x, w.y),
                    textcoords="offset points", xytext=(9, 5),
                    fontsize=8, color="#1b4f7e", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.75),
                )

        if pose is not None:
            arrow = max(span_x, span_y) * 0.03
            ax.plot(pose.x, pose.y, marker="*", ms=20, mfc="#d63b3b", mec="white", mew=1.4, zorder=8,
                    label="robot")
            ax.arrow(
                pose.x, pose.y,
                arrow * math.cos(pose.yaw), arrow * math.sin(pose.yaw),
                head_width=arrow * 0.4, head_length=arrow * 0.4,
                fc="#d63b3b", ec="#d63b3b", zorder=8, length_includes_head=True,
            )

        st = self.stats()
        ax.set_title(
            title
            or (
                f"{self.meta.name}   ({self.meta.recorded:%Y-%m-%d %H:%M})\n"
                f"{g.width}x{g.height} px @ {g.resolution:g} m/px   "
                f"{span_x:.1f} x {span_y:.1f} m   {st['free_m2']:.0f} m2 mapped free"
            ),
            fontsize=10,
        )
        ax.set_xlabel("x (m, map frame)")
        ax.set_ylabel("y (m, map frame)")
        ax.grid(alpha=0.15, lw=0.5)
        ax.set_aspect("equal")
        if ax.get_legend_handles_labels()[1]:
            ax.legend(loc="best", fontsize=8, framealpha=0.9)

        fig.tight_layout()
        fig.savefig(out_path, dpi=dpi)
        plt.close(fig)
        return out_path


# --------------------------------------------------------------------------- #
# live robot pose
# --------------------------------------------------------------------------- #
class A2MapClient:
    """Talks to the on-Orin AimRT gateway for map selection and live pose.

    All of these are proto3-JSON over HTTP-RPC at
        http://127.0.0.1:51056/rpc/aimdk.protocol.<Service>/<Method>
    """

    def __init__(self, base: str = GATEWAY, timeout: float = 5.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _rpc(self, service: str, method: str, body: dict | None = None) -> dict:
        import requests

        url = f"{self.base}/rpc/aimdk.protocol.{service}/{method}"
        payload = {"header": {}}
        payload.update(body or {})
        r = requests.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # -- map bookkeeping ---------------------------------------------------- #
    def stored_maps(self) -> list[dict]:
        return self._rpc("MappingService", "GetStoredMapNames").get("data", {}).get("map_lists", [])

    def current_working_map(self) -> str | None:
        return self._rpc("MappingService", "GetCurrentWorkingMap").get("data", {}).get("map_id")

    # -- localization ------------------------------------------------------- #
    def localization_state(self) -> bool:
        r = self._rpc("SLAMLocalizationService", "SLAMGetCurrentLocalizationState")
        return bool(r.get("isRunning", False))

    def mapping_state(self) -> bool:
        r = self._rpc("SLAMMappingService", "SLAMGetCurrentMappingState")
        return bool(r.get("isRunning", False))

    def relocalization_state(self) -> bool:
        r = self._rpc("SLAMRelocalizationService", "SLAMGetCurrentRelocalizationState")
        return bool(r.get("isRunning", False))

    def robot_pose(self, target: str = "map", source: str = "base_link") -> Pose:
        """Live pose as the map -> base_link transform.

        Raises RuntimeError if localization is not running — without it the TF
        tree has no map->odom link and this call would just block until timeout.
        """
        if not self.localization_state():
            raise RuntimeError(
                "localization is not running, so there is no map->base_link transform. "
                "Start it with start_localization(map_path, initial_pose) — or from the "
                "tablet app — then retry."
            )
        r = self._rpc(
            "TransFormService", "GetTransFormation",
            {"target_frame": target, "source_frame": source},
        )
        ts = r.get("transform_stamped") or {}
        tf = ts.get("transform") or {}
        pos = tf.get("position") or tf.get("translation") or {}
        rot = tf.get("orientation") or tf.get("rotation") or tf.get("quaternion") or {}
        stamp = ((ts.get("header") or {}).get("stamp") or {}).get("ms_since_epoch")
        return Pose(
            x=float(pos.get("x", 0.0)),
            y=float(pos.get("y", 0.0)),
            yaw=quat_to_yaw(
                float(rot.get("q_x", rot.get("x", 0.0))),
                float(rot.get("q_y", rot.get("y", 0.0))),
                float(rot.get("q_z", rot.get("z", 0.0))),
                float(rot.get("q_w", rot.get("w", 1.0))),
            ),
            stamp=float(stamp) / 1000.0 if stamp else None,
        )

    # -- state-changing; never called automatically -------------------------- #
    def start_localization(self, map_path: str, initial: Pose) -> dict:
        """Begin continuous localization against `map_path`, seeded at `initial`.

        CHANGES ROBOT STATE. `initial` must be roughly right (within a metre or
        two and the correct heading) or the scan match will latch onto the wrong
        part of the map. If you do not know where the robot is, run
        SLAMStartGlobalRelocalization first and use its result.
        """
        half = initial.yaw / 2.0
        return self._rpc(
            "SLAMLocalizationService", "SLAMStartLocalization",
            {
                "map_path": map_path,
                "reloc_pose": {
                    "position": {"x": initial.x, "y": initial.y, "z": 0.0},
                    "orientation": {
                        "q_x": 0.0, "q_y": 0.0,
                        "q_z": math.sin(half), "q_w": math.cos(half),
                    },
                },
            },
        )

    def stop_localization(self) -> dict:
        return self._rpc("SLAMLocalizationService", "SLAMStopLocalization")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_list(args: argparse.Namespace) -> int:
    store = MapStore(args.root)
    maps = store.maps()
    current = store.current_map_id()
    print(f"{'':2} {'name':22} {'map_id':15} {'grid':>13} {'res':>7} {'span (m)':>15}  recorded")
    print("-" * 104)
    for m in maps:
        mark = "*" if m.map_id == current else " "
        try:
            mp = A2Map(m)
            g = mp.grid
            grid = f"{g.width}x{g.height}"
            res = f"{g.resolution:g}"
            span = f"{g.width * g.resolution:.1f}x{g.height * g.resolution:.1f}"
        except (OSError, KeyError) as exc:
            grid, res, span = "?", "?", f"<{type(exc).__name__}>"
        print(
            f"{mark:2} {m.name:22} {m.map_id:15} {grid:>13} {res:>7} {span:>15}  "
            f"{m.recorded:%Y-%m-%d %H:%M}  ({len(m.waypoints)} wp)"
        )
    print("\n* = current working map")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    mp = MapStore(args.root).load(args.map)
    g, st = mp.grid, mp.stats()
    print(f"name          {mp.meta.name!r}")
    print(f"map_id        {mp.meta.map_id}")
    print(f"directory     {mp.meta.directory}")
    print(f"recorded      {mp.meta.recorded:%Y-%m-%d %H:%M:%S}")
    print(f"grid          {g.width} x {g.height} px @ {g.resolution:g} m/px")
    print(f"span          {st['span_m'][0]:.2f} x {st['span_m'][1]:.2f} m")
    print(f"origin (TL)   x={g.origin_x:g}  y={g.origin_y:g}   [world coord of top-left pixel]")
    print(f"cells         free={st['free_px']}  occupied={st['occupied_px']}  unknown={st['unknown_px']}")
    print(f"area          free={st['free_m2']:.1f} m2   occupied={st['occupied_m2']:.1f} m2")
    print(f"cloud         {mp.cloud_path()}  ({mp.cloud_point_count()} points)")
    print(f"trajectory    {len(mp.trajectory)} poses")
    print(f"rotate_angle  {mp.meta.rotate_angle:.3f} deg   [app display only — do NOT apply to coords]")
    if mp.meta.waypoints:
        print("waypoints")
        for w in mp.meta.waypoints:
            u, v = mp.world_to_pixel(w.x, w.y)
            flag = "" if g.contains_px(u, v) else "   <-- OUTSIDE GRID"
            print(
                f"  {w.id:>2}. {w.name:<16} x={w.x:+8.2f} y={w.y:+8.2f} "
                f"theta={math.degrees(w.theta):+7.1f}deg  px=({u:.0f},{v:.0f}){flag}"
            )
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    store = MapStore(args.root)
    mp = store.load(args.map)
    pose = None
    if args.pose:
        try:
            pose = A2MapClient().robot_pose()
            print(f"live pose: {pose}")
        except Exception as exc:  # noqa: BLE001 - report and still draw the map
            print(f"could not read live pose: {exc}", file=sys.stderr)
    out = args.out or f"{mp.meta.name.replace(' ', '_')}.png"
    mp.render(
        out,
        waypoints=not args.no_waypoints,
        trajectory=not args.no_trajectory,
        pose=pose,
        dpi=args.dpi,
    )
    print(f"wrote {out}  ({os.path.getsize(out) / 1024:.0f} KB)")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    mp = MapStore(args.root).load(args.map)
    out = args.out or f"{mp.meta.name.replace(' ', '_')}_export"
    mp.export(out)
    print(f"exported {mp.meta.name!r} -> {out}/")
    for f in sorted(os.listdir(out)):
        print(f"  {f}  ({os.path.getsize(os.path.join(out, f)) / 1024:.1f} KB)")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    c = A2MapClient()
    try:
        mapping = c.mapping_state()
        reloc = c.relocalization_state()
        loc = c.localization_state()
    except Exception as exc:  # noqa: BLE001
        print(f"gateway unreachable at {c.base}: {exc}", file=sys.stderr)
        return 1
    print(f"gateway            {c.base}  reachable")
    print(f"mapping            {'RUNNING' if mapping else 'idle'}")
    print(f"relocalization     {'RUNNING' if reloc else 'idle'}")
    print(f"localization       {'RUNNING' if loc else 'idle'}")
    print(f"working map_id     {c.current_working_map()}")
    sys.stdout.flush()  # keep the stderr hint below from jumping ahead when piped
    if not loc:
        print(
            "\nNo live pose available: localization is idle, so the TF tree has no\n"
            "map->base_link link. Start localization from the tablet app, or call\n"
            "A2MapClient().start_localization(map_path, Pose(x, y, yaw)).",
            file=sys.stderr,
        )
        return 2
    print(f"\npose               {c.robot_pose()}")
    return 0


def _cmd_pose(args: argparse.Namespace) -> int:
    c = A2MapClient()
    store = MapStore(args.root)
    mp = store.load(args.map) if args.map else None
    try:
        while True:
            try:
                pose = c.robot_pose()
                line = str(pose)
                if mp is not None:
                    u, v = mp.world_to_pixel(pose.x, pose.y)
                    occ = mp.occupancy
                    iu, iv = int(round(u)), int(round(v))
                    cell = (
                        {FREE: "free", OCCUPIED: "OCCUPIED", UNKNOWN: "unknown"}[int(occ[iv, iu])]
                        if mp.grid.contains_px(iu, iv)
                        else "off-grid"
                    )
                    line += f"   px=({u:7.1f},{v:7.1f}) {cell}"
                print(line, flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"pose unavailable: {exc}", file=sys.stderr)
                if not args.watch:
                    return 1
            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="a2_map.py",
        description="Read, render and export AgiBot A2 Ultra SLAM maps; locate the robot in one.",
    )
    p.add_argument("--root", default=MAP_ROOT, help=f"map store (default {MAP_ROOT})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list every recorded map").set_defaults(fn=_cmd_list)

    q = sub.add_parser("info", help="print one map's geometry and waypoints")
    q.add_argument("map")
    q.set_defaults(fn=_cmd_info)

    r = sub.add_parser("render", help="draw a map to PNG")
    r.add_argument("map")
    r.add_argument("-o", "--out")
    r.add_argument("--no-waypoints", action="store_true")
    r.add_argument("--no-trajectory", action="store_true")
    r.add_argument("--pose", action="store_true", help="overlay the live robot pose")
    r.add_argument("--dpi", type=int, default=150)
    r.set_defaults(fn=_cmd_render)

    e = sub.add_parser("export", help="write a portable ROS-correct copy (PGM + YAML)")
    e.add_argument("map")
    e.add_argument("-o", "--out")
    e.set_defaults(fn=_cmd_export)

    sub.add_parser("status", help="slam / localization state and live pose").set_defaults(fn=_cmd_status)

    o = sub.add_parser("pose", help="print the live robot pose")
    o.add_argument("--map", help="also report the pixel + occupancy cell in this map")
    o.add_argument("--watch", action="store_true", help="loop until Ctrl-C")
    o.add_argument("--interval", type=float, default=0.5)
    o.set_defaults(fn=_cmd_pose)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
