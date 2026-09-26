# AIMA EM Service Manager

## Purpose

The AIMA EM service manager is the optional Agibot-specific resource layer for
the computer that owns robot audio devices. It uses the `aima em` CLI locally to
control AIMA modules that can hold those devices.

- Agibot A2 Ultra: runs inside the local supervisor audio bridge service on PC2.
- Agibot X2 Ultra: runs inside the PC3 remote audio bridge manager.

This is only for Agibot audio ownership. It does not add Agibot behavior to
`audio_bridge.py` or the LiveKit agent.

## Runtime Modes

The manager has two operator modes when `agibot_aima.enabled` is true:

- `audio_bridge`
  Low-latency LiveKit mode. The manager stops configured AIMA audio owners and
  keeps them stopped so `POST /start` can launch `audio_bridge.py` without
  waiting on `aima em stop-app ...`.

- `agibot`
  Native Agibot mode. The manager starts configured AIMA apps so built-in
  Agibot audio and interaction behavior can run again.

The A2 PC2 and X2 PC3 defaults are `audio_bridge`. On manager startup, the
manager reconciles into this mode by running:

```text
aima em stop-app agent
aima em stop-app hal_audio
```

Stopping our LiveKit audio bridge does not restore AIMA apps. Restoring native
Agibot behavior is explicit:

```text
aima em start-app hal_audio
aima em start-app agent
```

## Configuration

The code default is disabled so non-Agibot and local development environments do
not execute `aima`. Agibot A2 auto-enables it from the supervisor robot model
context unless explicitly disabled. The X2 PC3 deployment example enables it in
`audio_bridge_manager.yaml`:

```yaml
agibot_aima:
  enabled: true
  command: aima
  default_mode: audio_bridge
  reconcile_on_manager_start: true
  auto_stop_before_bridge_start: false
  bridge_mode_stop_apps:
    - agent
    - hal_audio
  agibot_mode_start_apps:
    - hal_audio
    - agent
  command_timeout_seconds: 8.0
```

`auto_stop_before_bridge_start` should stay `false` for low latency. If the
manager is in `agibot` mode, `/start` fails with `409` and the operator must
switch to `audio_bridge` mode first.

## Manager APIs

On X2, AIMA endpoints are served by the PC3 manager and require the same bearer
token as the rest of that manager:

```text
Authorization: Bearer <manager-token>
```

- `GET /aima/status`
  Returns whether AIMA management is enabled, the current mode, configured app
  lists, last command results, and last error.

- `POST /aima/mode/audio-bridge`
  Stops configured `bridge_mode_stop_apps` and records `audio_bridge` mode.

- `POST /aima/mode/agibot`
  Starts configured `agibot_mode_start_apps` and records `agibot` mode. Returns
  `409` if the audio bridge child is running.

- `POST /aima/doctor`
  Runs `aima em doctor` and returns command output.

Examples from PC3:

```bash
curl -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  http://127.0.0.1:8766/aima/status

curl -X POST -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  http://127.0.0.1:8766/aima/mode/agibot

curl -X POST -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  http://127.0.0.1:8766/aima/mode/audio-bridge
```

## Supervisor Passthrough

The supervisor exposes the same logical endpoints for local A2 and remote X2
audio:

- `GET /api/audio-bridge/aima/status`
- `POST /api/audio-bridge/aima/mode/audio-bridge`
- `POST /api/audio-bridge/aima/mode/agibot`
- `POST /api/audio-bridge/aima/doctor`

For A2, these calls execute `aima em` locally on PC2. For X2, they proxy to the
PC3 manager. Non-Agibot local bridges report disabled AIMA management.

## Failure Rules

- If `aima` is missing, times out, or returns non-zero, the manager records
  `last_error` and returns a clean HTTP error.
- `/devices` does not change AIMA state.
- `/start` does not auto-stop AIMA apps by default; it requires
  `audio_bridge` mode.
- `/stop` only stops our bridge child and leaves AIMA state unchanged.
- switching to `agibot` mode refuses to run while the audio bridge process is
  active.

## Validation

Focused tests:

```bash
.venv/bin/python -m unittest \
  robot_supervisor_v2.testing_scripts.test_audio_bridge_manager \
  robot_supervisor_v2.testing_scripts.test_audio_bridge_remote
```
