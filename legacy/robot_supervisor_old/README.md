# Robot Supervisor (MVP)

A minimal FastAPI supervisor that exposes two core capabilities for the robot stack:

1. Report status for each managed service (`GET /api/v1/status`).
2. Start or stop any managed service with dependency-aware ordering (`POST /api/v1/services/{name}/{start|stop}`).
3. Restart a managed service deterministically (`POST /api/v1/services/{name}/restart`).

It includes token authentication, a pluggable service manager abstraction (systemd or subprocess),
and an in-memory audit trail. Extend this foundation later with logs, config management, or a web UI.

## Quick start

```bash
pip install -r requirements.txt
export ROBOT_SUPERVISOR_CONFIG="$(pwd)/robot_supervisor/config.example.yaml"
export ROBOT_SUPERVISOR_TOKEN="change-me"  # required; the API refuses to start without it
uvicorn robot_supervisor.app.main:app --reload --port 8080
uvicorn robot_supervisor.app.main:app --bind 0.0.0.0 --port 8080
```

Query the API:

```bash
curl -H "Authorization: Bearer change-me" http://localhost:8080/api/v1/status
curl -X POST -H "Authorization: Bearer change-me" http://localhost:8080/api/v1/services/voice-agent/start
```

Or open http://localhost:8080/ui for a lightweight status console (the page prompts once for your token and stores it in `localStorage`).

> **Authentication note**  
> `ROBOT_SUPERVISOR_TOKEN` must be set in the environment (or `.env`) before the server starts. There is no fallback token in the YAML config or code path; missing the variable causes startup to fail fast so production instances never run without auth.

## Configuration

The supervisor loads YAML configuration from `ROBOT_SUPERVISOR_CONFIG` (defaults to
`robot_supervisor/config.example.yaml`). Each service entry chooses a backend:

- `systemd` – control existing units via `systemctl`.
- `process` – spawn a configurable command from the repo (useful on development machines without systemd).

Example snippet (see the full file for the four core services):

```yaml
services:
  - name: livekit
    display_name: LiveKit Server
    backend: process
    command:
      - livekit-server
      - --dev
    working_dir: ..
```

Set `backend: systemd` and `unit_name: voice-agent.service` when the robot uses systemd units. When running
agents under the supervisor (or via `run_stack.sh`), invoke the LiveKit agent with the CLI’s `start` command
instead of `dev`; the `dev` entry point launches file-watch reloaders that spawn helper processes which do not
shut down cleanly when managed by the supervisor.

Logs for subprocess-backed services land in `robot_supervisor/logs` by default; adjust via `process_log_dir`.
Each time a service restarts, the supervisor automatically moves its previous log file into
`robot_supervisor/logs/history/<service>/<timestamp>.log`, keeping the active log small while preserving history.

### Dependencies

Declare inter-service dependencies with `depends_on`. The supervisor ensures each dependency is running
before restarting a service. In the example config, `voice-agent` depends on `livekit`, and both bridges
depend on `voice-agent`, so restarting a bridge first verifies the voice agent (and livekit transitively)
are healthy.

Dependency information is also included in `GET /api/v1/status`: each service lists its dependencies,
current states, and timestamps for dependencies the supervisor auto-started on its behalf.

When the supervisor shuts down (e.g., Ctrl+C or process exit), it automatically stops every managed service
in dependency-safe order—dependents first, then their parents—to leave the robot stack in a known state.

#### Warm-up delays

Some services need time to become “ready” before dependents should start. Set `ready_after_seconds` on a service
to enforce this delay. For example, `livekit` can specify `ready_after_seconds: 3` so the voice agent waits three
seconds after LiveKit reports active, and `voice-agent` can specify `ready_after_seconds: 5` to keep the audio/camera
bridges from starting until the agent has stabilized.

#### Restart behavior

`POST /api/v1/services/{name}/restart` performs a full cascade:

1. Stops the target service.
2. Walks “downstream” to stop any dependents that were running (e.g., both bridges when restarting the voice agent).
3. Walks “upstream” to stop each dependency in order (voice agent, then LiveKit when restarting a bridge).
4. Starts dependencies from the bottom up, honoring their ready delays.
5. Restarts any dependents that were previously active, ending with the requested service.

This ensures a bridge restart cycle like **camera → voice → LiveKit → voice → bridges** without leaving dangling
processes that still point at a restarting dependency.

#### Modes

Services such as the audio/camera bridges support multiple operating modes (external vs robot peripherals).
Declare per-mode overrides in the config (when `modes` are present you can omit the top-level `command`, so each mode
defines the exact process invocation):

```yaml
modes:
  - name: external
    display_name: External Audio
    command:
      - python
      - robot_audio_bridge.py
      - --iface
      - eth0
  - name: robot
    display_name: Robot Audio
    command:
      - python
      - robot_audio_bridge.py
      - --iface
      - robot
```

The supervisor stops the running process, applies the new mode’s overrides (command/env), and restarts the service.
The `/ui` console shows a “🎛 Mode” button on services with multiple modes so you can toggle without leaving the page.

### Logs

Two endpoints expose service logs:

- `GET /api/v1/services/{name}/logs?lines=200` – returns the last *N* log lines (from process log files or `journalctl`).
- `WS /api/v1/services/{name}/logs/stream?token=...` – streams new log lines in real time.
- `POST /api/v1/services/{name}/mode` – switches a service to a new mode (body: `{"mode": "robot"}`).

The `/ui` console surfaces these via a **Logs** button on each card, expanding inline log panels so you can watch multiple services in parallel; each panel uses the REST tail + WebSocket stream under the hood.

## Python interpreter

When the repository root has a `.venv/` directory (or you set `ROBOT_SUPERVISOR_VENV`), the process backend
automatically replaces any command that begins with `python`/`python3` with that virtualenv's interpreter.
To use a different interpreter explicitly, point `ROBOT_SUPERVISOR_PYTHON` to the desired binary.

## Web UI

A minimal, mobile-friendly UI is served at `/ui` (and `/`). It fetches `/api/v1/status`, renders service
cards with status/uptime/dependencies, and provides Start/Stop/Restart buttons that hit the corresponding
API endpoints. Authentication uses the same Bearer token as the API; the token is stored client-side so you
aren't prompted on every refresh. Services with multiple modes expose a toggle button (🎛) so you can switch
between configurations without leaving the dashboard.

## Next steps

- Add journald tail/stream endpoints using the same ServiceManager abstraction.
- Introduce config GET/PUT/apply handlers that rewrite the YAML and restart affected services.
- Serve a lightweight status UI from FastAPI (`/ui`).
