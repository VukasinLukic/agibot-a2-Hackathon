# Robot Config

## Purpose

This document defines the lean top-level robot supervisor config shape we want
to migrate toward.

The config should answer:

- Which robot is this supervisor running for?
- Which platform/model behavior should be selected?
- Which supervisor API bind settings should be used?
- Which device defaults should be preferred when available?

It should not become a full service inventory, deployment manifest, secret
store, or hardware topology model.

## Target Shape

```yaml
robot:
  id: agibot-x2-ultra-01
  name: "X2 Ultra"
  location: "Ljubljana lab"
  platform: agibot
  model: agibot_x2_ultra

api:
  host: 0.0.0.0
  port: 8080
  token: null

devices:
  audio:
    default_microphone: null
    default_speakers: null

  camera:
    default_device: null
```

## Field Semantics

### `robot`

`robot.id` is the stable config/deployment ID for one physical robot setup.
It should be unique within our development/deployment environment.

`robot.name` is the optional human-facing robot name used for display, logs,
operator orientation, and prompt variables. If it is missing, `null`, or blank,
the loader resolves it to the configured robot model's friendly display name
from `humanoid_platform`. Code should not branch on this value.

`robot.location` is metadata only for now. It can be shown in UI or logs, but it
must not select runtime behavior.

`robot.platform` selects the vendor/runtime family, currently `agibot` or
`unitree`.

`robot.model` selects the functional robot model, currently:

- `agibot_a2_ultra`
- `agibot_x2_ultra`
- `unitree_g1_edu`

The loader should validate that `robot.model` belongs to `robot.platform`.
Behavior such as local versus remote audio bridge mode, gesture support, and
temperature monitor support should be derived from model support maps, not
duplicated in this file.

For the temperature monitor specifically, the service uses this robot identity
to decide whether it has a backend. The legacy service config may still contain
runtime knobs such as `topic`, `network_interface`, and temperature thresholds,
but it should not contain platform/model selectors.

### `api`

`api.host` and `api.port` define where the supervisor API binds.

`api.token` is included because the current supervisor already has an API token
setting. During early migration this may remain in local config, but the
preferred long-term direction is to allow environment/secret management to
override it.

### `devices`

Device settings are optional defaults, not hard requirements. They describe
preferred devices for the relevant audio/camera owner. For local audio, that is
the supervisor machine. For X2 remote audio, that is PC3 through the remote
audio manager.

`devices.audio.default_microphone` is the preferred microphone ID/name/index for
the audio bridge. If it is `null`, the audio implementation may auto-detect or
use its own default. During the current migration, both local and remote
supervisor audio wrappers still read the legacy
`services.audio-bridge.config.default_microphone` value; keep the two values
aligned manually until an explicit mapping is added.

`devices.audio.default_speakers` is the preferred speaker/output ID/name/index
for the audio bridge. If it is `null`, the audio implementation may auto-detect
or use its own default. During the current migration, both local and remote
supervisor audio wrappers still read the legacy
`services.audio-bridge.config.default_speakers` value.

Focused audio bridge behavior, including device discovery, UI default handling,
and clean failure rules, is documented in `docs/audio_bridge.md`. Remote PC3
manager behavior is documented in `docs/remote_audio_bridge.md`. Optional
Agibot AIMA EM audio-resource mode management is documented in
`docs/agibot/aima_em_service_manager.md`.

`devices.camera.default_device` is the preferred camera device for camera bridge,
vision, and recording services. If it is `null`, each service may use its own
default until the camera config is consolidated.

## Examples

### Agibot A2 Ultra

```yaml
robot:
  id: agibot-a2-ultra-01
  name: "A2 Ultra"
  location: "Ljubljana lab"
  platform: agibot
  model: agibot_a2_ultra

api:
  host: 0.0.0.0
  port: 8080
  token: null

devices:
  audio:
    default_microphone: null
    default_speakers: null

  camera:
    default_device: "/dev/video10"
```

For A2, the local audio bridge runs on PC2. The supervisor injects the robot
model into the `audio-bridge` service, and the local bridge can use AIMA EM mode
management on PC2 to stop `agent` and `hal_audio` before the bridge starts.

### Agibot X2 Ultra

```yaml
robot:
  id: agibot-x2-ultra-01
  name: "X2 Ultra"
  location: "Ljubljana lab"
  platform: agibot
  model: agibot_x2_ultra

api:
  host: 0.0.0.0
  port: 8080
  token: null

devices:
  audio:
    default_microphone: null
    default_speakers: null

  camera:
    default_device: null
```

For X2, the remote audio bridge mode comes from `agibot_x2_ultra` in
`humanoid_platform`, not from this config. The PC3 manager URL/token belongs in
deployment/local `services.audio-bridge.config`, not in the robot identity
block.

Minimal X2 audio service shape:

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
      enable_aec: true
      enable_rnnoise: false
```

## Deliberately Excluded

Do not put these in the lean top config:

- full `services` lists
- command presets
- health monitor internals beyond basic API settings
- Azure, Truebar, LiveKit, or search secrets
- public IPs or external access details
- PC2/PC3 host maps
- Agibot preflight steps
- gesture catalogs or gesture backend IDs
- Python interpreter paths
- working directories
- service-specific startup commands
- UI branding or cosmetic titles

Those can stay in legacy config during migration or move into separate
deployment/local override files when needed.

## Ownership Rules

The lean top-level sections have specific owners:

- `robot` is owned by platform/model selection. It decides which registered
  platform and robot model the supervisor is running for.
- `api` is owned by supervisor startup. It defines API bind defaults and the
  local API token setting.
- `devices` is owned by hardware defaults. It provides preferred device hints,
  not hard requirements.
- `services` remains legacy supervisor service inventory until each service is
  migrated deliberately.
- `command_presets` remains legacy operator tooling.
- `health_monitoring` remains legacy service manager configuration.
- `engagement` remains legacy conversation/frontend configuration.

Future changes should not add unrelated fields to `robot`, `api`, or `devices`
just because those sections are easy to reach. Add a new top-level section only
when there is a clear owner and runtime contract.

## Future Work Rules

Use these rules when adding new config behavior:

1. Validate new canonical fields at startup.
2. Keep legacy service config raw until a service is intentionally migrated.
3. Do not silently translate many old fields into new fields.
4. Prefer one explicit mapping per migration step.
5. Keep robot model behavior in `humanoid_platform` support maps, not duplicated
   in YAML.
6. Keep deployment-specific values out of `robot`.
7. Keep secrets out of robot/platform/model config.

Examples:

- Good: map `devices.audio.default_microphone` into `audio-bridge.config` only
  after documenting and testing that fallback behavior.
- Good: place the X2 PC3 audio manager URL/token in deployment/local
  `services.audio-bridge.config`.
- Bad: infer robot model from service names.
- Bad: place public IPs, SSH targets, or cloud credentials under `robot`.
- Bad: keep both old and new versions of a setting active without clear
  precedence.

## Migration Notes

The current `robot_supervisor_v2/config.yaml` is still service-oriented and
contains local machine details. The first migration should add typed loading for
the lean sections above while continuing to tolerate the legacy sections.

Current first-pass loader behavior:

1. Load and validate `robot`, `api`, and `devices`.
2. Validate `robot.platform` and `robot.model` against `humanoid_platform`.
3. Keep existing `services` loading raw, with targeted robot context injection
   for migrated services.
4. Use `api.host` and `api.port` as `run_api.py` defaults.
5. Leave `devices.audio` and `devices.camera` as validated hints until the
   service mapping is explicit and tested.

Current audio bridge migration behavior:

- `robot.model` selects the local or remote supervisor audio wrapper through
  `humanoid_platform`.
- `services.audio-bridge.config.default_microphone` and
  `services.audio-bridge.config.default_speakers` still provide the active
  runtime device defaults.
- Top-level `devices.audio` remains a validated hint until a dedicated mapping
  is added and tested.

Focused service references:

- `docs/audio_bridge.md`
- `docs/remote_audio_bridge.md`
- `docs/agibot/aima_em_service_manager.md`

## Local Content Files

Prompt, quiz, and survey content is file-backed. Tracked `*.example.yaml` files
under `livekit_config/` provide robot/runtime defaults. Matching runtime files
without the `.example` suffix are local user content, ignored by git, and are
created by the supervisor when content is edited.

Examples are fallback content only. The robot uses them when no local runtime
file exists, but the supervisor content managers show only local user-created
content. This keeps shipped defaults out of the editable catalog until someone
explicitly creates or imports local content.

Runtime content files intentionally not committed:

- `livekit_config/prompt_config.yaml`
- `livekit_config/prompts/base.yaml`
- `livekit_config/prompts/personas.yaml`
- `livekit_config/prompts/contexts.yaml`
- `livekit_config/prompts/event_parts.yaml`
- `livekit_config/quizzes.yaml`
- `livekit_config/surveys.yaml`

Completed survey runs are archived as local JSON under
`robot_supervisor_v2/state/survey_runs/`.

Focused content references:

- `docs/prompts_personas.md`
- `docs/quizzes_surveys.md`
