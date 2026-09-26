# Robot Platform Abstraction

## Purpose

This document defines the first, intentionally small abstraction boundary for
supporting multiple humanoid robots in the same LiveKit supervisor solution.

The goal is not to model every future robot capability up front. The goal is to
name the current platforms and robot models, and to capture concrete runtime
splits only when they affect code behavior.

## Current Assumption

The supervisor runs on the robot development PC.

For the current robots, that means we should not model a broad onboard-computer
topology yet. Labels such as PC2/PC3 only matter when they change runtime
behavior. Right now the only place that is clearly true is Agibot X2 audio:
X2 audio devices are owned by PC3, so the audio bridge must be remote from the
supervisor.

## Current Model

The current code model has six concepts:

- `Platform`: vendor/runtime family.
- `RobotModel`: functional robot product.
- `AudioBridgeSpec`: local or remote audio bridge mode.
- `GestureSpec`: gesture backend and catalog selected for a robot model.
- `TemperatureMonitorSpec`: telemetry backend selected for a robot model.
- `VisualUiSpec`: visual feedback backend selected for a robot model.

Implemented platforms:

- `agibot`
- `unitree`

Implemented robot models:

- `agibot_a2_ultra`
- `agibot_x2_ultra`
- `unitree_g1_edu`

Implemented audio bridge modes:

- `local`: bridge runs on the same development PC as the supervisor.
- `remote`: bridge runs elsewhere and is controlled/monitored by the supervisor.

Implemented visual UI backends:

- `unitree_g1_audio_led`: maps agent state to Unitree G1 RGB LED control.

Implemented temperature monitor backends:

- `unitree_lowstate`: reads Unitree SDK2 low-state DDS motor telemetry.

## What Is Deliberately Not Modeled Yet

The first abstraction layer does not model:

- service lists
- broad robot capability catalogs
- locomotion
- vision topology
- broad Agibot runtime preflight outside audio-device ownership
- Agibot visual UI/VUI/AIMDK integration
- deployment metadata
- public IPs, site locations, customer names, or secrets
- cosmetic UI branding

Those should be added only when we have a concrete runtime integration that
needs the abstraction.

## Current Code Layout

```text
humanoid_platform/
  types.py
  registry.py
  gestures.py
  temperature.py
  visual_ui.py
  platforms/
    agibot.py
    unitree.py
  robot_models/
    agibot_a2_ultra.py
    agibot_x2_ultra.py
    unitree_g1_edu.py
```

`humanoid_platform` is runtime-neutral. It should not import LiveKit, supervisor
services, Agibot SDK modules, Unitree SDK modules, or hardware libraries.

## Audio Boundary

Audio is the first practical platform boundary.

Unitree G1 Edu and Agibot A2 Ultra currently use local audio:

```text
development PC -> supervisor -> local audio bridge -> LiveKit room
```

For Agibot A2 Ultra, the local supervisor audio bridge service can also manage
AIMA EM audio ownership on PC2 before the bridge process starts.

Agibot X2 Ultra uses remote audio:

```text
development PC -> supervisor audio-bridge service -> PC3 manager -> PC3 audio bridge -> LiveKit room
```

The supervisor treats both through the same high-level audio bridge control
surface named `audio-bridge`. The robot model only says `local` or `remote`;
deployment/service config supplies concrete PC3 manager URLs and tokens.

The current supervisor implementation injects robot context into the
`audio-bridge` service at startup. `ServiceRegistry` resolves the robot model
and creates either the local `AudioBridgeService` or the remote
`AudioBridgeRemoteService`.

For the optional AIMA EM audio-resource mode manager used by A2 local audio and
X2 remote audio, see `docs/agibot/aima_em_service_manager.md`.

## Gesture Boundary

Gestures are the second practical platform boundary.

The LiveKit agent and supervisor command paths use gesture names only. The
gesture bridge resolves the configured robot model to a `GestureSpec`, then
starts the gesture API with a concrete backend and catalog.

Current gesture support:

- `unitree_g1_edu`: supported through Unitree G1 arm action IDs.
- `agibot_a2_ultra`: unsupported until AIMDK/AIMA gesture execution is defined.
- `agibot_x2_ultra`: unsupported until AIMDK/AIMA gesture execution is defined.

Unsupported robots should fail the gesture bridge cleanly and remain optional,
so the agent, audio, and other supervisor services can continue running.

See `docs/gesture_bridge.md` for the component map and the steps for adding a
new robot gesture backend.

## Visual UI Boundary

Visual UI is the third practical platform boundary.

The shared behavior is intentionally generic: the LiveKit agent emits interaction
states such as `listening`, `thinking`, and `speaking`. A robot model may map
those states to a concrete visual system.

Current visual UI support:

- `unitree_g1_edu`: supported through Unitree G1 `AudioClient.LedControl`.
- `agibot_a2_ultra`: unsupported until the AIMDK/AIMA/VUI path is defined.
- `agibot_x2_ultra`: unsupported until the AIMDK/AIMA/VUI path is defined.

This is not a supervisor service today. It is an agent-side runtime because it
binds directly to LiveKit `agent_state_changed` events. The supervisor only
passes `HUMANOID_ROBOT_MODEL` into the voice agent process so the runtime can
select the right model behavior.

See `docs/visual_ui.md` for the component map, environment variables, and
compatibility notes.

## Temperature Monitor Boundary

Temperature monitoring is the fourth practical platform boundary, but it is
still intentionally narrow.

Current temperature monitor support:

- `unitree_g1_edu`: supported through Unitree SDK2 `LowState_` messages on
  `rt/lowstate`.
- `agibot_a2_ultra`: unsupported until a real Agibot telemetry source is known.
- `agibot_x2_ultra`: unsupported until a real Agibot telemetry source is known.

The supervisor service receives the configured `robot.platform` and
`robot.model` from the new top-level robot config. It resolves that model
through `humanoid_platform.temperature` before importing any vendor SDK. If the
model is unsupported, only the temperature monitor should fail; the rest of the
supervisor should continue.

See `docs/temperature_monitor.md` for the component map, API surface, and rules
for adding future support.

## Documentation Requirement

This migration should produce documentation as a first-class output. Coding
agents should be able to answer these questions from docs before reading large
parts of the codebase:

- What starts the system?
- Which code is shared core?
- Which code is robot/platform-specific?
- How does audio move between robot hardware, LiveKit, and the agent?
- How do supervisor commands reach the agent?
- How do platform profiles select runtime behavior?

The docs should stay code-backed: every important flow should include the main
entrypoint files and service contracts.

## Next Abstractions To Add Later

Do not add these until implementation pressure justifies them:

1. Broader Agibot runtime preflight beyond audio-device ownership.
2. Agibot gesture/action execution through AIMDK/AIMA.
3. Agibot visual UI/VUI/AIMDK integration once the concrete API is known.
4. Unitree G1 legacy/reference profile.
