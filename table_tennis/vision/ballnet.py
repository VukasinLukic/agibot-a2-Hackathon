"""BallNet: mala CNN koja kaže da li je kandidat loptica. Samo numpy, bez torch-a.

Težine su ``.npz`` izvezen iz torch ``state_dict``-a (ključevi ``c0w c0b c1w
c1b c2w c2b l0w l0b l1w l1b``, conv težina ``[out, in, 3, 3]``, linear
``[out, in]``). Fajl ne ide u git.

Arhitektura: 3 × (conv3x3 pad 1, ReLU, maxpool 2), pa 512 -> 32 -> 1.
Ulaz je patch 32 × 32 sa kanalima B, G, R, diff u [0, 1]; mreža vidi ``x - 0.5``.
Izlaz je sigmoid logita. To je skor modela, ne kalibrisana verovatnoća.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

PATCH = 32
CHANNELS = 4
_BUCKETS = (8, 16, 32, 64, 128)
_KEYS = ("c0w", "c0b", "c1w", "c1b", "c2w", "c2b", "l0w", "l0b", "l1w", "l1b")


class BallNet:
    """Batched inference over N × 32 × 32 × 4 patches. Weights are reshaped once, at load."""

    def __init__(self, weights: dict[str, np.ndarray]) -> None:
        missing = [key for key in _KEYS if key not in weights]
        if missing:
            raise ValueError(f"BallNet weights miss {missing}")
        self._convs: list[tuple[np.ndarray, np.ndarray]] = []
        channels = CHANNELS
        for index in range(3):
            kernel = np.asarray(weights[f"c{index}w"], dtype=np.float32)
            bias = np.asarray(weights[f"c{index}b"], dtype=np.float32)
            if kernel.ndim != 4 or kernel.shape[1:] != (channels, 3, 3) or bias.shape != (kernel.shape[0],):
                raise ValueError(f"c{index}w must be [out, {channels}, 3, 3]")
            # [out, in, ky, kx] -> [out, (ky, kx, in)], the same order as _im2col.
            self._convs.append((np.ascontiguousarray(kernel.transpose(0, 2, 3, 1).reshape(kernel.shape[0], -1)), bias))
            channels = kernel.shape[0]
        side = PATCH // 8
        hidden = np.asarray(weights["l0w"], dtype=np.float32)
        if hidden.shape[1] != channels * side * side:
            raise ValueError("l0w does not match the conv output")
        self._hidden = np.ascontiguousarray(hidden.T)
        self._hidden_bias = np.asarray(weights["l0b"], dtype=np.float32)
        self._out = np.ascontiguousarray(np.asarray(weights["l1w"], dtype=np.float32).T)
        self._out_bias = np.asarray(weights["l1b"], dtype=np.float32)
        if self._out.shape != (self._hidden.shape[1], 1):
            raise ValueError("l1w must be [1, hidden]")

    def logits(self, patches: np.ndarray) -> np.ndarray:
        """N × 32 × 32 × 4 float in [0, 1] -> N logits."""
        x = np.asarray(patches, dtype=np.float32)
        if x.ndim != 4 or x.shape[1:] != (PATCH, PATCH, CHANNELS):
            raise ValueError("patches must be N x 32 x 32 x 4")
        count = x.shape[0]
        if count == 0:
            return np.zeros(0, dtype=np.float32)
        # C × N × H × W with a zero border: one GEMM per layer, contiguous rows for im2col.
        # Each layer writes straight into the next padded buffer.
        padded = np.zeros((CHANNELS, count, PATCH + 2, PATCH + 2), dtype=np.float32)
        np.subtract(x.transpose(3, 0, 1, 2), np.float32(0.5), out=padded[:, :, 1:-1, 1:-1])
        for index, (kernel, bias) in enumerate(self._convs):
            side = padded.shape[2] - 2
            conv = (kernel @ _im2col(padded)).reshape(-1, count, side, side)
            half = side // 2
            if index + 1 < len(self._convs):
                nxt = np.zeros((conv.shape[0], count, half + 2, half + 2), dtype=np.float32)
                out = nxt[:, :, 1:-1, 1:-1]
            else:
                nxt = out = np.empty((conv.shape[0], count, half, half), dtype=np.float32)
            # max and ReLU commute with a per-channel bias: pool first, 4x less work.
            _pool2(conv, out)
            out += bias[:, None, None, None]
            np.maximum(out, 0.0, out=out)
            padded = nxt
        # Torch flattens C, H, W per sample.
        x = padded.transpose(1, 0, 2, 3).reshape(count, -1) @ self._hidden
        x += self._hidden_bias
        np.maximum(x, 0.0, out=x)
        return (x @ self._out + self._out_bias)[:, 0]

    def probs(self, patches: np.ndarray) -> np.ndarray:
        logits = self.logits(patches)
        return 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))


class OnnxBallNet:
    """The same net as ``.onnx``, run by OpenCV DNN. About 5x faster than numpy on a laptop CPU.

    Input ``x`` is N × 4 × 32 × 32 in [0, 1] (the graph subtracts 0.5), output is N logits.
    OpenCV 5.0 DNN kills the process when one net sees a batch larger than an
    earlier one, so each padded batch size (8, 16, ... ) keeps its own net.
    """

    def __init__(self, path: Path) -> None:
        self._path = str(path)
        self._nets: dict[int, object] = {}
        self._net_for(_BUCKETS[0])

    def logits(self, patches: np.ndarray) -> np.ndarray:
        x = np.asarray(patches, dtype=np.float32)
        if x.ndim != 4 or x.shape[1:] != (PATCH, PATCH, CHANNELS):
            raise ValueError("patches must be N x 32 x 32 x 4")
        count = x.shape[0]
        out = np.empty(count, dtype=np.float32)
        for start in range(0, count, _BUCKETS[-1]):
            chunk = x[start : start + _BUCKETS[-1]]
            size = next(bucket for bucket in _BUCKETS if bucket >= chunk.shape[0])
            batch = np.zeros((size, CHANNELS, PATCH, PATCH), dtype=np.float32)
            batch[: chunk.shape[0]] = chunk.transpose(0, 3, 1, 2)
            net = self._net_for(size)
            net.setInput(batch)
            out[start : start + chunk.shape[0]] = np.asarray(net.forward(), dtype=np.float32).reshape(-1)[: chunk.shape[0]]
        return out

    def _net_for(self, size: int) -> Any:
        net = self._nets.get(size)
        if net is None:
            import cv2

            net = cv2.dnn.readNetFromONNX(self._path)
            self._nets[size] = net
        return net

    def probs(self, patches: np.ndarray) -> np.ndarray:
        logits = self.logits(patches)
        return 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))


def load_ballnet(path: Path | str) -> BallNet | OnnxBallNet:
    """``.onnx`` goes to OpenCV DNN, anything else is the numpy ``.npz``. Missing weights fail at startup."""
    file = Path(path)
    if not file.is_file():
        raise ValueError(f"BallNet weights not found: {file}")
    if file.suffix.lower() == ".onnx":
        return OnnxBallNet(file)
    with np.load(file) as data:
        return BallNet({key: data[key] for key in data.files})


def _im2col(padded: np.ndarray) -> np.ndarray:
    """C × N × (H+2) × (W+2), already zero-padded -> 9C × (N·H·W) for a 3 × 3 window."""
    channels, count, height, width = padded.shape
    height -= 2
    width -= 2
    cols = np.empty((3, 3, channels, count, height, width), dtype=np.float32)
    for ky in range(3):
        for kx in range(3):
            cols[ky, kx] = padded[:, :, ky : ky + height, kx : kx + width]
    return cols.reshape(9 * channels, count * height * width)


def _pool2(x: np.ndarray, out: np.ndarray) -> None:
    np.maximum(x[:, :, 0::2, 0::2], x[:, :, 0::2, 1::2], out=out)
    np.maximum(out, x[:, :, 1::2, 0::2], out=out)
    np.maximum(out, x[:, :, 1::2, 1::2], out=out)
