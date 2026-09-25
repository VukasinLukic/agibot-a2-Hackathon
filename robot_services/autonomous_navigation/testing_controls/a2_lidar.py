#!/usr/bin/env python3
"""
a2_lidar.py — live LiDAR readings from the AgiBot A2 Ultra (Livox MID-360).

WHERE THE DATA COMES FROM
-------------------------
The MID-360 sits on the robot's chest/neck and streams raw UDP to the ORIN:
    point cloud -> UDP 56301      IMU -> UDP 56401      log -> UDP 56201
(config: /agibot/software/v0/config/mid360lidar/a2_mid360lidar_config.yaml)

Those ports are bound EXCLUSIVELY by the `hal_lidar` process, so you cannot read
them yourself. hal_lidar republishes onto the AimRT/ROS2 layer, and that is what we
subscribe to here:

    /aima/hal/lidar/neck/pointcloud   sensor_msgs/msg/PointCloud2   ~10 Hz
    /aima/hal/lidar/neck/imu          sensor_msgs/msg/Imu

THE TWO THINGS THAT MAKE ROS2 "SEE NOTHING" ON THIS ROBOT
---------------------------------------------------------
`ros2 topic list` returns zero topics unless BOTH are set:
    ROS_DOMAIN_ID=232
    FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml
The profile pins SHM + a UDPv4 interface whitelist of 192.168.100.110. With them set
there are ~326 topics. This script sets both for you.

FRAMES — READ THIS BEFORE TRUSTING A BEARING
--------------------------------------------
The cloud arrives in frame `livox_frame`, which is NOT in the robot's TF tree, so
tf2 cannot resolve it. The extrinsic lives in a calibration file instead:
    /agibot/data/param/calibration/extrinsic_baselink_T_lidar.txt
    -> "x y z qx qy qz qw" = -0.037792 0.0001 0.48759  -0.00753 0.00360 0.99994 0.00716

THE 7 NUMBERS, DECODED (settled empirically 2026-08-26 — do not re-guess this)
-----------------------------------------------------------------------------
    -0.037792  0.0001  0.48759   -0.00753139  0.00359739  0.99994  0.00716429
    <--- translation --->        <----------- quaternion ----------->
       x        y        z            qw          qx        qy        qz

  * ORDER IS **wxyz**, not the xyzw used by frame_poses.txt elsewhere in this stack.
  * translation: the LiDAR sits 0.4876 m ABOVE base_link, 0.0378 m BEHIND it, centred.
    (base_link is the pelvis, roughly 0.9 m off the floor — so the LiDAR is at neck
    height, which matches where it is physically bolted.)
  * rotation: qy dominates => ~180 deg about the **Y** axis, i.e. (x,y,z)->(-x,+y,-z).
    Physically: the LiDAR hangs from the neck UPSIDE DOWN and facing backwards.
    A 180 deg roll composed with a 180 deg yaw IS a 180 deg pitch, which is why the
    single number looks like a plain flip.

  HOW WE KNOW (two independent checks, both reproducible):
   1. A labelled walk-around (front -> left -> back -> right, logged to
      scan_test_front_left_back_right.csv) puts the operator's LEFT at NEGATIVE
      bearing under xyzw. Left must be positive with y-left, so xyzw mirrors the
      world. wxyz negates y and fixes it.
   2. Floor returns near z = -1.0 m exist ONLY under wxyz. Under xyzw the lowest
      return is -0.58 m, an impossibly shallow floor for a pelvis ~0.9 m up.

  Consequence for any CSV recorded BEFORE this was fixed: bearings are negated.
  To correct an old log, flip the sign of bearing_center_deg. Heights are also
  mirrored about the LiDAR plane (z_old = 0.9752 - z_new).

  Re-verify any time with: stand ~1.5 m off the robot's LEFT shoulder; the intruding
  sector should read a POSITIVE bearing near +90. --no-transform shows the raw frame.

USAGE
    python3 a2_lidar.py                    # live radar view, base_link frame
    python3 a2_lidar.py --once             # one frame, then exit
    python3 a2_lidar.py --raw 10           # print 10 raw points
    python3 a2_lidar.py --sectors 24       # finer angular resolution
    python3 a2_lidar.py --z-min 0.1 --z-max 1.8   # obstacle height band
    python3 a2_lidar.py --csv scan.csv     # log nearest-per-sector over time
    python3 a2_lidar.py --list-topics      # what else is published
    python3 a2_lidar.py --topic /exploration/terrain_collision_cloud
    python3 a2_lidar.py --imu              # IMU instead of the cloud

This is READ-ONLY. It never commands the robot.
"""
from __future__ import annotations

import argparse
import math
import os
import shlex
import sys
import time

# ---------------------------------------------------------------- ROS bootstrap

ROS_SETUP = "/opt/ros/humble/setup.bash"
DDS_PROFILE = "/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml"
ROS_DOMAIN_ID = "232"

# FORCE, do not setdefault. /home/agi/.bashrc line 128 exports ROS_DOMAIN_ID=231, but
# the robot's stack runs on 232 -- so every interactive shell starts on the WRONG domain
# and sees zero topics. Honour an explicit --domain/A2_ROS_DOMAIN_ID, otherwise pin 232.
_dom = os.environ.get("A2_ROS_DOMAIN_ID")
for i, a in enumerate(sys.argv):
    if a == "--domain" and i + 1 < len(sys.argv):
        _dom = sys.argv[i + 1]
    elif a.startswith("--domain="):
        _dom = a.split("=", 1)[1]
os.environ["ROS_DOMAIN_ID"] = _dom or ROS_DOMAIN_ID
os.environ["ROS_LOCALHOST_ONLY"] = "0"
os.environ["RMW_IMPLEMENTATION"] = "rmw_fastrtps_cpp"
if os.path.exists(DDS_PROFILE):
    os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = DDS_PROFILE

try:
    import rclpy  # noqa: F401
except ImportError:
    # Re-exec once under a shell that has sourced the ROS 2 environment.
    if os.environ.get("_A2_LIDAR_BOOTSTRAPPED") == "1":
        sys.exit("rclpy still unavailable after sourcing ROS. "
                 f"Check that {ROS_SETUP} exists and you are on system python3 "
                 "(3.10) — the project's .venv (3.12) has no rclpy.")
    os.environ["_A2_LIDAR_BOOTSTRAPPED"] = "1"
    args = " ".join(shlex.quote(a) for a in sys.argv[1:])
    os.execvp("bash", ["bash", "-lc",
                       f"source {ROS_SETUP} && exec python3 "
                       f"{shlex.quote(os.path.abspath(__file__))} {args}"])

import numpy as np
import rclpy
import rclpy.executors
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from sensor_msgs.msg import Imu, PointCloud2
from sensor_msgs_py import point_cloud2

CLOUD_TOPIC = "/aima/hal/lidar/neck/pointcloud"
IMU_TOPIC = "/aima/hal/lidar/neck/imu"
EXTRINSIC_FILE = "/agibot/data/param/calibration/extrinsic_baselink_T_lidar.txt"


# ---------------------------------------------------------------- geometry

def load_extrinsic(path: str = EXTRINSIC_FILE, quat_order: str = "xyzw"):
    """Return (translation[3], rotation[3x3]) for base_link_T_lidar, or None."""
    try:
        vals = [float(v) for v in open(path).read().split()]
    except Exception:
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


def yaw_deg(R) -> float:
    return math.degrees(math.atan2(R[1, 0], R[0, 0]))


# ---------------------------------------------------------------- rendering

def render(pts_bl: np.ndarray, args, meta: dict) -> str:
    """pts_bl: (N,3) in base_link. Returns the text block to display."""
    out = []
    n_raw = meta["n_raw"]

    if pts_bl.shape[0] == 0:
        return (f"frame={meta['frame']}  points={n_raw} -> 0 after filtering\n"
                "  nothing within the range/height window; widen --max-range or "
                "--z-min/--z-max")

    x, y, z = pts_bl[:, 0], pts_bl[:, 1], pts_bl[:, 2]
    rng = np.hypot(x, y)                       # horizontal distance
    bearing = np.degrees(np.arctan2(y, x))     # 0 = forward, + = left

    ns = args.sectors
    width = 360.0 / ns
    # sector 0 centred on straight ahead
    idx = np.floor((bearing + 180.0 + width / 2.0) % 360.0 / width).astype(int) % ns

    nearest = np.full(ns, np.inf)
    counts = np.zeros(ns, dtype=int)
    np.minimum.at(nearest, idx, rng)
    np.add.at(counts, idx, 1)

    imin = int(np.argmin(rng))
    out.append(
        f"frame={meta['frame']}  rate={meta['hz']:5.2f} Hz  "
        f"points={n_raw} -> {pts_bl.shape[0]} in window  "
        f"[{args.min_range:.2f}-{args.max_range:.1f} m, z {args.z_min:+.2f}..{args.z_max:+.2f}]")
    out.append(
        f"NEAREST: {rng[imin]:5.2f} m at bearing {bearing[imin]:+7.1f} deg  "
        f"(x={x[imin]:+.2f} y={y[imin]:+.2f} z={z[imin]:+.2f})"
        + ("   *** inside stop distance ***" if rng[imin] < args.warn else ""))
    out.append("")
    out.append(f"  {'bearing':>16s} {'nearest':>8s}  {'pts':>6s}  0{'':<4}"
               f"{args.max_range/2:.0f}{'':<14}{args.max_range:.0f} m")

    for s in range(ns):
        centre = -180.0 + width * s + width / 2.0
        lo, hi = centre - width / 2.0, centre + width / 2.0
        label = f"{lo:+6.0f}..{hi:+6.0f}"
        if not np.isfinite(nearest[s]):
            out.append(f"  {label:>16s} {'--':>8s}  {0:>6d}  |")
            continue
        d = nearest[s]
        filled = int(max(0, min(1.0, 1.0 - d / args.max_range)) * 30)
        bar = "#" * filled
        tag = ""
        if abs(centre) <= width:           # roughly straight ahead
            tag = "  <= AHEAD"
        if d < args.warn:
            tag += "  !"
        out.append(f"  {label:>16s} {d:8.2f}  {counts[s]:>6d}  |{bar:<30}{tag}")

    fwd = np.abs(bearing) <= args.corridor_deg
    if fwd.any():
        out.append("")
        out.append(f"  FORWARD CORRIDOR (+/-{args.corridor_deg:.0f} deg): "
                   f"clear to {rng[fwd].min():.2f} m over {int(fwd.sum())} points")
    else:
        out.append("")
        out.append(f"  FORWARD CORRIDOR (+/-{args.corridor_deg:.0f} deg): no returns")
    return "\n".join(out)


# ---------------------------------------------------------------- node

class LidarReader(Node):
    def __init__(self, args):
        super().__init__("a2_lidar_reader")
        self.args = args
        self.last_t = None
        self.hz = 0.0
        self.frames = 0
        self.csv = open(args.csv, "w") if args.csv else None
        if self.csv:
            self.csv.write("stamp,sector,bearing_center_deg,nearest_m,points\n")

        ext = None
        if not args.no_transform:
            ext = load_extrinsic(quat_order=args.quat_order)
            if ext is None:
                print(f"WARNING: could not read {EXTRINSIC_FILE}; "
                      "staying in raw lidar frame", file=sys.stderr)
        self.ext = ext
        if ext is not None:
            print(f"[a2] base_link_T_lidar: t={np.round(ext[0], 4).tolist()} "
                  f"yaw={yaw_deg(ext[1]):+.1f} deg (quat order {args.quat_order})")
        else:
            print("[a2] NO transform applied — coordinates are in raw livox_frame")

        # Sensor data is best-effort/volatile; a RELIABLE sub may never match.
        qos = QoSProfile(depth=5,
                         reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST,
                         durability=DurabilityPolicy.VOLATILE)
        if args.imu:
            self.sub = self.create_subscription(Imu, args.topic or IMU_TOPIC,
                                                self.on_imu, qos)
        else:
            self.sub = self.create_subscription(PointCloud2, args.topic or CLOUD_TOPIC,
                                                self.on_cloud, qos)
        print(f"[a2] subscribed to {args.topic or (IMU_TOPIC if args.imu else CLOUD_TOPIC)}"
              f"  (domain {os.environ.get('ROS_DOMAIN_ID')})")
        print("[a2] waiting for data ... (Ctrl-C to stop)\n")

    # ---- rate bookkeeping
    def _tick(self):
        now = time.monotonic()
        if self.last_t is not None:
            dt = now - self.last_t
            if dt > 0:
                inst = 1.0 / dt
                self.hz = inst if self.hz == 0 else 0.8 * self.hz + 0.2 * inst
        self.last_t = now
        self.frames += 1

    # ---- IMU
    def on_imu(self, msg: Imu):
        self._tick()
        a, g, o = msg.linear_acceleration, msg.angular_velocity, msg.orientation
        print(f"\r[{self.frames:6d}] {self.hz:5.2f} Hz  "
              f"accel=({a.x:+.3f},{a.y:+.3f},{a.z:+.3f}) m/s^2  "
              f"gyro=({g.x:+.3f},{g.y:+.3f},{g.z:+.3f}) rad/s  "
              f"quat=({o.x:+.3f},{o.y:+.3f},{o.z:+.3f},{o.w:+.3f})",
              end="", flush=True)
        if self.args.once:
            raise KeyboardInterrupt

    # ---- point cloud
    def on_cloud(self, msg: PointCloud2):
        self._tick()
        a = self.args
        if a.decimate > 1 and self.frames % a.decimate:
            return

        # NOTE: read_points_numpy() raises on this cloud —
        #   "All fields need to have the same datatype"
        # because the Livox layout mixes float32 (x,y,z,intensity), uint8 (tag,line)
        # and float64 (timestamp). read_points() returns a structured array instead.
        rec = point_cloud2.read_points(msg, field_names=("x", "y", "z"), skip_nans=True)
        pts = np.stack([rec["x"], rec["y"], rec["z"]], axis=-1).astype(float)
        n_raw = pts.shape[0]

        if a.raw:
            names = [f.name for f in msg.fields]
            print(f"\nfields: {names}  width={msg.width} height={msg.height} "
                  f"point_step={msg.point_step} frame={msg.header.frame_id}")
            print(f"first {a.raw} points (raw {msg.header.frame_id}):")
            for p in pts[:a.raw]:
                print(f"   x={p[0]:+8.3f} y={p[1]:+8.3f} z={p[2]:+8.3f}  "
                      f"r={math.sqrt(p[0]**2+p[1]**2+p[2]**2):7.3f}")
            raise KeyboardInterrupt

        if self.ext is not None and n_raw:
            t, R = self.ext
            pts = pts @ R.T + t

        if n_raw:
            rng = np.hypot(pts[:, 0], pts[:, 1])
            keep = ((rng >= a.min_range) & (rng <= a.max_range)
                    & (pts[:, 2] >= a.z_min) & (pts[:, 2] <= a.z_max))
            pts_f = pts[keep]
        else:
            pts_f = pts

        block = render(pts_f, a, {"frame": msg.header.frame_id,
                                  "hz": self.hz, "n_raw": n_raw})
        if self.csv and pts_f.shape[0]:
            x, y = pts_f[:, 0], pts_f[:, 1]
            rng = np.hypot(x, y)
            bear = np.degrees(np.arctan2(y, x))
            ns = a.sectors
            w = 360.0 / ns
            idx = np.floor((bear + 180.0 + w / 2.0) % 360.0 / w).astype(int) % ns
            stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            for s in range(ns):
                m = idx == s
                if m.any():
                    self.csv.write(f"{stamp:.3f},{s},"
                                   f"{-180.0 + w*s + w/2.0:.1f},"
                                   f"{rng[m].min():.3f},{int(m.sum())}\n")
            self.csv.flush()

        if a.once:
            print(block)
            raise KeyboardInterrupt
        sys.stdout.write("\033[H\033[J" + block + "\n")   # in-place redraw
        sys.stdout.flush()


def list_topics():
    rclpy.init()
    n = rclpy.create_node("a2_lidar_topics")
    time.sleep(1.5)
    for name, types in sorted(n.get_topic_names_and_types()):
        print(f"  {name:60s} {','.join(types)}")
    n.destroy_node()
    rclpy.shutdown()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--topic", default=None, help="override the topic to read")
    p.add_argument("--imu", action="store_true", help="read the LiDAR IMU instead")
    p.add_argument("--list-topics", action="store_true")
    p.add_argument("--once", action="store_true", help="one frame then exit")
    p.add_argument("--raw", type=int, default=0, metavar="N",
                   help="dump N raw points and the field layout, then exit")
    p.add_argument("--sectors", type=int, default=16, help="angular sectors (default 16)")
    p.add_argument("--min-range", type=float, default=0.40,
                   help="drop returns closer than this (default 0.40). The MID-360 sees "
                        "the robot's OWN torso/arms and any gantry frame; below ~0.4 m "
                        "every sector reads solid. Raise it if the robot's own body "
                        "still dominates, lower it only to inspect self-returns.")
    p.add_argument("--max-range", type=float, default=8.0)
    p.add_argument("--z-min", type=float, default=-0.30,
                   help="in base_link: floor cutoff (default -0.30 ignores ground)")
    p.add_argument("--z-max", type=float, default=1.70,
                   help="in base_link: ceiling cutoff")
    p.add_argument("--warn", type=float, default=0.60, help="flag returns closer than this")
    p.add_argument("--corridor-deg", type=float, default=20.0,
                   help="half-width of the 'straight ahead' corridor")
    p.add_argument("--decimate", type=int, default=1, help="process every Nth frame")
    p.add_argument("--csv", default=None, help="log nearest-per-sector to this file")
    p.add_argument("--no-transform", action="store_true",
                   help="do NOT apply the extrinsic; stay in raw livox_frame")
    p.add_argument("--quat-order", choices=["xyzw", "wxyz"], default="wxyz",
                   help="quaternion order in extrinsic_baselink_T_lidar.txt. "
                        "DEFAULT wxyz — established empirically (see module docstring); "
                        "xyzw mirrors left/right and flips the vertical axis.")
    p.add_argument("--domain", default=None,
                   help="ROS_DOMAIN_ID override. Default 232 (the robot stack's domain). "
                        "Your ~/.bashrc sets 231, which sees NOTHING — this script "
                        "overrides it deliberately.")
    a = p.parse_args()

    if a.list_topics:
        list_topics()
        return 0

    rclpy.init()
    node = LidarReader(a)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        if node.csv:
            node.csv.close()
            print(f"\n[a2] wrote {a.csv}")
        if node.frames == 0:
            print("\nNo data received. Checks:\n"
                  "  1) is hal_lidar running?   pgrep -af mid360lidar\n"
                  "  2) does the topic exist?   python3 a2_lidar.py --list-topics\n"
                  f"  3) ROS_DOMAIN_ID must be 232 (currently "
                  f"{os.environ.get('ROS_DOMAIN_ID')})", file=sys.stderr)
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
