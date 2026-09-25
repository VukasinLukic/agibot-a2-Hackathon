# Visual UI

## Purpose

Visual UI is the robot-facing visual feedback layer for the LiveKit agent.

The abstraction is named `visual_ui` instead of `led` because LEDs are only one
possible implementation. Other robots may use a face display, a VUI process,
AIMDK/AIMA visual behavior, or a remote visual bridge.

The shared concept is simple:

```text
LiveKit agent state -> generic visual UI state -> robot-model backend
```

Current generic states:

- `listening`
- `thinking`
- `speaking`

## Current Support

Only Unitree G1 Edu has a visual UI backend today.

```text
unitree_g1_edu -> unitree_g1_audio_led -> Unitree AudioClient.LedControl
```

Agibot A2 Ultra and Agibot X2 Ultra intentionally have no visual UI backend for
now. The runtime should disable itself cleanly on those models.

```text
agibot_a2_ultra -> no visual UI backend
agibot_x2_ultra -> no visual UI backend
```

This avoids guessing how Agibot AIMDK, AIMA, AIMA EM, or VUI should own visual
feedback before the real integration path is known.

## Head Screen Is A Separate Concern

Visual UI maps *agent state* to a continuous indicator. The Agibot head screen
is a different thing: a one-shot content surface used to show a readout (a time,
a temperature) for a couple of seconds and then restore the default face. It is
modelled separately as `head_screen`, so turning it on does not change the
robot's face on every listening/thinking/speaking transition.

```text
agibot_a2_ultra -> agibot_emoticon_player -> RcEmoticonPlayerService/PlayerEmoticon
```

- `humanoid_platform/head_screen.py`, `HeadScreenBackend`, `HeadScreenSpec`
- `robot_services/screen_manip/`
- `docs/agibot/head_screen.md` for the mechanism and its gotchas

Mapping the three generic visual UI states onto A2 emoticons (for example
`thinking` -> emoticon 21) is possible with the same backend but is deliberately
not wired up: it would change the robot's face throughout every conversation.

## Component Map

Platform/model declaration:

- `humanoid_platform/types.py`
- `humanoid_platform/visual_ui.py`
- `humanoid_platform/__init__.py`

Agent runtime:

- `livekit-client/robot/visual_ui_runtime.py`

Unitree backend:

- `livekit-client/robot/visual_ui_controller.py`

Compatibility wrappers:

- `livekit-client/robot/led_runtime.py`
- `livekit-client/robot/led_controller.py`

Active agent integration:

- `livekit-client/agent_main.py`

Supervisor environment handoff:

- `robot_supervisor_v2/app/api/main.py`
- `robot_supervisor_v2/app/services/voice_agent.py`

Tests:

- `robot_supervisor_v2/testing_scripts/test_visual_ui_runtime.py`
- `robot_supervisor_v2/testing_scripts/test_humanoid_platform.py`

## Runtime Flow

`AgentVisualUiRuntime` binds to the LiveKit `AgentSession`.

```text
AgentSession.agent_state_changed
  -> AgentVisualUiRuntime
  -> visual UI state queue
  -> selected backend controller
```

The runtime maps LiveKit agent states like this:

- `thinking` -> `thinking`
- `speaking` -> `speaking`
- `listening` -> `listening`
- `idle` -> `listening`

Unknown states are ignored.

The runtime coalesces state updates while the worker is busy. If `thinking`,
`speaking`, and `listening` arrive while a previous update is blocked, only the
latest pending state needs to be applied after the blocked update finishes.

## Backend Selection

Backend selection is model-driven.

The supervisor passes robot identity into the voice agent process:

- `HUMANOID_ROBOT_ID`
- `HUMANOID_ROBOT_NAME`
- `HUMANOID_ROBOT_PLATFORM`
- `HUMANOID_ROBOT_MODEL`

`AgentVisualUiRuntime` reads `HUMANOID_ROBOT_MODEL` first, then `ROBOT_MODEL` as
a fallback. If neither is set, it defaults to `unitree_g1_edu` for compatibility
with the original Unitree-focused agent startup.

The model is resolved through `humanoid_platform.get_visual_ui_spec`.

If the model has no spec, visual UI is disabled without importing the Unitree
controller. This is the expected path for Agibot A2 Ultra and X2 Ultra today.

## Unitree G1 Backend

`UnitreeG1AudioLedVisualUiController` uses:

```python
unitree_sdk2py.g1.audio.g1_audio_client.AudioClient
```

It calls:

```python
AudioClient.LedControl(r, g, b)
```

Default color mapping:

- `listening`: green, `(0, 255, 0)`
- `thinking`: orange, `(255, 140, 0)`
- `speaking`: blue, `(0, 0, 255)`

The backend enforces a minimum interval between Unitree LED calls because the
Unitree SDK documentation requires more than 200 ms between calls.

## Environment Variables

Preferred visual UI variables:

- `VISUAL_UI_ENABLE`: set to `0`, `false`, `no`, or `off` to disable visual UI.
- `VISUAL_UI_INTERFACE`: network interface for the Unitree backend.
- `VISUAL_UI_FORCE_REASSERT_MS`: optional interval for reasserting the latest state.

Compatibility variables still supported:

- `LED_ENABLE`
- `LED_INTERFACE`
- `LED_FORCE_REASSERT_MS`

Other existing robot-level variables still affect the Unitree backend:

- `AUDIO_TARGET=host` disables robot hardware control.
- `ROBOT_ENABLE=0` disables robot hardware control.
- `ROBOT_INTERFACE` and `UNITREE_NET_IF` are fallback interface names.

Interface precedence:

```text
VISUAL_UI_INTERFACE
LED_INTERFACE
ROBOT_INTERFACE
UNITREE_NET_IF
eth0
```

Force reassert precedence:

```text
VISUAL_UI_FORCE_REASSERT_MS
LED_FORCE_REASSERT_MS
```

## Compatibility

The old LED module names remain available:

```python
from robot.led_runtime import AgentLedRuntime
from robot.led_controller import RobotLedController, RobotLedConfig
```

They resolve to the renamed visual UI implementation.

New code should use:

```python
from robot.visual_ui_runtime import AgentVisualUiRuntime
from robot.visual_ui_controller import (
    UnitreeG1AudioLedVisualUiConfig,
    UnitreeG1AudioLedVisualUiController,
)
```

The compatibility wrappers exist only to keep older agent variants and local
scripts working during migration.

## Adding A Future Agibot Backend

Do not add an Agibot visual UI backend until the concrete control path is known.

When it is known, add:

1. A new `VisualUiBackend` enum value in `humanoid_platform/types.py`.
2. A model mapping in `humanoid_platform/visual_ui.py`.
3. A backend controller with the same minimal async methods:
   - `prepare()`
   - `set_listening()`
   - `set_thinking()`
   - `set_speaking()`
   - `aclose()`
4. Runtime backend selection in `livekit-client/robot/visual_ui_runtime.py`.
5. Tests that prove Agibot models no longer use the no-backend path.

Only make it a supervisor service if the concrete Agibot implementation needs a
long-running local or remote process independent of the LiveKit agent session.
