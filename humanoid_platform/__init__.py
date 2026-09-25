"""Shared platform and robot model definitions for humanoid deployments."""

from .registry import (
    get_platform,
    get_robot_model,
    list_platforms,
    list_robot_models,
    robot_models_for_platform,
)
from .gestures import (
    gesture_supported_models,
    get_gesture_spec,
)
from .head_screen import (
    get_head_screen_spec,
    head_screen_supported_models,
)
from .temperature import (
    get_temperature_monitor_spec,
    temperature_monitor_supported_models,
)
from .visual_ui import (
    get_visual_ui_spec,
    visual_ui_supported_models,
)
from .types import (
    AudioBridgeMode,
    AudioBridgeSpec,
    GestureBackend,
    GestureSpec,
    HeadScreenBackend,
    HeadScreenSpec,
    PlatformId,
    PlatformSpec,
    RobotModelId,
    RobotModelSpec,
    TemperatureMonitorBackend,
    TemperatureMonitorSpec,
    VisualUiBackend,
    VisualUiSpec,
)

__all__ = [
    "AudioBridgeMode",
    "AudioBridgeSpec",
    "GestureBackend",
    "GestureSpec",
    "HeadScreenBackend",
    "HeadScreenSpec",
    "PlatformId",
    "PlatformSpec",
    "RobotModelId",
    "RobotModelSpec",
    "TemperatureMonitorBackend",
    "TemperatureMonitorSpec",
    "VisualUiBackend",
    "VisualUiSpec",
    "gesture_supported_models",
    "get_gesture_spec",
    "get_head_screen_spec",
    "get_platform",
    "get_robot_model",
    "head_screen_supported_models",
    "get_temperature_monitor_spec",
    "get_visual_ui_spec",
    "list_platforms",
    "list_robot_models",
    "robot_models_for_platform",
    "temperature_monitor_supported_models",
    "visual_ui_supported_models",
]
