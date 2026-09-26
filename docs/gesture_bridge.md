# Gesture Bridge

## Purpose

The gesture bridge owns robot gesture execution. It keeps the LiveKit agent and
supervisor command paths robot-agnostic by accepting gesture names and resolving
them to a robot-specific backend only inside the gesture service boundary.

Unsupported robots must fail cleanly and remain optional. Gesture availability
must not block audio, the LiveKit agent, or other supervisor services.

## Components

`robot_services/gestures/catalog.py`

Defines the shared catalog model:

- `GestureEntry(name, id, safety=unrestricted)`
- `GestureSafety`: `unrestricted`, `restricted`, `safe_only`
- derived safety pools
- name and numeric ID normalization
- prompt/list helper functions

Safety pools are derived from each entry's safety marker. Do not maintain
separate duplicate gesture lists for each pool.

`robot_services/gestures/catalogs/`

Contains concrete robot/backend catalogs. `unitree_g1_edu` maps gesture names
to Unitree G1 arm action IDs. `agibot_a2_ultra` maps curated semantic names to
live AIMA motion presets discovered through `ResourceService/GetMotion`.

`humanoid_platform/gestures.py`

Maps robot models to gesture support:

- backend ID
- catalog ID
- short notes

Unitree G1 Edu and Agibot A2 Ultra have gesture specs. X2 Ultra remains
unsupported until its concrete AIMDK/AIMA execution API is integrated here.

`robot_supervisor_v2/app/services/gesture_bridge.py`

Reads supervisor robot context, resolves `GestureSpec`, and starts
`robot_services/gestures/gesture_api.py` with:

- `GESTURE_BACKEND`
- `GESTURE_CATALOG_ID`
- `GESTURE_SAFETY_POOL`

If the configured robot has no gesture spec, startup fails before launching the
subprocess and the service is treated as optional.

For A2, discovered routines are capped at six seconds by default. Only explicit
per-action overrides may run longer. The Hall of Fame rotates three explicitly
curated routines: `panel explanation`, `panel explanation extended`, and
`panel explanation long`. They select the vendor's 22-, 25-, and 29-second
general-explanation presets with caps of 23, 26, and 30 seconds respectively.
This does not relax the limit for any other motion. AIMA exposes no verified
playback-speed field here, so the integration uses natively longer routines
instead of attempting to stretch a short motion.

`robot_services/gestures/gesture_api.py`

Loads the selected catalog/backend from environment, validates incoming gesture
names, applies the active safety pool, queues accepted gestures, and exposes
`/health`.

`robot_services/gestures/arm_controller.py`

Executes the selected backend. It currently implements the Unitree G1 arm action
client and receives its action mapping from the selected catalog.

## Runtime Flow

```text
agent or supervisor command
  -> semantic gesture name
  -> gesture API validation and safety pool check
  -> selected catalog maps name to backend ID
  -> backend executes robot-specific action
```

The agent should only import the shared gesture helpers from
`robot_services.gestures`. It should not import `humanoid_platform` or robot SDK
modules.

## Adding Future Robot Support

1. Add or update a catalog under `robot_services/gestures/catalogs/`.
   Use stable gesture names, backend IDs, and per-entry safety markers.

2. Add a backend enum value and `GestureSpec` entry in `humanoid_platform`.
   The spec should select the backend and catalog for the robot model.

3. Implement the backend inside the gesture service boundary.
   SDK imports should stay in the backend/controller path, not in the agent,
   platform registry, or catalog model.

4. Update `gesture_api.py` backend selection only as much as needed to route to
   the new backend.

5. Add focused tests:
   catalog names/IDs/pools, platform support mapping, unsupported robot behavior,
   and backend selection without requiring robot hardware.

Do not add robot-specific gesture behavior to prompts, the LiveKit agent, or the
top-level robot config. The top-level config selects `robot.platform` and
`robot.model`; support maps select the gesture behavior.
