# Audio Bridge

## Purpose

The audio bridge connects local robot audio devices to LiveKit. It publishes the
selected microphone as a LiveKit audio track and plays selected remote LiveKit
audio tracks through the selected speaker output.

The bridge should stay robot-agnostic. Robot model selection may decide whether
the bridge is local or remote, but device discovery and selection should not
depend on hard-coded vendor device lists in shared supervisor code.

## Current Support

Supported topology today:

- `unitree_g1_edu`: local bridge mode. The supervisor starts
  `robot_services/audio/audio_bridge.py` directly.
- `agibot_a2_ultra`: local bridge mode on PC2. The local supervisor audio
  bridge service can keep AIMA audio owners stopped for low-latency startup.
- `agibot_x2_ultra`: remote bridge mode. The supervisor keeps the service name
  `audio-bridge` but selects the PC3 manager wrapper from the configured robot
  model. The PC3 manager can optionally keep AIMA audio owners stopped for
  low-latency bridge startup.

For concrete Agibot audio module notes, see `docs/agibot/AIMA_EM.md`.

## Code Map

- `humanoid_platform/types.py`
  Defines `AudioBridgeMode` and `AudioBridgeSpec`.

- `humanoid_platform/robot_models/*.py`
  Selects local versus remote bridge topology per robot model.

- `robot_supervisor_v2/app/services/audio_bridge.py`
  Owns the supervisor service wrapper. It lists devices by running the bridge
  script with `--list-devices`, starts the bridge subprocess, passes configured
  defaults as CLI arguments, and proxies localhost control requests.

- `robot_supervisor_v2/app/services/audio_bridge_remote.py`
  Owns the supervisor wrapper for remote bridge mode. It preserves the same
  service contract but calls the PC3 manager for device listing, lifecycle, and
  runtime control.

- `robot_services/audio/audio_bridge.py`
  Owns PortAudio device enumeration, LiveKit microphone publishing, LiveKit
  remote playback, AEC/RNNoise processing, and the local control API.

- `robot_services/audio/audio_bridge_manager.py`
  Owns the first PC3 remote-manager control API. It advertises PC3 devices and
  starts/stops the real bridge child process on request.

- `robot_supervisor_v2/frontend/src/components/services/ServiceConfigRow.tsx`
  Renders the supervisor UI microphone/speaker selectors for the audio bridge.

## Device Discovery

Device streaming is PortAudio-based through `sounddevice`. On Linux, discovery
also attaches generic ALSA identity metadata from `arecord`, `aplay`, and udev
when available.

```bash
python3 robot_services/audio/audio_bridge.py --list-devices
```

The JSON result has separate lists:

- `input_devices`
- `output_devices`

Each listed device has an `index`, `name`, `channels`, `sample_rate`, `hostapi`,
and `is_default` marker. Linux devices may also include `alsa_device`,
`alsa_card_id`, `alsa_device_index`, `id_path`, `id_path_tag`, and serial
metadata.

ALSA-only identities may be listed so operators can see stable Linux device
names such as `plughw:CARD=RX,DEV=0`. These are still resolved back to a real
PortAudio-openable device before streaming starts.

The supervisor no longer fabricates legacy device options. Do not reintroduce
hard-coded shared lists such as:

- `wireless_rx_mic`
- `combined_speakers`
- forced EarPods slots
- forced JBL slots
- forced Zgmicro slots
- hard-coded ALSA path-based device slots

If a platform needs richer device ownership later, add it behind a small
platform/model boundary instead of scattering vendor checks through shared UI or
service code.

## Default Device Config

The supervisor audio bridge wrappers read active device defaults from the legacy
service entry:

```yaml
services:
  - name: audio-bridge
    type: audio-bridge
    config:
      default_microphone: null
      default_speakers: null
```

`null` means no explicit device is passed to the bridge, so PortAudio may use
its system default.

Non-null values may be:

- a PortAudio device index, for example `"1"`
- an exact PortAudio device name, for example `"MacBook Pro Microphone"`
- a Linux ALSA identifier, for example `"plughw:CARD=RX,DEV=0"` or `"hw:4,0"`

Digit strings are normalized to integer PortAudio indices in the bridge. Names
are matched exactly first, then case-insensitively. Substring matching is
intentionally not supported because it can select the wrong device when several
USB devices have similar names. ALSA identifiers are matched against generic
Linux ALSA metadata and must resolve to a usable PortAudio device before the
bridge starts.

On startup, unresolved configured devices fail cleanly. The bridge raises an
error that includes the missing configured value and the available device list.
This is preferable to silently falling back to a different microphone or
speaker.

Mic-array inputs can be opened with more capture channels than the mono
LiveKit track uses. Set `input_capture_channels` to the number of hardware
channels to open and `input_mix_channels` to the zero-based captured channel or
channels to publish as mono. The default is one captured channel with
`input_mix_channels: [0]`, which preserves legacy behavior. For the Rockchip
ES7210 array, start with:

```yaml
default_microphone: "hw:2,0"
input_capture_channels: 8
input_mix_channels: [0]
```

## Supervisor UI Behavior

The audio bridge row loads two things:

- `GET /api/devices/audio`
- `GET /api/audio-bridge/config`

If the configured default matches a current device by select value, PortAudio
index, full name, `id_path`, or `alsa_device`, the UI selects that live device.
When a live device has an `alsa_device`, the UI saves that stable value instead
of the PortAudio index. Devices without ALSA metadata still use the PortAudio
index.

If the configured value does not exist in the live device list, the UI still
shows it as a disabled option:

```text
Configured: <value> (not found)
```

This makes persisted defaults visible instead of leaving the select blank. The
operator can switch to any currently listed device, which updates the service
config and restarts the bridge if it is running.

## Local Runtime Flow

```text
supervisor config
  services.audio-bridge.config.default_microphone/default_speakers
        |
        v
AudioBridgeService.start()
        |
        v
python robot_services/audio/audio_bridge.py
  --input-device <configured mic, if present>
  --output-device <configured speaker, if present>
        |
        v
PortAudio index/name or Linux ALSA identity resolution
        |
        v
LiveKit microphone publish + remote playback
```

## Remote Mode

For robots where audio devices live on PC3, remote mode uses the manager in
`robot_services/audio/audio_bridge_manager.py`. The manager is control-plane
only: it advertises devices with `audio_bridge.py --list-devices` and starts
the real bridge child only when requested.

The supervisor selects the remote wrapper when the configured robot model has
`AudioBridgeMode.REMOTE`. The focused remote topology, API, and config are
documented in `docs/remote_audio_bridge.md`.

Remote supervisor flow:

```text
robot.model agibot_x2_ultra
        |
        v
ServiceRegistry creates AudioBridgeRemoteService for service name audio-bridge
        |
        v
GET /api/devices/audio -> PC3 manager GET /devices
POST /api/services/audio-bridge/start -> PC3 manager POST /start
POST /api/services/audio-bridge/stop -> PC3 manager POST /stop
GET /api/services/audio-bridge/logs -> PC3 manager GET /logs
        |
        v
PC3 manager starts/stops audio_bridge.py child only while service is running
```

The supervisor API and frontend still use the same `audio-bridge` service name.
There is no separate UI-visible `audio-bridge-remote` service.

For Agibot A2, the same AIMA EM mode manager runs inside the local supervisor
audio bridge service on PC2. For Agibot X2, it runs inside the PC3 manager. See
`docs/agibot/aima_em_service_manager.md`.

PC3 install scaffolding for remote mode lives in
`deploy/pc3/audio-bridge/`. It includes apt and Python requirements, the
manager systemd unit, and `install.sh` for venv creation, initial manager
config/env files, service registration, and manager startup. The assumed PC3
checkout path is `/agibot/data/home/agi/humanoid-livekit`.

## Control API

When running locally, the bridge exposes a localhost control API on
`control_port`, default `8766`. When running remotely, the PC3 manager proxies
to the child bridge control API. The supervisor uses the same logical controls
in both modes:

- `GET /status`
- `POST /mute`
- `POST /input-gain`
- `POST /remote-playback/release`

These controls avoid a full bridge restart for mute, gain, and remote playback
release.

In remote mode, supervisor log endpoints also go through the PC3 manager. The
manager tails the configured child bridge log file on PC3, redacts configured
manager and LiveKit secrets, and returns the content to the existing service log
viewer. PC2 does not need direct filesystem access to the PC3 log directory.

## Clean Failure Rules

Expected clean failure cases:

- configured input index is not present
- configured output index is not present
- configured device name is not present
- there are no usable input or output devices
- LiveKit URL, room, API key, or API secret is missing or invalid
- the bridge child cannot connect to the configured LiveKit server or room
- the bridge control API is unavailable while the process is stopped or failed
- the PC3 manager URL/token is missing for remote mode
- the PC3 manager is unreachable or rejects the supervisor token
- the PC3 manager starts the child, but the child exits or fails to become ready

The service should expose the failure through supervisor status/logs. It should
not invent placeholder devices or silently swap to legacy hardware IDs.

## Migration Notes

`ROBOT_CONFIG.md` defines the lean target config shape with
`devices.audio.default_microphone` and `devices.audio.default_speakers`.
During the migration, both local and remote supervisor audio wrappers still read
the legacy `services.audio-bridge.config` values for active runtime device
defaults. Keep these values aligned manually until there is a documented and
tested mapping from top-level `devices.audio` into the service config.

Remote bridge mode for X2 is wired as a platform/model capability. Do not put
PC3 hostnames, public IPs, SSH credentials, or other deployment details into the
robot identity block. Keep PC3 manager URL/token values in deployment/local
service configuration.
