# Temperature Monitor

## Purpose

The robot temperature monitor streams motor temperature telemetry into the
supervisor API/UI. It is currently a narrow Unitree G1 Edu integration behind
the new platform/model selection layer.

This is intentionally not a generic telemetry framework yet. Unsupported robot
models should fail the temperature monitor cleanly while the rest of the
supervisor continues to run.

## Current Support

Supported robot models:

- `unitree_g1_edu`: supported through Unitree SDK2 low-state DDS telemetry.

Unsupported robot models:

- `agibot_a2_ultra`
- `agibot_x2_ultra`

For unsupported robots, the service reports `supported: false`, becomes optional
through `is_optional()`, and fails with an explanatory error if explicitly
started. This should not block LiveKit, audio, gestures, vision, or other
supervisor services.

## Code Map

- `humanoid_platform/types.py`
  Defines `TemperatureMonitorBackend` and `TemperatureMonitorSpec`.

- `humanoid_platform/temperature.py`
  Owns the support map from `RobotModelId` to `TemperatureMonitorSpec`.
  This is the first place to check when deciding whether a robot model should
  have temperature monitoring.

- `robot_supervisor_v2/app/supervisor_config.py`
  Loads and validates `robot.platform` and `robot.model`, then exposes
  `RobotConfig.to_service_context()`.

- `robot_supervisor_v2/app/api/main.py`
  Passes the supervisor robot context into migrated services through
  `SERVICE_ROBOT_CONTEXT_KEY`. The temperature monitor receives this context
  when the configured service type is `robot-temperature-monitor`.

- `robot_supervisor_v2/app/services/robot_temperature_monitor.py`
  Implements the runtime service. It resolves the configured robot model,
  checks `humanoid_platform.temperature`, and imports Unitree SDK modules only
  after support has been confirmed.

- `robot_supervisor_v2/frontend/src/components/teleoperation/RobotTemperaturePanel.tsx`
  Displays the latest temperature state in the teleoperation UI.

## Runtime Flow

```text
robot_supervisor_v2/config.yaml
  robot.platform + robot.model
        |
        v
load_startup_config()
        |
        v
RobotConfig.to_service_context()
        |
        v
RobotTemperatureMonitorService
        |
        v
humanoid_platform.get_temperature_monitor_spec(robot_model)
        |
        v
Unitree SDK2 rt/lowstate subscription, if supported
```

The service subscribes to `rt/lowstate` with `unitree_sdk2py` and reads
`LowState_.motor_state`. For each motor it looks for one of these fields:

- `temperature`
- `temp`
- `motor_temperature`

It publishes a snapshot containing per-motor temperatures, hottest motor,
warning/critical counts, freshness, and error state.

## Supervisor API

The latest snapshot is available through:

```text
GET /api/robot-temperature/status
```

The same state is also included in:

- `GET /api/status` as `robot_temperature`
- `GET /api/events` SSE payloads as `robot_temperature`

## Service Config

The service remains configured in the legacy `services` list:

```yaml
robot:
  platform: unitree
  model: unitree_g1_edu

services:
  - name: robot-temperature-monitor
    type: robot-temperature-monitor
    config:
      display_name: "Robot Temperature Monitor"
      topic: rt/lowstate
      network_interface: eth0
      startup_timeout: 5.0
      stale_timeout: 3.0
      warn_threshold_c: 65.0
      critical_threshold_c: 95.0
      manual_only: true
      optional: true
```

The selected robot model comes from the top-level `robot` section, not from the
service config. Do not add `platform`, `model`, or vendor flags under the
temperature monitor service.

## Clean Failure Rules

The service must reject unsupported configurations before loading vendor SDKs.
This keeps Agibot development machines from needing Unitree SDK dependencies
just because the temperature monitor service is present in config.

Expected clean failure cases:

- missing supervisor robot context
- unknown `robot.model`
- unknown `robot.platform`
- platform/model mismatch
- known robot model with no temperature monitor support
- known support spec using a backend that the service has not implemented yet

The tests in
`robot_supervisor_v2/testing_scripts/test_robot_temperature_monitor.py` cover
the current platform/model behavior.

## Adding Future Robot Support

Add support only when a real telemetry path is known.

1. Add a new backend enum to `TemperatureMonitorBackend` only if the existing
   `UNITREE_LOWSTATE` backend cannot represent the robot.
2. Add a `TemperatureMonitorSpec` entry for the robot model in
   `humanoid_platform/temperature.py`.
3. Keep SDK imports inside backend-specific runtime code, after the support
   check has passed.
4. Keep output shape compatible with the existing API fields where possible:
   `motors`, `temperature_c`, `max_temperature_c`, `hottest_motor`,
   `warning_count`, `critical_count`, freshness, and `last_error`.
5. Add focused tests for supported, unsupported, and misconfigured platform
   behavior before wiring UI assumptions to the new backend.

Do not add broad capability catalogs, host maps, PC2/PC3 topology, deployment
metadata, or service inventory fields just to support temperature monitoring.
