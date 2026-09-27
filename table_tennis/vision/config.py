"""Local vision settings. Ball color stays here until a ball is chosen."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


EXAMPLE_CONFIG_PATH = Path(__file__).with_name("config.example.yaml")


@dataclass(frozen=True, slots=True)
class BallColor:
    """OpenCV-style HSV bounds. None means the color has not been chosen yet."""

    hsv_lower: tuple[int, int, int] | None
    hsv_upper: tuple[int, int, int] | None

    @property
    def configured(self) -> bool:
        return self.hsv_lower is not None and self.hsv_upper is not None


@dataclass(frozen=True, slots=True)
class Roi:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class VisionConfig:
    camera_id: str
    origin: str
    ball: BallColor
    min_diameter_px: int
    max_diameter_px: int
    roi: Roi | None
    missing_frames: int
    model_path: str | None = None
    ballnet_path: str | None = None
    work_width_px: int = 960
    compensate_motion: bool = True
    detect_darker: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, object]) -> VisionConfig:
        ball_raw = _require_mapping(data, "ball")
        roi_raw = data.get("roi", None)
        min_diameter = _require_positive_int(ball_raw, "min_diameter_px")
        max_diameter = _require_positive_int(ball_raw, "max_diameter_px")
        if min_diameter > max_diameter:
            raise ValueError("min_diameter_px cannot be greater than max_diameter_px")
        model_path = _optional_path(data, "model_path")
        ballnet_path = _optional_path(data, "ballnet_path")
        if model_path is not None and ballnet_path is not None:
            raise ValueError("model_path and ballnet_path are exclusive; choose one learned detector")
        ballnet_raw = data.get("ballnet", None)
        if ballnet_raw is None:
            ballnet_raw = {}
        if not isinstance(ballnet_raw, dict):
            raise ValueError("ballnet must be a mapping")
        return cls(
            camera_id=_require_camera_id(data),
            origin=_require_origin(data),
            ball=BallColor(
                hsv_lower=_optional_hsv(ball_raw, "hsv_lower"),
                hsv_upper=_optional_hsv(ball_raw, "hsv_upper"),
            ),
            min_diameter_px=min_diameter,
            max_diameter_px=max_diameter,
            roi=None if roi_raw is None else _require_roi(roi_raw),
            missing_frames=_require_positive_int(
                _require_mapping(data, "tracker"), "missing_frames"
            ),
            model_path=model_path,
            ballnet_path=ballnet_path,
            work_width_px=_optional_work_width(ballnet_raw),
            compensate_motion=_optional_bool(ballnet_raw, "compensate_motion", True),
            detect_darker=_optional_bool(ballnet_raw, "detect_darker", False),
        )


def load_config(path: Path | str) -> VisionConfig:
    text = Path(path).read_text(encoding="utf-8")
    parsed = _parse_simple_yaml(text)
    if not isinstance(parsed, dict):
        raise ValueError("vision config must be a mapping")
    return VisionConfig.from_mapping(parsed)


def load_example_config() -> VisionConfig:
    return load_config(EXAMPLE_CONFIG_PATH)


def _require_mapping(data: dict[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def _require_camera_id(data: dict[str, object]) -> str:
    value = data.get("camera_id")
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("camera_id must be a non-empty string")
    return value


def _require_origin(data: dict[str, object]) -> str:
    value = data.get("origin")
    if value not in {"file", "a2_h264", "a2_fisheye"}:
        raise ValueError("origin must be 'file', 'a2_h264', or 'a2_fisheye'")
    return value


def _require_positive_int(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if type(value) is not int or value <= 0:
        raise ValueError(f"{key} must be a positive int")
    return value


def _optional_hsv(
    data: dict[str, object], key: str
) -> tuple[int, int, int] | None:
    value = data.get(key, None)
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3 or not all(type(item) is int for item in value):
        raise ValueError(f"{key} must be null or three integers [h, s, v]")
    hue, saturation, value_channel = value
    if not 0 <= hue <= 179 or not 0 <= saturation <= 255 or not 0 <= value_channel <= 255:
        raise ValueError(f"{key} is outside OpenCV HSV bounds")
    return hue, saturation, value_channel


def _optional_path(data: dict[str, object], key: str) -> str | None:
    value = data.get(key, None)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{key} must be null or a path")
    return value


def _optional_work_width(data: dict[str, object]) -> int:
    value = data.get("work_width_px", 960)
    if type(value) is not int or not 64 <= value <= 4096:
        raise ValueError("work_width_px must be an int between 64 and 4096")
    return value


def _optional_bool(data: dict[str, object], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{key} must be true or false")
    return value


def _require_roi(value: object) -> Roi:
    if not isinstance(value, list) or len(value) != 4 or not all(type(item) is int for item in value):
        raise ValueError("roi must be null or [x, y, width, height]")
    x, y, width, height = value
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("roi must lie inside the image and have positive size")
    return Roi(x=x, y=y, width=width, height=height)


def _parse_simple_yaml(text: str) -> object:
    """Read the small config subset this package ships. No PyYAML dependency."""
    lines = text.splitlines()
    parsed, index = _parse_block(lines, 0, 0)
    while index < len(lines):
        if lines[index].strip() and not lines[index].lstrip().startswith("#"):
            raise ValueError(f"unexpected content on line {index + 1}")
        index += 1
    return parsed


def _parse_block(
    lines: list[str], index: int, indent: int
) -> tuple[dict[str, object], int]:
    result: dict[str, object] = {}
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        current = len(raw) - len(raw.lstrip(" "))
        if current < indent:
            break
        if current != indent:
            raise ValueError(f"bad indent on line {index + 1}")
        if ":" not in stripped:
            raise ValueError(f"expected key on line {index + 1}")
        key, rest = stripped.split(":", 1)
        key = key.strip()
        rest = _strip_comment(rest).strip()
        index += 1
        if rest == "":
            child, index = _parse_block(lines, index, indent + 2)
            result[key] = child
        else:
            result[key] = _parse_scalar(rest)
    return result, index


def _strip_comment(text: str) -> str:
    in_quote = False
    quote = ""
    for index, char in enumerate(text):
        if char in {"'", '"'} and (index == 0 or text[index - 1] != "\\"):
            if in_quote and char == quote:
                in_quote = False
            elif not in_quote:
                in_quote = True
                quote = char
        elif char == "#" and not in_quote:
            return text[:index]
    return text


def _parse_scalar(text: str) -> object:
    if text in {"null", "~"}:
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    if any(char.isspace() for char in text):
        raise ValueError(f"unsupported config value: {text}")
    return text
