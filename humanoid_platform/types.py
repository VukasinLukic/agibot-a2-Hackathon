"""Canonical platform and robot model data types.

These models are intentionally small. They capture only the stable abstraction
needed before runtime wiring starts.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class PlatformId(str, Enum):
    """Vendor/runtime families supported by this repository."""

    AGIBOT = "agibot"
    UNITREE = "unitree"


class RobotModelId(str, Enum):
    """Functional robot models currently relevant to the project."""

    AGIBOT_A2_ULTRA = "agibot_a2_ultra"
    AGIBOT_X2_ULTRA = "agibot_x2_ultra"
    UNITREE_G1_EDU = "unitree_g1_edu"


class AudioBridgeMode(str, Enum):
    """Where the LiveKit audio bridge process runs relative to the supervisor."""

    LOCAL = "local"
    REMOTE = "remote"


class TemperatureMonitorBackend(str, Enum):
    """Backend used to read robot temperature telemetry."""

    UNITREE_LOWSTATE = "unitree_lowstate"


class GestureBackend(str, Enum):
    """Backend used to execute robot gestures."""

    UNITREE_G1_ARM_ACTIONS = "unitree_g1_arm_actions"
    AGIBOT_A2_MOTION_PLAYER = "agibot_a2_motion_player"


class VisualUiBackend(str, Enum):
    """Backend used to present agent visual state on the robot."""

    UNITREE_G1_AUDIO_LED = "unitree_g1_audio_led"


class HeadScreenBackend(str, Enum):
    """Backend used to show arbitrary content on the robot's head screen."""

    AGIBOT_EMOTICON_PLAYER = "agibot_emoticon_player"


class PlatformSpec(BaseModel):
    """Description of a vendor/runtime platform."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: PlatformId
    display_name: str
    sdk_modules: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class AudioBridgeSpec(BaseModel):
    """Minimal audio bridge topology selected by a robot model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: AudioBridgeMode
    notes: tuple[str, ...] = ()


class TemperatureMonitorSpec(BaseModel):
    """Temperature monitor implementation selected for a robot model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: TemperatureMonitorBackend
    notes: tuple[str, ...] = ()


class GestureSpec(BaseModel):
    """Gesture implementation selected for a robot model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: GestureBackend
    catalog_id: str
    notes: tuple[str, ...] = ()


class VisualUiSpec(BaseModel):
    """Visual UI implementation selected for a robot model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: VisualUiBackend
    notes: tuple[str, ...] = ()


class HeadScreenSpec(BaseModel):
    """Head screen implementation selected for a robot model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: HeadScreenBackend
    notes: tuple[str, ...] = ()


class RobotModelSpec(BaseModel):
    """Functional robot model spec.

    This should contain behavior-changing defaults only. It should not contain
    deployment metadata such as public IPs, location, customer/site names, or
    secrets.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: RobotModelId
    platform: PlatformId
    display_name: str
    audio_bridge: AudioBridgeSpec
    notes: tuple[str, ...] = ()
