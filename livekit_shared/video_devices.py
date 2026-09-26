from __future__ import annotations

import sys
from pathlib import Path


def resolve_camera_device(device: str) -> str:
    """Resolve a configured camera device path/index/serial to an OpenCV target."""
    normalized = str(device).strip()
    if not normalized:
        return normalized

    if sys.platform.startswith("win"):
        return normalized

    if normalized.isdigit() or normalized.startswith("/dev/video"):
        return normalized

    candidate_path = Path(normalized)
    if candidate_path.exists():
        return str(candidate_path.resolve())

    by_id_match = _resolve_v4l_by_id(normalized)
    if by_id_match:
        return by_id_match

    udev_match = _resolve_linux_serial(normalized)
    if udev_match:
        return udev_match

    return normalized


def _resolve_v4l_by_id(serial: str) -> str | None:
    by_id_dir = Path("/dev/v4l/by-id")
    if not by_id_dir.exists():
        return None

    matches = sorted(path for path in by_id_dir.iterdir() if serial in path.name)
    if not matches:
        return None

    preferred_match = next((path for path in matches if "index0" in path.name), matches[0])
    try:
        return str(preferred_match.resolve())
    except OSError:
        return None


def _resolve_linux_serial(serial: str) -> str | None:
    """Resolve a camera by USB serial via sysfs (similar to teleimager's uvc flow)."""
    sys_video_root = Path("/sys/class/video4linux")
    if not sys_video_root.exists():
        return None

    serial = str(serial)
    matches: list[Path] = []

    for video_node in sorted(sys_video_root.glob("video*")):
        sys_path = (video_node / "device").resolve()
        sn = _find_usb_serial(sys_path)
        if sn and sn == serial:
            matches.append(video_node)

    if not matches:
        return None

    # Prefer the RGB stream if OpenCV is available; otherwise return the first match.
    for video_node in matches:
        if _is_rgb_video_node(video_node):
            return f"/dev/{video_node.name}"

    return f"/dev/{matches[0].name}"


def _find_usb_serial(sys_path: Path) -> str | None:
    """Walk up sysfs to find a USB 'serial' attribute."""
    current = sys_path
    for _ in range(10):
        serial_path = current / "serial"
        if serial_path.exists():
            try:
                return serial_path.read_text(errors="ignore").strip()
            except Exception:
                return None
        if current.parent == current:
            break
        current = current.parent
    return None


def _is_rgb_video_node(video_node: Path) -> bool:
    """Check if /dev/videoX yields RGB frames."""
    try:
        import cv2
    except Exception:
        return False

    dev_path = f"/dev/{video_node.name}"
    cap = cv2.VideoCapture(dev_path)
    if not cap.isOpened():
        return False
    ok, frame = cap.read()
    cap.release()
    return bool(ok and frame is not None and frame.ndim == 3 and frame.shape[2] == 3)
