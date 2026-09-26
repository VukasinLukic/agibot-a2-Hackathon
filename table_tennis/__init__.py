"""A2 table tennis referee: shared foundation (contracts, core, storage, API, sim, adapters).

Importing this package is intentionally cheap: it must never pull in ROS, GPU,
LiveKit, OpenCV, vendor SDKs or the Supervisor app. Hardware-facing code lives
behind explicit adapters that are only constructed in ``mode: real``.
"""

__version__ = "0.1.0"
