# PC3 Remote Audio Bridge Files

This directory contains the first PC3 setup pieces for the remote audio bridge:

- `apt-requirements.txt`
  Debian/Ubuntu system packages needed for Python, PortAudio, ALSA discovery,
  and building audio Python packages.

- `requirements.txt`
  Minimal Python dependencies for `robot_services/audio/audio_bridge_manager.py`
  and the child `robot_services/audio/audio_bridge.py`.

- `remote-audio-bridge-manager.service`
  Minimal systemd unit for the always-running PC3 manager. The actual bridge
  child remains manager-owned and starts only when the supervisor calls
  `POST /start`.

- `install.sh`
  Installs apt packages, creates the repo-local Python venv, writes initial
  manager config/env files, registers the systemd unit, and starts the manager.

Expected PC3 layout for the service file:

```text
/agibot/data/home/agi/humanoid-livekit
/agibot/data/home/agi/humanoid-livekit/.venv
/etc/humanoid-livekit/audio_bridge_manager.yaml
/etc/humanoid-livekit/audio-bridge-manager.env
```

The env file should contain secrets and deployment overrides such as:

```bash
AUDIO_BRIDGE_MANAGER_TOKEN=change-me
LIVEKIT_URL=ws://<pc2-ip>:7880
LIVEKIT_ROOM=g1-lab
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
```

Install from the PC3 checkout:

```bash
cd /agibot/data/home/agi/humanoid-livekit
bash deploy/pc3/audio-bridge/install.sh
```

The installer refuses to register systemd against a different checkout path by
default. For a temporary test checkout, pass `--install-root "$PWD"`.

After installation, edit `/etc/humanoid-livekit/audio-bridge-manager.env` with
the real LiveKit URL, room, API key, and API secret before starting the actual
child bridge from the supervisor.

The installed example manager config enables Agibot AIMA EM audio-resource
management. On manager startup, PC3 enters low-latency audio-bridge-ready mode
by stopping `agent` and `hal_audio` once. Stopping our bridge child does not
restart those apps.

Check or change the AIMA mode directly on PC3:

```bash
curl -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  "http://127.0.0.1:8766/aima/status"

curl -X POST -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  "http://127.0.0.1:8766/aima/mode/agibot"

curl -X POST -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  "http://127.0.0.1:8766/aima/mode/audio-bridge"
```

The manager serves child bridge logs from the configured `child_log_path`.
Check them directly on PC3 with:

```bash
curl -H "Authorization: Bearer $AUDIO_BRIDGE_MANAGER_TOKEN" \
  "http://127.0.0.1:8766/logs?lines=100"
```

The supervisor service log viewer uses the same manager endpoint for remote
audio bridge logs.

See `docs/remote_audio_bridge.md` for the full manager/supervisor contract and
`docs/agibot/aima_em_service_manager.md` for focused AIMA EM mode management.
