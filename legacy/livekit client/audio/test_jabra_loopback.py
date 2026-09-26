from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio.device_selector import resolve_host_audio_devices  # noqa: E402

LOG = logging.getLogger("jabra_loopback")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record audio from Jabra mic and play it back on the Jabra speaker."
    )
    parser.add_argument("--seconds", type=float, default=5.0, help="Record duration.")
    parser.add_argument("--channels", type=int, default=1, help="Number of channels.")
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=0,
        help="Override sample rate (default uses device default).",
    )
    parser.add_argument(
        "--save-wav",
        type=str,
        default="",
        help="Optional WAV path to save the recording.",
    )
    args = parser.parse_args()

    if not sys.platform.startswith("linux"):
        LOG.warning("This script is intended for Linux; continuing anyway.")

    host_devices = resolve_host_audio_devices()
    if host_devices.input_device is None or host_devices.output_device is None:
        LOG.error(
            "No audio device selected. Ensure Jabra is connected or set "
            "HOST_AUDIO_INPUT_DEVICE/HOST_AUDIO_OUTPUT_DEVICE."
        )
        return 2

    sample_rate = _resolve_sample_rate(host_devices, args.sample_rate)
    frames = int(sample_rate * args.seconds)
    LOG.info(
        "Recording %.2fs from input=%s at %s Hz (channels=%s).",
        args.seconds,
        host_devices.input_device,
        sample_rate,
        args.channels,
    )
    recording = sd.rec(
        frames,
        samplerate=sample_rate,
        channels=args.channels,
        dtype="int16",
        device=host_devices.input_device,
    )
    sd.wait()

    if host_devices.volume is not None:
        recording = _apply_volume(recording, host_devices.volume)

    if args.save_wav:
        _save_wav(Path(args.save_wav), recording, sample_rate)
        LOG.info("Saved WAV: %s", args.save_wav)

    LOG.info("Playing back on output=%s.", host_devices.output_device)
    sd.play(recording, samplerate=sample_rate, device=host_devices.output_device)
    sd.wait()
    LOG.info("Done.")
    return 0


def _resolve_sample_rate(host_devices, override: int) -> int:
    if override:
        return int(override)
    if host_devices.sample_rate:
        return int(host_devices.sample_rate)
    for device in (host_devices.input_device, host_devices.output_device):
        if device is None:
            continue
        try:
            info = sd.query_devices(device)
        except Exception:
            continue
        rate = info.get("default_samplerate")
        if rate:
            return int(rate)
    default_rate = getattr(sd.default, "samplerate", None)
    if default_rate:
        return int(default_rate)
    return 48000


def _apply_volume(audio: np.ndarray, volume: float) -> np.ndarray:
    volume = max(0.0, min(1.0, float(volume)))
    if volume == 1.0:
        return audio
    scaled = audio.astype(np.float32) * volume
    return np.clip(scaled, -32768, 32767).astype(np.int16)


def _save_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(audio.shape[1] if audio.ndim > 1 else 1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio.tobytes())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
