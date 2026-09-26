#!/usr/bin/env python3
"""
a2_costmap.py — live LOCAL COSTMAP from the A2's MID-360 LiDAR, in base_link.

WHAT THIS IS
------------
A robot-centred occupancy grid built from the LiDAR point cloud, refreshed every
time the sensor publishes (~10 Hz). The robot sits at the centre of the grid,
+x is forward, +y is left. Cells that collect enough returns are "occupied".

It is READ-ONLY. It subscribes to a topic that is already being published and
never commands the robot.

WHY A SIDECAR PROCESS AND NOT A SUPERVISOR MODULE
-------------------------------------------------
The supervisor runs on the repo `.venv`, which is **python3.12 and has no rclpy**.
rclpy only exists on system python3.10 with /opt/ros/humble sourced. Rather than
force the supervisor onto a different interpreter, this runs as its own process
and speaks NDJSON on stdout; the supervisor spawns it and relays frames to the
browser. That also means the team can run it standalone for debugging.

TWO OUTPUT MODES
    --ascii    (default) live terminal view, for a quick look over SSH
    --ndjson   one compact JSON object per line on stdout, for the supervisor

WHY THE GRID ACCUMULATES (--decay)
-----------------------------------
The MID-360 is a **non-repetitive scanner**: consecutive frames sample DIFFERENT
directions, so any single 10 Hz frame is sparse and speckled. Rendering one frame
at a time flickers badly and makes small obstacles blink in and out. So each cell
holds a confidence that is multiplied by `--decay` every frame and topped up by
new hits. Default 0.80 gives roughly a half-second memory — enough to fill in the
scan pattern, short enough to drop stale obstacles quickly.

    --decay 0     pure single-frame, no memory (speckled, but zero lag)
    --decay 0.95  long memory, very smooth, slower to forget

LIMITATION, STATED PLAINLY: the grid accumulates in base_link, which MOVES WITH
THE ROBOT, and this build does not compensate with odometry. While the robot
drives, remembered cells smear opposite the motion by roughly
(1/(1-decay)) x frame_period x speed — about 0.25 m at 0.5 m/s with the default.
Standing still it is exact. Use --decay 0 while driving, or wire in
/legged_odometry_node/legged_odometry to shift the grid before trusting it in
motion.

THE EXTRINSIC — SETTLED, DO NOT RE-GUESS
-----------------------------------------
/agibot/data/param/calibration/extrinsic_baselink_T_lidar.txt is
    x y z  then  qw qx qy qz     <-- **wxyz**, not the xyzw used by frame_poses.txt
qy dominates => 180 deg about Y: (x,y,z) -> (-x,+y,-z). The MID-360 hangs from the
neck upside down AND backwards. Read it as xyzw and every bearing is ~180 deg
wrong and the floor lands at an impossible height. See a2_lidar.py for the two
independent proofs.

SELF-HITS: the LiDAR sees the robot's own torso and arms. Everything closer than
--min-range (default 0.40 m) is discarded, otherwise the robot walls itself in.

FRAME FORMAT (--ndjson)
-----------------------
One JSON object per line. Geometry fields are metres; the grid is `n` x `n` cells
of `resolution` m, spanning +/-`radius` about the robot.

    {"type":"frame","seq":12,"stamp":1788452301.4,"hz":9.98,
     "radius":10.0,"resolution":0.1,"n":200,
     "z_min":-0.3,"z_max":1.7,"min_range":0.4,"decay":0.8,
     "points_raw":20064,"points_used":5727,"n_cells":1834,
     "footprint":[[0.18,0.2],...],
     "cells":"<base64 Uint16LE, interleaved ix,iy,ix,iy,...>",
     "hits":"<base64 Uint8, one per cell, confidence 1..255>",
     "n_glass":37,
     "glass_cells":"<base64 Uint16LE, same packing as `cells`>",
     "glass_conf":"<base64 Uint8, evidence 1..255>",
     "glass_lines":[{"x1":..,"y1":..,"x2":..,"y2":..,
                     "inliers":14,"rms":0.03,"length":1.8}],
     "glass_stats":{"windows":9,"windows_discarded":2,"window_frames":40,
                    "frames_in_window":12,"motion":0.0,"drift_m":0.01,
                    "observing":true,"settling":false}}

**Read `observing`, not `n_glass`.** It means "the last window was usable" — it
is false while settling and false whenever the scene is moving. `n_glass == 0`
with `observing` false does NOT mean there is no glass; it means nothing was
looked at. Treating the two as the same is the one mistake here that could get
somebody hurt.

The `glass_*` fields are present only when detection is enabled (it is by
default; `--no-glass` removes them).

GLASS DETECTION, IN ONE PARAGRAPH
---------------------------------
A 905 nm LiDAR passes through glass at most incidence angles, so a pane does not
read as "absent" — it reads as INCONSISTENT, appearing and vanishing between
frames. Cells that keep flipping (and whose bearing also shows a wide spread of
return ranges) are flagged, and straight segments are fitted through them,
because glass is nearly always planar. Evidence is gathered over multi-second
windows so that a person walking past cannot be mistaken for a wall, and every
window is DISCARDED while the robot is moving, because this grid is in base_link
and is not odometry-compensated. See the big comment above `GlassDetector` for
the full reasoning and the measured numbers behind each threshold.

**This is advisory.** It feeds the view, not vectorflux, and it does not stop the
robot. `glass_stats.settling` is true until enough windows have accumulated for
the output to mean anything.

Cell index -> metres (both axes, same rule):
    x_centre = (ix + 0.5) * resolution - radius
    y_centre = (iy + 0.5) * resolution - radius

`decode_frame(obj)` below does this for you and returns an (N,3) array of
[x, y, confidence]. Use it rather than re-deriving the packing.

USAGE
    python3 a2_costmap.py                          # terminal view, 10 m
    python3 a2_costmap.py --radius 5               # tighter
    python3 a2_costmap.py --resolution 0.05        # finer cells
    python3 a2_costmap.py --decay 0                # no accumulation
    python3 a2_costmap.py --ndjson                 # machine-readable stream
    python3 a2_costmap.py --ndjson --max-hz 5      # halve the output rate
    python3 a2_costmap.py --no-glass               # glass detection off
    python3 a2_costmap.py --glass-threshold 0.3    # more sensitive to glass
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import shlex
import sys
import time
from typing import Optional

# ---------------------------------------------------------------- ROS bootstrap
# Identical bootstrap to a2_lidar.py. See that file for the full rationale:
# .bashrc pins ROS_DOMAIN_ID=231 but the robot stack is on 232, so an
# un-forced domain yields ZERO topics with no error message at all.

ROS_SETUP = "/opt/ros/humble/setup.bash"
DDS_PROFILE = "/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml"
ROS_DOMAIN_ID = "232"

_dom = os.environ.get("A2_ROS_DOMAIN_ID")
for _i, _a in enumerate(sys.argv):
    if _a == "--domain" and _i + 1 < len(sys.argv):
        _dom = sys.argv[_i + 1]
    elif _a.startswith("--domain="):
        _dom = _a.split("=", 1)[1]
os.environ["ROS_DOMAIN_ID"] = _dom or ROS_DOMAIN_ID
os.environ["ROS_LOCALHOST_ONLY"] = "0"
os.environ["RMW_IMPLEMENTATION"] = "rmw_fastrtps_cpp"
if os.path.exists(DDS_PROFILE):
    os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = DDS_PROFILE

# The interpreter to re-exec into. MUST be spelled out rather than left as bare
# `python3`. run_robot_supervisor_v2.sh launches the supervisor with
# `source .venv/bin/activate`, which puts .venv/bin (python3.12, no rclpy) FIRST on
# PATH; a bare `python3` here re-exec'd straight back into 3.12 and aborted with
# "rclpy still unavailable after sourcing ROS". That is why this worked when run by
# hand but the LiDAR tab stayed empty when spawned by the supervisor.
# (Found 2026-09-09 while debugging the same failure in a2_nav_stream.py.)
SYS_PYTHON = "/usr/bin/python3"          # 3.10.12, the one with rclpy

# !! THE RE-EXEC ONLY HAPPENS WHEN RUN AS A SCRIPT !!
# This block replaces the running process. Doing that on a plain `import` means
# any program that imports this module for its pure logic — a test, a notebook,
# an in-process caller — gets silently turned INTO a2_costmap and never returns.
# That is exactly what happened the first time the glass tests were run under
# the supervisor venv: the test process became a live LiDAR node and hung until
# it was killed. So the bootstrap is gated on __main__, and importing the module
# without rclpy is allowed to succeed with the ROS half missing.
_RUN_AS_SCRIPT = __name__ == "__main__"

try:
    import rclpy  # noqa: F401
    HAVE_ROS = True
except ImportError:
    HAVE_ROS = False
    if _RUN_AS_SCRIPT:
        if os.environ.get("_A2_COSTMAP_BOOTSTRAPPED") == "1":
            sys.exit(f"rclpy still unavailable after sourcing ROS via {SYS_PYTHON}. "
                     f"Check that /opt/ros/humble is installed for that interpreter.")
        os.environ["_A2_COSTMAP_BOOTSTRAPPED"] = "1"
        os.environ.pop("VIRTUAL_ENV", None)
        os.environ.pop("PYTHONHOME", None)
        os.environ.pop("PYTHONPATH", None)
        _py = SYS_PYTHON if os.path.exists(SYS_PYTHON) else "python3"
        _args = " ".join(shlex.quote(a) for a in sys.argv[1:])
        os.execvp("bash", ["bash", "-lc",
                           f"source {ROS_SETUP} && exec {shlex.quote(_py)} "
                           f"{shlex.quote(os.path.abspath(__file__))} {_args}"])

import numpy as np

if HAVE_ROS:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import (QoSProfile, ReliabilityPolicy, HistoryPolicy,
                           DurabilityPolicy)
    from sensor_msgs.msg import PointCloud2
    from sensor_msgs_py import point_cloud2
else:
    # Enough for the module to import and for LocalCostmap / GlassDetector /
    # fit_lines to be used. Anything that actually touches ROS fails loudly at
    # the point of use rather than at import.
    rclpy = None            # type: ignore[assignment]
    Node = object           # type: ignore[assignment,misc]
    PointCloud2 = None      # type: ignore[assignment]
    point_cloud2 = None     # type: ignore[assignment]

CLOUD_TOPIC = "/aima/hal/lidar/neck/pointcloud"
EXTRINSIC_FILE = "/agibot/data/param/calibration/extrinsic_baselink_T_lidar.txt"

# The planner's real footprint, lifted verbatim from vectorflux's live config
# (config/vectorflux_common/a2/app2.yaml: `footprint`). base_link, [x, y] metres.
# Total width 0.672 m — this is the shape the nav stack actually collision-checks,
# so drawing THIS (rather than a guessed box) keeps the view honest.
FOOTPRINT = [[0.18, 0.2], [0.05, 0.336], [-0.01, 0.336],
             [-0.01, -0.336], [0.075, -0.336], [0.18, -0.2]]


# ---------------------------------------------------------------- geometry
def load_extrinsic(path: str = EXTRINSIC_FILE, quat_order: str = "wxyz"):
    """Return (translation[3], rotation[3x3]) for base_link_T_lidar, or None.

    Default order is wxyz — settled empirically, see the module docstring.
    """
    try:
        vals = [float(v) for v in open(path).read().split()]
    except OSError:
        return None
    if len(vals) < 7:
        return None
    t = np.array(vals[0:3], dtype=float)
    q = vals[3:7]
    if quat_order == "xyzw":
        qx, qy, qz, qw = q
    else:
        qw, qx, qy, qz = q
    n = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw) or 1.0
    qx, qy, qz, qw = qx / n, qy / n, qz / n, qw / n
    R = np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ], dtype=float)
    return t, R


def decode_frame(obj: dict) -> np.ndarray:
    """Turn an NDJSON frame back into an (N,3) array of [x_m, y_m, confidence].

    Use this instead of unpacking `cells`/`hits` by hand.
    """
    raw = base64.b64decode(obj["cells"])
    idx = np.frombuffer(raw, dtype="<u2").reshape(-1, 2).astype(float)
    conf = np.frombuffer(base64.b64decode(obj["hits"]), dtype=np.uint8).astype(float)
    res, radius = obj["resolution"], obj["radius"]
    xy = (idx + 0.5) * res - radius
    return np.column_stack([xy[:, 0], xy[:, 1], conf])


# ---------------------------------------------------------------- the grid
class LocalCostmap:
    """Robot-centred occupancy accumulator.

    Holds one float per cell. Every frame the whole grid is scaled by `decay`
    and cells with returns are topped up, so confidence rises where the sensor
    keeps seeing something and fades where it stops.
    """

    def __init__(self, radius: float, resolution: float, decay: float,
                 hit_gain: float, threshold: float, conf_max: float = 8.0):
        self.radius = float(radius)
        self.resolution = float(resolution)
        self.n = max(1, int(round(2.0 * radius / resolution)))
        self.decay = float(decay)
        self.hit_gain = float(hit_gain)
        self.threshold = float(threshold)
        self.conf_max = float(conf_max)
        self.grid = np.zeros((self.n, self.n), dtype=np.float32)

    def update(self, pts: np.ndarray) -> np.ndarray:
        """Fold one filtered point cloud (N,3 in base_link) into the grid.

        Returns THIS FRAME's raw occupancy mask (n x n bool) — the cells that got
        at least one return right now, before any decay. The glass detector needs
        that rather than `self.grid`, because decay exists precisely to smooth out
        the frame-to-frame flicker that glass detection is looking for.
        """
        if self.decay <= 0.0:
            self.grid.fill(0.0)
        else:
            self.grid *= self.decay

        mask = np.zeros((self.n, self.n), dtype=bool)
        if pts.shape[0] == 0:
            return mask

        # metres -> cell index. floor() then bounds-check; points outside the
        # window are simply dropped.
        ix = np.floor((pts[:, 0] + self.radius) / self.resolution).astype(np.int32)
        iy = np.floor((pts[:, 1] + self.radius) / self.resolution).astype(np.int32)
        ok = (ix >= 0) & (ix < self.n) & (iy >= 0) & (iy < self.n)
        ix, iy = ix[ok], iy[ok]
        if ix.size == 0:
            return mask

        # bincount over the flattened index is the fast way to count hits per
        # cell — np.add.at is correct but ~50x slower at 20k points.
        flat = ix.astype(np.int64) * self.n + iy.astype(np.int64)
        counts = np.bincount(flat, minlength=self.n * self.n)
        counts2d = counts.reshape(self.n, self.n)
        self.grid += counts2d.astype(np.float32) * self.hit_gain
        np.clip(self.grid, 0.0, self.conf_max, out=self.grid)
        return counts2d > 0

    def occupied(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (indices (M,2) as uint16, confidence (M,) as uint8 1..255)."""
        ix, iy = np.nonzero(self.grid >= self.threshold)
        if ix.size == 0:
            return (np.empty((0, 2), dtype=np.uint16), np.empty(0, dtype=np.uint8))
        conf = self.grid[ix, iy] / self.conf_max
        conf8 = np.clip(conf * 255.0, 1, 255).astype(np.uint8)
        return np.column_stack([ix, iy]).astype(np.uint16), conf8


# ---------------------------------------------------------------- glass
#
# WHY GLASS NEEDS ITS OWN DETECTOR
# --------------------------------
# A 905 nm LiDAR mostly passes through glass. Whether a pane returns anything at
# all depends on the incidence angle, so the SAME pane reads as solid from one
# angle, as empty space from another, and flips between the two as the beam
# pattern rotates. That is the failure this looks for: not "absent", but
# **INCONSISTENT**.
#
# Measured on this robot (scan_test_door_facing_side.csv, 2026-08-26): bearings
# containing glass had a range standard deviation of 0.81 and 0.91 m — median
# 1.51 m but p90 3.49 m, i.e. the beam sometimes stops at the pane and sometimes
# carries on to whatever is behind it. Solid surfaces in the same scans sat at
# std <= 0.05 m. That bimodality is the second signal used below.
#
# THREE THINGS THAT MAKE THE NAIVE VERSION USELESS, AND WHAT IS DONE ABOUT THEM
# -----------------------------------------------------------------------------
# 1. The MID-360 is NON-REPETITIVE. Consecutive frames sample different
#    directions, so ordinary solid surfaces also blink in and out purely from
#    sampling — worse the further away they are. Counting raw appear/disappear
#    events therefore flags most of the room.
#    => A cell only qualifies if its hit fraction lands in a MIDDLE BAND
#       (`--glass-min-hit` .. `--glass-max-hit`). Sparse-but-real surfaces sit
#       below the band, solid walls sit above it, glass sits inside it. Cells
#       beyond `--glass-max-range` are ignored outright, because out there the
#       beam density alone puts everything in the band.
#
# 2. A WALKING PERSON FLICKERS IDENTICALLY -- almost. This is the dangerous one:
#    a single pedestrian crossing the view must not be painted as a glass wall.
#    Two independent guards, and the first is the one that does the work:
#    => TRANSITION COUNT, not occupancy. A person entering and leaving a cell is
#       exactly TWO transitions. A pane flickers dozens of times in the same
#       window. `--glass-min-flicker` is a rate of hit<->miss flips per frame, so
#       a body passing through scores near zero however solid it is.
#    => SUSTAINED EVIDENCE. Windows are folded into a persistent map with an EMA,
#       so a cell must keep flickering across several windows to be believed.
#       `--glass-evidence-decay` 0.7 needs ~3 consistent windows. That covers the
#       case the first guard cannot: someone pacing back and forth on one spot.
#
# 3. THE GRID IS IN base_link AND IS NOT ODOMETRY-COMPENSATED. If the robot
#    turns or walks, every cell's world position changes and the whole map
#    flickers at once.
#    => Windows are discarded while the scene is moving. NOTE the obvious test
#       ("what fraction of occupied cells changed?") DOES NOT WORK and was tried:
#       a flickering pane changes a large fraction of occupied cells by
#       definition, so that test gated out every true positive. What actually
#       separates them is the SOLID structure -- glass flicker leaves the walls
#       where they are, driving takes the walls with it. See `_moved()`.
#
# STATUS: ADVISORY. This layer is a VIEW, not a safety system. It does not feed
# vectorflux's costmap and it does not stop the robot. Treat a drawn line as
# "look here with your eyes", never as "the robot knows about this".

N_SECTORS = 72          # 5-degree bearing sectors for the range-variance signal
SECTOR_MIN_PTS = 6      # below this a sector's std is noise, not a measurement


def fit_lines(xy: np.ndarray, *, resolution: float, max_lines: int = 4,
              min_inliers: int = 10, tol: float = 0.12, min_length: float = 0.5,
              max_length: float = 6.0, min_density: float = 0.55,
              iters: int = 80, seed: int = 0) -> tuple[list[dict], np.ndarray]:
    """Greedy RANSAC: pull straight segments out of a set of 2-D points.

    Returns (lines, keep_mask) where `keep_mask` selects the points that ended up
    as inliers of an ACCEPTED segment. The mask is the point of this function:
    see the density note below.

    !! THE DENSITY REQUIREMENT IS WHAT MAKES THIS WORK !!
    Measured on the real sensor (2026-09-22, lab, 5 windows, 5757 cells): about
    20-25% of touched cells pass the per-cell flicker gate AT EVERY RANGE BAND.
    That is the non-repetitive scan pattern, not glass. Feeding those ~1800
    scattered points to a plain RANSAC produced confident nonsense every time —
    11 m segments straight across the view with 30 inliers and rms ~0.1.

    A real pane is DENSE along its own length: a 1.5 m run at 0.1 m cells should
    have most of its ~15 cells present. A noise fit spreads 30 inliers over 110
    possible cells. So a segment is accepted only when

        inliers >= min_density * (length / resolution)

    which rejects the diagonal fits outright while keeping genuine short runs.
    `max_length` is a second guard against a fit that walks across the room.

    Points consumed by one attempt are removed whether or not the segment was
    kept, so the loop always terminates.
    """
    n = xy.shape[0]
    keep = np.zeros(n, dtype=bool)
    if n < min_inliers:
        return [], keep

    rng = np.random.default_rng(seed)
    idx = np.arange(n)
    remaining = xy.astype(float)
    lines: list[dict] = []

    while len(lines) < max_lines and remaining.shape[0] >= min_inliers:
        best_mask = None
        best_count = 0
        for _ in range(iters):
            i, j = rng.choice(remaining.shape[0], size=2, replace=False)
            p0, p1 = remaining[i], remaining[j]
            d = p1 - p0
            norm = math.hypot(d[0], d[1])
            if norm < 1e-6:
                continue
            # unit normal; perpendicular distance of every point to the line
            nx, ny = -d[1] / norm, d[0] / norm
            dist = np.abs((remaining - p0) @ np.array([nx, ny]))
            mask = dist < tol
            count = int(mask.sum())
            if count > best_count:
                best_count, best_mask = count, mask

        if best_mask is None or best_count < min_inliers:
            break

        inliers = remaining[best_mask]
        inlier_idx = idx[best_mask]
        # Refine: principal axis of the inliers beats the random 2-point seed.
        centre = inliers.mean(axis=0)
        centred = inliers - centre
        # SVD is overkill for 2xN but it is exact and this runs a few times a second.
        _, _, vt = np.linalg.svd(centred, full_matrices=False)
        direction = vt[0]
        t = centred @ direction
        residual = centred - np.outer(t, direction)
        rms = float(np.sqrt((residual ** 2).sum(axis=1).mean()))
        p_start = centre + direction * t.min()
        p_end = centre + direction * t.max()
        length = float(np.hypot(*(p_end - p_start)))

        # Drop these points either way, so a rejected blob cannot be re-picked.
        remaining = remaining[~best_mask]
        idx = idx[~best_mask]

        slots = max(1.0, length / resolution)
        density = best_count / slots
        if min_length <= length <= max_length and density >= min_density:
            keep[inlier_idx] = True
            lines.append({
                "x1": round(float(p_start[0]), 3), "y1": round(float(p_start[1]), 3),
                "x2": round(float(p_end[0]), 3), "y2": round(float(p_end[1]), 3),
                "inliers": int(best_count), "rms": round(rms, 3),
                "length": round(length, 3), "density": round(density, 2),
            })

    lines.sort(key=lambda ln: ln["length"], reverse=True)
    return lines, keep


class GlassDetector:
    """Accumulates 'this cell keeps appearing and disappearing' evidence.

    Fed the RAW per-frame occupancy mask (not the decayed grid). See the block
    comment above for why each guard exists.
    """

    def __init__(self, n: int, resolution: float, radius: float, *,
                 window: int, min_hit: float, max_hit: float,
                 min_flicker: float, evidence_decay: float, threshold: float,
                 max_range: float, motion_gate: float, variance_boost: bool):
        self.n = n
        self.resolution = resolution
        self.radius = radius
        self.window = max(4, int(window))
        self.min_hit = float(min_hit)
        self.max_hit = float(max_hit)
        self.min_flicker = float(min_flicker)
        self.evidence_decay = float(evidence_decay)
        self.threshold = float(threshold)
        self.max_range = float(max_range)
        self.motion_gate = float(motion_gate)
        self.variance_boost = bool(variance_boost)

        # Per-window counters
        self.hits = np.zeros((n, n), dtype=np.uint16)
        self.flips = np.zeros((n, n), dtype=np.uint16)
        self.prev_mask = np.zeros((n, n), dtype=bool)
        self.frames = 0
        # Previous window's hit fraction — the reference the motion gate uses.
        self.prev_hit_frac: Optional[np.ndarray] = None
        self.last_motion = 0.0
        self.last_drift = 0.0
        self.judgeable = False

        # Persistent belief, 0..1
        self.evidence = np.zeros((n, n), dtype=np.float32)
        self.windows = 0
        self.windows_discarded = 0

        # Per-cell geometry, precomputed once: centre range and bearing sector.
        ii = (np.arange(n, dtype=np.float32) + 0.5) * resolution - radius
        gx = ii.reshape(-1, 1) * np.ones((1, n), dtype=np.float32)   # +x forward
        gy = np.ones((n, 1), dtype=np.float32) * ii.reshape(1, -1)   # +y left
        self.cell_x, self.cell_y = gx, gy
        self.cell_range = np.hypot(gx, gy)
        bearing = np.arctan2(gy, gx)                                 # -pi..pi
        self.cell_sector = np.clip(
            ((bearing + math.pi) / (2 * math.pi) * N_SECTORS).astype(np.int32),
            0, N_SECTORS - 1)
        self.in_range = self.cell_range <= self.max_range

        # Per-bearing range spread, EMA across frames, plus whether that bearing
        # has ever had enough returns to have been MEASURED. The two are separate
        # on purpose: an unmeasured bearing must score neutral, not "clean".
        self.sector_std = np.zeros(N_SECTORS, dtype=np.float32)
        self.sector_valid = np.zeros(N_SECTORS, dtype=bool)

    # ------------------------------------------------------------ per frame
    def observe(self, mask: np.ndarray, pts: np.ndarray) -> None:
        """Fold one frame's raw occupancy mask (and its points) into the window."""
        self.hits += mask
        self.flips += mask ^ self.prev_mask
        self.prev_mask = mask
        self.frames += 1

        if self.variance_boost and pts.shape[0]:
            self._observe_sector_spread(pts)

        if self.frames >= self.window:
            self._close_window()

    def _observe_sector_spread(self, pts: np.ndarray) -> None:
        """EMA of per-bearing range standard deviation — the bimodality signal."""
        rng_m = np.hypot(pts[:, 0], pts[:, 1])
        bearing = np.arctan2(pts[:, 1], pts[:, 0])
        sect = np.clip(((bearing + math.pi) / (2 * math.pi) * N_SECTORS).astype(np.int32),
                       0, N_SECTORS - 1)
        counts = np.bincount(sect, minlength=N_SECTORS).astype(np.float32)
        s1 = np.bincount(sect, weights=rng_m, minlength=N_SECTORS)
        s2 = np.bincount(sect, weights=rng_m * rng_m, minlength=N_SECTORS)
        ok = counts >= SECTOR_MIN_PTS
        std = np.zeros(N_SECTORS, dtype=np.float32)
        with np.errstate(invalid="ignore"):
            mean = np.where(ok, s1 / np.maximum(counts, 1), 0.0)
            var = np.where(ok, s2 / np.maximum(counts, 1) - mean * mean, 0.0)
            std[ok] = np.sqrt(np.maximum(var[ok], 0.0))
        # Only update sectors that had enough points; leave the rest as they were.
        self.sector_std[ok] = 0.7 * self.sector_std[ok] + 0.3 * std[ok]
        self.sector_valid |= ok

    # ------------------------------------------------------------ per window
    STABLE_HIT = 0.6        # hit fraction above which a cell counts as solid structure
    LOST_HIT = 0.3          # ...and below which it counts as having gone away
    MIN_STABLE_CELLS = 20   # too little structure to judge motion from
    DRIFT_CELLS = 1.5       # centroid shift (in cells) that counts as translation

    def _centroid(self, stable: np.ndarray) -> Optional[tuple[float, float]]:
        """Robust centre of the solid structure.

        MEDIAN, not mean. A flickering cell clears STABLE_HIT by chance about 13%
        of the time (40 coin flips landing >=24 heads), so a handful of glass
        cells always leak into the "stable" set. With a mean centroid those few
        outliers dragged the centre around between windows and the drift test
        fired on a perfectly stationary robot — inverting the whole gate. The
        median ignores them while still tracking a genuine bulk translation.
        """
        n = int(stable.sum())
        if n < self.MIN_STABLE_CELLS:
            return None
        return (float(np.median(self.cell_x[stable])),
                float(np.median(self.cell_y[stable])))

    def _moved(self, hit_frac: np.ndarray) -> bool:
        """Did the SCENE shift between the last window and this one?

        TWO tests, because each one alone has a blind spot.

        (1) LOST STRUCTURE. The obvious test — "what fraction of occupied cells
        changed this frame" — is useless here, because a flickering pane changes
        a large fraction of occupied cells by definition. It cannot tell glass
        from motion, and it gated out every real detection when tried. What does
        separate them is what happens to the SOLID structure: glass flicker
        leaves the walls exactly where they were, driving takes the walls with
        it. So take the cells that were reliably occupied last window (hit
        fraction >= STABLE_HIT — glass never qualifies, it sits near 0.5) and ask
        how many have gone quiet.

        (2) COHERENT DRIFT. Test (1) only catches motion fast enough to empty a
        cell within one window. Measured 2026-09-23: at 0.05 m/s the walls creep
        about two cells per window, test (1) never fires, and the detector
        quietly returns ZERO glass — reporting "I looked and found nothing" when
        the truth is "I cannot see". That silence is the dangerous failure, so
        the centroid of the stable structure is also tracked; if the whole scene
        slides coherently, that is translation however slow it is.
        """
        if self.prev_hit_frac is None:
            self.judgeable = False
            return False
        prev_stable = self.prev_hit_frac >= self.STABLE_HIT
        n_stable = int(prev_stable.sum())
        if n_stable < self.MIN_STABLE_CELLS:
            # Nothing solid enough to track, so neither test can run. Do not
            # claim this is stillness: above ~0.05 m/s the structure smears so
            # badly that NO cell stays stable, which lands here. Abstaining is
            # recorded (`judgeable`) so `observing` can report the difference
            # between "looked, saw no glass" and "could not look".
            self.last_motion = 0.0
            self.judgeable = False
            return False
        self.judgeable = True

        lost = int((prev_stable & (hit_frac < self.LOST_HIT)).sum())
        self.last_motion = lost / n_stable
        if self.last_motion > self.motion_gate:
            return True

        prev_c = self._centroid(prev_stable)
        now_c = self._centroid(hit_frac >= self.STABLE_HIT)
        if prev_c is not None and now_c is not None:
            drift = math.hypot(now_c[0] - prev_c[0], now_c[1] - prev_c[1])
            self.last_drift = drift
            if drift > self.DRIFT_CELLS * self.resolution:
                # Report it on the same 0..1 scale as `motion` so one number in
                # the UI means "detection is suppressed", whichever test fired.
                self.last_motion = max(self.last_motion, 1.0)
                return True
        return False

    def _close_window(self) -> None:
        frames = float(self.frames)
        hit_frac = self.hits.astype(np.float32) / frames
        moved = self._moved(hit_frac)
        # Always re-baseline, including after motion: otherwise every later window
        # is compared against where the walls used to be and nothing ever recovers.
        self.prev_hit_frac = hit_frac.copy()

        if moved:
            # The robot moved: this window says nothing about materials. Let the
            # existing belief fade rather than poisoning it.
            self.windows_discarded += 1
            self.evidence *= self.evidence_decay
        else:
            flicker = self.flips.astype(np.float32) / frames

            candidate = (
                (hit_frac >= self.min_hit) & (hit_frac <= self.max_hit)
                & (flicker >= self.min_flicker) & self.in_range
            )
            # Score rises with flicker rate; 0.5 flips/frame is as flickery as a
            # cell can usefully get (alternating every single frame).
            score = np.clip(flicker / 0.5, 0.0, 1.0).astype(np.float32)

            if self.variance_boost:
                # A cell whose bearing also shows a wide spread of return ranges
                # is far more likely to be glass than one that merely blinks —
                # measured at std 0.81-0.91 m for glass vs <=0.05 m for solid
                # surfaces. Cells in a "clean" bearing are damped rather than
                # excluded, so the temporal signal can still carry a detection on
                # its own. A bearing that has never had enough returns to measure
                # scores NEUTRAL (1.0): unknown must not read as "clean", or the
                # corroboration silently halves every score it cannot judge.
                spread = self.sector_std[self.cell_sector]
                mult = np.clip(0.5 + spread / 0.7, 0.5, 1.5).astype(np.float32)
                mult[~self.sector_valid[self.cell_sector]] = 1.0
                score = np.clip(score * mult, 0.0, 1.0)

            score *= candidate
            self.evidence *= self.evidence_decay
            self.evidence += (1.0 - self.evidence_decay) * score
            self.windows += 1

        np.clip(self.evidence, 0.0, 1.0, out=self.evidence)
        self.hits.fill(0)
        self.flips.fill(0)
        self.frames = 0

    # ------------------------------------------------------------ output
    def suspected(self) -> tuple[np.ndarray, np.ndarray]:
        """Cells believed to be glass: (indices (M,2) uint16, confidence uint8)."""
        ix, iy = np.nonzero(self.evidence >= self.threshold)
        if ix.size == 0:
            return (np.empty((0, 2), dtype=np.uint16), np.empty(0, dtype=np.uint8))
        conf = np.clip(self.evidence[ix, iy] * 255.0, 1, 255).astype(np.uint8)
        return np.column_stack([ix, iy]).astype(np.uint16), conf

    def lines(self, cells: np.ndarray, conf: np.ndarray, **kw):
        """Fit planar segments, and keep only the cells that belong to one.

        Returns (lines, cells, conf) with the cell arrays already filtered. The
        filtering is deliberate: per-cell flicker alone is not specific enough on
        this sensor (see the density note in `fit_lines`), so "glass" means
        "flickering AND part of a plane", never flickering on its own.
        """
        if cells.shape[0] == 0:
            return [], cells, conf
        xy = (cells.astype(float) + 0.5) * self.resolution - self.radius
        lines, keep = fit_lines(xy, resolution=self.resolution, **kw)
        return lines, cells[keep], conf[keep]

    def stats(self) -> dict:
        return {
            "windows": self.windows,
            "windows_discarded": self.windows_discarded,
            "window_frames": self.window,
            "frames_in_window": self.frames,
            "motion": round(float(self.last_motion), 3),
            "drift_m": round(float(self.last_drift), 3),
            # True means "the last window was usable". A caller that needs a
            # trustworthy reading must check this, not just `n_glass == 0`:
            # zero glass while blind is not the same as zero glass after looking.
            "observing": (self.windows >= 3 and self.judgeable
                          and self.last_motion <= self.motion_gate),
            "settling": self.windows < 3,
        }


# ---------------------------------------------------------------- ascii view
def ascii_view(cm: LocalCostmap, meta: dict, cols: int = 61,
               glass: Optional[np.ndarray] = None,
               glass_stats: Optional[dict] = None) -> str:
    """Coarse top-down text render for standalone/SSH use.

    Screen convention matches the web view: up = +x (forward), left = +y (left).
    Suspected glass cells are drawn as 'G' and overwrite the obstacle glyph, so
    a pane reads as a distinct shape rather than hiding inside the speckle.
    """
    rows = cols // 2
    canvas = [[" "] * cols for _ in range(rows)]
    cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0

    def plot(cells: np.ndarray, glyphs) -> None:
        if not cells.size:
            return
        x = (cells[:, 0].astype(float) + 0.5) * cm.resolution - cm.radius
        y = (cells[:, 1].astype(float) + 0.5) * cm.resolution - cm.radius
        # +x forward -> up (row 0 at top); +y left -> left (col 0 at left)
        px = np.round(cx - y * (cx / cm.radius)).astype(int)
        py = np.round(cy - x * (cy / cm.radius)).astype(int)
        for a, b, g in zip(py, px, glyphs):
            if 0 <= a < rows and 0 <= b < cols:
                canvas[a][b] = g

    cells, conf = cm.occupied()
    plot(cells, ["#" if c > 160 else ("+" if c > 80 else ".") for c in conf])
    if glass is not None and glass.size:
        plot(glass, ["G"] * glass.shape[0])

    canvas[int((rows - 1) / 2)][int((cols - 1) / 2)] = "R"
    body = "\n".join("".join(r) for r in canvas)
    bar = "-" * cols

    gline = ""
    if glass_stats is not None:
        n_glass = 0 if glass is None else int(glass.shape[0])
        state = "settling" if glass_stats.get("settling") else "ready"
        gline = (f"glass: {n_glass} cells ({state})   "
                 f"windows {glass_stats['windows']} kept / "
                 f"{glass_stats['windows_discarded']} discarded (motion)\n")

    return (f"a2 local costmap   radius {cm.radius:g} m   cell {cm.resolution:g} m   "
            f"grid {cm.n}x{cm.n}\n"
            f"{meta['hz']:.1f} Hz   pts {meta['points_used']}/{meta['points_raw']}   "
            f"occupied cells {len(conf)}   decay {cm.decay:g}\n" + gline +
            f"+{bar}+\n" + "\n".join("|" + r + "|" for r in body.split("\n")) +
            f"\n+{bar}+\n  up = forward (+x)   left = left (+y)   R = robot"
            f"   G = suspected glass (advisory)\n")


# ---------------------------------------------------------------- ROS node
class CostmapNode(Node):
    def __init__(self, args):
        super().__init__("a2_costmap")
        self.args = args
        self.seq = 0
        self.hz = 0.0
        self.last_t = None
        self.last_emit = 0.0

        self.cm = LocalCostmap(args.radius, args.resolution, args.decay,
                               args.hit_gain, args.threshold)

        self.glass = None
        if args.glass:
            self.glass = GlassDetector(
                self.cm.n, self.cm.resolution, self.cm.radius,
                window=args.glass_window,
                min_hit=args.glass_min_hit, max_hit=args.glass_max_hit,
                min_flicker=args.glass_min_flicker,
                evidence_decay=args.glass_evidence_decay,
                threshold=args.glass_threshold,
                max_range=args.glass_max_range,
                motion_gate=args.glass_motion_gate,
                variance_boost=not args.glass_no_variance,
            )

        self.ext = None if args.no_transform else load_extrinsic(quat_order=args.quat_order)
        if self.ext is None and not args.no_transform:
            print("WARNING: could not read the extrinsic; staying in raw lidar frame",
                  file=sys.stderr)

        # Sensor data is BEST_EFFORT/VOLATILE. A RELIABLE subscription would
        # never match the publisher and you'd wait forever with no error.
        qos = QoSProfile(depth=5,
                         reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST,
                         durability=DurabilityPolicy.VOLATILE)
        self.create_subscription(PointCloud2, args.topic, self.on_cloud, qos)

        if args.ndjson:
            self._emit({"type": "hello", "topic": args.topic,
                        "radius": self.cm.radius, "resolution": self.cm.resolution,
                        "n": self.cm.n, "decay": self.cm.decay,
                        "z_min": args.z_min, "z_max": args.z_max,
                        "min_range": args.min_range, "footprint": FOOTPRINT,
                        "glass": bool(self.glass),
                        "glass_threshold": args.glass_threshold if self.glass else None,
                        "domain": os.environ.get("ROS_DOMAIN_ID")})
        else:
            print(f"[a2] subscribed to {args.topic} (domain "
                  f"{os.environ.get('ROS_DOMAIN_ID')}) — waiting for data ...",
                  file=sys.stderr)

    def _emit(self, obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def on_cloud(self, msg: PointCloud2):
        now = time.monotonic()
        if self.last_t is not None:
            dt = now - self.last_t
            if dt > 0:
                inst = 1.0 / dt
                self.hz = inst if self.hz == 0 else 0.8 * self.hz + 0.2 * inst
        self.last_t = now
        self.seq += 1
        a = self.args

        # read_points_numpy() RAISES on this cloud ("All fields need to have the
        # same datatype") — Livox mixes float32 / uint8 / float64. The structured
        # read_points() is the one that works.
        rec = point_cloud2.read_points(msg, field_names=("x", "y", "z"), skip_nans=True)
        pts = np.stack([rec["x"], rec["y"], rec["z"]], axis=-1).astype(float)
        n_raw = pts.shape[0]

        if self.ext is not None and n_raw:
            t, R = self.ext
            pts = pts @ R.T + t

        if n_raw:
            rng = np.hypot(pts[:, 0], pts[:, 1])
            keep = ((rng >= a.min_range) & (rng <= a.radius * 1.5)
                    & (pts[:, 2] >= a.z_min) & (pts[:, 2] <= a.z_max))
            pts = pts[keep]

        mask = self.cm.update(pts)
        if self.glass is not None:
            # Fed the RAW mask, deliberately: --decay exists to smooth away the
            # very flicker this is looking for, so the decayed grid would hide it.
            self.glass.observe(mask, pts)

        # rate-limit output independently of the sensor rate. NOTE the glass
        # detector is updated ABOVE this gate — it must see every frame, or the
        # flicker statistics are computed on a decimated signal and mean nothing.
        if a.max_hz > 0 and (now - self.last_emit) < (1.0 / a.max_hz):
            return
        self.last_emit = now

        meta = {"points_raw": int(n_raw), "points_used": int(pts.shape[0]),
                "hz": round(self.hz, 2)}

        g_cells = g_conf = None
        g_lines: list[dict] = []
        if self.glass is not None:
            g_cells, g_conf = self.glass.suspected()
            g_lines, g_cells, g_conf = self.glass.lines(
                g_cells, g_conf,
                max_lines=a.glass_max_lines,
                min_inliers=a.glass_line_min_cells,
                tol=max(1.5 * self.cm.resolution, 0.06),
                min_length=a.glass_line_min_length,
                max_length=a.glass_line_max_length,
                min_density=a.glass_line_min_density,
            )

        if a.ndjson:
            cells, conf = self.cm.occupied()
            frame = {
                "type": "frame", "seq": self.seq,
                "stamp": msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
                "hz": meta["hz"], "radius": self.cm.radius,
                "resolution": self.cm.resolution, "n": self.cm.n,
                "z_min": a.z_min, "z_max": a.z_max, "min_range": a.min_range,
                "decay": self.cm.decay,
                "points_raw": meta["points_raw"], "points_used": meta["points_used"],
                "n_cells": int(cells.shape[0]),
                "footprint": FOOTPRINT,
                "cells": base64.b64encode(cells.tobytes()).decode("ascii"),
                "hits": base64.b64encode(conf.tobytes()).decode("ascii"),
            }
            if self.glass is not None:
                frame.update({
                    "n_glass": int(g_cells.shape[0]),
                    "glass_cells": base64.b64encode(g_cells.tobytes()).decode("ascii"),
                    "glass_conf": base64.b64encode(g_conf.tobytes()).decode("ascii"),
                    "glass_lines": g_lines,
                    "glass_stats": self.glass.stats(),
                })
            self._emit(frame)
        else:
            sys.stdout.write("\033[H\033[J" + ascii_view(
                self.cm, meta, glass=g_cells,
                glass_stats=self.glass.stats() if self.glass else None) + "\n")
            sys.stdout.flush()

        if a.once:
            raise KeyboardInterrupt


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="a2_costmap.py",
        description="Live local costmap from the A2's MID-360 LiDAR (read-only).")
    p.add_argument("--radius", type=float, default=10.0,
                   help="half-width of the window in metres (default 10 = 20x20 m)")
    p.add_argument("--resolution", type=float, default=0.10,
                   help="cell size in metres (default 0.10)")
    p.add_argument("--decay", type=float, default=0.80,
                   help="per-frame confidence decay 0..1; 0 = single frame, "
                        "0.80 ~ half-second memory (default 0.80)")
    p.add_argument("--hit-gain", type=float, default=1.0,
                   help="confidence added per return in a cell (default 1.0)")
    p.add_argument("--threshold", type=float, default=1.0,
                   help="confidence at which a cell counts as occupied (default 1.0)")
    p.add_argument("--min-range", type=float, default=0.40,
                   help="drop returns closer than this — the LiDAR sees the "
                        "robot's own torso and arms (default 0.40)")
    p.add_argument("--z-min", type=float, default=-0.30,
                   help="lowest obstacle height in base_link (default -0.30; the "
                        "floor is near -0.9, so this excludes it. Lower it to catch "
                        "curbs, at the risk of floor returns on uneven ground)")
    p.add_argument("--z-max", type=float, default=1.70)
    p.add_argument("--max-hz", type=float, default=10.0,
                   help="cap the OUTPUT rate; 0 = every frame (default 10)")
    p.add_argument("--topic", default=CLOUD_TOPIC)
    p.add_argument("--ndjson", action="store_true",
                   help="emit one JSON frame per line on stdout")
    p.add_argument("--ascii", action="store_true", help="terminal view (default)")
    p.add_argument("--once", action="store_true", help="one frame then exit")
    p.add_argument("--no-transform", action="store_true",
                   help="stay in the raw livox frame (debugging only)")
    p.add_argument("--quat-order", choices=["xyzw", "wxyz"], default="wxyz",
                   help="extrinsic quaternion order (default wxyz — settled)")
    p.add_argument("--domain", default=None, help="ROS_DOMAIN_ID override (default 232)")

    g = p.add_argument_group(
        "glass detection (ADVISORY — a view, not a safety system)",
        "Flags cells that keep appearing and disappearing, which is how glass "
        "reads to a 905 nm LiDAR, and fits straight segments through them. "
        "Suppressed while the robot is moving; see the block comment in this file.")
    g.add_argument("--glass", action=argparse.BooleanOptionalAction, default=True,
                   help="enable glass detection (default: on; --no-glass to disable)")
    g.add_argument("--glass-window", type=int, default=40,
                   help="frames per evidence window, ~4 s at 10 Hz (default 40)")
    g.add_argument("--glass-min-hit", type=float, default=0.15,
                   help="a cell must be occupied at least this fraction of the "
                        "window — below it is sparse sampling, not glass (default 0.15)")
    g.add_argument("--glass-max-hit", type=float, default=0.85,
                   help="...and at most this fraction — above it is a solid "
                        "surface (default 0.85)")
    g.add_argument("--glass-min-flicker", type=float, default=0.15,
                   help="minimum hit<->miss transitions per frame. Measured on "
                        "real data, 0.15 passes ~21%% of all touched cells and "
                        "0.35 passes ~11%% (default 0.35)")
    g.add_argument("--glass-evidence-decay", type=float, default=0.7,
                   help="EMA factor across windows; higher = slower to believe "
                        "and slower to forget. 0.7 needs ~3 consistent windows, "
                        "which is what rejects a passing pedestrian (default 0.7)")
    g.add_argument("--glass-threshold", type=float, default=0.45,
                   help="evidence at which a cell is reported (default 0.45)")
    g.add_argument("--glass-max-range", type=float, default=8.0,
                   help="ignore cells beyond this — out there the beam is sparse "
                        "enough that everything looks intermittent (default 8.0)")
    g.add_argument("--glass-motion-gate", type=float, default=0.35,
                   help="if more than this fraction of the previously-SOLID "
                        "cells go quiet, the scene has moved and the window is "
                        "discarded (default 0.35)")
    g.add_argument("--glass-no-variance", action="store_true",
                   help="disable the per-bearing range-spread corroboration and "
                        "use the temporal flicker signal alone")
    g.add_argument("--glass-max-lines", type=int, default=4,
                   help="most segments to fit per frame (default 4)")
    g.add_argument("--glass-line-min-cells", type=int, default=10,
                   help="minimum inlier cells for a segment (default 10)")
    g.add_argument("--glass-line-min-length", type=float, default=0.5,
                   help="minimum segment length in metres (default 0.5)")
    g.add_argument("--glass-line-max-length", type=float, default=6.0,
                   help="reject segments longer than this — a fit that walks "
                        "across the whole room is noise (default 6.0)")
    g.add_argument("--glass-line-min-density", type=float, default=0.55,
                   help="inliers as a fraction of the cells the segment passes "
                        "through. THIS is what separates a real pane from a "
                        "diagonal noise fit; see fit_lines (default 0.55)")

    args = p.parse_args(argv)

    if not HAVE_ROS:
        sys.exit("rclpy is unavailable. Run this file directly (it re-execs into "
                 "the ROS interpreter itself) rather than importing it and "
                 "calling main().")
    rclpy.init()
    node = CostmapNode(args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
