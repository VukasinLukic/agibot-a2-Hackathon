# Remote Audio Bridge

## Purpose

Remote audio bridge mode is for robots where the audio hardware is not on the
same machine as the supervisor. The current target is Agibot X2, where PC3 owns
the microphone and speaker devices while the supervisor runs elsewhere.

The remote design keeps the media path direct and low latency:

```text
PC2 supervisor or operator
        |
        | HTTP control through AudioBridgeRemoteService
        v
PC3 audio_bridge_manager.py on 0.0.0.0:8766
        |
        | local subprocess ownership
        v
PC3 audio_bridge.py child with local control API on 127.0.0.1:8767
        |
        | LiveKit media
        v
LiveKit room
```

The manager is control-plane only. It does not connect to LiveKit, publish
tracks, play remote audio, or open PC3 audio devices. The actual
`audio_bridge.py` child does that work only between `/start` and `/stop`.

## Components

- `robot_services/audio/audio_bridge_manager.py`
  Runs continuously on PC3. It authenticates control requests, lists devices by
  shelling out to the bridge script, starts/stops the real bridge child, and
  proxies runtime control calls to the child while it is running.

- `robot_services/audio/audio_bridge.py`
  The real audio bridge. It enumerates PortAudio/ALSA devices, connects to
  LiveKit, publishes the selected microphone, plays selected remote tracks, and
  exposes its localhost control API.

- `robot_services/audio/audio_bridge_manager.example.yaml`
  Example long-lived PC3 manager configuration.

- `docs/agibot/aima_em_service_manager.md`
  Focused contract for the optional Agibot AIMA EM audio-resource mode manager.

- `robot_supervisor_v2/app/services/audio_bridge_remote.py`
  Supervisor-side service wrapper. It keeps the existing `audio-bridge`
  service name and API contract, but maps supervisor lifecycle and control calls
  to the PC3 manager.

- `humanoid_platform/robot_models/agibot_x2_ultra.py`
  Marks X2 as `remote` audio bridge mode. The supervisor service registry uses
  this model flag to choose the remote wrapper for the configured
  `audio-bridge` service.

## Lifecycle

The manager is the only always-running process in this first migration step.
The child bridge process is intentionally transient:

1. `GET /devices` runs `audio_bridge.py --list-devices` on PC3 and returns that
   payload. It does not start the bridge or hold audio devices.
2. `POST /start` starts `audio_bridge.py` as a child process with the requested
   device/runtime options.
3. The child opens devices, connects to LiveKit, publishes microphone audio, and
   plays remote tracks.
4. `POST /stop` terminates the child and releases devices and tracks.
5. `POST /restart` performs a deterministic stop/start with the supplied
   request body, or with bridge defaults if no body is supplied.

This matches the classic local bridge lifecycle: devices and LiveKit tracks
exist only while the audio bridge service is actively running.

## API

The manager binds to `0.0.0.0:8766` by default. All endpoints except
`GET /health` require:

```text
Authorization: Bearer <manager-token>
```

Endpoints:

- `GET /health`
  Manager liveness only. It does not report child bridge state.

- `GET /status`
  Returns manager state, child state, child PID, uptime, last error, sanitized
  config, and proxied child `/status` when the child is running.

- `GET /devices`
  Returns the exact JSON payload from `audio_bridge.py --list-devices` on PC3.

- `GET /config`
  Returns long-lived manager config with token and LiveKit secrets redacted.

- `GET /logs?lines=100`
  Returns the recent child bridge log from the manager-configured
  `child_log_path`. The manager redacts its bearer token and configured LiveKit
  key/secret values before returning content.

- `GET /aima/status`
  Returns optional PC3 AIMA EM resource-management state, including enabled
  flag, current mode, configured apps, last command results, and last error.

- `POST /aima/mode/audio-bridge`
  Stops configured AIMA audio owners and records `audio_bridge` mode. The
  default X2 PC3 command order is `agent`, then `hal_audio`.

- `POST /aima/mode/agibot`
  Starts configured AIMA apps and records `agibot` mode. This returns `409` if
  the bridge child is running. The default command order is `hal_audio`, then
  `agent`.

- `POST /aima/doctor`
  Runs `aima em doctor` on PC3 and returns command output.

- `POST /start`
  Starts the child bridge and waits for the child to report ready after LiveKit
  connect, microphone publish, and audio-device startup. Returns `409` if the
  child is already running. Returns a clean startup failure if the child exits,
  cannot connect to LiveKit, cannot publish the microphone track, or cannot open
  the selected audio devices. If AIMA management is enabled, this also returns
  `409` unless the manager is already in `audio_bridge` mode.

- `POST /stop`
  Stops the child bridge. Safe to call when already stopped.

- `POST /restart`
  Stops any running child and starts a new one.

- `POST /mute`
  Proxies to child `/mute`. Returns `409` when the child is stopped.

- `POST /input-gain`
  Proxies to child `/input-gain`. Returns `409` when the child is stopped.

- `POST /remote-playback/release`
  Proxies to child `/remote-playback/release`. Returns `409` when the child is
  stopped.

Example start request:

```json
{
  "input_device": "plughw:CARD=RX,DEV=0",
  "output_devices": ["plughw:CARD=Speaker,DEV=0"],
  "input_capture_channels": 1,
  "input_mix_channels": [0],
  "enable_aec": true,
  "enable_rnnoise": false
}
```

Runtime fields in the start request are per-run only. The manager does not write
them back to YAML.

## Child Bridge Command

The manager starts the child using the existing bridge CLI:

```text
python audio_bridge.py
  --name <bridge_name>
  --control-port <child_control_port>
  --mic-track-name <mic_track_name>
  --input-device <input_device, if supplied>
  --input-capture-channels <input_capture_channels, if supplied>
  --input-mix-channel <channel, repeated when supplied>
  --output-device <output_device, repeated>
  --disable-aec, when enable_aec is false
  --enable-rnnoise, when enable_rnnoise is true
```

The child bridge currently binds its control API to `127.0.0.1` in
`audio_bridge.py`; the manager uses `child_control_host` only as the URL for
proxying local child requests. The default child control port is `8767` so it
does not collide with the manager API on `8766`.

## PC3 Manager Configuration

Long-lived manager config comes from YAML plus PC3 environment overrides. The
example file is:

```text
robot_services/audio/audio_bridge_manager.example.yaml
```

Important YAML fields:

- `host`, `port`
- `manager_token`
- `python_executable`
- `working_dir`
- `bridge_script`
- `child_log_path`
- `bridge_name`
- `mic_track_name`
- `child_control_host`, `child_control_port`
- child startup/request/stop/list-device timeouts
- LiveKit URL, room, API key, and API secret
- optional `agibot_aima` settings for PC3 AIMA EM audio ownership

See `docs/agibot/aima_em_service_manager.md` for the focused AIMA EM mode
contract, including how the same helper is used locally on A2 and remotely on
X2.

Environment overrides:

- `AUDIO_BRIDGE_MANAGER_TOKEN`
- `LIVEKIT_URL`
- `LIVEKIT_ROOM`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `AUDIO_BRIDGE_IDENTITY`
- `LIVEKIT_TRACK_NAME`

For normal PC3 setup, use the deploy scaffold:

```bash
cd /agibot/data/home/agi/humanoid-livekit
bash deploy/pc3/audio-bridge/install.sh
```

The installer creates the repo-local venv, writes initial files under
`/etc/humanoid-livekit/`, installs the manager systemd unit, and starts the
manager. The example PC3 config enables `agibot_aima` and reconciles to
`audio_bridge` mode on manager startup, stopping `agent` and `hal_audio` once
so bridge startup stays low latency. After installation, edit
`/etc/humanoid-livekit/audio-bridge-manager.env` with the real LiveKit values
before starting the child bridge from the supervisor.

For a development-only foreground run on PC3:

```bash
export AUDIO_BRIDGE_MANAGER_CONFIG=/path/to/audio_bridge_manager.yaml
export AUDIO_BRIDGE_MANAGER_TOKEN=<shared-token>
python3 robot_services/audio/audio_bridge_manager.py
```

The manager is intended to become the systemd-managed process on PC3. The child
bridge should remain manager-owned for this version, not a separate always-on
systemd bridge service.

PC3 setup scaffolding lives in:

```text
deploy/pc3/audio-bridge/
```

It includes Debian/Ubuntu apt packages, a minimal Python requirements file, a
systemd unit for the PC3 manager, and an installer script that creates the venv,
writes initial config files, registers the unit, and starts the manager.

## Supervisor Configuration

The supervisor keeps the same service name and frontend/API contract:

```yaml
services:
  - name: audio-bridge
    type: audio-bridge
    config:
      display_name: "Audio Bridge"
      manager_url: http://<pc3-ip>:8766
      manager_token_env: AUDIO_BRIDGE_MANAGER_TOKEN
      default_microphone: null
      default_speakers: null
      input_capture_channels: null
      input_mix_channels: []
      enable_aec: true
      enable_rnnoise: false
```

Supervisor-side fields:

- `manager_url`
  URL of the PC3 manager API, for example `http://10.0.0.23:8766`. If omitted,
  the remote wrapper falls back to `AUDIO_BRIDGE_MANAGER_URL`.

- `manager_token`
  Inline bearer token for the PC3 manager. Prefer `manager_token_env` for local
  config files.

- `manager_token_env`
  Environment variable name that contains the PC3 manager bearer token. Defaults
  to `AUDIO_BRIDGE_MANAGER_TOKEN`.

- `default_microphone`
  Per-run input device passed to PC3 manager `/start` as `input_device`.

- `default_speakers`
  Per-run output device passed to PC3 manager `/start` as one `output_devices`
  entry. Lists are also accepted by the remote wrapper for multi-output starts.

- `input_capture_channels`
  Per-run input channel count passed to PC3 manager `/start`. Use `8` for the
  Rockchip ES7210 mic array when direct ALSA capture works as eight channels.

- `input_mix_channels`
  Zero-based captured input channels averaged into the mono LiveKit microphone
  track. Start with `[0]` for the Rockchip ES7210 array; `[0, 1, 2, 3]` is a
  simple downmix fallback, not beamforming.

- `enable_aec`, `enable_rnnoise`
  Per-run processing flags passed to PC3 manager `/start`.

At startup, the supervisor injects the top-level robot context into the
`audio-bridge` service config. The service registry resolves
`robot.model` through `humanoid_platform`:

- `local` models use `AudioBridgeService` and start `audio_bridge.py` locally.
- `remote` models use `AudioBridgeRemoteService` and call the PC3 manager.

The frontend still uses:

- `GET /api/devices/audio`
- `GET /api/services/audio-bridge/logs`
- `GET /api/services/audio-bridge/logs/stream`
- `GET /api/audio-bridge/config`
- `POST /api/audio-bridge/config`
- `GET /api/audio-bridge/status`
- `POST /api/audio-bridge/mute`
- `POST /api/audio-bridge/input-gain`
- `GET /api/audio-bridge/aima/status`
- `POST /api/audio-bridge/aima/mode/audio-bridge`
- `POST /api/audio-bridge/aima/mode/agibot`
- `POST /api/audio-bridge/aima/doctor`

The remote wrapper translates those calls to the manager API. There is no
separate UI-visible `audio-bridge-remote` service type.

The supervisor service status reports `backend: remote-manager` and
`current_mode: remote` for the remote wrapper. PC3 child logs are owned by the
manager and live at the manager-configured `child_log_path`, not in the local
supervisor log directory. The supervisor log endpoints call manager
`GET /logs`, so the existing service log viewer can show and stream remote
child logs without mounting the PC3 filesystem on PC2.

## End-To-End Startup

1. Start LiveKit on PC2 or ensure the configured LiveKit server is reachable.
2. Start the PC3 manager with `AUDIO_BRIDGE_MANAGER_CONFIG` and
   `AUDIO_BRIDGE_MANAGER_TOKEN`.
3. Configure PC2 supervisor `robot.model: agibot_x2_ultra`.
4. Configure PC2 supervisor `services.audio-bridge.config.manager_url` and
   `manager_token_env`.
5. Confirm PC3 manager `GET /aima/status` reports `audio_bridge` mode when
   AIMA management is enabled.
6. Start supervisor service `audio-bridge`.
7. The supervisor calls PC3 manager `/start`; PC3 manager starts
   `audio_bridge.py`; the child opens PC3 devices and connects to LiveKit.

## Clean Failure Rules

Expected clean failures:

- Missing or invalid bearer token returns an auth error.
- Missing remote `manager_url` or manager token fails remote service startup.
- `/start` returns `409` when the child is already running.
- `/start` returns `409` when AIMA management is enabled but the manager is not
  in `audio_bridge` mode.
- `/start` returns a child startup failure when LiveKit config is missing,
  LiveKit is unreachable, token/room connection fails, microphone publishing
  fails, or selected audio devices cannot be opened.
- `/aima/mode/agibot` returns `409` while the bridge child is running.
- Proxy controls return `409` when the child is stopped.
- Device listing subprocess failures return an HTTP error with the bridge
  failure detail.
- Invalid device-list JSON returns a clean parse error.
- Child startup failures include recent child log output when available, record
  `last_error`, stop the child, and clean manager state.

The manager should not invent devices, silently pick replacement hardware, or
keep a child process alive after a failed start.

## Validation

Focused checks:

```bash
.venv/bin/python -m unittest \
  robot_supervisor_v2.testing_scripts.test_audio_bridge_remote \
  robot_supervisor_v2.testing_scripts.test_audio_bridge_manager
```

The remote supervisor wrapper tests mock manager HTTP calls. They verify model
selection, startup payloads, device listing, status/proxy controls, token env
handling, and clean failed-state reset.

## Agibot Notes

The supervisor remote wrapper still controls only the PC3 manager API; it does
not shell into PC3. The PC3 manager can optionally use `aima em` locally to
switch between low-latency `audio_bridge` mode and native `agibot` mode. Keep
broader Agibot AIMDK/AIMA features outside the audio bridge until their concrete
control APIs are known. See `docs/agibot/AIMA_EM.md` and
`docs/agibot/aima_em_service_manager.md`.
