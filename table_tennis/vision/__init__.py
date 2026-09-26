"""Table-tennis vision. This package does not import ROS, Supervisor, or Ultralytics."""

from table_tennis.vision.config import VisionConfig, load_config, load_example_config
from table_tennis.vision.frame import ORIGIN_A2_H264, ORIGIN_FILE, Frame

__all__ = [
    "Frame",
    "ORIGIN_A2_H264",
    "ORIGIN_FILE",
    "VisionConfig",
    "load_config",
    "load_example_config",
]
