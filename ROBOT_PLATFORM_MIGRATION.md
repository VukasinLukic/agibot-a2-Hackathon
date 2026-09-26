# Robot Platform Migration Notes

## Purpose

This document tracks the early direction for moving from a Unitree G1 Edu focused
implementation toward a multi-robot humanoid stack that can support Unitree G1
Edu, Agibot A2 Ultra, and Agibot X2 Ultra without duplicating the shared LiveKit
and supervisor work.

The detailed platform/model boundary is defined in
`ROBOT_PLATFORM_ABSTRACTION.md`.

The lean target supervisor config shape is defined in `ROBOT_CONFIG.md`.

## Direction

Keep the repository organized around:

- Shared orchestration core
- Minimal platform and robot model definitions
- Platform-specific adapters only when implementation needs them
- Per-robot profiles later, once config loading is ready

The LiveKit agent, supervisor API/UI, conversation lifecycle, transcript capture,
RAG, prompt/content services, conference scripts, and command byte-stream protocol
should remain robot-agnostic where possible.

Robot-specific behavior should live behind small platform/model support maps and
service adapters. Audio bridge topology, gesture execution, temperature
monitoring, and visual UI state are the concrete splits currently modeled.

## Initial Boundaries

- The agent should not import platform or robot model modules directly.
- Local and remote audio bridges should expose the same supervisor-facing control
  contract.
- Agibot runtime cleanup such as stopping `hal_audio` or `agent` should stay out
  of the LiveKit agent. A2 local audio and X2 remote audio handle AIMA EM audio
  ownership in their audio manager layer.
- Gesture execution should stay behind the gesture bridge. Unitree G1 Edu has a
  catalog/backend now; Agibot AIMDK/AIMA gesture support should be added only
  when the concrete execution API is known.
- Visual UI state should describe generic agent interaction state, not LEDs as a
  universal robot concept. Unitree G1 Edu maps that state to RGB LED control
  through the Unitree audio client. Agibot robots intentionally have no visual UI
  backend until the AIMDK/AIMA/VUI path is known.
- Long-lived robot-specific branches should be avoided once platform adapters and
  profiles exist on `main`.

## Candidate Shape

```text
humanoid_platform/
  types.py
  registry.py
  gestures.py
  temperature.py
  visual_ui.py
  platforms/
  robot_models/

robot_supervisor_v2/
  profiles/  # later

robot_services/
  gestures/
    catalog.py
    catalogs/
```

## Requirements Direction

Keep dependency management boring until the runtime split forces something more
specific.

The root `requirements.txt` is the default shared runtime install for the
supervisor, LiveKit agent, local audio bridge, camera bridge, and lightweight
repo utilities. It should stay grouped by runtime area so dependencies remain
auditable, but it should not become a robot capability catalog.

Service-specific environments should remain owned by the service that needs
them. Heavy or constrained stacks such as RAG, vision detection, teleoperation,
and hardware vendor development tools should keep their own requirements files
or deploy scaffolding. Do not fold those into the shared root install only
because one robot model can use them.

Robot model selection should decide behavior through `humanoid_platform` and
service adapters, not by importing vendor SDKs from shared code at module import
time. If a future robot model truly requires a different dependency set for the
main supervisor process, add a small documented profile then, preferably as a
composition of existing service groups rather than a copied full requirements
file.

For now:

- Keep `requirements.txt` as the shared main-process install.
- Keep special-service venvs out of the shared requirements.
- Keep apt/system package notes beside the service or deploy area that owns
  them.
- Add robot-specific dependency profiles only when a concrete model needs a
  different main-process environment.

## First Migration Targets

1. Define the minimal platform/model abstraction.
2. Define and wire the Agibot X2 remote audio bridge control boundary.
3. Keep gesture catalogs/backend selection inside the gesture bridge boundary.
4. Keep visual UI as an agent-side runtime with model-selected backends, not as a supervisor service.
5. Keep Agibot AIMA EM audio ownership inside the audio manager layer on the
   computer that owns audio devices; add broader AIMDK/AIMA integration
   boundaries only when concrete APIs are known.
6. Preserve the current Unitree G1 Edu setup as a legacy/reference profile.

See `docs/visual_ui.md` for the visual UI concept and current implementation map.
See `docs/temperature_monitor.md` for the temperature monitor platform boundary
and rules for adding future robot support.
See `docs/audio_bridge.md` and `docs/remote_audio_bridge.md` for the local and
remote audio bridge runtime boundaries. See
`docs/agibot/aima_em_service_manager.md` for the Agibot AIMA EM audio ownership
boundary.
