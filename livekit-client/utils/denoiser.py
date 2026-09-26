"""
RNNoise-based audio denoising utilities for LiveKit.
Provides frame-level noise cancellation with automatic resampling.
"""

import numpy as np
import math
from livekit import rtc
from livekit.agents.voice.io import AudioInput
from utils.logging_utils import global_logger

try:
    import pyrnnoise  # pip install pyrnnoise
    _RNNOISE_AVAILABLE = True
except Exception as _e:
    _RNNOISE_AVAILABLE = False


def _to_float32(arr_like, *, assume_float32=True) -> np.ndarray:
    """
    Convert memoryview/bytes/ndarray to float32 mono array.
    If data is int16, set assume_float32=False.
    """
    if isinstance(arr_like, (memoryview, bytes, bytearray)):
        if assume_float32:
            return np.frombuffer(arr_like, dtype=np.float32)
        else:
            a16 = np.frombuffer(arr_like, dtype=np.int16)
            return (a16.astype(np.float32) / 32768.0)
    elif isinstance(arr_like, np.ndarray):
        if assume_float32 and arr_like.dtype == np.float32:
            return arr_like
        if not assume_float32 and arr_like.dtype == np.int16:
            return (arr_like.astype(np.float32) / 32768.0)
        return arr_like.astype(np.float32, copy=False)
    else:
        return np.asarray(arr_like, dtype=np.float32)


def _to_mono(pcm: np.ndarray, channels: int) -> np.ndarray:
    if channels <= 1:
        return pcm.astype(np.float32, copy=False)
    frames_total = pcm.shape[0] // channels
    if frames_total <= 0:
        return pcm.astype(np.float32, copy=False)
    return pcm[:frames_total*channels].reshape(frames_total, channels).mean(axis=1).astype(np.float32)


def _resample_linear(pcm: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """
    Simple linear resampler with minimal latency and OK quality for voice.
    Uses np.interp; keeps contiguous float32.
    """
    if sr_in == sr_out or pcm.size == 0:
        return pcm

    ratio = sr_out / sr_in
    n_out = int(math.floor(pcm.size * ratio))
    if n_out == 0:
        return np.empty(0, dtype=np.float32)

    x_in = np.arange(pcm.size, dtype=np.float64)
    x_out = np.linspace(0, pcm.size - 1, num=n_out, dtype=np.float64)
    y = np.interp(x_out, x_in, pcm.astype(np.float64, copy=False)).astype(np.float32)
    return y


class RnnoiseDenoiser:
    """
    Stateful RNNoise wrapper for pyrnnoise.
    - Expects float32 mono PCM @ 48 kHz.
    - Processes in 10 ms (480-sample) frames.
    """
    FRAME_SAMPLES = 480

    def __init__(self):
        self.enabled = _RNNOISE_AVAILABLE
        self._buf = np.empty(0, dtype=np.float32)
        self._proc = None
        if self.enabled:
            try:
                # Most pyrnnoise builds accept these kwargs; if not, we'll try a fallback
                self._proc = pyrnnoise.RNNoise(sample_rate=48000)  # type: ignore
            except TypeError:
                global_logger.exception(f"Failed to init pyrnnoise.RNNoise, disabling denoiser: {e}")
                self.enabled = False
        if not self.enabled or self._proc is None:
            global_logger.warning("RNNoise not available; denoiser will passthrough.")
            self.enabled = False

    def _denoise_chunk(self, seg: np.ndarray) -> np.ndarray:
        """
        Call into pyrnnoise using denoise_chunk() which properly initializes channels.
        pyrnnoise requires 2D int16 arrays [channels, samples] for denoise_chunk.
        """
        proc = self._proc
        if proc is None:
            return seg
        # Ensure contiguous float32 1-D of length 480
        seg = np.ascontiguousarray(seg.astype(np.float32, copy=False))

        # Only sanitize if there are NaN/inf values
        if not np.all(np.isfinite(seg)):
            seg = np.nan_to_num(seg, nan=0.0, posinf=0.0, neginf=0.0)

        try:
            # Convert float32 [-1.0, 1.0] to int16 [-32768, 32767]
            # Clip to prevent overflow
            seg_clipped = np.clip(seg, -1.0, 1.0)
            seg_int16 = (seg_clipped * 32767.0).astype(np.int16)
            # pyrnnoise.denoise_chunk expects 2D: [channels, samples]
            seg_2d = seg_int16.reshape(1, -1)
            # denoise_chunk returns an iterator of (speech_prob, denoised_frame) tuples
            results = list(proc.denoise_chunk(seg_2d))  # type: ignore
            if results:
                # Get the denoised frame from the first result (int16 format)
                _, denoised_frame = results[0]
                # Convert int16 back to float32 and flatten to 1D
                denoised_float = (denoised_frame.flatten().astype(np.float32) / 32767.0)
                return denoised_float
            return seg
        except Exception as e:
            global_logger.exception(f"RNNoise processing error, falling back to passthrough for this frame: {e}")
            return seg

    def process_48k(self, pcm48: np.ndarray) -> np.ndarray:
        """Input/Output: float32 mono @48k."""
        if not self.enabled or pcm48.size == 0:
            return pcm48

        x = pcm48.astype(np.float32, copy=False)
        if self._buf.size:
            x = np.concatenate([self._buf, x])
            self._buf = np.empty(0, dtype=np.float32)

        n = x.size
        n_frames = n // self.FRAME_SAMPLES
        out = np.empty(n_frames * self.FRAME_SAMPLES, dtype=np.float32)
        for i in range(n_frames):
            seg = x[i*self.FRAME_SAMPLES:(i+1)*self.FRAME_SAMPLES]
            out[i*self.FRAME_SAMPLES:(i+1)*self.FRAME_SAMPLES] = self._denoise_chunk(seg)

        # Keep leftover (sub-frame) for next call; zero-latency compromise: return original tail now
        leftover = n - out.size
        if leftover > 0:
            self._buf = x[-leftover:].copy()
            out = np.concatenate([out, self._buf])

        return out[:n]


class DenoiseAudioInput(AudioInput):
    """
    Decorator over session.input.audio:
      - Converts inbound to float32 mono
      - Resamples to 48 kHz for RNNoise
      - Runs RNNoise @48k
      - Resamples to `force_out_sr` if provided; else back to ORIGINAL SR
        * Use force_out_sr=16000 when using_vitasis=True (TrueBar STT needs 16k)
    """
    def __init__(self, source: AudioInput, force_out_sr: int | None = None):
        super().__init__(label="denoised", source=source)
        self._rn = RnnoiseDenoiser()
        self._force_out_sr = force_out_sr  # None => preserve original SR

    def on_attached(self) -> None:
        if self.source:
            self.source.on_attached()

    def on_detached(self) -> None:
        if self.source:
            self.source.on_detached()

    async def __anext__(self) -> rtc.AudioFrame:
        frame: rtc.AudioFrame = await self.source.__anext__()  # type: ignore

        ch = int(frame.num_channels or 1)
        sr_in = int(frame.sample_rate or 48000)
        spc_hint = getattr(frame, "samples_per_channel", None)

        # LiveKit sends int16 PCM data - convert to float32 properly
        if isinstance(frame.data, (memoryview, bytes, bytearray)):
            data_bytes = bytes(frame.data) if isinstance(frame.data, memoryview) else frame.data
            pcm_int16 = np.frombuffer(data_bytes, dtype=np.int16)
            pcm = pcm_int16.astype(np.float32) / 32768.0
        elif isinstance(frame.data, np.ndarray):
            # Already numpy array - check dtype
            if frame.data.dtype == np.int16:
                pcm = frame.data.astype(np.float32) / 32768.0
            elif frame.data.dtype == np.float32:
                pcm = frame.data
            else:
                pcm = frame.data.astype(np.float32)
        else:
            pcm = _to_float32(frame.data, assume_float32=False)

        # Trim to declared sample count if provided
        if spc_hint is not None:
            total_samples = int(spc_hint) * ch
            if pcm.shape[0] > total_samples:
                pcm = pcm[:total_samples]

        # 2) Mono mixdown
        pcm_mono = _to_mono(pcm, ch)

        # 3) Resample -> 48k for RNNoise
        if sr_in != 48000:
            pcm_48 = _resample_linear(pcm_mono, sr_in=sr_in, sr_out=48000)
        else:
            pcm_48 = pcm_mono

        # 4) Denoise @48k
        cleaned_48 = self._rn.process_48k(pcm_48)

        # 5) Resample to target SR
        out_sr = self._force_out_sr if self._force_out_sr else sr_in
        if out_sr != 48000:
            cleaned = _resample_linear(cleaned_48, sr_in=48000, sr_out=out_sr)
        else:
            cleaned = cleaned_48

        cleaned = np.ascontiguousarray(cleaned, dtype=np.float32)
        samples_per_channel = int(cleaned.shape[0])  # mono

        # CRITICAL: LiveKit expects int16 data back, not float32!
        # Convert float32 back to int16 for the output frame
        cleaned_int16 = (np.clip(cleaned, -1.0, 1.0) * 32767.0).astype(np.int16)

        return rtc.AudioFrame(
            data=cleaned_int16.tobytes(),
            sample_rate=out_sr,
            num_channels=1,
            samples_per_channel=samples_per_channel,
        )
