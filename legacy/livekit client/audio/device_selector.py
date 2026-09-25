from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

from .config import _parse_device

LOG = logging.getLogger("audio_devices")

DEFAULT_JABRA_VOLUME = 1
DEFAULT_JABRA_HINTS = ("jabra speak2 55", "jabra speak2", "jabra speak 2 55")


@dataclass(frozen=True)
class HostAudioDevices:
    input_device: int | str | None
    output_device: int | str | None
    volume: float | None
    matched_name: str | None
    sample_rate: int | None


def resolve_host_audio_devices() -> HostAudioDevices:
    input_env = _parse_device(os.getenv("HOST_AUDIO_INPUT_DEVICE"))
    output_env = _parse_device(os.getenv("HOST_AUDIO_OUTPUT_DEVICE"))
    volume_env = _parse_volume(os.getenv("HOST_AUDIO_VOLUME"))

    if input_env is not None or output_env is not None:
        sample_rate = _resolve_samplerate(input_env, output_env)
        return HostAudioDevices(
            input_device=input_env,
            output_device=output_env,
            volume=volume_env,
            matched_name=None,
            sample_rate=sample_rate,
        )

    jabra = _find_jabra_devices()
    if jabra:
        if volume_env is not None:
            jabra = HostAudioDevices(
                input_device=jabra.input_device,
                output_device=jabra.output_device,
                volume=volume_env,
                matched_name=jabra.matched_name,
                sample_rate=jabra.sample_rate,
            )
        LOG.info(
            "Detected Jabra device: %s (input=%s, output=%s)",
            jabra.matched_name,
            jabra.input_device,
            jabra.output_device,
        )
        return jabra

    sample_rate = _resolve_samplerate(None, None)
    return HostAudioDevices(
        input_device=None,
        output_device=None,
        volume=volume_env,
        matched_name=None,
        sample_rate=sample_rate,
    )


def _find_jabra_devices() -> HostAudioDevices | None:
    devices = _query_sound_devices()
    if not devices:
        return None

    hints = _load_jabra_hints()
    both = _first_match(devices, hints, require_input=True, require_output=True)
    if both:
        idx, name = both
        rate = _default_samplerate(devices, idx)
        return HostAudioDevices(
            input_device=idx,
            output_device=idx,
            volume=DEFAULT_JABRA_VOLUME,
            matched_name=name,
            sample_rate=rate,
        )

    input_match = _first_match(devices, hints, require_input=True, require_output=False)
    output_match = _first_match(devices, hints, require_input=False, require_output=True)
    if not input_match and not output_match:
        return None

    input_idx, input_name = input_match or (None, None)
    output_idx, output_name = output_match or (None, None)
    rate = (
        _default_samplerate(devices, input_idx)
        if input_idx is not None
        else _default_samplerate(devices, output_idx)
    )
    return HostAudioDevices(
        input_device=input_idx,
        output_device=output_idx,
        volume=DEFAULT_JABRA_VOLUME,
        matched_name=input_name or output_name,
        sample_rate=rate,
    )


def _query_sound_devices() -> list[dict] | None:
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        LOG.debug("sounddevice not available: %s", exc)
        return None
    try:
        return list(sd.query_devices())
    except Exception as exc:  # pragma: no cover - device-dependent
        LOG.debug("Unable to query audio devices: %s", exc)
        return None


def _load_jabra_hints() -> tuple[str, ...]:
    env_hint = os.getenv("JABRA_DEVICE_NAME")
    if env_hint and env_hint.strip():
        return tuple(item.strip().lower() for item in env_hint.split(",") if item.strip())
    return DEFAULT_JABRA_HINTS


def _first_match(
    devices: Iterable[dict],
    hints: Iterable[str],
    *,
    require_input: bool,
    require_output: bool,
) -> tuple[int, str] | None:
    for idx, dev in enumerate(devices):
        name = str(dev.get("name", ""))
        if not _matches_hints(name, hints):
            continue
        if require_input and int(dev.get("max_input_channels", 0)) <= 0:
            continue
        if require_output and int(dev.get("max_output_channels", 0)) <= 0:
            continue
        return idx, name
    return None


def _matches_hints(name: str, hints: Iterable[str]) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in hints)


def _default_samplerate(devices: Iterable[dict], idx: int | None) -> int | None:
    if idx is None:
        return None
    try:
        device_list = list(devices) if not isinstance(devices, list) else devices
        device = device_list[idx]
    except Exception:
        return None
    rate = device.get("default_samplerate")
    if rate is None:
        return None
    try:
        return int(rate)
    except Exception:
        return None


def _resolve_samplerate(
    input_device: int | str | None,
    output_device: int | str | None,
) -> int | None:
    devices = _query_sound_devices()
    if not devices:
        return None
    idx = _resolve_device_index(devices, input_device)
    if idx is None:
        idx = _resolve_device_index(devices, output_device)
    if idx is None:
        idx = _default_device_index()
    return _default_samplerate(devices, idx)


def _resolve_device_index(devices: Iterable[dict], device: int | str | None) -> int | None:
    if device is None:
        return None
    if isinstance(device, int):
        if device < 0:
            return None
        return device
    needle = str(device).strip().lower()
    if not needle:
        return None
    for idx, info in enumerate(devices):
        name = str(info.get("name", "")).lower()
        if needle in name:
            return idx
    return None


def _default_device_index() -> int | None:
    try:
        import sounddevice as sd  # type: ignore
    except Exception:
        return None
    try:
        default_input, default_output = sd.default.device
    except Exception:
        return None
    for candidate in (default_input, default_output):
        if isinstance(candidate, int) and candidate >= 0:
            return candidate
    return None


def _parse_volume(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value > 1.0:
        value = value / 100.0
    return max(0.0, min(1.0, value))


__all__ = ["HostAudioDevices", "resolve_host_audio_devices"]
