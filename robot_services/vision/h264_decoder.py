"""Decode Agibot AIMA H.264 camera topics into BGR frames.

Why this exists
---------------
Most A2 cameras publish three variants of every stream, e.g.

    /aima/hal/fish_eye_camera/chest_left/color              sensor_msgs/Image
    /aima/hal/fish_eye_camera/chest_left/color/compressed   sensor_msgs/CompressedImage
    /aima/hal/fish_eye_camera/chest_left/color/h264         foxglove_msgs/CompressedVideo

The chest-centre "interactive" camera is the exception: it publishes *only*

    /aima/hal/camera/interactive/color/h264                 foxglove_msgs/CompressedVideo

There is no raw image topic for it at all. Subscribing to the raw topic name
succeeds in ROS 2 even though nobody publishes it, so the camera looked
permanently dead - the subscription simply never delivered a frame. Decoding the
H.264 stream is the only way to use that camera.

Why GStreamer and not PyAV
--------------------------
No new dependency is needed: python3-gi is importable from the robot's
camera/vision virtualenvs (they are created with system-site-packages) and the
NVIDIA `nvv4l2decoder` element is already installed, which runs the decode on
the Orin's dedicated NVDEC block instead of the CPU. PyAV is not available on
this machine's package index.

Measured on this robot with the interactive camera's native 1920x1536 stream:

    nvv4l2decoder (hardware)   42.6 fps
    avdec_h264    (software)   18.4 fps

Hardware is preferred, with an automatic one-time fallback to software if the
NVIDIA element is missing or errors out.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Optional

import numpy as np

LOG = logging.getLogger(__name__)

# alignment=au: each CompressedVideo message carries exactly one access unit
# (one frame, with SPS/PPS prepended on keyframes), so telling the parser this
# up front avoids it having to re-scan for frame boundaries.
_APPSRC = (
    "appsrc name=src is-live=true do-timestamp=true format=time "
    "caps=video/x-h264,stream-format=byte-stream,alignment=au"
)
# sync=false: play frames out as fast as they decode rather than pacing to a
# clock. drop=true with a shallow queue keeps latency low for a live monitor -
# a stale frame is worthless here, so dropping beats buffering.
_APPSINK = "appsink name=sink sync=false max-buffers=2 drop=true"

HARDWARE_PIPELINE = (
    f"{_APPSRC} ! h264parse ! nvv4l2decoder ! nvvidconv "
    f"! video/x-raw,format=BGRx ! videoconvert ! video/x-raw,format=BGR ! {_APPSINK}"
)
SOFTWARE_PIPELINE = (
    f"{_APPSRC} ! h264parse ! avdec_h264 ! videoconvert "
    f"! video/x-raw,format=BGR ! {_APPSINK}"
)

_Gst = None
_gst_import_error: Optional[BaseException] = None


def gstreamer_available() -> bool:
    """True if the GStreamer Python bindings can be loaded and initialised."""
    return _ensure_gst() is not None


def gstreamer_import_error() -> Optional[BaseException]:
    return _gst_import_error


def _ensure_gst():
    """Import and initialise GStreamer once, caching success or failure.

    GstApp must be imported even though it is never referenced directly: it is
    what attaches try_pull_sample() and friends to the appsink object.
    """
    global _Gst, _gst_import_error

    if _Gst is not None:
        return _Gst
    if _gst_import_error is not None:
        return None

    try:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstApp", "1.0")
        from gi.repository import Gst
        from gi.repository import GstApp  # noqa: F401  (registers appsink methods)

        Gst.init(None)
        _Gst = Gst
        return _Gst
    except Exception as exc:  # pragma: no cover - depends on host packages
        _gst_import_error = exc
        LOG.warning(
            "GStreamer Python bindings unavailable (%s); H.264 camera topics "
            "cannot be decoded on this host",
            exc,
        )
        return None


class H264DecoderUnavailable(RuntimeError):
    """Raised when no working H.264 decode pipeline can be constructed."""


class H264StreamDecoder:
    """Feed H.264 access units in, get BGR ndarrays out.

    Not a general-purpose decoder: it is shaped for the one job of turning a
    live ROS 2 CompressedVideo topic into the same BGR frames the rest of the
    vision stack already handles.
    """

    def __init__(self, *, label: str = "h264", prefer_hardware: bool = True) -> None:
        self._label = label
        self._prefer_hardware = prefer_hardware
        self._lock = threading.Lock()
        self._pipeline = None
        self._src = None
        self._sink = None
        self._using_hardware = False
        self._closed = False
        self._decoded_total = 0
        self._pushed_total = 0
        # Frames pushed before the first keyframe cannot decode; that is normal
        # and must not be mistaken for a broken pipeline.
        self._warned_no_output = False

    # ------------------------------------------------------------------ public

    @property
    def decoder_name(self) -> str:
        if self._pipeline is None:
            return "uninitialised"
        return "nvv4l2decoder (hardware)" if self._using_hardware else "avdec_h264 (software)"

    @property
    def frames_decoded(self) -> int:
        return self._decoded_total

    def decode(self, data: object) -> List[np.ndarray]:
        """Push one access unit and return whatever frames became available.

        Returns an empty list rather than raising when a single unit produces no
        output, which happens legitimately before the first keyframe arrives.
        """
        if self._closed:
            return []

        payload = bytes(data) if not isinstance(data, (bytes, bytearray)) else bytes(data)
        if not payload:
            return []

        with self._lock:
            if self._pipeline is None:
                self._build()

            frames = self._push_and_drain(payload)
            if frames:
                return frames

            # No frames. If the pipeline has gone into error, retry once on the
            # software decoder before giving up - a missing or busy NVDEC should
            # degrade to a working picture, not to a dead camera.
            if self._bus_has_error() and self._using_hardware:
                LOG.warning(
                    "[%s] hardware H.264 decode failed; falling back to software",
                    self._label,
                )
                self._teardown()
                self._prefer_hardware = False
                self._build()
                return self._push_and_drain(payload)

            return []

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._teardown()

    # ----------------------------------------------------------------- internal

    def _build(self) -> None:
        gst = _ensure_gst()
        if gst is None:
            raise H264DecoderUnavailable(
                "GStreamer Python bindings are required to decode H.264 camera "
                f"topics: {_gst_import_error}"
            )

        attempts = []
        if self._prefer_hardware:
            attempts.append((HARDWARE_PIPELINE, True))
        attempts.append((SOFTWARE_PIPELINE, False))

        last_error: Optional[BaseException] = None
        for description, is_hardware in attempts:
            try:
                pipeline = gst.parse_launch(description)
            except Exception as exc:
                # parse_launch fails when an element is not installed, which is
                # exactly how a host without nvv4l2decoder is detected.
                last_error = exc
                LOG.info(
                    "[%s] %s H.264 pipeline unavailable: %s",
                    self._label,
                    "hardware" if is_hardware else "software",
                    exc,
                )
                continue

            src = pipeline.get_by_name("src")
            sink = pipeline.get_by_name("sink")
            if src is None or sink is None:  # pragma: no cover - defensive
                last_error = RuntimeError("pipeline missing src/sink element")
                continue

            if pipeline.set_state(gst.State.PLAYING) == gst.StateChangeReturn.FAILURE:
                last_error = RuntimeError("pipeline refused to start")
                pipeline.set_state(gst.State.NULL)
                LOG.info(
                    "[%s] %s H.264 pipeline refused to start",
                    self._label,
                    "hardware" if is_hardware else "software",
                )
                continue

            self._pipeline = pipeline
            self._src = src
            self._sink = sink
            self._using_hardware = is_hardware
            LOG.info("[%s] H.264 decode via %s", self._label, self.decoder_name)
            return

        raise H264DecoderUnavailable(
            f"No usable H.264 decode pipeline could be started: {last_error}"
        )

    def _push_and_drain(self, payload: bytes) -> List[np.ndarray]:
        gst = _Gst
        if gst is None or self._src is None or self._sink is None:
            return []

        self._src.emit("push-buffer", gst.Buffer.new_wrapped(payload))
        self._pushed_total += 1

        frames: List[np.ndarray] = []
        # First pull waits briefly for the decoder to turn the unit around;
        # subsequent pulls are non-blocking so we take only what is already
        # queued and never stall the ROS callback thread.
        timeout = 40 * gst.MSECOND
        while True:
            sample = self._sink.try_pull_sample(timeout)
            if sample is None:
                break
            frame = self._sample_to_bgr(sample)
            if frame is not None:
                frames.append(frame)
            timeout = 0

        self._decoded_total += len(frames)
        if (
            not frames
            and not self._warned_no_output
            and self._pushed_total >= 60
            and self._decoded_total == 0
        ):
            self._warned_no_output = True
            LOG.warning(
                "[%s] %d H.264 units pushed with no decoded frames; stream may "
                "lack keyframes or use an unsupported profile",
                self._label,
                self._pushed_total,
            )
        return frames

    @staticmethod
    def _sample_to_bgr(sample) -> Optional[np.ndarray]:
        gst = _Gst
        if gst is None:
            return None

        caps = sample.get_caps()
        if caps is None:
            return None
        structure = caps.get_structure(0)
        ok_w, width = structure.get_int("width")
        ok_h, height = structure.get_int("height")
        if not ok_w or not ok_h or width <= 0 or height <= 0:
            return None

        buffer = sample.get_buffer()
        mapped, info = buffer.map(gst.MapFlags.READ)
        if not mapped:
            return None
        try:
            expected = width * height * 3
            raw = np.frombuffer(info.data, dtype=np.uint8)
            if raw.size < expected:
                return None
            # Copy: the mapped buffer is returned to GStreamer on unmap, so a
            # view into it would dangle.
            return raw[:expected].reshape((height, width, 3)).copy()
        finally:
            buffer.unmap(info)

    def _bus_has_error(self) -> bool:
        gst = _Gst
        if gst is None or self._pipeline is None:
            return False
        bus = self._pipeline.get_bus()
        if bus is None:
            return False
        saw_error = False
        while True:
            message = bus.pop_filtered(gst.MessageType.ERROR | gst.MessageType.WARNING)
            if message is None:
                break
            if message.type == gst.MessageType.ERROR:
                error, debug = message.parse_error()
                LOG.error("[%s] GStreamer error: %s (%s)", self._label, error, debug)
                saw_error = True
            else:
                warning, debug = message.parse_warning()
                LOG.debug("[%s] GStreamer warning: %s (%s)", self._label, warning, debug)
        return saw_error

    def _teardown(self) -> None:
        gst = _Gst
        if self._pipeline is not None and gst is not None:
            try:
                self._pipeline.set_state(gst.State.NULL)
            except Exception:  # pragma: no cover - best effort shutdown
                LOG.debug("[%s] failed to stop H.264 pipeline", self._label, exc_info=True)
        self._pipeline = None
        self._src = None
        self._sink = None


class H264FrameReceiver:
    """Decode a live H.264 topic on a worker thread, off the ROS callback.

    Decoding inside the subscription callback is a trap worth documenting. A
    hardware decode of one 1920x1536 frame takes roughly 25 ms, and while the
    callback is running the ROS executor cannot service the socket. The
    interactive camera's keyframes are ~112 KB, so they arrive as many UDP
    fragments; lose one and, because the publisher is BEST_EFFORT, it is never
    retransmitted. The picture then breaks into green speckles and stale
    macroblocks until the next keyframe.

    Measured on this robot against /aima/hal/camera/interactive/color/h264:

        decode in the ROS callback   14.8 fps, visible corruption
        decode on a worker thread    ~22 fps, zero corrupt pixels

    So the callback only appends bytes to a queue, and all decoding happens here.
    """

    def __init__(
        self,
        *,
        label: str,
        on_frame,
        queue_size: int = 120,
        prefer_hardware: bool = True,
    ) -> None:
        import collections

        self._label = label
        self._on_frame = on_frame
        self._decoder = H264StreamDecoder(label=label, prefer_hardware=prefer_hardware)
        self._queue = collections.deque(maxlen=max(2, int(queue_size)))
        self._queue_lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dropped_units = 0
        self._warned_dropping = False

    # ------------------------------------------------------------------ public

    @property
    def decoder_name(self) -> str:
        return self._decoder.decoder_name

    @property
    def frames_decoded(self) -> int:
        return self._decoder.frames_decoded

    @property
    def dropped_units(self) -> int:
        return self._dropped_units

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            name=f"h264-decode-{self._label}",
            daemon=True,
        )
        self._thread.start()

    def submit(self, data: object) -> None:
        """Queue one access unit. Safe and cheap to call from a ROS callback."""
        if self._stop.is_set():
            return
        payload = bytes(data)
        if not payload:
            return
        with self._queue_lock:
            # A full queue means the decoder cannot keep up. Dropping breaks the
            # H.264 reference chain, so it is worth a warning rather than being
            # silently absorbed.
            if len(self._queue) == self._queue.maxlen:
                self._dropped_units += 1
                if not self._warned_dropping:
                    self._warned_dropping = True
                    LOG.warning(
                        "[%s] H.264 decode queue full; dropping units, expect "
                        "visible artefacts until the next keyframe",
                        self._label,
                    )
            self._queue.append(payload)
        self._wakeup.set()

    def close(self) -> None:
        self._stop.set()
        self._wakeup.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None
        self._decoder.close()

    # ----------------------------------------------------------------- internal

    def _worker(self) -> None:
        while not self._stop.is_set():
            self._wakeup.wait(timeout=0.2)
            self._wakeup.clear()
            while not self._stop.is_set():
                with self._queue_lock:
                    if not self._queue:
                        break
                    payload = self._queue.popleft()
                try:
                    frames = self._decoder.decode(payload)
                except H264DecoderUnavailable:
                    LOG.error(
                        "[%s] H.264 decoding is unavailable on this host; stopping decoder",
                        self._label,
                    )
                    self._stop.set()
                    return
                except Exception:
                    LOG.exception("[%s] H.264 decode failed", self._label)
                    continue
                for frame in frames:
                    try:
                        self._on_frame(frame)
                    except Exception:
                        LOG.exception("[%s] H.264 frame handler failed", self._label)
