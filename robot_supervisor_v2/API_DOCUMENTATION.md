# Robot Supervisor V2 - API Documentation

## Overview

REST API for service orchestration and conversation management for the humanoid robot system.

**Base URL**: `http://localhost:8000`
**Interactive Docs**: `http://localhost:8000/docs`

---

## Architecture

### Lifecycle Separation

The API maintains a clear separation between:

1. **Service Lifecycle** (slow, once):
   - Start/stop services via ServiceManager
   - Services include: livekit, voice-agent, audio-bridge, camera-bridge, gesture-bridge
   - Services start in dependency order and remain running

2. **Conversation Lifecycle** (fast, repeated):
   - Dispatch/wrap conversations via ManualController
   - Just creates/cancels LiveKit agent jobs
   - Services stay running between conversations for instant reset

### Concurrency Model

- **No Queueing**: API calls return immediately if operation already in progress
- **Thread-Safe**: Uses asyncio.Lock to prevent race conditions
- **Idempotent**: Repeated calls return current state instead of erroring

---

## Endpoints

### Service Management

#### `GET /api/services`
List all registered services with their status.

**Response**:
```json
{
  "services": [
    {
      "name": "livekit",
      "state": "running",
      "display_name": "LiveKit Server",
      "pid": 12345,
      "uptime_seconds": 123.45
    }
  ]
}
```

#### `GET /api/services/{service_name}`
Get status for a specific service.

**Response**:
```json
{
  "name": "livekit",
  "state": "running",
  "display_name": "LiveKit Server",
  "pid": 12345,
  "uptime_seconds": 123.45
}
```

#### `POST /api/services/start-all`
Start all services in dependency order (parallel batches).

**Response**:
```json
{
  "status": "success",
  "services": [...]
}
```

#### `POST /api/services/{service_name}/start`
Start a specific service (auto-starts dependencies).

**Query Parameters**:
- `start_dependencies` (bool, default=true): Start dependencies first

**Response**:
```json
{
  "status": "success",
  "service": {...}
}
```

#### `POST /api/services/{service_name}/stop`
Stop a specific service.

**Response**:
```json
{
  "status": "success",
  "service": {...}
}
```

#### `POST /api/services/stop-all`
Stop all services in reverse dependency order.

**Response**:
```json
{
  "status": "success",
  "services": [...]
}
```

#### `POST /api/services/{service_name}/restart`
Restart a specific service (without restarting dependencies).

**Response**:
```json
{
  "status": "success",
  "service": {...}
}
```

---

### Conversation Control

#### `POST /api/conversation/dispatch`
Dispatch agent to start a conversation.
**Requires**: Services must already be running.

**Query Parameters**:
- `room` (string, default="main-room"): LiveKit room name
- `agent_implementation` (string, optional): Local agent implementation to select before dispatch

**Response** (Success):
```json
{
  "status": "success",
  "state": "engaged",
  "room": "main-room",
  "job_id": "job_abc123",
  "dispatch_time": 1234567890.123
}
```

**Response** (Already Engaged):
```json
{
  "status": "already_engaged",
  "state": "engaged",
  "room": "main-room",
  "job_id": "job_abc123",
  "uptime_seconds": 45.67
}
```

**Error** (Operation In Progress):
```json
HTTP 409 Conflict
{
  "detail": "Another operation is already in progress"
}
```

**Error** (Services Not Running):
```json
HTTP 400 Bad Request
{
  "detail": "Required service 'livekit' is not running. Start services first."
}
```

#### `POST /api/conversation/wrap`
Wrap (end) current conversation.
Services remain running for quick re-dispatch.

**Query Parameters**:
- `graceful` (bool, default=false): Send goodbye event (future feature)

**Response** (Success):
```json
{
  "status": "success",
  "state": "idle",
  "previous_job_id": "job_abc123",
  "previous_room": "main-room",
  "duration_seconds": 123.45
}
```

**Response** (Already Idle):
```json
{
  "status": "already_idle",
  "state": "idle"
}
```

**Error** (Operation In Progress):
```json
HTTP 409 Conflict
{
  "detail": "Another operation is already in progress"
}
```

#### `GET /api/conversation/status`
Get current conversation/engagement status.

**Response**:
```json
{
  "state": "engaged",
  "room": "main-room",
  "job_id": "job_abc123",
  "uptime_seconds": 45.67,
  "dispatch_time": 1234567890.123,
  "last_error": null
}
```

**Engagement States**:
- `idle` - No active conversation
- `dispatching` - Dispatching agent job
- `engaged` - Conversation in progress
- `wrapping` - Ending conversation
- `error` - Error occurred

---

### Agent Management

#### `GET /api/agents`
List all available agents.

**Response**:
```json
{
  "agents": [
    {
      "name": "friendly_agent",
      "module": "friendly_agent",
      "path": "/path/to/friendly_agent.py"
    }
  ]
}
```

#### `GET /api/agents/current`
Get currently selected agent.

**Response**:
```json
{
  "name": "friendly_agent",
  "module": "friendly_agent",
  "path": "/path/to/friendly_agent.py"
}
```

#### `POST /api/agents/select/{agent_implementation}`
Select a local agent implementation for the next conversation.
**Restriction**: Cannot change during active conversation.

**Response**:
```json
{
  "name": "friendly_agent",
  "module": "friendly_agent",
  "path": "/path/to/friendly_agent.py"
}
```

**Error** (Conversation Active):
```json
HTTP 409 Conflict
{
  "detail": "Cannot change agent during active conversation. Wrap conversation first."
}
```

---

### Device Enumeration

#### `GET /api/devices/audio`
List available audio devices.

**Response**:
```json
{
  "input_devices": [
    {"index": 0, "name": "Built-in Microphone", "channels": 2}
  ],
  "output_devices": [
    {"index": 1, "name": "Built-in Speakers", "channels": 2}
  ]
}
```

#### `GET /api/devices/video`
List available video/camera devices.

**Response**:
```json
{
  "devices": ["/dev/video0", "/dev/video1"]
}
```

---

### System Status

#### `GET /api/health`
Basic health check.

**Response**:
```json
{
  "status": "ok"
}
```

#### `GET /api/status`
Comprehensive system status.

**Response**:
```json
{
  "services": [...],
  "conversation": {...},
  "system": {
    "version": "2.0.0",
    "total_services": 5
  }
}
```

---

### Real-time Events

#### `GET /api/events`
Server-Sent Events (SSE) stream for real-time updates.

**Stream Format**:
```
data: {"services": [...], "conversation": {...}}

data: {"services": [...], "conversation": {...}}
```

**Usage** (JavaScript):
```javascript
const eventSource = new EventSource('/api/events');

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Services:', data.services);
  console.log('Conversation:', data.conversation);
};
```

**Note**: Currently polls every second. Future: Event-driven.

---

## Typical Workflows

### Start System & Begin Conversation

```bash
# 1. Start all services (slow, once)
POST /api/services/start-all

# 2. Wait for services to be running
GET /api/services
# Check that all services have state="running"

# 3. Dispatch conversation (fast)
POST /api/conversation/dispatch?room=main-room&agent_implementation=friendly_agent

# 4. User converses with agent...

# 5. Wrap conversation (fast)
POST /api/conversation/wrap

# 6. Quick re-dispatch (milliseconds, no service restart)
POST /api/conversation/dispatch?room=main-room
```

### Change Agent Between Conversations

```bash
# 1. Ensure no active conversation
GET /api/conversation/status
# Verify state="idle"

# 2. Select new agent
POST /api/agents/select/professional_agent

# 3. Dispatch with new agent
POST /api/conversation/dispatch?room=main-room
```

### Service Restart

```bash
# Restart a single service (e.g., after config change)
POST /api/services/livekit/restart
```

---

## Error Handling

### Standard Error Responses

**400 Bad Request**: Invalid parameters or precondition not met
```json
{
  "detail": "Required service 'livekit' is not running"
}
```

**404 Not Found**: Resource not found
```json
{
  "detail": "Service 'unknown' not found"
}
```

**409 Conflict**: Operation conflict (e.g., already in progress)
```json
{
  "detail": "Another operation is already in progress"
}
```

**500 Internal Server Error**: Unexpected error
```json
{
  "detail": "Failed to start service: ..."
}
```

---

## Configuration

### Environment Variables

Required for LiveKit API operations:
```bash
export LIVEKIT_URL="ws://localhost:7880"
export LIVEKIT_API_KEY="devkey"
export LIVEKIT_API_SECRET="secret"
```

Conversation dispatch configuration:
```bash
export LIVEKIT_ROOM="main-room"        # Default room name for dispatch
export LIVEKIT_AGENT_NAME="voice-agent" # Required LiveKit dispatch name registered by AgentServer
```

**Note**: The `room` default can be overridden by passing a query parameter to `/api/conversation/dispatch`.
`LIVEKIT_AGENT_NAME` must match the running agent server's `agent_name`; `agent_implementation`
selects the local Python implementation, not the LiveKit dispatch name.

### Service Configuration

Services are configured via `config.yaml`:
```yaml
services:
  - type: livekit-server
    name: livekit
    config:
      display_name: "LiveKit Server"

  - type: voice-agent
    name: voice-agent
    config:
      display_name: "Voice Agent"
      agents_directory: "livekit-client"
```

---

## Development

### Running the Server

```bash
# Production mode
python robot_supervisor_v2/run_api.py

# Development mode (auto-reload)
python robot_supervisor_v2/run_api.py --reload

# Custom host/port
python robot_supervisor_v2/run_api.py --host 0.0.0.0 --port 8080
```

### Interactive API Docs

Visit `http://localhost:8000/docs` for:
- Interactive API explorer
- Request/response schemas
- Try-it-out functionality

### CORS Configuration

Currently allows all origins (`*`). Configure appropriately for production in `app/api/main.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Frontend origin
    ...
)
```

---

## Future Enhancements

1. **Graceful Conversation End**: Send goodbye event via LiveKit data channel
2. **Event-Driven SSE**: Replace polling with event-driven updates
3. **Conversation History**: Track and query past conversations
4. **Vision Controller**: Higher-level orchestration for autonomous interactions
5. **WebSocket Alternative**: For bidirectional real-time communication
6. **Authentication**: Add API key or JWT authentication
7. **Rate Limiting**: Prevent abuse of API endpoints
