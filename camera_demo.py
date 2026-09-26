#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import html
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROS_DOMAIN_ID = "232"
DEFAULT_FASTDDS_PROFILE = "/agibot/software/v0/entry/bin/cfg/ros_dds_configuration.xml"
REEXEC_MARKER = "CAMERA_DEMO_AGIBOT_ENV_READY"
DEFAULT_LOCAL_CACHE = SCRIPT_DIR / "jetson_deps" / ".cache"


def configure_local_cache_defaults() -> None:
    """Keep ML libraries from writing into the Jetson root filesystem."""
    cache_dir = DEFAULT_LOCAL_CACHE
    config_dir = cache_dir / "config"
    ultralytics_dir = cache_dir / "ultralytics"
    for path in (cache_dir, config_dir, ultralytics_dir):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue

    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    os.environ.setdefault("XDG_CONFIG_HOME", str(config_dir))
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir / "matplotlib"))
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ultralytics_dir))


configure_local_cache_defaults()

# Agibot camera topics are only discoverable when these DDS variables exist
# before the Python process starts. Restart once with the right environment.
_required_env = {
    "ROS_DOMAIN_ID": DEFAULT_ROS_DOMAIN_ID,
    "ROS_LOCALHOST_ONLY": "0",
    "FASTRTPS_DEFAULT_PROFILES_FILE": DEFAULT_FASTDDS_PROFILE,
}
if __name__ == "__main__" and os.environ.get(REEXEC_MARKER) != "1" and any(os.environ.get(k) != v for k, v in _required_env.items()):
    _env = os.environ.copy()
    _env.update(_required_env)
    _env[REEXEC_MARKER] = "1"
    os.execvpe(sys.executable, [sys.executable, *sys.argv], _env)

os.environ.update(_required_env)

import signal
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Optional

try:
    import cv2  # type: ignore
    import numpy as np
except Exception as exc:
    raise SystemExit(
        "OpenCV failed to import. On the A2 Jetson, run with the pinned Jetson deps:\n"
        "  PYTHONPATH=/agibot/humanoid-platform/jetson_deps python3 camera_demo.py\n"
        "or create a Python 3.10 venv with numpy<2 and OpenCV installed."
    ) from exc


A2_CAMERA_ALIASES = {
    "CHEST_MAIN": "/dev/video0",
    "CHEST_FISHEYE_L": "/dev/video2",
    "CHEST_FISHEYE_R": "/dev/video4",
}

A2_ROS2_TOPICS = {
    "CHEST_LEFT_FISHEYE": "/aima/hal/fish_eye_camera/chest_left/color",
    "CHEST_RIGHT_FISHEYE": "/aima/hal/fish_eye_camera/chest_right/color",
    "INTERACTIVE_MAIN": "/aima/hal/camera/interactive/color",
    "HEAD_FRONT_RGBD": "/aima/hal/rgbd_camera/head_front/color",
    "WAIST_FRONT_RGBD": "/aima/hal/rgbd_camera/waist_front/color",
}

@dataclass
class CameraSpec:
    name: str
    device: str
    backend: str
    width: int
    height: int
    fps: int


class CameraWorker:
    def __init__(self, spec: CameraSpec) -> None:
        self.spec = spec
        self.frame: Optional[np.ndarray] = None
        self.detected_frame: Optional[np.ndarray] = None
        self.detected_at = 0.0
        self.detection_error: Optional[str] = None
        self.error: Optional[str] = None
        self.frames = 0
        self.started_at = time.monotonic()
        self.last_frame_at = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"camera-{spec.name}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def snapshot(self) -> tuple[Optional[np.ndarray], Optional[str], Optional[str], float, float, int]:
        with self._lock:
            source = self.detected_frame if self.detected_frame is not None else self.frame
            frame = None if source is None else source.copy()
            return frame, self.error, self.detection_error, self.last_frame_at, self.detected_at, self.frames

    def raw_frame_snapshot(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self.frame is None else self.frame.copy()

    def update_frame(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        if frame.shape[1] != self.spec.width or frame.shape[0] != self.spec.height:
            frame = cv2.resize(frame, (self.spec.width, self.spec.height))
        with self._lock:
            self.frame = frame
            self.error = None
            self.frames += 1
            self.last_frame_at = now

    def set_detected_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self.detected_frame = frame
            self.detected_at = time.monotonic()
            self.detection_error = None

    def set_detection_error(self, err_msg: str) -> None:
        with self._lock:
            self.detection_error = err_msg

    def set_error(self, err_msg: str) -> None:
        with self._lock:
            self.error = err_msg

    def _run(self) -> None:
        if self.spec.backend == "ros2":
            # Event lifecycle managed by the external ROS 2 Subscription Engine
            while not self._stop.is_set():
                time.sleep(0.1)
            return

        cap = open_capture(self.spec)
        if not cap.isOpened():
            with self._lock:
                self.error = "failed to open"
            return

        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    with self._lock:
                        self.error = "read failed"
                    time.sleep(0.03)
                    continue

                if frame.ndim == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                
                self.update_frame(frame)
        finally:
            cap.release()


class ClothingDetector:
    def __init__(self, model_path: str, conf: float, image_size: int, max_fps: float, device: str) -> None:
        try:
            from ultralytics import YOLO
            import torch
        except Exception as exc:
            raise SystemExit(
                "Ultralytics is not installed in this Python environment. Install the vision "
                "dependencies or run with --no-detection to view cameras only.\n"
                "For this repo, the detector dependency is listed in "
                "robot_services/vision/detection/requirements.txt."
            ) from exc

        self.model_path = resolve_model_path(model_path)
        self.model = YOLO(self.model_path)
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.model.to(self.device)
        self.conf = conf
        self.image_size = image_size
        self.max_interval = 1.0 / max_fps if max_fps > 0 else 0.0
        self._condition = threading.Condition()
        self._pending: dict[CameraWorker, np.ndarray] = {}
        self._last_run: dict[CameraWorker, float] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="clothing-detector", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def submit(self, worker: CameraWorker) -> None:
        frame = worker.raw_frame_snapshot()
        if frame is None:
            return
        now = time.monotonic()
        if now - self._last_run.get(worker, 0.0) < self.max_interval:
            return
        with self._condition:
            self._pending[worker] = frame
            self._condition.notify()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._condition:
                while not self._pending and not self._stop.is_set():
                    self._condition.wait(timeout=0.2)
                if self._stop.is_set():
                    return
                worker, frame = self._pending.popitem()

            self._last_run[worker] = time.monotonic()
            try:
                results = self.model.predict(
                    frame,
                    conf=self.conf,
                    imgsz=self.image_size,
                    device=self.device,
                    verbose=False,
                )
                worker.set_detected_frame(results[0].plot())
            except Exception as exc:
                worker.set_detection_error(f"detection failed: {exc}")


class Ros2Spinner:
    def __init__(self, workers: list[CameraWorker], args: argparse.Namespace) -> None:
        self.workers = workers
        self.args = args
        self.node = None
        self._thread = None
        self._subs = []

    def start(self) -> bool:
        configure_ros_environment(self.args)

        try:
            import rclpy
            from sensor_msgs.msg import Image
            from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
        except ImportError:
            print("[ERROR] Could not import rclpy/sensor_msgs. Make sure to source your ROS 2 / AIMA environment!", file=sys.stderr)
            for w in self.workers:
                if w.spec.backend == "ros2":
                    w.set_error("rclpy import failed")
            return False

        if not rclpy.ok():
            rclpy.init()

        self.node = rclpy.create_node("camera_demo_bridge")
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        has_subs = False

        for worker in self.workers:
            if worker.spec.backend == "ros2":
                topic = worker.spec.device
                print(f"Subscribing to image stream: {topic} -> {worker.spec.name}")
                sub = self.node.create_subscription(
                    Image,
                    topic,
                    self._make_callback(worker),
                    sensor_qos,
                )
                self._subs.append(sub)
                has_subs = True

        return True


    def spin_until(self, stop_event: threading.Event) -> None:
        if self.node is None:
            return
        import rclpy
        try:
            while not stop_event.is_set() and rclpy.ok():
                rclpy.spin_once(self.node, timeout_sec=0.1)
        except Exception as exc:
            print(f"[ERROR] ROS 2 spin failed: {exc}", file=sys.stderr)
        finally:
            if self.node:
                self.node.destroy_node()
                self.node = None

    def _make_callback(self, worker: CameraWorker):
        def callback(msg) -> None:
            try:
                frame = ros_image_to_bgr(msg)
                if worker.frames == 0:
                    print(
                        f"Received first frame from {worker.spec.name}: "
                        f"{msg.width}x{msg.height} {msg.encoding}",
                        flush=True,
                    )
                worker.update_frame(frame)
            except Exception as e:
                worker.set_error(f"decode error: {str(e)}")
        return callback

    def _run(self) -> None:
        import rclpy
        try:
            while self.node is not None and rclpy.ok():
                rclpy.spin_once(self.node, timeout_sec=0.1)
        except Exception as exc:
            print(f"[ERROR] ROS 2 spin failed: {exc}", file=sys.stderr)
        finally:
            if self.node:
                self.node.destroy_node()


def configure_ros_environment(args: argparse.Namespace) -> None:
    os.environ.setdefault("ROS_DOMAIN_ID", str(args.ros_domain_id))
    os.environ.setdefault("ROS_LOCALHOST_ONLY", str(args.ros_localhost_only))
    if args.fastdds_profile:
        os.environ.setdefault("FASTRTPS_DEFAULT_PROFILES_FILE", args.fastdds_profile)


def _image_rows(msg) -> np.ndarray:
    data = np.frombuffer(msg.data, dtype=np.uint8)
    expected = int(msg.height) * int(msg.step)
    if expected and data.size >= expected:
        return data[:expected].reshape((msg.height, msg.step))
    return data


def ros_image_to_bgr(msg) -> np.ndarray:
    height = int(msg.height)
    width = int(msg.width)
    encoding = str(msg.encoding).lower()
    rows = _image_rows(msg)

    if encoding in ("bgr8", "bgr"):
        return rows[:, : width * 3].reshape((height, width, 3)).copy()
    if encoding in ("rgb8", "rgb"):
        rgb = rows[:, : width * 3].reshape((height, width, 3))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if encoding in ("bgra8", "bgra"):
        bgra = rows[:, : width * 4].reshape((height, width, 4))
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    if encoding in ("rgba8", "rgba"):
        rgba = rows[:, : width * 4].reshape((height, width, 4))
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
    if encoding in ("mono8", "8uc1"):
        gray = rows[:, :width].reshape((height, width))
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if encoding in ("16uc1", "mono16"):
        data16 = np.frombuffer(msg.data, dtype=np.uint16).reshape((height, msg.step // 2))
        depth = data16[:, :width].astype(np.float32)
        max_value = float(depth.max())
        gray = ((depth / max_value) * 255).astype(np.uint8) if max_value > 0 else np.zeros((height, width), dtype=np.uint8)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if encoding in ("yuv422", "yuyv", "yuyv422"):
        yuyv = rows[:, : width * 2].reshape((height, width, 2))
        return cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUY2)

    channels = len(msg.data) // (height * width) if height and width else 0
    if channels >= 3:
        return np.frombuffer(msg.data, dtype=np.uint8).reshape((height, width, channels))[:, :, :3].copy()
    raise ValueError(f"unsupported encoding: {msg.encoding}")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class StreamHandler(BaseHTTPRequestHandler):
    workers: list[CameraWorker] = []
    tile_width = 640
    tile_height = 360
    jpeg_quality = 80

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._write_index()
            return
        if self.path == "/stream.mjpg":
            self._write_mjpeg(lambda: compose_grid(self.workers, self.tile_width, self.tile_height))
            return
        if self.path.startswith("/camera/") and self.path.endswith(".mjpg"):
            name = self.path.removeprefix("/camera/").removesuffix(".mjpg")
            worker = next((item for item in self.workers if item.spec.name == name), None)
            if worker is None:
                self.send_error(404, "unknown camera")
                return
            self._write_mjpeg(lambda: compose_single(worker, self.tile_width, self.tile_height))
            return
        self.send_error(404)

    def _write_index(self) -> None:
        rows = "\n".join(
            f"<li><a href='/camera/{html.escape(worker.spec.name)}.mjpg'>"
            f"{html.escape(worker.spec.name)}</a> - {html.escape(worker.spec.device)}"
            f" ({html.escape(worker.spec.backend)})</li>"
            for worker in self.workers
        )
        body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>A2 Camera Demo</title>
  <style>
    body {{ margin: 0; background: #111; color: #eee; font: 14px sans-serif; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 16px; }}
    img {{ width: 100%; height: auto; background: #222; }}
    a {{ color: #8cc8ff; }}
  </style>
</head>
<body>
  <main>
    <img src="/stream.mjpg" alt="A2 camera grid">
    <ul>{rows}</ul>
  </main>
</body>
</html>"""
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _write_mjpeg(self, frame_factory) -> None:
        self.send_response(200)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        while True:
            frame = frame_factory()
            ok, jpeg = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)],
            )
            if ok:
                try:
                    payload = jpeg.tobytes()
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(payload)
                    self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    return
            time.sleep(0.04)


def device_name(device: str) -> str:
    base = os.path.basename(device)
    sysfs_name = f"/sys/class/video4linux/{base}/name"
    try:
        return open(sysfs_name, "r", encoding="utf-8").read().strip()
    except OSError:
        return base


def discover_video_devices() -> list[str]:
    devices = sorted(glob.glob("/dev/video*"), key=video_sort_key)
    return [device for device in devices if os.path.exists(device)]


def video_sort_key(device: str) -> tuple[int, str]:
    suffix = device.replace("/dev/video", "")
    return (int(suffix), device) if suffix.isdigit() else (9999, device)


def find_orbbec_rgb_path() -> Optional[str]:
    for device in discover_video_devices():
        name = device_name(device).lower()
        if "orbbec" in name and "rgb" in name:
            return device
    return None


def resolve_model_path(model_path: str) -> str:
    path = Path(model_path).expanduser()
    if path.is_file():
        return str(path)

    zip_path = Path(f"{path}.zip")
    if path.is_dir() and zip_path.is_file():
        link_path = Path("/tmp/camera_demo_best_cloth.pt")
        try:
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            link_path.symlink_to(zip_path.resolve())
            print(f"Using model archive {zip_path} via {link_path}", flush=True)
            return str(link_path)
        except OSError:
            print(f"Using model archive {zip_path}; {path} is an extracted directory.", flush=True)
            return str(zip_path)

    if zip_path.is_file():
        return str(zip_path)
    raise SystemExit(f"YOLO model file not found: {model_path}")


def parse_devices(value: str) -> list[str]:
    if value == "auto":
        return discover_video_devices()
    if value == "aliases":
        devices = list(A2_CAMERA_ALIASES.values())
        groin = find_orbbec_rgb_path()
        if groin:
            devices.append(groin)
        return devices
    return [item.strip() for item in value.split(",") if item.strip()]


def gst_v4l2_pipeline(device: str, width: int, height: int, fps: int) -> str:
    return (
        f"v4l2src device={device} do-timestamp=true ! "
        f"video/x-raw,format=YUY2,width={width},height={height},framerate={fps}/1 ! "
        "videoconvert n-threads=2 ! video/x-raw,format=BGR ! "
        "appsink drop=true max-buffers=1 sync=false"
    )


def argus_pipeline(sensor_id: int, width: int, height: int, fps: int) -> str:
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM),width={width},height={height},format=NV12,framerate={fps}/1 ! "
        "nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=true max-buffers=1 sync=false"
    )


def open_capture(spec: CameraSpec):
    if spec.backend == "gst-v4l2":
        return cv2.VideoCapture(
            gst_v4l2_pipeline(spec.device, spec.width, spec.height, spec.fps),
            cv2.CAP_GSTREAMER,
        )
    if spec.backend == "argus":
        sensor_id = int(spec.device)
        return cv2.VideoCapture(
            argus_pipeline(sensor_id, spec.width, spec.height, spec.fps),
            cv2.CAP_GSTREAMER,
        )

    target: int | str = spec.device
    if spec.device.startswith("/dev/video") and spec.device.replace("/dev/video", "").isdigit():
        target = int(spec.device.replace("/dev/video", ""))
    cap = cv2.VideoCapture(target, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, spec.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, spec.height)
    cap.set(cv2.CAP_PROP_FPS, spec.fps)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
    if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def frame_health(frame: np.ndarray) -> tuple[str, tuple[int, int, int]]:
    pixels = frame.reshape(-1, frame.shape[-1])
    mean = pixels.mean(axis=0)
    std = pixels.std(axis=0)
    if float(std.max()) < 1.0:
        bgr = tuple(int(round(value)) for value in mean)
        if bgr[1] > 80 and bgr[0] < 10 and bgr[2] < 10:
            return f"flat green placeholder BGR={bgr}", (0, 180, 255)
        if max(bgr) < 10:
            return f"flat black placeholder BGR={bgr}", (0, 180, 255)
        return f"flat placeholder BGR={bgr}", (0, 180, 255)
    return "", (80, 220, 120)


def label_frame(worker: CameraWorker) -> np.ndarray:
    frame, error, detection_error, last_frame_at, detected_at, frames = worker.snapshot()
    missing_frame = frame is None
    if missing_frame:
        frame = np.zeros((worker.spec.height, worker.spec.width, 3), dtype=np.uint8)
    age = time.monotonic() - last_frame_at if last_frame_at else 0.0
    detect_age = time.monotonic() - detected_at if detected_at else 0.0
    label = f"{worker.spec.name} {worker.spec.device} ({worker.spec.backend})"
    if error:
        status = error
        color = (0, 180, 255)
    elif missing_frame:
        elapsed = time.monotonic() - worker.started_at
        status = f"waiting for first frame ({elapsed:.1f}s)"
        color = (0, 180, 255)
    elif detection_error:
        status = detection_error[:120]
        color = (0, 180, 255)
    else:
        health, color = frame_health(frame)
        detection = f", detect age {detect_age:.1f}s" if detected_at else ""
        status = health or f"{frames} frames, age {age:.1f}s{detection}"
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 58), (0, 0, 0), -1)
    cv2.putText(frame, label, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.putText(frame, status, (12, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2)
    return frame


def compose_single(worker: CameraWorker, width: int, height: int) -> np.ndarray:
    return cv2.resize(label_frame(worker), (width, height))


def compose_grid(workers: list[CameraWorker], tile_width: int, tile_height: int) -> np.ndarray:
    if not workers:
        return np.zeros((tile_height, tile_width, 3), dtype=np.uint8)
    cols = 2 if len(workers) > 1 else 1
    rows = (len(workers) + cols - 1) // cols
    blank = np.zeros((tile_height, tile_width, 3), dtype=np.uint8)
    tiles = [cv2.resize(label_frame(worker), (tile_width, tile_height)) for worker in workers]
    while len(tiles) < rows * cols:
        tiles.append(blank.copy())
    return np.vstack(
        [np.hstack(tiles[row * cols : (row + 1) * cols]) for row in range(rows)]
    )


def build_specs(args: argparse.Namespace) -> list[CameraSpec]:
    if args.backend == "ros2":
        if args.camera:
            key = args.camera.upper()
            if key in A2_ROS2_TOPICS:
                return [CameraSpec(key, A2_ROS2_TOPICS[key], "ros2", args.width, args.height, args.fps)]
            if key in A2_CAMERA_ALIASES or key == "ORBBEC_GROIN":
                aliases = ", ".join(sorted(A2_ROS2_TOPICS))
                raise SystemExit(
                    f"{args.camera} is not a known ROS 2 image topic alias. "
                    f"Known ROS 2 aliases: {aliases}. Use --backend v4l2 for /dev/video aliases."
                )
            if not args.camera.startswith("/"):
                aliases = ", ".join(sorted(A2_ROS2_TOPICS))
                raise SystemExit(
                    f"Unknown camera alias or ROS 2 topic: {args.camera}. "
                    f"Known ROS 2 aliases: {aliases}. Raw ROS 2 topics should start with /."
                )
            return [CameraSpec("CUSTOM_ROS2", args.camera, "ros2", args.width, args.height, args.fps)]
        if args.devices == "aliases":
            return [CameraSpec(name, topic, "ros2", args.width, args.height, args.fps) for name, topic in A2_ROS2_TOPICS.items()]
        
        topics = [item.strip() for item in args.devices.split(",") if item.strip()]
        return [CameraSpec(f"TOPIC_{i}", topic, "ros2", args.width, args.height, args.fps) for i, topic in enumerate(topics)]

    if args.camera:
        key = args.camera.upper()
        if key == "ORBBEC_GROIN":
            device = find_orbbec_rgb_path()
            if not device:
                raise SystemExit("ORBBEC_GROIN was requested, but no Orbbec RGB video node was found.")
            return [CameraSpec(key, device, "v4l2", args.width, args.height, args.fps)]
        if key not in A2_CAMERA_ALIASES:
            raise SystemExit(f"Unknown camera alias: {args.camera}")
        return [CameraSpec(key, A2_CAMERA_ALIASES[key], args.backend, args.width, args.height, args.fps)]

    devices = parse_devices(args.devices)
    specs = []
    for device in devices:
        name = os.path.basename(device).replace("video", "cam")
        if device in A2_CAMERA_ALIASES.values():
            name = next(key for key, value in A2_CAMERA_ALIASES.items() if value == device)
        specs.append(CameraSpec(name, device, args.backend, args.width, args.height, args.fps))
    return specs


def feed_detector_until(detector: ClothingDetector, workers: list[CameraWorker], stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        for worker in workers:
            detector.submit(worker)
        time.sleep(0.02)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="View Agibot A2 cameras as a local MJPEG grid.")
    parser.add_argument("--camera", default="HEAD_FRONT_RGBD", help="Alias: HEAD_FRONT_RGBD, WAIST_FRONT_RGBD, CHEST_LEFT_FISHEYE, CHEST_RIGHT_FISHEYE, INTERACTIVE_MAIN, ORBBEC_GROIN, CHEST_MAIN, CHEST_FISHEYE_L, CHEST_FISHEYE_R, or a raw ROS 2 topic")
    parser.add_argument("--devices", default="aliases", help="auto, aliases, or comma-separated /dev/video paths / ROS topics")
    parser.add_argument("--backend", choices=["v4l2", "gst-v4l2", "argus", "ros2"], default="ros2")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--tile-width", type=int, default=640)
    parser.add_argument("--tile-height", type=int, default=360)
    parser.add_argument("--jpeg-quality", type=int, default=80)
    parser.add_argument("--model", default="best_cloth.pt", help="Ultralytics YOLO clothing model path")
    parser.add_argument("--no-detection", action="store_true", help="Disable YOLO clothing detection overlay")
    parser.add_argument("--detection-conf", type=float, default=0.25, help="YOLO confidence threshold")
    parser.add_argument("--detection-imgsz", type=int, default=640, help="YOLO inference image size")
    parser.add_argument("--detection-fps", type=float, default=5.0, help="Maximum detector FPS across the camera grid")
    parser.add_argument("--detection-device", default="auto", help="YOLO device: auto, cpu, cuda, cuda:0, etc.")
    parser.add_argument("--ros-domain-id", default=os.getenv("ROS_DOMAIN_ID", DEFAULT_ROS_DOMAIN_ID))
    parser.add_argument("--ros-localhost-only", default=os.getenv("ROS_LOCALHOST_ONLY", "0"))
    parser.add_argument("--fastdds-profile", default=os.getenv("FASTRTPS_DEFAULT_PROFILES_FILE", DEFAULT_FASTDDS_PROFILE))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    specs = build_specs(args)
    if not specs:
        raise SystemExit("No cameras found.")

    workers = [CameraWorker(spec) for spec in specs]
    for worker in workers:
        print(f"Opening {worker.spec.name}: {worker.spec.device} via {worker.spec.backend}", flush=True)

    detector = None
    if not args.no_detection:
        detector = ClothingDetector(
            args.model,
            conf=args.detection_conf,
            image_size=args.detection_imgsz,
            max_fps=args.detection_fps,
            device=args.detection_device,
        )
        detector.start()
        print(f"Clothing detection enabled: {detector.model_path} on {detector.device}", flush=True)

    spinner = Ros2Spinner(workers, args)
    if args.backend == "ros2":
        if not spinner.start():
            return 1
    else:
        for worker in workers:
            worker.start()

    StreamHandler.workers = workers
    StreamHandler.tile_width = args.tile_width
    StreamHandler.tile_height = args.tile_height
    StreamHandler.jpeg_quality = args.jpeg_quality
    server = ThreadedHTTPServer((args.host, args.port), StreamHandler)

    stop_event = threading.Event()

    def stop(*_: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print(f"Live camera grid: http://127.0.0.1:{args.port}/", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    detector_thread = None
    if detector is not None:
        detector_thread = threading.Thread(
            target=feed_detector_until,
            args=(detector, workers, stop_event),
            name="camera-demo-detector-feed",
            daemon=True,
        )
        detector_thread.start()
    if args.backend == "ros2":
        spinner.spin_until(stop_event)
    else:
        stop_event.wait()

    server.shutdown()
    if detector is not None:
        detector.stop()
    if detector_thread and detector_thread.is_alive():
        detector_thread.join(timeout=1.0)
    for worker in workers:
        worker.stop()

    if args.backend == "ros2":
        import rclpy
        rclpy.try_shutdown()

    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())