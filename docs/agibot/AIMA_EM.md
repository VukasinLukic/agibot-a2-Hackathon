# AIMA EM

`aima em` is the AIMA module manager used on the robot to inspect, start, stop,
and restart onboard services.

Use it when debugging or controlling robot-side modules such as `agent` or
`hal_audio`.

## Help

```bash
aima em --help
```

Use this to list available `aima em` commands on the robot.

Common pattern:

```bash
aima em list-apps
aima em stop-app <module>
aima em start-app <module>
aima em doctor
```

## `agent`

`agent` is the built-in AgiBot interaction module. It may handle voice,
conversation, face/interaction logic, or related behaviors depending on the
robot configuration.

Stop it when using our own agent, voice, or conversation stack:

```bash
aima em stop-app agent
```

Start it again with:

```bash
aima em start-app agent
```

### Interaction modes

The `agent` module can run in different interaction modes:

* `normal` - full built-in AgiBot interaction flow, including voice/conversation
  behavior.
* `only_voice` - keeps only the voice/audio processing path active, useful when
  we want processed microphone audio but not the built-in conversation stack.
* `voice_face` - keeps voice/audio processing and face recognition active, while
  avoiding the full built-in interaction flow.

After changing the interaction mode, restart `agent`:

```bash
aima em stop-app agent
aima em start-app agent
```

## `hal_audio`

`hal_audio` is the hardware/audio module used for robot audio access.

Restart it when changing audio configuration or when audio input/output is not behaving correctly:

```bash
aima em stop-app hal_audio
aima em start-app hal_audio
```

Check status after changes:

```bash
aima em doctor
```

## Audio bridge modes

The audio manager on the computer that owns Agibot audio devices can optionally
manage AIMA EM audio owners for low-latency LiveKit audio startup. For A2 this
runs in the local supervisor audio bridge service on PC2. For X2 this runs in
the PC3 remote audio bridge manager.

For the full PC3 manager API, mode behavior, config, and failure rules, see
`docs/agibot/aima_em_service_manager.md`.

The intended Agibot audio default is `audio_bridge` mode:

```text
manager startup -> aima em stop-app agent -> aima em stop-app hal_audio
```

In this mode, AIMA audio owners stay stopped and the manager can start
`audio_bridge.py` without waiting for `aima em stop-app ...` during the
conversation startup path.

When native Agibot features are needed, explicitly switch to `agibot` mode:

```text
POST /aima/mode/agibot -> aima em start-app hal_audio -> aima em start-app agent
```

Switch back before using the LiveKit bridge:

```text
POST /aima/mode/audio-bridge -> aima em stop-app agent -> aima em stop-app hal_audio
```

Stopping the LiveKit audio bridge does not automatically restore AIMA modules.
This keeps PC3 ready for the next low-latency bridge start. Restoration is an
explicit operator action through the manager API.

Useful PC3 manager checks for X2:

```bash
curl -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  http://127.0.0.1:8766/aima/status

curl -X POST -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  http://127.0.0.1:8766/aima/doctor
```
