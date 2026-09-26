#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "livekit",
#   "sounddevice",
#   "python-dotenv",
#   "asyncio",
#   "numpy",
# ]
# ///
import base64
import os
import logging
import asyncio
import argparse
import sys
import time
import threading
import json
import math
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import signal
from dataclasses import dataclass
from pathlib import Path

# Ensure repository root is on sys.path when running as a script
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# RNNoise denoiser lives in livekit-client/utils — add that package root
_LIVEKIT_CLIENT_PATH = REPO_ROOT / "livekit-client"
if str(_LIVEKIT_CLIENT_PATH) not in sys.path:
    sys.path.insert(0, str(_LIVEKIT_CLIENT_PATH))
try:
    from utils.denoiser import RnnoiseDenoiser as _RnnoiseDenoiser
    _RNNOISE_IMPORT_OK = True
except Exception:
    _RnnoiseDenoiser = None  # type: ignore
    _RNNOISE_IMPORT_OK = False

from dotenv import load_dotenv
from livekit import rtc
from livekit.rtc import apm
import sounddevice as sd
import numpy as np
from livekit_shared.auth import generate_token
from robot_services.audio.echo_guard import capture_should_be_suppressed

load_dotenv()
# ensure LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET are set in your .env file
LIVEKIT_URL = os.environ.get("LIVEKIT_URL")
ROOM_NAME = os.environ.get("LIVEKIT_ROOM")
DEFAULT_MIC_TRACK_NAME = os.getenv("LIVEKIT_TRACK_NAME", "g1-mic")

SAMPLE_RATE = 48000  # 48kHz to match DC Microphone native rate
NUM_CHANNELS = 1
DEFAULT_INPUT_CAPTURE_CHANNELS = NUM_CHANNELS
DEFAULT_INPUT_MIX_CHANNELS = (0,)
FRAME_SAMPLES = 480  # 10ms at 48kHz - required for APM
BLOCKSIZE = 4800  # 100ms buffer
FRAME_BYTES = FRAME_SAMPLES * NUM_CHANNELS * 2
SILENCE_FRAME_BYTES = bytes(FRAME_BYTES)
INPUT_MIC_GAIN_DB = 0.0
INPUT_MIC_GAIN_DB_MIN = -40.0
INPUT_MIC_GAIN_DB_MAX = 12.0
OUTPUT_SPEAKER_GAIN_DB = 0.0
OUTPUT_SPEAKER_GAIN_DB_MIN = -20.0
OUTPUT_SPEAKER_GAIN_DB_MAX = 12.0
DEFAULT_MAX_OUTPUT_BUFFER_MS = 500
# Pacing for play_pcm_clip(). Chunks must be well under
# DEFAULT_MAX_OUTPUT_BUFFER_MS or the queue trims the start of the clip.
CLIP_CHUNK_S = 0.1
CLIP_TARGET_BACKLOG_S = 0.3
DEFAULT_PLAYBACK_ECHO_TAIL_MS = 500
OUTPUT_BUFFER_TRIM_LOG_INTERVAL_SECONDS = 5.0
BYTES_PER_AUDIO_FRAME = NUM_CHANNELS * 2
INPUT_QUEUE_MAX_BATCHES = 10
OUTPUT_REVERSE_QUEUE_MAX_BATCHES = 10
AUDIO_QUEUE_DROP_LOG_INTERVAL_SECONDS = 5.0
MAX_OUTPUT_BUFFER_BYTES = max(
    BLOCKSIZE * BYTES_PER_AUDIO_FRAME,
    int(SAMPLE_RATE * DEFAULT_MAX_OUTPUT_BUFFER_MS / 1000) * BYTES_PER_AUDIO_FRAME,
)
DEFAULT_REMOTE_AUDIO_IDENTITY_PREFIXES = ("agent-",)
IGNORED_TEXT_STREAM_TOPICS = ("lk.agent.events", "lk.transcription")
IGNORED_BYTE_STREAM_TOPICS = (os.getenv("AGENT_COMMAND_TOPIC", "agent-command"),)


def _db_to_linear_gain(gain_db: float) -> float:
    return 10 ** (gain_db / 20.0)


def _clamp_input_mic_gain_db(gain_db: float) -> float:
    return min(INPUT_MIC_GAIN_DB_MAX, max(INPUT_MIC_GAIN_DB_MIN, gain_db))


def _clamp_output_speaker_gain_db(gain_db: float) -> float:
    return min(OUTPUT_SPEAKER_GAIN_DB_MAX, max(OUTPUT_SPEAKER_GAIN_DB_MIN, gain_db))


def _normalize_input_capture_channels(value: int | str | None) -> int:
    if value is None:
        return DEFAULT_INPUT_CAPTURE_CHANNELS
    if isinstance(value, bool):
        raise ValueError("input_capture_channels must be a positive integer")
    try:
        channels = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("input_capture_channels must be a positive integer") from exc
    if channels < 1:
        raise ValueError("input_capture_channels must be at least 1")
    return channels


def _normalize_input_mix_channels(
    value: list[int | str] | tuple[int | str, ...] | int | str | None,
    capture_channels: int,
) -> tuple[int, ...]:
    if value is None:
        raw_channels: list[int | str] | tuple[int | str, ...] = DEFAULT_INPUT_MIX_CHANNELS
    elif isinstance(value, (list, tuple)):
        raw_channels = value
    else:
        raw_channels = (value,)

    if not raw_channels:
        raise ValueError("input_mix_channels must include at least one channel")

    channels: list[int] = []
    for raw_channel in raw_channels:
        if isinstance(raw_channel, bool):
            raise ValueError("input_mix_channels must contain integer channel indexes")
        try:
            channel = int(raw_channel)
        except (TypeError, ValueError) as exc:
            raise ValueError("input_mix_channels must contain integer channel indexes") from exc
        if channel < 0:
            raise ValueError("input_mix_channels cannot contain negative channel indexes")
        if channel >= capture_channels:
            raise ValueError(
                f"input_mix_channels channel {channel} is outside capture channel range 0-{capture_channels - 1}"
            )
        channels.append(channel)

    return tuple(channels)


def _extract_mono_input_samples(indata: np.ndarray, mix_channels: tuple[int, ...]) -> np.ndarray:
    """Select or average captured input channels into one int16 mono stream."""
    if len(mix_channels) == 1:
        return np.ascontiguousarray(indata[:, mix_channels[0]], dtype=np.int16)

    selected = indata[:, list(mix_channels)].astype(np.float32, copy=False)
    mixed = np.rint(np.mean(selected, axis=1))
    return np.clip(mixed, -32768, 32767).astype(np.int16)


@dataclass(slots=True)
class CaptureAudioBatch:
    pcm: bytes | None
    frame_count: int
    muted: bool
    delay_ms: int


@dataclass(slots=True)
class RenderAudioBatch:
    pcm: bytes
    frame_count: int

def _normalize_device_id(device):
    if isinstance(device, str):
        device = device.strip()
        if device.isdigit():
            return int(device)
    return device

def _parse_key_value_output(raw_text: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in raw_text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties

def _query_udev_audio_properties(card_number: int) -> dict[str, str]:
    if sys.platform == "win32":
        return {}

    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", f"--name=/dev/snd/controlC{card_number}"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return {}

    if result.returncode != 0:
        return {}

    return _parse_key_value_output(result.stdout)

def _query_alsa_playback_devices() -> list[dict]:
    """Query Linux ALSA playback devices for stable identity metadata."""
    if sys.platform == "win32":
        return []

    try:
        result = subprocess.run(
            ["aplay", "-l"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    devices = []
    udev_properties_by_card: dict[int, dict[str, str]] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("card "):
            continue

        try:
            card_prefix, device_prefix = line.split(", device ", 1)
            _, card_body = card_prefix.split("card ", 1)
            card_number_str, card_details = card_body.split(": ", 1)
            card_id, _, card_name_block = card_details.partition(" [")
            device_number_str, device_details = device_prefix.split(": ", 1)
            device_id, _, device_name_block = device_details.partition(" [")
        except ValueError:
            continue

        card_number = int(card_number_str)
        device_number = int(device_number_str)
        udev_properties = udev_properties_by_card.setdefault(
            card_number,
            _query_udev_audio_properties(card_number),
        )

        devices.append({
            "card": card_number,
            "card_id": card_id.strip(),
            "card_name": card_name_block.rstrip("]").strip(),
            "device": device_number,
            "device_id": device_id.strip(),
            "device_name": device_name_block.rstrip("]").strip(),
            "alsa_device": f"plughw:CARD={card_id.strip()},DEV={device_number}",
            "id_path": udev_properties.get("ID_PATH"),
            "id_path_tag": udev_properties.get("ID_PATH_TAG"),
            "serial": udev_properties.get("ID_SERIAL"),
            "serial_short": udev_properties.get("ID_SERIAL_SHORT"),
        })

    return devices

def _parse_proc_asound_cards() -> dict[int, dict[str, str]]:
    cards: dict[int, dict[str, str]] = {}
    try:
        raw_text = Path("/proc/asound/cards").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return cards

    lines = raw_text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or not stripped[0].isdigit() or "[" not in stripped:
            continue
        try:
            card_number_str, remainder = stripped.split(" [", 1)
            card_id = remainder.split("]", 1)[0].strip()
            card_name = stripped.split(": ", 1)[1].strip() if ": " in stripped else card_id
        except Exception:
            continue
        details = lines[index + 1].strip() if index + 1 < len(lines) else ""
        cards[int(card_number_str)] = {
            "card_id": card_id,
            "card_name": card_name,
            "details": details,
        }
    return cards

def _parse_proc_asound_capture_devices() -> list[dict[str, int]]:
    devices: list[dict[str, int]] = []
    try:
        raw_text = Path("/proc/asound/devices").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return devices

    for line in raw_text.splitlines():
        stripped = line.strip()
        if "digital audio capture" not in stripped:
            continue
        try:
            bracketed = stripped.split("[", 1)[1].split("]", 1)[0]
            card_number_str, device_number_str = bracketed.split("-", 1)
            devices.append({
                "card": int(card_number_str.strip()),
                "device": int(device_number_str.strip()),
            })
        except Exception:
            continue
    return devices

def _query_alsa_capture_devices() -> list[dict]:
    """Query Linux ALSA capture devices for stable identity metadata."""
    if sys.platform == "win32":
        return []

    devices = []
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        result = None

    if result is not None and result.returncode == 0:
        udev_properties_by_card: dict[int, dict[str, str]] = {}
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("card "):
                continue

            try:
                card_prefix, device_prefix = line.split(", device ", 1)
                _, card_body = card_prefix.split("card ", 1)
                card_number_str, card_details = card_body.split(": ", 1)
                card_id, _, card_name_block = card_details.partition(" [")
                device_number_str, device_details = device_prefix.split(": ", 1)
                device_id, _, device_name_block = device_details.partition(" [")
            except ValueError:
                continue

            card_number = int(card_number_str)
            device_number = int(device_number_str)
            udev_properties = udev_properties_by_card.setdefault(
                card_number,
                _query_udev_audio_properties(card_number),
            )

            devices.append({
                "card": card_number,
                "card_id": card_id.strip(),
                "card_name": card_name_block.rstrip("]").strip(),
                "device": device_number,
                "device_id": device_id.strip(),
                "device_name": device_name_block.rstrip("]").strip(),
                "alsa_device": f"plughw:CARD={card_id.strip()},DEV={device_number}",
                "id_path": udev_properties.get("ID_PATH"),
                "id_path_tag": udev_properties.get("ID_PATH_TAG"),
                "serial": udev_properties.get("ID_SERIAL"),
                "serial_short": udev_properties.get("ID_SERIAL_SHORT"),
            })

        if devices:
            return devices

    cards = _parse_proc_asound_cards()
    udev_properties_by_card: dict[int, dict[str, str]] = {}
    for capture_device in _parse_proc_asound_capture_devices():
        card_number = capture_device["card"]
        device_number = capture_device["device"]
        card = cards.get(card_number, {})
        card_id = card.get("card_id", str(card_number))
        udev_properties = udev_properties_by_card.setdefault(
            card_number,
            _query_udev_audio_properties(card_number),
        )
        devices.append({
            "card": card_number,
            "card_id": card_id,
            "card_name": card.get("card_name", card_id),
            "device": device_number,
            "device_id": "",
            "device_name": card.get("details", ""),
            "alsa_device": f"plughw:CARD={card_id},DEV={device_number}",
            "id_path": udev_properties.get("ID_PATH"),
            "id_path_tag": udev_properties.get("ID_PATH_TAG"),
            "serial": udev_properties.get("ID_SERIAL"),
            "serial_short": udev_properties.get("ID_SERIAL_SHORT"),
        })

    return devices

def _current_alsa_device_identifiers(alsa_device) -> set[str]:
    identifiers: set[str] = set()
    if not alsa_device:
        return identifiers

    card_id = str(alsa_device.get("card_id", "")).casefold()
    if card_id:
        identifiers.add(card_id)

    card_number = alsa_device.get("card")
    device_number = alsa_device.get("device")
    if isinstance(card_number, int) and isinstance(device_number, int):
        identifiers.add(f"hw:{card_number},{device_number}")

    alsa_name = str(alsa_device.get("alsa_device", "")).casefold()
    if alsa_name:
        identifiers.add(alsa_name)

    id_path = str(alsa_device.get("id_path", "")).casefold()
    if id_path:
        identifiers.add(id_path)

    id_path_tag = str(alsa_device.get("id_path_tag", "")).casefold()
    if id_path_tag:
        identifiers.add(id_path_tag)

    return identifiers

def _sounddevice_match_score(name, alsa_device) -> int:
    normalized_name = name.casefold()
    score = 0
    if not alsa_device:
        return score

    card_number = alsa_device.get("card")
    device_number = alsa_device.get("device")
    if isinstance(card_number, int) and isinstance(device_number, int):
        hw_token = f"hw:{card_number},{device_number}"
        if hw_token in normalized_name:
            score += 100

    alsa_name = str(alsa_device.get("alsa_device", "")).casefold()
    if alsa_name and alsa_name in normalized_name:
        score += 100

    card_id = str(alsa_device.get("card_id", "")).casefold()
    if card_id and card_id in normalized_name:
        score += 10

    card_name = str(alsa_device.get("card_name", "")).casefold()
    if card_name and card_name in normalized_name:
        score += 1

    return score

def _match_sounddevice_to_alsa_device(sounddevice_device, alsa_devices):
    normalized_name = str(sounddevice_device.get("name", "")).casefold()
    scored_candidates = [
        (_sounddevice_match_score(normalized_name, alsa_device), alsa_device)
        for alsa_device in alsa_devices
    ]
    scored_candidates = [candidate for candidate in scored_candidates if candidate[0] > 0]
    if not scored_candidates:
        return None
    scored_candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    if len(scored_candidates) > 1 and scored_candidates[0][0] == scored_candidates[1][0]:
        return None
    return scored_candidates[0][1]

def _find_alsa_device_by_identifier(device, alsa_devices):
    normalized_device = str(device).casefold()
    for alsa_device in alsa_devices:
        if normalized_device in _current_alsa_device_identifiers(alsa_device):
            return alsa_device
    return None

def _annotate_device_with_alsa_metadata(device, alsa_device):
    device.setdefault("alsa_device", alsa_device.get("alsa_device"))
    device.setdefault("alsa_card_id", alsa_device.get("card_id"))
    device.setdefault("alsa_device_index", alsa_device.get("device"))
    device.setdefault("card_name", alsa_device.get("card_name"))
    device.setdefault("device_name", alsa_device.get("device_name"))
    device.setdefault("id_path", alsa_device.get("id_path"))
    device.setdefault("id_path_tag", alsa_device.get("id_path_tag"))
    device.setdefault("serial", alsa_device.get("serial"))
    device.setdefault("serial_short", alsa_device.get("serial_short"))

def _alsa_device_entry(alsa_device, direction: str):
    fallback_name = "ALSA input" if direction == "input" else "ALSA output"
    name = str(alsa_device.get("card_name") or alsa_device.get("card_id") or fallback_name)
    return {
        "index": alsa_device["alsa_device"],
        "name": name,
        "channels": 1 if direction == "input" else 2,
        "sample_rate": SAMPLE_RATE,
        "is_default": False,
        "hostapi": "ALSA",
        "connected": True,
        "alsa_only": True,
        "alsa_device": alsa_device.get("alsa_device"),
        "alsa_card_id": alsa_device.get("card_id"),
        "alsa_device_index": alsa_device.get("device"),
        "card_name": alsa_device.get("card_name"),
        "device_name": alsa_device.get("device_name"),
        "id_path": alsa_device.get("id_path"),
        "id_path_tag": alsa_device.get("id_path_tag"),
        "serial": alsa_device.get("serial"),
        "serial_short": alsa_device.get("serial_short"),
    }

def _augment_input_devices(input_devices):
    """Attach generic ALSA identity metadata and include ALSA-only capture devices."""
    devices = []
    alsa_capture_devices = _query_alsa_capture_devices()
    for input_device in input_devices:
        device = dict(input_device)
        matched_alsa_device = _match_sounddevice_to_alsa_device(device, alsa_capture_devices)
        if matched_alsa_device:
            _annotate_device_with_alsa_metadata(device, matched_alsa_device)
        device.setdefault("connected", True)
        devices.append(device)

    known_alsa_devices = {
        str(device.get("alsa_device"))
        for device in devices
        if device.get("alsa_device")
    }
    for alsa_device in alsa_capture_devices:
        if str(alsa_device.get("alsa_device")) in known_alsa_devices:
            continue
        devices.append(_alsa_device_entry(alsa_device, "input"))
    return devices

def _augment_output_devices(output_devices):
    """Attach generic ALSA identity metadata and include ALSA-only playback devices."""
    devices = []
    alsa_playback_devices = _query_alsa_playback_devices()
    for output_device in output_devices:
        device = dict(output_device)
        matched_alsa_device = _match_sounddevice_to_alsa_device(device, alsa_playback_devices)
        if matched_alsa_device:
            _annotate_device_with_alsa_metadata(device, matched_alsa_device)
        device.setdefault("connected", True)
        devices.append(device)

    known_alsa_devices = {
        str(device.get("alsa_device"))
        for device in devices
        if device.get("alsa_device")
    }
    for alsa_device in alsa_playback_devices:
        if str(alsa_device.get("alsa_device")) in known_alsa_devices:
            continue
        devices.append(_alsa_device_entry(alsa_device, "output"))
    return devices

def _resolve_audio_device(device, direction: str):
    """Resolve a configured PortAudio index/name or generic ALSA identity."""
    device = _normalize_device_id(device)
    if device is None:
        return device

    devices = sd.query_devices()
    channel_key = "max_input_channels" if direction == "input" else "max_output_channels"
    available_devices = [
        (idx, info)
        for idx, info in enumerate(devices)
        if info[channel_key] > 0
    ]
    if isinstance(device, int):
        if 0 <= device < len(devices) and devices[device][channel_key] > 0:
            return device
        available = ", ".join(
            f"{idx}:{info['name']}"
            for idx, info in available_devices
        ) or "<none>"
        raise ValueError(
            f"Configured {direction} device index {device} was not found. "
            f"Available {direction} devices: {available}"
        )

    alsa_devices = (
        _query_alsa_capture_devices()
        if direction == "input"
        else _query_alsa_playback_devices()
    )
    current_alsa_device = _find_alsa_device_by_identifier(device, alsa_devices)
    if current_alsa_device is not None:
        scored_matches = [
            (
                _sounddevice_match_score(str(info["name"]), current_alsa_device),
                idx,
                str(info["name"]),
            )
            for idx, info in available_devices
        ]
        scored_matches = [match for match in scored_matches if match[0] > 0]
        if scored_matches:
            scored_matches.sort(key=lambda item: item[0], reverse=True)
            top_score = scored_matches[0][0]
            top_matches = [match for match in scored_matches if match[0] == top_score]
            if len(top_matches) == 1:
                logging.getLogger(__name__).info(
                    "Resolved ALSA %s '%s' to PortAudio device %s:%s",
                    direction,
                    device,
                    top_matches[0][1],
                    top_matches[0][2],
                )
                return top_matches[0][1]

            raise ValueError(
                "Configured ALSA %s '%s' matched multiple PortAudio devices equally: %s"
                % (
                    direction,
                    device,
                    ", ".join(f"{idx}:{name}" for _, idx, name in top_matches),
                )
            )

        available = ", ".join(
            f"{idx}:{info['name']}"
            for idx, info in available_devices
        ) or "<none>"
        raise ValueError(
            "Configured ALSA %s '%s' resolved to %s, but no matching PortAudio "
            "%s device was available. Available %s devices: %s"
            % (
                direction,
                device,
                current_alsa_device.get("alsa_device"),
                direction,
                direction,
                available,
            )
        )

    exact_matches = [
        idx for idx, info in available_devices
        if info["name"] == device
    ]
    if exact_matches:
        if len(exact_matches) > 1:
            logging.getLogger(__name__).warning(
                "Multiple %s devices matched '%s'; using index %s",
                direction,
                device,
                exact_matches[0],
            )
        return exact_matches[0]

    casefold_matches = [
        idx for idx, info in available_devices
        if info["name"].casefold() == device.casefold()
    ]
    if casefold_matches:
        if len(casefold_matches) > 1:
            logging.getLogger(__name__).warning(
                "Multiple %s devices matched '%s' case-insensitively; using index %s",
                direction,
                device,
                casefold_matches[0],
            )
        return casefold_matches[0]

    available = ", ".join(
        f"{idx}:{info['name']}"
        for idx, info in available_devices
    ) or "<none>"
    raise ValueError(
        f"Configured {direction} device '{device}' was not found. "
        f"Available {direction} devices: {available}"
    )

def _expand_output_devices(output_device):
    if output_device is None:
        return None
    if not isinstance(output_device, (list, tuple)):
        output_device = [output_device]
    expanded = []
    for dev in output_device:
        expanded.append(_normalize_device_id(dev))
    return expanded

def _split_config_list(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value
    return tuple(item.strip() for item in values if item and item.strip())

def _default_remote_audio_identities() -> tuple[str, ...]:
    return _split_config_list(
        os.getenv("AUDIO_BRIDGE_PLAYBACK_IDENTITIES")
        or os.getenv("AUDIO_BRIDGE_AGENT_IDENTITY")
    )

def _default_remote_audio_identity_prefixes() -> tuple[str, ...]:
    return (
        _split_config_list(os.getenv("AUDIO_BRIDGE_PLAYBACK_IDENTITY_PREFIXES"))
        or DEFAULT_REMOTE_AUDIO_IDENTITY_PREFIXES
    )

def _apply_rnnoise(denoiser, frame: rtc.AudioFrame) -> rtc.AudioFrame:
    """Run RNNoise on a 48 kHz int16 AudioFrame and return a denoised copy."""
    raw = bytes(frame.data) if isinstance(frame.data, memoryview) else frame.data
    pcm_i16 = np.frombuffer(raw, dtype=np.int16)
    pcm_f32 = pcm_i16.astype(np.float32) / 32768.0
    cleaned = denoiser.process_48k(pcm_f32)
    cleaned_i16 = (np.clip(cleaned, -1.0, 1.0) * 32767.0).astype(np.int16)
    return rtc.AudioFrame(
        data=cleaned_i16.tobytes(),
        sample_rate=frame.sample_rate,
        num_channels=frame.num_channels,
        samples_per_channel=frame.samples_per_channel,
    )

def list_audio_devices():
    """List all available audio devices for debugging using SoundDevice"""
    print("\n=== AUDIO DEVICES DEBUG (SoundDevice) ===")
    try:
        devices = sd.query_devices()
        print(f"Total devices found: {len(devices)}")
        for i, device in enumerate(devices):
            print(f"Device {i}: {device['name']}")
            print(f"  Channels: in={device['max_input_channels']}, out={device['max_output_channels']}")
            print(f"  Sample rates: {device['default_samplerate']}")
            print(f"  Hostapi: {device['hostapi']}")

        default_in, default_out = sd.default.device
        print(f"\nDefault input device: {default_in}")
        print(f"Default output device: {default_out}")

        if default_in is not None:
            in_info = sd.query_devices(default_in)
            print(f"Default input info: {in_info['name']} - {in_info['max_input_channels']} channels")

        if default_out is not None:
            out_info = sd.query_devices(default_out)
            print(f"Default output info: {out_info['name']} - {out_info['max_output_channels']} channels")

    except Exception as e:
        print(f"Error listing audio devices: {e}")
    print("=== END AUDIO DEVICES ===\n")



class AudioStreamer:
    def __init__(
        self,
        enable_aec: bool = True,
        loop: asyncio.AbstractEventLoop = None,
        enable_rnnoise: bool = False,
        input_capture_channels: int | str | None = None,
        input_mix_channels: list[int | str] | tuple[int | str, ...] | None = None,
        suppress_input_during_playback: bool = False,
        playback_echo_tail_ms: int = DEFAULT_PLAYBACK_ECHO_TAIL_MS,
    ):
        self.enable_aec = enable_aec
        self.enable_rnnoise = enable_rnnoise
        self.running = True
        self.logger = logging.getLogger(__name__)
        self.loop = loop  # Store the event loop reference
        self.input_capture_channels = _normalize_input_capture_channels(input_capture_channels)
        self.input_mix_channels = _normalize_input_mix_channels(
            input_mix_channels,
            self.input_capture_channels,
        )
        self.suppress_input_during_playback = suppress_input_during_playback
        self.playback_echo_tail_ms = max(0, int(playback_echo_tail_ms))
        self.playback_echo_tail_seconds = self.playback_echo_tail_ms / 1000.0
        self.playback_guard_lock = threading.Lock()
        self.last_playback_at = -1.0

        # Mute state
        self.is_muted = False
        self.mute_lock = threading.Lock()
        self.input_gain_lock = threading.Lock()
        self.input_mic_gain_db = _clamp_input_mic_gain_db(INPUT_MIC_GAIN_DB)
        self.input_mic_gain = _db_to_linear_gain(self.input_mic_gain_db)
        self.output_gain_lock = threading.Lock()
        self.output_speaker_gain_db = _clamp_output_speaker_gain_db(OUTPUT_SPEAKER_GAIN_DB)
        self.output_speaker_gain = _db_to_linear_gain(self.output_speaker_gain_db)

        # Debug counters
        self.input_callback_count = 0
        self.output_callback_count = 0
        self.frames_processed = 0
        self.frames_sent_to_livekit = 0
        self.last_debug_time = time.time()

        # Audio I/O streams
        self.input_stream: sd.InputStream | None = None
        self.output_stream: sd.OutputStream | None = None
        self.output_streams: list[sd.OutputStream] = []

        # LiveKit components
        self.source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
        self.room: rtc.Room | None = None
        self.livekit_connected = False
        self.livekit_room_name: str | None = None
        self.ready = False

        # Audio processing
        self.audio_processor: apm.AudioProcessingModule | None = None
        if enable_aec:
            self.logger.info("Initializing Audio Processing Module with Echo Cancellation")
            self.audio_processor = apm.AudioProcessingModule(
                echo_cancellation=True,
                noise_suppression=True,
                high_pass_filter=True,
                auto_gain_control=False
            )

        # RNNoise denoiser — second noise-reduction pass applied before LiveKit publish
        self.rnnoise = None
        if enable_rnnoise and _RNNOISE_IMPORT_OK:
            candidate = _RnnoiseDenoiser()
            if candidate.enabled:
                self.rnnoise = candidate
                self.logger.info("RNNoise denoiser enabled on audio bridge")
            else:
                self.logger.warning("RNNoise loaded but disabled (pyrnnoise missing or failed)")
        elif enable_rnnoise:
            self.logger.warning("RNNoise requested but denoiser import failed")
        else:
            self.logger.info("RNNoise denoiser disabled")

        # Audio buffers and synchronization
        self.output_buffer = bytearray()
        self.output_lock = threading.Lock()
        self.output_buffers: list[bytearray] = []
        self.output_locks: list[threading.Lock] = []
        self.output_buffer_dropped_bytes: list[int] = []
        self.output_buffer_last_trim_log_times: list[float] = []
        self.max_output_buffer_ms = DEFAULT_MAX_OUTPUT_BUFFER_MS
        self.max_output_buffer_bytes = MAX_OUTPUT_BUFFER_BYTES
        self.output_bytes_per_ms = SAMPLE_RATE * BYTES_PER_AUDIO_FRAME / 1000.0
        self.audio_input_queue: asyncio.Queue[CaptureAudioBatch] = asyncio.Queue(
            maxsize=INPUT_QUEUE_MAX_BATCHES
        )
        self.audio_reverse_queue: asyncio.Queue[RenderAudioBatch] = asyncio.Queue(
            maxsize=OUTPUT_REVERSE_QUEUE_MAX_BATCHES
        )
        self.audio_input_queue_dropped_batches = 0
        self.audio_reverse_queue_dropped_batches = 0
        self.audio_input_queue_last_drop_log_time = 0.0
        self.audio_reverse_queue_last_drop_log_time = 0.0

        # Timing and delay tracking for AEC
        self.output_delay = 0.0
        self.input_delay = 0.0

        self.input_device_name = "Microphone"

        # Track participant identity for supervisor status/UI.
        self.participants = {}  # participant_id -> {'name': str}
        self.participants_lock = threading.Lock()

        # Remote playback control. The agent can publish multiple audio tracks
        # (for example speech plus LiveKit background_audio), so mix all
        # subscribed remote publications before writing to the speaker buffers.
        self.active_remote_participant_id: str | None = None
        self.active_remote_publication_id: str | None = None
        self.remote_playback_enabled = True
        self.remote_audio_task: asyncio.Task | None = None
        self.remote_audio_tasks: dict[str, asyncio.Task] = {}
        self.remote_audio_metadata: dict[str, dict[str, str | None]] = {}
        self.remote_audio_buffers: dict[str, bytearray] = {}
        self.remote_audio_buffers_lock = threading.Lock()
        self.remote_audio_mixer_task: asyncio.Task | None = None
        self.remote_audio_participant_identities = set(_default_remote_audio_identities())
        self.remote_audio_participant_prefixes = _default_remote_audio_identity_prefixes()
        self.control_server: ThreadingHTTPServer | None = None
        self.control_server_thread: threading.Thread | None = None
        self.control_port: int | None = None

        # Output stream watchdog
        self._resolved_output_devices: list = []
        self._watchdog_last_callback_count: int = 0
        self._watchdog_last_check_time: float = 0.0
        self._watchdog_output_restart_fails: int = 0
        self._output_slot_active: list[bool] = []

    def configure_remote_audio_subscriptions(
        self,
        *,
        identities: list[str] | tuple[str, ...] | None = None,
        identity_prefixes: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """Configure which remote participants may feed speaker playback."""
        if identities is not None:
            self.remote_audio_participant_identities = set(_split_config_list(identities))
        if identity_prefixes is not None:
            self.remote_audio_participant_prefixes = _split_config_list(identity_prefixes)

        self.logger.info(
            "Remote audio subscription filter: identities=%s prefixes=%s",
            sorted(self.remote_audio_participant_identities) or ["<none>"],
            list(self.remote_audio_participant_prefixes) or ["<none>"],
        )

    def should_subscribe_to_remote_audio(
        self,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> bool:
        """Return True when a remote audio track should be delivered to this bridge."""
        if publication.kind != rtc.TrackKind.KIND_AUDIO:
            return False

        identity = participant.identity or ""
        if not identity:
            return False

        if identity in self.remote_audio_participant_identities:
            return True

        return any(
            identity.startswith(prefix)
            for prefix in self.remote_audio_participant_prefixes
            if prefix
        )

    def update_remote_audio_subscription(
        self,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Apply selective subscription to a single remote publication."""
        should_subscribe = self.should_subscribe_to_remote_audio(publication, participant)
        if publication.kind != rtc.TrackKind.KIND_AUDIO:
            return

        try:
            publication.set_subscribed(should_subscribe)
        except Exception as exc:
            self.logger.warning(
                "Failed to update subscription for %s from %s: %s",
                publication.sid,
                participant.identity,
                exc,
            )
            return

        if should_subscribe:
            self.logger.info(
                "Subscribed to remote audio track %s from %s",
                publication.sid,
                participant.identity,
            )
        else:
            self.logger.info(
                "Skipped remote audio track %s from %s",
                publication.sid,
                participant.identity,
            )

    def start_remote_audio_mixer(self) -> None:
        """Start the remote track mixer that feeds the physical speaker buffers."""
        if self.remote_audio_mixer_task and not self.remote_audio_mixer_task.done():
            return

        self.remote_audio_mixer_task = asyncio.create_task(self._run_remote_audio_mixer())

    async def _run_remote_audio_mixer(self) -> None:
        try:
            while self.running:
                mixed_frame = self._pop_mixed_remote_audio_frame()
                if mixed_frame is None:
                    await asyncio.sleep(0.005)
                    continue

                if self.remote_playback_enabled:
                    self.append_output_audio(self._apply_output_speaker_gain(mixed_frame))
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.error("Remote audio mixer failed: %s", exc)

    def _pop_mixed_remote_audio_frame(self) -> bytes | None:
        """Pop one 10 ms playback frame from active remote track buffers.

        The speech track is the playback clock. Background audio is a separate
        LiveKit track, but it should fill speech silence instead of being summed
        with TTS or emitted as an extra frame.
        """
        def pop_samples(buffer: bytearray) -> np.ndarray | None:
            if not buffer:
                return None

            bytes_to_take = min(len(buffer), FRAME_BYTES)
            bytes_to_take -= bytes_to_take % BYTES_PER_AUDIO_FRAME
            if bytes_to_take <= 0:
                return None

            chunk = bytes(buffer[:bytes_to_take])
            del buffer[:bytes_to_take]
            return np.frombuffer(chunk, dtype=np.int16)

        def is_background_track(publication_id: str) -> bool:
            metadata = self.remote_audio_metadata.get(publication_id) or {}
            track_name = str(metadata.get("track_name") or "").lower()
            return track_name == "background_audio" or "background" in track_name

        def is_active_audio(samples: np.ndarray | None) -> bool:
            if samples is None or samples.size <= 0:
                return False
            return bool(np.max(np.abs(samples.astype(np.int32))) >= 256)

        with self.remote_audio_buffers_lock:
            if not any(self.remote_audio_buffers.values()):
                return None

            foreground_ids = [
                publication_id
                for publication_id in self.remote_audio_buffers
                if not is_background_track(publication_id)
            ]
            background_ids = [
                publication_id
                for publication_id in self.remote_audio_buffers
                if is_background_track(publication_id)
            ]

            foreground_samples: np.ndarray | None = None
            if foreground_ids:
                foreground_samples = pop_samples(self.remote_audio_buffers[foreground_ids[0]])
                if foreground_samples is None:
                    return None

            background_samples: np.ndarray | None = None
            if background_ids:
                background_samples = pop_samples(self.remote_audio_buffers[background_ids[0]])

            # Keep any auxiliary tracks aligned without letting them add gain or
            # create extra playback frames.
            consumed_ids = set(foreground_ids[:1] + background_ids[:1])
            for publication_id, buffer in self.remote_audio_buffers.items():
                if publication_id not in consumed_ids:
                    pop_samples(buffer)

            output_samples = foreground_samples
            if not is_active_audio(foreground_samples) and background_samples is not None:
                output_samples = background_samples
            if output_samples is None:
                return None

            output_frame = np.zeros(FRAME_SAMPLES, dtype=np.int16)
            samples_to_copy = min(output_samples.size, FRAME_SAMPLES)
            output_frame[:samples_to_copy] = output_samples[:samples_to_copy]
            return output_frame.tobytes()

    def append_remote_track_audio(
        self,
        publication_id: str,
        audio_data: bytes | bytearray | memoryview,
    ) -> None:
        """Append decoded remote PCM to a per-publication buffer for mixing."""
        if isinstance(audio_data, memoryview):
            audio_bytes: bytes | bytearray | memoryview = audio_data.cast("B")
        else:
            audio_bytes = audio_data

        with self.remote_audio_buffers_lock:
            buffer = self.remote_audio_buffers.setdefault(publication_id, bytearray())
            buffer.extend(audio_bytes)
            overflow_bytes = len(buffer) - self.max_output_buffer_bytes
            if overflow_bytes > 0:
                del buffer[:overflow_bytes]

    def register_remote_audio_task(
        self,
        *,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
        task: asyncio.Task,
    ) -> None:
        """Track an active remote audio publication being relayed to the mixer."""
        publication_id = publication.sid
        participant_id = participant.sid

        self.remote_audio_tasks[publication_id] = task
        self.remote_audio_metadata[publication_id] = {
            "participant_id": participant_id,
            "participant_identity": participant.identity,
            "publication_id": publication_id,
            "track_name": publication.name,
        }
        with self.remote_audio_buffers_lock:
            self.remote_audio_buffers.setdefault(publication_id, bytearray())

        if self.active_remote_participant_id is None:
            self.active_remote_participant_id = participant_id
            self.active_remote_publication_id = publication_id
        self.remote_audio_task = next(iter(self.remote_audio_tasks.values()), None)

    async def stop_remote_audio_playback(self, *, reason: str | None = None) -> None:
        """Stop all remote track relay tasks and close the mixer."""
        tasks = list(self.remote_audio_tasks.values())
        self.release_remote_playback(reason=reason)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.remote_audio_tasks.clear()
        self.remote_audio_metadata.clear()
        self.remote_audio_task = None
        self.active_remote_participant_id = None
        self.active_remote_publication_id = None

        mixer_task = self.remote_audio_mixer_task
        self.remote_audio_mixer_task = None
        if mixer_task and not mixer_task.done():
            mixer_task.cancel()
            await asyncio.gather(mixer_task, return_exceptions=True)

        with self.remote_audio_buffers_lock:
            self.remote_audio_buffers.clear()
        self.clear_output_buffers()

    def _reset_output_buffer_tracking(self, buffer_count: int) -> None:
        self.output_buffer_dropped_bytes = [0] * buffer_count
        self.output_buffer_last_trim_log_times = [0.0] * buffer_count

    def _trim_output_buffer_locked(self, output_buffer: bytearray, buffer_index: int) -> None:
        overflow_bytes = len(output_buffer) - self.max_output_buffer_bytes
        if overflow_bytes <= 0:
            return

        del output_buffer[:overflow_bytes]

        if buffer_index >= len(self.output_buffer_dropped_bytes):
            return

        self.output_buffer_dropped_bytes[buffer_index] += overflow_bytes
        now = time.time()
        if (
            now - self.output_buffer_last_trim_log_times[buffer_index]
            >= OUTPUT_BUFFER_TRIM_LOG_INTERVAL_SECONDS
        ):
            self.logger.warning(
                "Trimmed %.1f ms of queued audio from output buffer %d; backlog capped at %.1f ms",
                self.output_buffer_dropped_bytes[buffer_index] / self.output_bytes_per_ms,
                buffer_index + 1,
                len(output_buffer) / self.output_bytes_per_ms,
            )
            self.output_buffer_last_trim_log_times[buffer_index] = now

    def release_remote_playback(
        self,
        *,
        participant_id: str | None = None,
        publication_id: str | None = None,
        reason: str | None = None,
        cancel_task: bool = True,
    ) -> None:
        matching_publication_ids = [
            tracked_publication_id
            for tracked_publication_id, metadata in list(self.remote_audio_metadata.items())
            if (
                participant_id is None
                or metadata.get("participant_id") == participant_id
            )
            and (
                publication_id is None
                or tracked_publication_id == publication_id
            )
        ]

        if not matching_publication_ids and self.active_remote_participant_id is None:
            return

        tasks_to_cancel: list[asyncio.Task] = []
        for tracked_publication_id in matching_publication_ids:
            task = self.remote_audio_tasks.pop(tracked_publication_id, None)
            self.remote_audio_metadata.pop(tracked_publication_id, None)
            with self.remote_audio_buffers_lock:
                self.remote_audio_buffers.pop(tracked_publication_id, None)
            if task is not None:
                tasks_to_cancel.append(task)

        if self.remote_audio_metadata:
            next_publication_id, metadata = next(iter(self.remote_audio_metadata.items()))
            self.active_remote_participant_id = metadata.get("participant_id")
            self.active_remote_publication_id = next_publication_id
            self.remote_audio_task = self.remote_audio_tasks.get(next_publication_id)
        else:
            self.remote_audio_task = None
            self.active_remote_participant_id = None
            self.active_remote_publication_id = None
            self.clear_output_buffers()

        if reason:
            if self.remote_audio_metadata:
                self.logger.info("%s; released remote audio track(s)", reason)
            else:
                self.logger.info("%s; releasing remote playback and clearing output buffers", reason)

        if cancel_task:
            for task_to_cancel in tasks_to_cancel:
                if not task_to_cancel.done():
                    task_to_cancel.cancel()

    def clear_remote_playback_buffers(self) -> None:
        """Drop queued speaker audio while keeping subscribed LiveKit tracks alive."""
        with self.remote_audio_buffers_lock:
            for buffer in self.remote_audio_buffers.values():
                buffer.clear()
        self.clear_output_buffers()
        self.logger.info("Cleared queued remote playback audio")

    def start_audio_devices(self):
        """Initialize and start audio input/output devices"""
        try:
            self.logger.info("Starting audio devices...")

            # Use configured devices or fall back to system defaults
            default_input, default_output = sd.default.device
            configured_input = getattr(self, '_input_device', None)
            input_device = (
                _resolve_audio_device(configured_input, "input")
                if configured_input is not None
                else default_input
            )
            output_devices = _expand_output_devices(getattr(self, '_output_device', None))
            if output_devices is None:
                output_devices = [default_output] if default_output is not None else []
            output_devices = [
                _resolve_audio_device(dev, "output")
                for dev in output_devices
                if dev is not None
            ]
            if len(output_devices) != len(set(output_devices)):
                raise RuntimeError(
                    "Resolved combined output devices are not unique: %s"
                    % output_devices
                )

            if not output_devices:
                raise RuntimeError("No audio output device available")

            self.logger.info(f"Using input device: {input_device}, output device(s): {output_devices}")

            if input_device is not None:
                device_info = sd.query_devices(input_device)
                if isinstance(device_info, dict):
                    self.input_device_name = device_info.get("name", "Microphone")
                    self.logger.info(f"Input device info: {device_info}")

                    # Check if device supports our requested capture shape.
                    if device_info['max_input_channels'] < self.input_capture_channels:
                        raise RuntimeError(
                            "Input device only has "
                            f"{device_info['max_input_channels']} channels, "
                            f"need {self.input_capture_channels}"
                        )

            self.logger.info(
                "Creating input stream: rate=%d, channels=%d, mix_channels=%s, blocksize=%d",
                SAMPLE_RATE,
                self.input_capture_channels,
                list(self.input_mix_channels),
                BLOCKSIZE,
            )

            # Start input stream
            self.input_stream = sd.InputStream(
                callback=self._input_callback,
                dtype="int16",
                channels=self.input_capture_channels,
                device=input_device,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCKSIZE,
            )
            self.input_stream.start()
            self.logger.info(f"Started audio input: {self.input_device_name}")

            # Start output stream(s)
            self._resolved_output_devices = output_devices
            self.output_streams = []
            self.output_buffers = []
            self.output_locks = []
            self._output_slot_active = []
            self._reset_output_buffer_tracking(len(output_devices))
            for idx, device in enumerate(output_devices):
                self.output_buffers.append(bytearray())
                self.output_locks.append(threading.Lock())
                try:
                    stream = sd.OutputStream(
                        callback=lambda outdata, frame_count, time_info, status, buffer_index=idx: self._output_callback(
                            outdata, frame_count, time_info, status, buffer_index
                        ),
                        dtype="int16",
                        channels=NUM_CHANNELS,
                        device=device,
                        samplerate=SAMPLE_RATE,
                        blocksize=BLOCKSIZE,
                    )
                    stream.start()
                    self.output_streams.append(stream)
                    self._output_slot_active.append(True)
                    if idx == 0:
                        self.output_stream = stream
                        self.output_buffer = self.output_buffers[0]
                        self.output_lock = self.output_locks[0]
                except Exception as exc:
                    self.logger.warning("Output slot %d (%s) failed to open at start: %s", idx, device, exc)
                    self.output_streams.append(None)
                    self._output_slot_active.append(False)
            self.logger.info("Started audio output streams")

            # Test if streams are active
            time.sleep(0.1)  # Give streams time to start
            self.logger.info(f"Input stream active: {self.input_stream.active}")
            output_states = [stream.active for stream in self.output_streams]
            self.logger.info(f"Output streams active: {output_states}")

        except Exception as e:
            self.logger.error(f"Failed to start audio devices: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    def stop_audio_devices(self):
        """Stop and cleanup audio devices"""
        self.logger.info("Stopping audio devices...")

        if self.input_stream:
            try:
                self.input_stream.stop()
            except Exception as exc:
                self.logger.warning("Failed to stop input stream cleanly: %s", exc)
            try:
                self.input_stream.close()
            except Exception as exc:
                self.logger.warning("Failed to close input stream cleanly: %s", exc)
            self.input_stream = None
            self.logger.info("Stopped input stream")

        if self.output_streams:
            for stream in self.output_streams:
                try:
                    stream.stop()
                except Exception as exc:
                    self.logger.warning("Failed to stop output stream cleanly: %s", exc)
                try:
                    stream.close()
                except Exception as exc:
                    self.logger.warning("Failed to close output stream cleanly: %s", exc)
            self.output_streams = []
            self.output_buffers = []
            self.output_locks = []
            self._output_slot_active = []
            self._reset_output_buffer_tracking(0)
            self.output_stream = None
            self.logger.info("Stopped output streams")
        elif self.output_stream:
            try:
                self.output_stream.stop()
            except Exception as exc:
                self.logger.warning("Failed to stop output stream cleanly: %s", exc)
            try:
                self.output_stream.close()
            except Exception as exc:
                self.logger.warning("Failed to close output stream cleanly: %s", exc)
            self.output_stream = None
            self._output_slot_active = []
            self._reset_output_buffer_tracking(0)
            self.logger.info("Stopped output stream")

        self.logger.info("Audio devices stopped")

    def restart_output_streams(self) -> bool:
        """Stop and reopen only the output streams, leaving input and LiveKit untouched."""
        self.logger.warning("Restarting output streams due to stall detection")
        devices = self._resolved_output_devices
        if not devices:
            self.logger.error("Cannot restart output streams: no resolved device list")
            return False

        # Stop existing output streams
        for stream in self.output_streams:
            try:
                stream.stop()
            except Exception as exc:
                self.logger.debug("Error stopping output stream: %s", exc)
            try:
                stream.close()
            except Exception as exc:
                self.logger.debug("Error closing output stream: %s", exc)

        self.output_streams = []
        self.output_buffers = []
        self.output_locks = []
        self._output_slot_active = []
        self.output_stream = None
        self._reset_output_buffer_tracking(len(devices))

        self._output_slot_active = []
        successes = 0
        for idx, device in enumerate(devices):
            self.output_buffers.append(bytearray())
            self.output_locks.append(threading.Lock())
            try:
                resolved = _resolve_audio_device(device, "output")
                stream = sd.OutputStream(
                    callback=lambda outdata, frame_count, time_info, status, buffer_index=idx: self._output_callback(
                        outdata, frame_count, time_info, status, buffer_index
                    ),
                    dtype="int16",
                    channels=NUM_CHANNELS,
                    device=resolved,
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCKSIZE,
                )
                stream.start()
                self.output_streams.append(stream)
                self._output_slot_active.append(True)
                if idx == 0:
                    self.output_stream = stream
                    self.output_buffer = self.output_buffers[0]
                    self.output_lock = self.output_locks[0]
                successes += 1
            except Exception as exc:
                self.logger.warning("Output slot %d (%s) unavailable during restart: %s", idx, device, exc)
                self.output_streams.append(None)
                self._output_slot_active.append(False)

        self._watchdog_last_callback_count = self.output_callback_count
        self._watchdog_last_check_time = time.monotonic()
        self.logger.info(
            "Output streams restarted: %d/%d slot(s) active", successes, len(devices)
        )
        return successes > 0

    def _full_audio_restart(self) -> bool:
        """Re-initialize PortAudio and reopen both input and output streams.
        Used when output-only restart fails (e.g. ALSA card renumbered after disconnect)."""
        self.logger.warning("Performing full PortAudio restart to re-enumerate audio devices")

        # Close input stream
        if self.input_stream is not None:
            try:
                self.input_stream.stop()
            except Exception:
                pass
            try:
                self.input_stream.close()
            except Exception:
                pass
            self.input_stream = None

        # Close output streams
        for stream in self.output_streams:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self.output_streams = []
        self.output_stream = None
        self.output_buffers = []
        self.output_locks = []
        self._reset_output_buffer_tracking(0)

        # Force PortAudio to re-enumerate audio devices so stale card indices are refreshed
        try:
            sd._terminate()
        except Exception as exc:
            self.logger.debug("sd._terminate error: %s", exc)
        try:
            sd._initialize()
        except Exception as exc:
            self.logger.warning("sd._initialize error: %s", exc)

        try:
            self.start_audio_devices()
            self.logger.info("Full audio restart succeeded")
            return True
        except Exception as exc:
            self.logger.error("Full audio restart failed: %s", exc)
            return False

    def append_output_audio(self, audio_data: bytes | bytearray | memoryview) -> None:
        """Append audio data to all output buffers."""
        if isinstance(audio_data, memoryview):
            audio_bytes: bytes | bytearray | memoryview = audio_data.cast("B")
        else:
            audio_bytes = audio_data

        if audio_bytes:
            self._mark_playback_active()

        if self.output_buffers:
            for idx, buffer in enumerate(self.output_buffers):
                if idx < len(self._output_slot_active) and not self._output_slot_active[idx]:
                    continue
                with self.output_locks[idx]:
                    buffer.extend(audio_bytes)
                    self._trim_output_buffer_locked(buffer, idx)
            return

        with self.output_lock:
            self.output_buffer.extend(audio_bytes)
            self._trim_output_buffer_locked(self.output_buffer, 0)

    def clear_output_buffers(self) -> None:
        """Clear all output buffers."""
        if self.output_buffers:
            for idx, buffer in enumerate(self.output_buffers):
                with self.output_locks[idx]:
                    buffer.clear()
            return

        with self.output_lock:
            self.output_buffer.clear()

    def play_pcm_clip(self, pcm: bytes) -> float:
        """Play a complete PCM clip through the speakers, paced in real time.

        WHY THIS IS NOT JUST append_output_audio(pcm):
        the output buffers are a REAL-TIME queue capped at
        DEFAULT_MAX_OUTPUT_BUFFER_MS (500 ms), and _trim_output_buffer_locked drops
        the OLDEST bytes on overflow. Handing it a whole 2 s sentence therefore
        throws away the first 1.5 s and plays only the tail — measured exactly that
        way on 2026-09-10 ("Trimmed 1496.9 ms of queued audio").

        So a feeder thread hands over one chunk at a time and sleeps, keeping the
        backlog around CLIP_TARGET_BACKLOG_S — enough to ride out scheduling jitter,
        comfortably under the cap. Returns the clip's real duration in seconds.

        Only one clip plays at a time: starting a new one stops the previous feeder,
        which is the behaviour you want for barge-in.
        """
        bytes_per_second = SAMPLE_RATE * NUM_CHANNELS * 2
        duration = len(pcm) / float(bytes_per_second)
        self.stop_pcm_clip()

        stop_event = threading.Event()
        self._pcm_clip_stop = stop_event

        def feeder() -> None:
            chunk = int(bytes_per_second * CLIP_CHUNK_S)
            chunk -= chunk % (NUM_CHANNELS * 2)          # keep whole samples
            pos = 0
            next_due = time.monotonic()
            while pos < len(pcm) and not stop_event.is_set():
                self.append_output_audio(pcm[pos:pos + chunk])
                pos += chunk
                # Stay ~CLIP_TARGET_BACKLOG_S ahead of real time, no further.
                next_due += CLIP_CHUNK_S
                delay = next_due - time.monotonic()
                if delay > 0:
                    stop_event.wait(delay)
            self.logger.debug("PCM clip feeder finished (%d/%d bytes)", pos, len(pcm))

        thread = threading.Thread(target=feeder, name="pcm-clip-feeder", daemon=True)
        self._pcm_clip_thread = thread
        thread.start()
        self.logger.info("Playing PCM clip: %.2fs (%d bytes)", duration, len(pcm))
        return duration

    def stop_pcm_clip(self) -> None:
        """Stop any clip being fed and drop what is already queued."""
        stop_event = getattr(self, "_pcm_clip_stop", None)
        if stop_event is not None:
            stop_event.set()
        thread = getattr(self, "_pcm_clip_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._pcm_clip_stop = None
        self._pcm_clip_thread = None
        self.clear_output_buffers()

    def _try_reconnect_output_slot(self, idx: int) -> bool:
        """Attempt to reopen a single disconnected output slot without touching other slots."""
        if idx >= len(self._resolved_output_devices):
            return False
        device = self._resolved_output_devices[idx]
        try:
            resolved = _resolve_audio_device(device, "output")
        except Exception:
            return False
        try:
            stream = sd.OutputStream(
                callback=lambda outdata, frame_count, time_info, status, buffer_index=idx: self._output_callback(
                    outdata, frame_count, time_info, status, buffer_index
                ),
                dtype="int16",
                channels=NUM_CHANNELS,
                device=resolved,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCKSIZE,
            )
            stream.start()
            if idx < len(self.output_buffers):
                with self.output_locks[idx]:
                    self.output_buffers[idx].clear()
            else:
                self.output_buffers.append(bytearray())
                self.output_locks.append(threading.Lock())
            self.output_streams[idx] = stream
            self._output_slot_active[idx] = True
            if idx == 0:
                self.output_stream = stream
                self.output_buffer = self.output_buffers[0]
                self.output_lock = self.output_locks[0]
            self.logger.info("Reconnected output slot %d (%s)", idx, device)
            return True
        except Exception as exc:
            self.logger.debug("Output slot %d reconnect attempt failed: %s", idx, exc)
            return False

    def _check_output_slot_health(self) -> None:
        """Detect stopped output streams and attempt per-slot reconnect. Called from watchdog."""
        for idx, stream in enumerate(self.output_streams):
            if stream is not None and not stream.active:
                self.logger.warning(
                    "Output slot %d stopped unexpectedly; closing and marking for reconnect", idx
                )
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
                self.output_streams[idx] = None
                if idx < len(self._output_slot_active):
                    self._output_slot_active[idx] = False
                if idx < len(self.output_buffers):
                    with self.output_locks[idx]:
                        self.output_buffers[idx].clear()
            elif stream is None and idx < len(self._output_slot_active) and not self._output_slot_active[idx]:
                self._try_reconnect_output_slot(idx)

    def _new_audio_frame(self) -> rtc.AudioFrame:
        """Create an empty 10 ms mono audio frame."""
        return rtc.AudioFrame.create(
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            samples_per_channel=FRAME_SAMPLES,
        )

    def _copy_chunk_into_frame(self, frame: rtc.AudioFrame, chunk: np.ndarray) -> None:
        """Copy an int16 chunk into an AudioFrame without an intermediate bytes allocation."""
        np.frombuffer(frame.data, dtype=np.int16, count=FRAME_SAMPLES)[:] = chunk

    def _zero_audio_frame(self, frame: rtc.AudioFrame) -> None:
        """Fill an AudioFrame with silence."""
        frame.data.cast("B")[:] = SILENCE_FRAME_BYTES

    def _log_input_queue_drop(self) -> None:
        self.audio_input_queue_dropped_batches += 1
        now = time.monotonic()
        if now - self.audio_input_queue_last_drop_log_time < AUDIO_QUEUE_DROP_LOG_INTERVAL_SECONDS:
            return

        self.logger.warning(
            "Audio input queue full; dropped %d capture batch(es)",
            self.audio_input_queue_dropped_batches,
        )
        self.audio_input_queue_dropped_batches = 0
        self.audio_input_queue_last_drop_log_time = now

    def _log_reverse_queue_drop(self) -> None:
        self.audio_reverse_queue_dropped_batches += 1
        now = time.monotonic()
        if now - self.audio_reverse_queue_last_drop_log_time < AUDIO_QUEUE_DROP_LOG_INTERVAL_SECONDS:
            return

        self.logger.warning(
            "Audio reverse queue full; dropped %d render batch(es)",
            self.audio_reverse_queue_dropped_batches,
        )
        self.audio_reverse_queue_dropped_batches = 0
        self.audio_reverse_queue_last_drop_log_time = now

    def toggle_mute(self):
        """Toggle microphone mute state"""
        with self.mute_lock:
            self.is_muted = not self.is_muted
            status = "MUTED" if self.is_muted else "LIVE"
            self.logger.info(f"Microphone {status}")

    def set_muted(self, muted: bool):
        """Explicitly set microphone mute state."""
        with self.mute_lock:
            self.is_muted = muted
            status = "MUTED" if self.is_muted else "LIVE"
            self.logger.info(f"Microphone {status}")

    def set_input_mic_gain_db(self, gain_db: float) -> float:
        """Set microphone gain in dB for live input publishing."""
        if not math.isfinite(gain_db):
            raise ValueError("input_mic_gain_db must be finite")

        gain_db = _clamp_input_mic_gain_db(gain_db)
        with self.input_gain_lock:
            self.input_mic_gain_db = gain_db
            self.input_mic_gain = _db_to_linear_gain(gain_db)
        self.logger.info("Input microphone gain set to %.1f dB", gain_db)
        return gain_db

    def set_output_speaker_gain_db(self, gain_db: float) -> float:
        """Apply a bounded digital gain to remote speaker playback."""
        if not math.isfinite(gain_db):
            raise ValueError("output_speaker_gain_db must be finite")
        gain_db = _clamp_output_speaker_gain_db(gain_db)
        with self.output_gain_lock:
            self.output_speaker_gain_db = gain_db
            self.output_speaker_gain = _db_to_linear_gain(gain_db)
        self.logger.info("Output speaker gain set to %.1f dB", gain_db)
        return gain_db

    def _apply_output_speaker_gain(self, pcm: bytes) -> bytes:
        with self.output_gain_lock:
            gain = self.output_speaker_gain
        if abs(gain - 1.0) < 1e-6:
            return pcm
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        return np.clip(samples * gain, -32768, 32767).astype(np.int16).tobytes()

    def _mark_playback_active(self) -> None:
        with self.playback_guard_lock:
            self.last_playback_at = time.monotonic()

    def is_playback_echo_guard_active(self) -> bool:
        with self.playback_guard_lock:
            last_playback_at = self.last_playback_at
        return capture_should_be_suppressed(
            enabled=self.suppress_input_during_playback,
            now=time.monotonic(),
            last_playback_at=last_playback_at,
            tail_seconds=self.playback_echo_tail_seconds,
        )

    def get_input_mic_gain(self) -> tuple[float, float]:
        """Return current microphone gain as (dB, linear multiplier)."""
        with self.input_gain_lock:
            return self.input_mic_gain_db, self.input_mic_gain

    def get_status(self) -> dict:
        """Return current bridge status for supervisor control."""
        with self.mute_lock:
            muted = self.is_muted
        input_mic_gain_db, input_mic_gain = self.get_input_mic_gain()
        with self.output_gain_lock:
            output_speaker_gain_db = self.output_speaker_gain_db
        active_remote_participant_id = self.active_remote_participant_id
        active_remote_participant_identity = None
        if active_remote_participant_id:
            with self.participants_lock:
                participant = self.participants.get(active_remote_participant_id)
                if participant:
                    active_remote_participant_identity = participant.get("name")
        active_remote_tracks = list(self.remote_audio_metadata.values())

        return {
            "running": self.running,
            "ready": self.ready,
            "livekit_connected": self.livekit_connected,
            "livekit_room": self.livekit_room_name,
            "muted": muted,
            "input_mic_gain_db": input_mic_gain_db,
            "input_mic_gain": input_mic_gain,
            "input_mic_gain_db_min": INPUT_MIC_GAIN_DB_MIN,
            "input_mic_gain_db_max": INPUT_MIC_GAIN_DB_MAX,
            "output_speaker_gain_db": output_speaker_gain_db,
            "output_speaker_gain_db_min": OUTPUT_SPEAKER_GAIN_DB_MIN,
            "output_speaker_gain_db_max": OUTPUT_SPEAKER_GAIN_DB_MAX,
            "input_capture_channels": self.input_capture_channels,
            "input_mix_channels": list(self.input_mix_channels),
            "suppress_input_during_playback": self.suppress_input_during_playback,
            "playback_echo_tail_ms": self.playback_echo_tail_ms,
            "playback_echo_guard_active": self.is_playback_echo_guard_active(),
            "control_port": self.control_port,
            "control_available": self.control_server is not None,
            "active_remote_participant_id": active_remote_participant_id,
            "active_remote_participant_identity": active_remote_participant_identity,
            "active_remote_track_count": len(active_remote_tracks),
            "active_remote_tracks": active_remote_tracks,
        }

    def start_control_server(self, port: int):
        """Expose a tiny localhost API for supervisor mute control."""
        if self.control_server:
            return

        streamer = self

        class ControlHandler(BaseHTTPRequestHandler):
            def _send_json(self, status_code: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path != "/status":
                    self._send_json(404, {"error": "Not found"})
                    return
                self._send_json(200, streamer.get_status())

            def do_POST(self):
                if self.path not in ("/mute", "/input-gain", "/output-gain",
                                     "/remote-playback/release", "/play-pcm",
                                     "/play-pcm/stop"):
                    self._send_json(404, {"error": "Not found"})
                    return

                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0

                try:
                    raw_body = self.rfile.read(content_length) if content_length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "Invalid JSON"})
                    return

                if self.path == "/remote-playback/release":
                    streamer.clear_remote_playback_buffers()
                    self._send_json(200, streamer.get_status())
                    return

                if self.path == "/play-pcm/stop":
                    # Drop queued speaker audio only. Deliberately NOT
                    # /remote-playback/release, which also clears the subscribed
                    # LiveKit track buffers and would cut the voice agent off
                    # mid-sentence; this is just "stop the clip we queued".
                    streamer.stop_pcm_clip()
                    self._send_json(200, {"stopped": True})
                    return

                if self.path == "/play-pcm":
                    # Speak arbitrary audio through the robot's speakers.
                    #
                    # Added for the supervisor's Navigation missions, whose `speak`
                    # action synthesizes with the team's own TTS (ElevenLabs) rather
                    # than AgiBot's built-in TTSService — that one lives in the
                    # `agent` app, which this bridge stops in order to own the audio
                    # devices, so it is never available while we are running.
                    #
                    # Routing through append_output_audio (rather than aplay to the
                    # ALSA device) matters for two reasons: this bridge holds the
                    # speaker, and this path is also the AEC render reference, so the
                    # microphone does not transcribe the robot's own voice.
                    #
                    # Body: {"pcm_base64": "<48kHz mono signed-16-bit LE>"}
                    b64 = payload.get("pcm_base64")
                    if not isinstance(b64, str) or not b64:
                        self._send_json(400, {"error": "'pcm_base64' must be a non-empty string"})
                        return
                    try:
                        pcm = base64.b64decode(b64, validate=True)
                    except Exception:
                        self._send_json(400, {"error": "'pcm_base64' is not valid base64"})
                        return
                    bytes_per_sample = NUM_CHANNELS * 2
                    if len(pcm) % bytes_per_sample:
                        self._send_json(
                            400,
                            {"error": f"PCM length {len(pcm)} is not a whole number of "
                                      f"{SAMPLE_RATE} Hz / {NUM_CHANNELS}ch / 16-bit frames"})
                        return
                    seconds = streamer.play_pcm_clip(pcm)
                    self._send_json(200, {"queued_bytes": len(pcm),
                                          "seconds": round(seconds, 3),
                                          "sample_rate": SAMPLE_RATE,
                                          "channels": NUM_CHANNELS})
                    return

                if self.path == "/input-gain":
                    gain_db = payload.get("input_mic_gain_db")
                    if not isinstance(gain_db, (int, float)) or isinstance(gain_db, bool):
                        self._send_json(400, {"error": "'input_mic_gain_db' must be a number"})
                        return

                    try:
                        streamer.set_input_mic_gain_db(float(gain_db))
                    except ValueError as exc:
                        self._send_json(400, {"error": str(exc)})
                        return

                    self._send_json(200, streamer.get_status())
                    return

                if self.path == "/output-gain":
                    gain_db = payload.get("output_speaker_gain_db")
                    if not isinstance(gain_db, (int, float)) or isinstance(gain_db, bool):
                        self._send_json(400, {"error": "'output_speaker_gain_db' must be a number"})
                        return
                    try:
                        streamer.set_output_speaker_gain_db(float(gain_db))
                    except ValueError as exc:
                        self._send_json(400, {"error": str(exc)})
                        return
                    self._send_json(200, streamer.get_status())
                    return

                muted = payload.get("muted")
                if not isinstance(muted, bool):
                    self._send_json(400, {"error": "'muted' must be a boolean"})
                    return

                streamer.set_muted(muted)
                self._send_json(200, streamer.get_status())

            def log_message(self, format, *args):
                streamer.logger.debug("Control API: " + format, *args)

        self.control_server = ThreadingHTTPServer(("127.0.0.1", port), ControlHandler)
        self.control_server_thread = threading.Thread(
            target=self.control_server.serve_forever,
            name="audio-bridge-control",
            daemon=True,
        )
        self.control_port = port
        self.control_server_thread.start()
        self.logger.info("Started audio bridge control API on 127.0.0.1:%d", port)

    def stop_control_server(self):
        """Stop the localhost control API."""
        if self.control_server:
            self.control_server.shutdown()
            self.control_server.server_close()
            self.control_server = None
            self.logger.info("Stopped audio bridge control API")
        self.control_server_thread = None
        self.control_port = None

    def _input_callback(self, indata: np.ndarray, frame_count: int, time_info, status) -> None:
        """Sounddevice input callback - processes microphone audio"""
        self.input_callback_count += 1

        if self.logger.isEnabledFor(logging.DEBUG):
            current_time = time.time()
            if current_time - self.last_debug_time > 30.0:
                self.logger.debug(
                    "Input callback stats: called %d times, processed %d frames, sent %d to LiveKit",
                    self.input_callback_count,
                    self.frames_processed,
                    self.frames_sent_to_livekit,
                )
                self.last_debug_time = current_time

        if status:
            self.logger.warning(f"Input callback status: {status}")

        if not self.running:
            self.logger.debug("Input callback: not running, returning")
            return

        if self.logger.isEnabledFor(logging.DEBUG) and self.input_callback_count <= 5:
            self.logger.debug(
                "Input callback #%d: frame_count=%d indata.shape=%s indata.dtype=%s",
                self.input_callback_count,
                frame_count,
                indata.shape,
                indata.dtype,
            )

        # Snapshot mute state once; only copy microphone data when publishing live audio.
        with self.mute_lock:
            is_muted = self.is_muted
        echo_guard_active = self.is_playback_echo_guard_active()
        effective_muted = is_muted or echo_guard_active

        # Calculate delays for AEC
        self.input_delay = time_info.currentTime - time_info.inputBufferAdcTime
        total_delay = self.output_delay + self.input_delay

        if frame_count < FRAME_SAMPLES:
            return

        pcm = None
        if not effective_muted:
            mono_samples = _extract_mono_input_samples(
                indata[:frame_count],
                self.input_mix_channels,
            )
            pcm = mono_samples.tobytes()
        batch = CaptureAudioBatch(
            pcm=pcm,
            frame_count=frame_count,
            muted=effective_muted,
            delay_ms=int(total_delay * 1000),
        )

        if self.loop and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self._enqueue_capture_batch, batch)

                if self.logger.isEnabledFor(logging.DEBUG) and self.input_callback_count <= 3:
                    self.logger.debug("Queued %d sample(s) to audio processing task", frame_count)
            except Exception as e:
                if self.input_callback_count <= 10:
                    self.logger.warning(f"Failed to queue capture batch: {e}")
        else:
            if self.input_callback_count <= 5:
                self.logger.error("No valid event loop available for queuing capture audio")

    def _enqueue_capture_batch(self, batch: CaptureAudioBatch) -> None:
        """Move callback-produced microphone samples onto the asyncio queue."""
        try:
            self.audio_input_queue.put_nowait(batch)
        except asyncio.QueueFull:
            self._log_input_queue_drop()

    def _enqueue_reverse_batch(self, batch: RenderAudioBatch) -> None:
        """Move callback-produced speaker samples onto the asyncio reverse queue."""
        try:
            self.audio_reverse_queue.put_nowait(batch)
        except asyncio.QueueFull:
            self._log_reverse_queue_drop()

    def _output_callback(self, outdata: np.ndarray, frame_count: int, time_info, status, buffer_index: int = 0) -> None:
        """Sounddevice output callback - plays received audio"""
        self.output_callback_count += 1

        if status:
            self.logger.warning(f"Output callback status: {status}")

        if self.logger.isEnabledFor(logging.DEBUG) and self.output_callback_count <= 3:
            buffer_size = len(self.output_buffers[buffer_index]) if self.output_buffers else len(self.output_buffer)
            self.logger.debug(
                "Output callback #%d (buffer %d): frame_count=%d, buffer_size=%d",
                self.output_callback_count,
                buffer_index,
                frame_count,
                buffer_size,
            )

        if not self.running:
            outdata.fill(0)
            return

        # Update output delay for AEC (primary output only)
        if buffer_index == 0:
            self.output_delay = time_info.outputBufferDacTime - time_info.currentTime

        # Fill output buffer from received audio
        if self.output_buffers:
            if buffer_index >= len(self.output_buffers):
                outdata.fill(0)
                return
            output_buffer = self.output_buffers[buffer_index]
            output_lock = self.output_locks[buffer_index]
        else:
            output_buffer = self.output_buffer
            output_lock = self.output_lock

        played_audio = False
        with output_lock:
            bytes_needed = frame_count * 2  # 2 bytes per int16 sample
            if len(output_buffer) < bytes_needed:
                # Not enough data, fill what we have and zero the rest
                available_bytes = len(output_buffer)
                if available_bytes > 0:
                    played_audio = True
                    outdata[:available_bytes // 2, 0] = np.frombuffer(
                        output_buffer[:available_bytes],
                        dtype=np.int16,
                        count=available_bytes // 2,
                    )
                    outdata[available_bytes // 2:, 0] = 0
                    del output_buffer[:available_bytes]
                else:
                    outdata.fill(0)
            else:
                # Enough data available
                played_audio = True
                chunk = output_buffer[:bytes_needed]
                outdata[:, 0] = np.frombuffer(chunk, dtype=np.int16, count=frame_count)
                del output_buffer[:bytes_needed]

        if (
            self.audio_processor
            and buffer_index == 0
            and played_audio
            and self.loop
            and not self.loop.is_closed()
        ):
            try:
                render_batch = RenderAudioBatch(
                    pcm=outdata[:frame_count, 0].tobytes(),
                    frame_count=frame_count,
                )
                self.loop.call_soon_threadsafe(self._enqueue_reverse_batch, render_batch)
            except Exception as e:
                if self.output_callback_count <= 10:
                    self.logger.warning(f"Failed to queue reverse audio batch: {e}")

        if buffer_index == 0 and played_audio:
            self._mark_playback_active()


async def main(
    participant_name: str,
    enable_aec: bool = True,
    enable_rnnoise: bool = False,
    input_device=None,
    output_device=None,
    input_capture_channels: int | str | None = None,
    input_mix_channels: list[int | str] | tuple[int | str, ...] | None = None,
    control_port: int = 8766,
    mic_track_name: str = DEFAULT_MIC_TRACK_NAME,
    playback_identities: list[str] | None = None,
    playback_identity_prefixes: list[str] | None = None,
    suppress_input_during_playback: bool = False,
    playback_echo_tail_ms: int = DEFAULT_PLAYBACK_ECHO_TAIL_MS,
):
    logger = logging.getLogger(__name__)
    logger.info("=== STARTING AUDIO STREAMER ===")
    exit_code = 0

    # Get the running event loop
    loop = asyncio.get_running_loop()

    # Verify environment
    logger.info(f"LIVEKIT_URL: {LIVEKIT_URL}")
    logger.info(f"ROOM_NAME: {ROOM_NAME}")
    
    normalized_input_capture_channels = _normalize_input_capture_channels(input_capture_channels)
    normalized_input_mix_channels = _normalize_input_mix_channels(
        input_mix_channels,
        normalized_input_capture_channels,
    )

    logger.info(
        "Audio settings: enable_aec=%s, enable_rnnoise=%s, input_device=%s, "
        "output_device=%s, input_capture_channels=%d, input_mix_channels=%s, "
        "input_mic_gain_db=%.1f, suppress_input_during_playback=%s, playback_echo_tail_ms=%d",
        enable_aec,
        enable_rnnoise,
        input_device,
        output_device,
        normalized_input_capture_channels,
        list(normalized_input_mix_channels),
        INPUT_MIC_GAIN_DB,
        suppress_input_during_playback,
        playback_echo_tail_ms,
    )

    if not LIVEKIT_URL or not ROOM_NAME:
        logger.error("Missing LIVEKIT_URL or ROOM_NAME environment variables")
        return 1

    # Create audio streamer with loop reference
    streamer = AudioStreamer(
        enable_aec,
        loop=loop,
        enable_rnnoise=enable_rnnoise,
        input_capture_channels=normalized_input_capture_channels,
        input_mix_channels=normalized_input_mix_channels,
        suppress_input_during_playback=suppress_input_during_playback,
        playback_echo_tail_ms=playback_echo_tail_ms,
    )
    streamer._input_device = input_device
    streamer._output_device = output_device
    streamer.configure_remote_audio_subscriptions(
        identities=playback_identities,
        identity_prefixes=playback_identity_prefixes,
    )

    # Create room
    room = rtc.Room(loop=loop)
    streamer.room = room

    # Audio processing task
    async def audio_processing_task():
        """Process queued audio batches and publish 10 ms frames to LiveKit."""
        frames_sent = 0
        logger.info("Audio processing task started")

        def process_reverse_batch(batch: RenderAudioBatch) -> None:
            """Feed played speaker audio into APM outside the PortAudio callback."""
            if not streamer.audio_processor:
                return

            num_frames = batch.frame_count // FRAME_SAMPLES
            if num_frames <= 0:
                return

            render_samples = np.frombuffer(batch.pcm, dtype=np.int16, count=batch.frame_count)
            for i in range(num_frames):
                start = i * FRAME_SAMPLES
                end = start + FRAME_SAMPLES
                if end > batch.frame_count:
                    break

                render_frame = streamer._new_audio_frame()
                streamer._copy_chunk_into_frame(render_frame, render_samples[start:end])
                try:
                    streamer.audio_processor.process_reverse_stream(render_frame)
                except Exception as e:
                    if streamer.output_callback_count <= 10:
                        logger.warning(f"Error processing reverse stream with AEC: {e}")

        def drain_reverse_batches() -> None:
            while True:
                try:
                    reverse_batch = streamer.audio_reverse_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                process_reverse_batch(reverse_batch)

        async def process_capture_batch(batch: CaptureAudioBatch) -> int:
            """Convert one callback batch into processed LiveKit frames."""
            if streamer.audio_processor:
                try:
                    streamer.audio_processor.set_stream_delay_ms(batch.delay_ms)
                except RuntimeError as e:
                    # Log the error but continue processing - this is a known issue with APM
                    if not hasattr(streamer, '_delay_error_logged'):
                        logger.warning(f"Failed to set APM stream delay: {e}")
                        logger.warning("Continuing without delay compensation - audio quality may be affected")
                        streamer._delay_error_logged = True

            num_frames = batch.frame_count // FRAME_SAMPLES
            if num_frames <= 0:
                return 0

            capture_samples = None
            if not batch.muted and batch.pcm is not None:
                capture_samples = np.frombuffer(batch.pcm, dtype=np.int16, count=batch.frame_count)
                _, input_mic_gain = streamer.get_input_mic_gain()
                capture_samples = np.clip(
                    np.rint(capture_samples.astype(np.float32) * input_mic_gain),
                    -32768,
                    32767,
                ).astype(np.int16)

            batch_frames_sent = 0
            for i in range(num_frames):
                drain_reverse_batches()

                start = i * FRAME_SAMPLES
                end = start + FRAME_SAMPLES
                if end > batch.frame_count:
                    break

                capture_frame = streamer._new_audio_frame()
                if capture_samples is None:
                    streamer._zero_audio_frame(capture_frame)
                else:
                    streamer._copy_chunk_into_frame(capture_frame, capture_samples[start:end])

                streamer.frames_processed += 1

                # Apply AEC if enabled
                if streamer.audio_processor:
                    try:
                        streamer.audio_processor.process_stream(capture_frame)
                        if logger.isEnabledFor(logging.DEBUG) and streamer.frames_processed <= 5:
                            logger.debug(f"Applied AEC to frame {streamer.frames_processed}")
                    except Exception as e:
                        # Log the error but continue processing
                        if streamer.frames_processed <= 10:
                            logger.warning(f"Error processing audio stream with AEC: {e}")

                if streamer.rnnoise is not None:
                    capture_frame = _apply_rnnoise(streamer.rnnoise, capture_frame)

                await streamer.source.capture_frame(capture_frame)
                streamer.frames_sent_to_livekit += 1
                batch_frames_sent += 1

                if logger.isEnabledFor(logging.DEBUG) and streamer.frames_sent_to_livekit <= 5:
                    logger.debug(
                        "Sent frame %d to LiveKit source",
                        streamer.frames_sent_to_livekit,
                    )

            return batch_frames_sent

        while streamer.running:
            try:
                drain_reverse_batches()
                batch = await streamer.audio_input_queue.get()
                frames_sent += await process_capture_batch(batch)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in audio processing: {e}")
                break

        logger.info(f"Audio processing task ended. Total frames sent: {frames_sent}")

    # Function to handle received audio frames
    async def receive_audio_frames(
        stream: rtc.AudioStream,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        frames_received = 0

        # Use participant info passed from event handler
        participant_id = participant.sid
        participant_name = participant.identity or f"User_{participant.sid[:8]}"
        publication_id = publication.sid
        track_name = publication.name or publication_id

        logger.info(
            "Receiving audio from %s (%s), track %s (%s)",
            participant_name,
            participant_id,
            track_name,
            publication_id,
        )

        try:
            async for frame_event in stream:
                if not streamer.running:
                    break

                frames_received += 1
                if logger.isEnabledFor(logging.DEBUG) and frames_received <= 5:
                    logger.debug(
                        "Received audio frame %d from %s track %s",
                        frames_received,
                        participant_name,
                        track_name,
                    )

                if streamer.remote_playback_enabled:
                    streamer.append_remote_track_audio(publication_id, frame_event.frame.data)
        except asyncio.CancelledError:
            logger.info("Audio receive task cancelled for %s track %s", participant_name, track_name)
            raise
        finally:
            try:
                await stream.aclose()
            except Exception as exc:
                logger.debug("Failed to close audio stream for %s track %s: %s", participant_name, track_name, exc)

            logger.info(
                "Audio receive task ended for %s track %s. Total frames received: %d",
                participant_name,
                track_name,
                frames_received,
            )
            streamer.release_remote_playback(
                participant_id=participant_id,
                publication_id=publication_id,
                cancel_task=False,
            )

    ignored_stream_tasks: set[asyncio.Task] = set()

    def _track_ignored_stream_task(task: asyncio.Task) -> None:
        ignored_stream_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            ignored_stream_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("Ignored data stream drain failed: %s", exc)

        task.add_done_callback(_done)

    async def _drain_text_stream(reader: rtc.TextStreamReader, participant_identity: str) -> None:
        async for _chunk in reader:
            pass

    async def _drain_byte_stream(reader: rtc.ByteStreamReader, participant_identity: str) -> None:
        async for _chunk in reader:
            pass

    def _ignore_text_stream(reader: rtc.TextStreamReader, participant_identity: str) -> None:
        _track_ignored_stream_task(
            asyncio.create_task(_drain_text_stream(reader, participant_identity))
        )

    def _ignore_byte_stream(reader: rtc.ByteStreamReader, participant_identity: str) -> None:
        _track_ignored_stream_task(
            asyncio.create_task(_drain_byte_stream(reader, participant_identity))
        )

    # Event handlers
    @room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        logger.info(
            "track subscribed: %s from participant %s (%s)",
            publication.sid,
            participant.sid,
            participant.identity,
        )

        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return

        # Preserve the HEAD behavior of switching playback when a different
        # remote participant becomes active, but do not release playback for
        # additional tracks from the same participant. This allows speech +
        # background_audio, etc. to be mixed together.
        previous_participant_id = streamer.active_remote_participant_id
        if (
            previous_participant_id is not None
            and previous_participant_id != participant.sid
        ):
            logger.info(
                "Switching remote playback from participant %s to %s",
                previous_participant_id,
                participant.identity,
            )
            streamer.release_remote_playback(
                reason=f"New remote audio participant subscribed for {participant.identity}",
            )

        if publication.sid in streamer.remote_audio_tasks:
            logger.info(
                "Remote audio track %s from %s is already active",
                publication.sid,
                participant.identity,
            )
            return

        streamer.start_remote_audio_mixer()

        audio_stream = rtc.AudioStream(
            track,
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        remote_audio_task = asyncio.create_task(
            receive_audio_frames(audio_stream, publication, participant)
        )

        streamer.register_remote_audio_task(
            publication=publication,
            participant=participant,
            task=remote_audio_task,
        )

        logger.info(
            "Activating remote playback for %s track %s (%s); active remote tracks=%d",
            participant.identity,
            publication.name,
            publication.sid,
            len(streamer.remote_audio_tasks),
        )

    @room.on("track_unsubscribed")
    def on_track_unsubscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        logger.info("track unsubscribed: %s from participant %s (%s)", publication.sid, participant.sid, participant.identity)
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            streamer.release_remote_playback(
                participant_id=participant.sid,
                publication_id=publication.sid,
                reason=f"Active remote audio track unsubscribed for {participant.identity}",
            )

    @room.on("track_published")
    def on_track_published(
        publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
    ):
        logger.info(
            "track published: %s from participant %s (%s)",
            publication.sid,
            participant.sid,
            participant.identity,
        )
        streamer.update_remote_audio_subscription(publication, participant)

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info("participant connected: %s %s", participant.sid, participant.identity)
        # Initialize participant in our tracking
        with streamer.participants_lock:
            streamer.participants[participant.sid] = {
                'name': participant.identity or f"User_{participant.sid[:8]}",
            }
        logger.info(f"Added participant to tracking: {participant.identity}")

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info("participant disconnected: %s %s", participant.sid, participant.identity)
        # Remove participant from our tracking
        with streamer.participants_lock:
            if participant.sid in streamer.participants:
                del streamer.participants[participant.sid]
                logger.info(f"Removed participant from tracking: {participant.identity}")
        streamer.release_remote_playback(
            participant_id=participant.sid,
            reason=f"Active remote participant disconnected: {participant.identity}",
        )

    @room.on("connected")
    def on_connected():
        streamer.livekit_connected = True
        streamer.livekit_room_name = room.name
        logger.info("Successfully connected to LiveKit room")

    @room.on("disconnected")
    def on_disconnected(reason):
        streamer.livekit_connected = False
        streamer.livekit_room_name = None
        streamer.ready = False
        logger.info(f"Disconnected from LiveKit room: {reason}")
        streamer.release_remote_playback(reason=f"Room disconnected: {reason}")

    try:
        streamer.start_control_server(control_port)

        # Connect to LiveKit room
        logger.info("Connecting to LiveKit room...")
        token = generate_token(ROOM_NAME, participant_name, participant_name)
        logger.info(f"Generated token for participant: {participant_name}")

        await room.connect(
            LIVEKIT_URL,
            token,
            options=rtc.RoomOptions(auto_subscribe=False),
        )
        streamer.livekit_connected = True
        streamer.livekit_room_name = room.name
        logger.info("connected to room %s", room.name)

        for topic in IGNORED_TEXT_STREAM_TOPICS:
            room.register_text_stream_handler(topic, _ignore_text_stream)
        for topic in IGNORED_BYTE_STREAM_TOPICS:
            room.register_byte_stream_handler(topic, _ignore_byte_stream)
        logger.info(
            "Registered ignored data stream handlers: text=%s byte=%s",
            list(IGNORED_TEXT_STREAM_TOPICS),
            list(IGNORED_BYTE_STREAM_TOPICS),
        )

        # Publish microphone track
        logger.info("Publishing microphone track '%s'...", mic_track_name)
        track = rtc.LocalAudioTrack.create_audio_track(mic_track_name, streamer.source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        publication = await room.local_participant.publish_track(track, options)
        logger.info("published track %s", publication.sid)

        if enable_aec:
            logger.info("Echo cancellation is enabled")
        else:
            logger.info("Echo cancellation is disabled")

        # Start background tasks
        logger.info("Starting background tasks...")
        audio_task = asyncio.create_task(audio_processing_task())

        # Start audio devices only after the LiveKit publishing path is ready.
        logger.info("Starting audio devices...")
        streamer.start_audio_devices()
        streamer.ready = True

        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                streamer.update_remote_audio_subscription(publication, participant)

        logger.info("=== Audio streaming started (headless mode). Press Ctrl+C to stop. ===")

        # Keep running until interrupted; watchdog checks for stalled output streams
        WATCHDOG_INTERVAL = 5.0
        WATCHDOG_MIN_BUFFER_MS = 200  # only flag a stall if this much audio is backed up
        streamer._watchdog_last_callback_count = streamer.output_callback_count
        streamer._watchdog_last_check_time = time.monotonic()
        try:
            while streamer.running:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                now = time.monotonic()
                current_count = streamer.output_callback_count
                streamer._check_output_slot_health()
                buffer_bytes = sum(len(b) for b in streamer.output_buffers) if streamer.output_buffers else len(streamer.output_buffer)
                buffer_ms = buffer_bytes / streamer.output_bytes_per_ms

                if (
                    streamer.remote_playback_enabled
                    and bool(streamer.remote_audio_tasks)
                    and buffer_ms >= WATCHDOG_MIN_BUFFER_MS
                    and current_count == streamer._watchdog_last_callback_count
                ):
                    logger.warning(
                        "Output stream stall detected: callback count unchanged at %d with %.0f ms queued; restarting output streams",
                        current_count,
                        buffer_ms,
                    )
                    if streamer.restart_output_streams():
                        streamer._watchdog_output_restart_fails = 0
                    else:
                        streamer._watchdog_output_restart_fails += 1
                        if streamer._watchdog_output_restart_fails >= 2:
                            logger.warning(
                                "Output-only restart failed %d times; escalating to full PortAudio restart",
                                streamer._watchdog_output_restart_fails,
                            )
                            if streamer._full_audio_restart():
                                streamer._watchdog_output_restart_fails = 0
                else:
                    streamer._watchdog_output_restart_fails = 0
                    streamer._watchdog_last_callback_count = current_count
                    streamer._watchdog_last_check_time = now
        except KeyboardInterrupt:
            logger.info("Stopping audio streaming...")

    except Exception as e:
        exit_code = 1
        logger.error(f"Error in main: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
    finally:
        # Cleanup
        logger.info("Starting cleanup...")
        streamer.running = False
        streamer.ready = False
        streamer.livekit_connected = False

        if 'audio_task' in locals():
            audio_task.cancel()
            try:
                await audio_task
            except asyncio.CancelledError:
                pass

        for task in list(ignored_stream_tasks):
            task.cancel()
        if ignored_stream_tasks:
            await asyncio.gather(*ignored_stream_tasks, return_exceptions=True)
            ignored_stream_tasks.clear()

        await streamer.stop_remote_audio_playback(reason="Shutting down audio bridge")
        streamer.stop_audio_devices()
        streamer.stop_control_server()
        try:
            await asyncio.wait_for(room.disconnect(), timeout=5.0)
        except Exception as exc:
            logger.warning("Room disconnect did not complete cleanly: %s", exc)
        logger.info("=== CLEANUP COMPLETE ===")
    return exit_code

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="LiveKit bidirectional audio streaming with AEC")
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio devices as JSON and exit"
    )
    parser.add_argument(
        "--name",
        "-n",
        type=str,
        default="audio-streamer",
        help="Participant name to use when connecting to the room (default: audio-streamer)"
    )
    parser.add_argument(
        "--input-device",
        type=str,
        default=None,
        help="Audio input device index/name (default: system default)"
    )
    parser.add_argument(
        "--output-device",
        action="append",
        type=str,
        default=None,
        help="Audio output device index/name (repeat for multiple outputs)"
    )
    parser.add_argument(
        "--input-capture-channels",
        type=int,
        default=None,
        help="Number of channels to open on the input device (default: 1)",
    )
    parser.add_argument(
        "--input-mix-channel",
        action="append",
        type=int,
        default=None,
        help=(
            "Zero-based captured input channel to publish; repeat to average "
            "multiple channels into mono (default: 0)"
        ),
    )
    parser.add_argument(
        "--disable-aec",
        action="store_true",
        help="Disable acoustic echo cancellation (AEC)"
    )
    parser.add_argument(
        "--enable-rnnoise",
        action="store_true",
        help="Enable RNNoise denoising before publishing microphone audio"
    )
    parser.add_argument(
        "--suppress-input-during-playback",
        action="store_true",
        help="Publish silence while speaker playback is active to prevent self-echo turns",
    )
    parser.add_argument(
        "--playback-echo-tail-ms",
        type=int,
        default=DEFAULT_PLAYBACK_ECHO_TAIL_MS,
        help="Keep capture suppressed this long after speaker playback (default: 500 ms)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=8766,
        help="Localhost control API port for supervisor integration"
    )
    parser.add_argument(
        "--mic-track-name",
        type=str,
        default=DEFAULT_MIC_TRACK_NAME,
        help="Track name for microphone publishing (default: LIVEKIT_TRACK_NAME or g1-mic)",
    )
    parser.add_argument(
        "--playback-identity",
        action="append",
        default=None,
        help=(
            "Remote participant identity to subscribe to for speaker playback "
            "(repeatable; defaults to AUDIO_BRIDGE_PLAYBACK_IDENTITIES)"
        ),
    )
    parser.add_argument(
        "--playback-identity-prefix",
        action="append",
        default=None,
        help=(
            "Remote participant identity prefix to subscribe to for speaker playback "
            "(repeatable; defaults to AUDIO_BRIDGE_PLAYBACK_IDENTITY_PREFIXES or 'agent-')"
        ),
    )
    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("stream_audio.log"),
            logging.StreamHandler(),
        ],
    )

    # Handle device listing early, before any logging setup
    if args.list_devices:
        import json
        devices = sd.query_devices()
        default_input = sd.default.device[0] if sd.default.device else None
        default_output = sd.default.device[1] if sd.default.device else None

        result = {
            "input_devices": [],
            "output_devices": []
        }

        raw_input_devices = []
        for idx, dev in enumerate(devices):
            # Input devices
            if dev['max_input_channels'] > 0:
                raw_input_devices.append({
                    "index": idx,
                    "name": dev['name'],
                    "channels": dev['max_input_channels'],
                    "sample_rate": int(dev['default_samplerate']),
                    "is_default": idx == default_input,
                    "hostapi": sd.query_hostapis(dev['hostapi'])['name']
                })

            # Output devices
            if dev['max_output_channels'] > 0:
                result["output_devices"].append({
                    "index": idx,
                    "name": dev['name'],
                    "channels": dev['max_output_channels'],
                    "sample_rate": int(dev['default_samplerate']),
                    "is_default": idx == default_output,
                    "hostapi": sd.query_hostapis(dev['hostapi'])['name']
                })

        result["input_devices"] = _augment_input_devices(raw_input_devices)
        result["output_devices"] = _augment_output_devices(result["output_devices"])

        print(json.dumps(result, indent=2))
        sys.exit(0)

    # Cross-platform signal handling:
    # - Unix: use loop.add_signal_handler
    # - Windows: fallback to signal.signal (add_signal_handler is unsupported)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main_task = loop.create_task(
            main(
                args.name,
                enable_aec=not args.disable_aec,
                enable_rnnoise=args.enable_rnnoise,
                input_device=args.input_device,
                output_device=args.output_device,
                input_capture_channels=args.input_capture_channels,
                input_mix_channels=args.input_mix_channel,
                control_port=args.control_port,
                mic_track_name=args.mic_track_name,
                playback_identities=args.playback_identity,
                playback_identity_prefixes=args.playback_identity_prefix,
                suppress_input_during_playback=args.suppress_input_during_playback,
                playback_echo_tail_ms=args.playback_echo_tail_ms,
            )
        )

        def request_shutdown(sig_name: str) -> None:
            logging.getLogger(__name__).info("Received %s; shutting down...", sig_name)
            if not main_task.done():
                loop.call_soon_threadsafe(main_task.cancel)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, request_shutdown, sig.name)
            except NotImplementedError:
                # Windows fallback (ProactorEventLoop does not implement add_signal_handler)
                signal.signal(
                    sig,
                    lambda _signum, _frame, s=sig: request_shutdown(s.name),
                )

        try:
            exit_code = loop.run_until_complete(main_task)
        except KeyboardInterrupt:
            exit_code = 130
            pass
        except asyncio.CancelledError:
            exit_code = 130
            pass
        finally:
            loop.close()
        raise SystemExit(exit_code)
    except KeyboardInterrupt:
        raise SystemExit(130)
