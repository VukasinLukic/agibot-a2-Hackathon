from __future__ import annotations

import logging
import os

from .bridge import LiveKitAudioBridge
from .config import AudioTarget, load_audio_target, load_livekit_config
from .device_selector import resolve_host_audio_devices

LOG = logging.getLogger("audio_handler")


class AudioHandler:
    def __init__(self, *, target: AudioTarget, bridge: LiveKitAudioBridge) -> None:
        self._target = target
        self._bridge = bridge
        self._started = False

    @property
    def target(self) -> AudioTarget:
        return self._target

    @classmethod
    def from_env(cls, *, room_name: str | None = None) -> "AudioHandler":
        target = load_audio_target()
        bridge_room = _resolve_bridge_room(room_name)

        if target == AudioTarget.ROBOT:
            from .input.robot_mic import RobotMicConfig, RobotMicInput
            from .output.robot_speaker import RobotSpeakerConfig, RobotSpeakerOutput

            mic_cfg = RobotMicConfig(
                mcast_group=_require_env("MCAST_GRP"),
                local_iface_ip=_require_env("LOCAL_IFACE_IP"),
                frame_ms=int(os.getenv("LIVEKIT_FRAME_MS", "20")),
                queue_size=int(os.getenv("LIVEKIT_MAX_QUEUE", "50")),
            )
            speaker_cfg = RobotSpeakerConfig(
                iface=_require_env("UNITREE_NET_IF"),
                app_name=os.getenv("ROBOT_SPEAKER_APP", "livekit_bridge"),
                chunk_ms=int(os.getenv("ROBOT_SPEAKER_CHUNK_MS", "200")),
                prebuffer_ms=int(os.getenv("ROBOT_SPEAKER_PREBUFFER_MS", "600")),
                volume=_parse_optional_int(os.getenv("ROBOT_SPEAKER_VOLUME")),
            )
            input_source = RobotMicInput(mic_cfg)
            output_sink = RobotSpeakerOutput(speaker_cfg)
            livekit_cfg = load_livekit_config(
                room_name=bridge_room,
                identity_default="robot-bridge",
                mic_track_name_default="g1-mic",
            )
        else:
            from .input.host_mic import HostMicConfig, HostMicInput
            from .output.host_speaker import HostSpeakerConfig, HostSpeakerOutput

            host_devices = resolve_host_audio_devices()
            sample_rate_env = os.getenv("HOST_AUDIO_SAMPLE_RATE")
            if sample_rate_env and sample_rate_env.strip():
                sample_rate = int(sample_rate_env)
            else:
                sample_rate = _resolve_sample_rate(host_devices)
            mic_cfg = HostMicConfig(
                device=host_devices.input_device,
                sample_rate=sample_rate,
                channels=int(os.getenv("HOST_AUDIO_CHANNELS", "1")),
                frame_ms=int(os.getenv("HOST_AUDIO_FRAME_MS", "20")),
                queue_size=int(os.getenv("HOST_AUDIO_QUEUE", "50")),
            )
            speaker_cfg = HostSpeakerConfig(
                device=host_devices.output_device,
                sample_rate=sample_rate,
                channels=int(os.getenv("HOST_AUDIO_CHANNELS", "1")),
                frame_ms=int(os.getenv("HOST_AUDIO_FRAME_MS", "20")),
                max_buffer_ms=int(os.getenv("HOST_AUDIO_MAX_BUFFER_MS", "500")),
                volume=host_devices.volume,
            )
            input_source = HostMicInput(mic_cfg)
            output_sink = HostSpeakerOutput(speaker_cfg)
            livekit_cfg = load_livekit_config(
                room_name=bridge_room,
                identity_default="host-bridge",
                mic_track_name_default="host-mic",
            )

        bridge = LiveKitAudioBridge(
            livekit_cfg,
            input_source=input_source,
            output_sink=output_sink,
        )
        return cls(target=target, bridge=bridge)

    async def start(self) -> None:
        if self._started:
            return
        LOG.info("Starting audio handler (%s)", self._target.value)
        await self._bridge.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        LOG.info("Stopping audio handler (%s)", self._target.value)
        await self._bridge.stop()
        self._started = False


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _resolve_bridge_room(room_name: str | None) -> str | None:
    override = os.getenv("AUDIO_BRIDGE_ROOM")
    if override and override.strip():
        return override.strip()
    if room_name and room_name.lower() in {"console", "mock_room", "console-room"}:
        return None
    return room_name


def _resolve_sample_rate(host_devices) -> int:
    """Robustly resolve the sample rate for the host audio device."""
    if host_devices.sample_rate:
        return int(host_devices.sample_rate)
    
    try:
        import sounddevice as sd  # type: ignore
        
        for device in (host_devices.input_device, host_devices.output_device):
            if device is None:
                continue
            try:
                info = sd.query_devices(device)
                rate = info.get("default_samplerate")
                if rate:
                    return int(rate)
            except Exception:
                continue
        
        default_rate = getattr(sd.default, "samplerate", None)
        if default_rate:
            return int(default_rate)
    except Exception:
        pass
    
    return 48000
