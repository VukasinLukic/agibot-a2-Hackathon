"""BGR picture stored as bytes. No OpenCV or NumPy."""

from __future__ import annotations


class BgrImage:
    def __init__(self, width: int, height: int, data: bytes | bytearray | None = None) -> None:
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            raise ValueError("width and height must be positive ints")
        size = width * height * 3
        if data is None:
            payload = bytearray(size)
        else:
            payload = bytearray(data)
        if len(payload) != size:
            raise ValueError(f"BGR buffer must be {size} bytes, got {len(payload)}")
        self.shape = (height, width, 3)
        self.data = payload

    @property
    def width(self) -> int:
        return int(self.shape[1])

    @property
    def height(self) -> int:
        return int(self.shape[0])

    def copy(self) -> BgrImage:
        return BgrImage(self.width, self.height, self.data)

    def get(self, x: int, y: int) -> tuple[int, int, int]:
        index = self._index(x, y)
        return self.data[index], self.data[index + 1], self.data[index + 2]

    def set(self, x: int, y: int, bgr: tuple[int, int, int]) -> None:
        index = self._index(x, y)
        self.data[index] = bgr[0]
        self.data[index + 1] = bgr[1]
        self.data[index + 2] = bgr[2]

    def fill(self, bgr: tuple[int, int, int]) -> None:
        pixel = bytes((bgr[0] & 255, bgr[1] & 255, bgr[2] & 255))
        self.data[:] = pixel * (self.width * self.height)

    def _index(self, x: int, y: int) -> int:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            raise ValueError("pixel is outside the image")
        return (y * self.width + x) * 3
