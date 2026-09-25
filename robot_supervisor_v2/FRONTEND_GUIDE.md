# Robot Supervisor V2 - Frontend Implementation Guide

## Overview

This guide provides specifications for implementing the frontend UI for Robot Supervisor V2. The backend API is ready and documented in [API_DOCUMENTATION.md](API_DOCUMENTATION.md).

**Backend API**: `http://localhost:8000`
**Interactive API Docs**: `http://localhost:8000/docs`

---

## Architecture Overview

### System Design

The Robot Supervisor V2 follows a **two-phase lifecycle model**:

1. **Service Lifecycle** (Slow, Once):
   - Start/stop system services (LiveKit server, voice agent, audio bridge, etc.)
   - Takes seconds to complete
   - Done once at system startup

2. **Conversation Lifecycle** (Fast, Repeated):
   - Dispatch/wrap conversations by creating/canceling LiveKit agent jobs
   - Takes milliseconds to complete
   - Can be repeated many times without restarting services
   - **Key Goal**: Near-instant conversation reset for quick testing iterations

### Key Principles

- **Separation of Concerns**: Service management ≠ Conversation control
- **No Queueing**: Operations return immediately if already in progress (409 Conflict)
- **Idempotent**: Repeated calls return current state instead of errors
- **Real-time Updates**: SSE stream for live status updates

---

## UI Layout

### Recommended Structure: 3-Tab Interface

```
┌─────────────────────────────────────────────────────────────┐
│  Robot Supervisor V2                                         │
├─────────────────────────────────────────────────────────────┤
│  [Services] [Conversation] [System]                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  (Tab Content)                                               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Tab 1: Services

**Purpose**: Manage system services (start/stop, view status)

### Components

#### 1. Service List/Table

Display all registered services with their status:

| Service | Status | Uptime | Actions |
|---------|--------|--------|---------|
| LiveKit Server | 🟢 Running | 5m 23s | [Restart] [Stop] |
| Voice Agent | 🟢 Running | 5m 18s | [Restart] [Stop] |
| Audio Bridge | 🟢 Running | 5m 15s | [Restart] [Stop] |
| Camera Bridge | 🟡 Stopped | - | [Start] |
| Gesture Bridge | 🟡 Stopped | - | [Start] |

**Status Indicators**:
- 🟢 Green - Running
- 🟡 Yellow - Stopped
- 🔴 Red - Error
- 🔵 Blue - Starting/Stopping

**API Integration**:
```javascript
// Get all services
GET /api/services
Response: { services: [...] }

// Start a service
POST /api/services/{service_name}/start

// Stop a service
POST /api/services/{service_name}/stop

// Restart a service
POST /api/services/{service_name}/restart
```

#### 2. Bulk Actions

```
[Start All Services] [Stop All Services]
```

**API Integration**:
```javascript
// Start all services in dependency order
POST /api/services/start-all

// Stop all services in reverse order
POST /api/services/stop-all
```

#### 3. Service Details Panel (Optional)

When a service is selected, show:
- Display name
- Current state
- PID (if running)
- Uptime
- Log tail (last 20 lines)
- Dependencies (what it requires)

---

## Tab 2: Conversation

**Purpose**: Control conversation dispatch/wrap and select agents

### Components

#### 1. Engagement Status Card

Large, prominent display of current conversation state:

```
┌─────────────────────────────────────────────┐
│  Status: 🟢 ENGAGED                          │
│  Room: main-room                            │
│  Agent: friendly_agent                      │
│  Duration: 2m 34s                           │
│  Job ID: job_abc123def                      │
└─────────────────────────────────────────────┘
```

**States to Display**:
- `idle` - 🟡 No active conversation
- `dispatching` - 🔵 Starting conversation
- `engaged` - 🟢 Conversation in progress
- `wrapping` - 🔵 Ending conversation
- `error` - 🔴 Error occurred

**API Integration**:
```javascript
// Get conversation status
GET /api/conversation/status
Response: {
  state: "engaged",
  room: "main-room",
  job_id: "job_abc123",
  uptime_seconds: 154.23
}
```

#### 2. Agent Selection

```
┌─────────────────────────────────────────────┐
│  Select Agent:                              │
│  ○ friendly_agent                           │
│  ● professional_agent  (Currently Selected) │
│  ○ assistant_agent                          │
│  ○ therapist_agent                          │
└─────────────────────────────────────────────┘
```

**Requirements**:
- Show all available agents
- Highlight currently selected agent
- **Disable selection during active conversation** (show tooltip: "Wrap conversation first")
- Show agent metadata (name, module, path) on hover

**API Integration**:
```javascript
// List all agents
GET /api/agents
Response: { agents: [{name, module, path}, ...] }

// Get current agent
GET /api/agents/current
Response: {name, module, path}

// Select an agent (only when idle)
POST /api/agents/select/{agent_implementation}
// Returns 409 if conversation is active
```

#### 3. Conversation Controls

```
┌─────────────────────────────────────────────┐
│  Room Name: [main-room        ] (editable) │
│                                             │
│  [🚀 Start Conversation]  [⏹ End Conversation] │
│                                             │
│  ☐ Graceful wrap (future feature)          │
└─────────────────────────────────────────────┘
```

**Button States**:
- **Start** - Enabled only when:
  - State is `idle`
  - Required services are running (livekit, voice-agent, audio-bridge)
  - No operation in progress

- **End** - Enabled only when:
  - State is `engaged`
  - No operation in progress

**API Integration**:
```javascript
// Start conversation
POST /api/conversation/dispatch?room=main-room&agent_implementation=friendly_agent
Response: {status: "success", job_id, ...}
// Note: room and agent_implementation are optional
// Room defaults from LIVEKIT_ROOM; LIVEKIT_AGENT_NAME must match the AgentServer dispatch name
// May return 409 if operation in progress
// May return 400 if services not running

// End conversation
POST /api/conversation/wrap?graceful=false
Response: {status: "success", duration_seconds, ...}
// May return 409 if operation in progress
```

#### 4. Quick Reset Flow

Show expected workflow prominently:
```
💡 Quick Reset Flow:
1. Start all services once (slow, ~10s)
2. Dispatch conversation (fast, <500ms)
3. Test your agent...
4. Wrap conversation (fast, <500ms)
5. Dispatch again instantly! (no service restart)
```

---

## Tab 3: System

**Purpose**: Overall system status, device info, logs

### Components

#### 1. System Status Summary

```
┌─────────────────────────────────────────────┐
│  System Status: 🟢 Healthy                  │
│  Version: 2.0.0                             │
│  Total Services: 5                          │
│  Services Running: 3                        │
│  Conversation State: engaged                │
└─────────────────────────────────────────────┘
```

**API Integration**:
```javascript
// Get comprehensive system status
GET /api/status
Response: {
  services: [...],
  conversation: {...},
  system: {version, total_services}
}
```

#### 2. Device Information

**Audio Devices**:
```
Input Devices:
  • [0] Built-in Microphone (2 channels)
  • [1] USB Microphone (1 channel)

Output Devices:
  • [0] Built-in Speakers (2 channels)
  • [1] HDMI Audio (8 channels)
```

**Video Devices**:
```
Camera Devices:
  • /dev/video0
  • /dev/video2
```

**API Integration**:
```javascript
// Get audio devices
GET /api/devices/audio
Response: {input_devices: [...], output_devices: [...]}

// Get video devices
GET /api/devices/video
Response: {devices: ["/dev/video0", ...]}
```

#### 3. System Logs (Optional)

Real-time log viewer showing:
- Service startup/shutdown events
- Conversation dispatch/wrap events
- Errors and warnings

Filter by:
- Log level (INFO, WARN, ERROR)
- Service name
- Time range

---

## Real-time Updates

### Server-Sent Events (SSE)

The backend provides a real-time event stream for live UI updates.

**API Integration**:
```javascript
// Connect to SSE stream
const eventSource = new EventSource('http://localhost:8000/api/events');

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);

  // Update services UI
  updateServicesTable(data.services);

  // Update conversation status
  updateConversationStatus(data.conversation);
};

eventSource.onerror = (error) => {
  console.error('SSE connection error:', error);
  // Fallback to polling if needed
};
```

**What to Update**:
- Service status indicators (running/stopped/error)
- Service uptime counters
- Conversation state and duration
- Button enabled/disabled states

**Fallback**: If SSE fails, poll `/api/status` every 2 seconds.

---

## Error Handling

### HTTP Status Codes

Handle these status codes gracefully:

**409 Conflict** - Operation already in progress:
```javascript
// Show non-blocking notification
showNotification("Operation already in progress, please wait", "warning");
// Don't retry automatically
```

**400 Bad Request** - Precondition not met:
```javascript
// Example: Services not running before dispatch
showError("Cannot start conversation: Services not running. Start services first.");
// Guide user to fix the issue
```

**404 Not Found** - Resource not found:
```javascript
// Example: Agent not found
showError("Agent 'unknown_agent' not found");
```

**500 Internal Server Error** - Unexpected error:
```javascript
// Show error details
showError(`System error: ${response.detail}`);
// Allow user to retry
```

### User-Friendly Messages

Map API errors to helpful messages:

| API Error | User Message |
|-----------|--------------|
| "Services not running" | "⚠️ Start services first before dispatching conversation" |
| "Operation already in progress" | "⏳ Please wait, another operation is in progress" |
| "Cannot change agent during conversation" | "⏹ End conversation first before changing agents" |
| "Service not found" | "❌ Service not available. Check configuration." |

---

## Typical User Workflows

### Workflow 1: First-Time Startup

1. User opens UI → All services stopped
2. User clicks **[Start All Services]** in Services tab
3. UI shows progress: LiveKit starting → Voice Agent starting → Bridges starting
4. After ~10s, all services show 🟢 Running
5. User switches to Conversation tab
6. User selects an agent (e.g., "friendly_agent")
7. User clicks **[Start Conversation]**
8. Status changes: idle → dispatching → engaged
9. Conversation begins (agent joins LiveKit room)

### Workflow 2: Quick Reset (The Key Use Case)

1. User has active conversation (engaged state)
2. User wants to test a code change
3. User clicks **[End Conversation]** (takes <500ms)
4. Status: engaged → wrapping → idle
5. User immediately clicks **[Start Conversation]** again (takes <500ms)
6. New conversation starts instantly (no service restart needed!)
7. User can repeat steps 3-6 as many times as needed

### Workflow 3: Agent Switching

1. User has idle conversation (no active conversation)
2. User goes to Conversation tab → Agent Selection
3. User selects different agent (e.g., "professional_agent")
4. Current agent indicator updates
5. User clicks **[Start Conversation]**
6. New agent joins the conversation

### Workflow 4: Service Restart

1. User modifies a service configuration
2. User goes to Services tab
3. User clicks **[Restart]** for specific service (e.g., Voice Agent)
4. Service briefly shows 🔵 Stopping → 🔵 Starting → 🟢 Running
5. Other services remain unaffected

---

## UI/UX Best Practices

### Visual Feedback

1. **Loading States**: Show spinners/progress during async operations
2. **Status Colors**: Use consistent color coding (green=good, yellow=warning, red=error)
3. **Uptime Counters**: Auto-update every second for engaged conversations
4. **Button States**: Clearly disable buttons when operations unavailable

### Notifications

Use non-blocking toast notifications for:
- ✓ Operation successful (success, 3s auto-dismiss)
- ⏳ Operation in progress (info, 2s auto-dismiss)
- ⚠️ User action required (warning, 5s dismissible)
- ❌ Error occurred (error, manual dismiss)

### Keyboard Shortcuts (Optional)

- `Ctrl+S` - Start all services
- `Ctrl+D` - Dispatch conversation
- `Ctrl+W` - Wrap conversation
- `Ctrl+R` - Restart selected service

### Responsive Design

- Desktop: 3-column layout with tabs
- Tablet: Stacked tabs, full-width content
- Mobile: Simplified view with essential controls only

### Accessibility

- Use semantic HTML (`<button>`, `<table>`, etc.)
- ARIA labels for status indicators
- Keyboard navigation support
- High contrast mode support

---

## Technical Recommendations

### Frontend Framework

Recommended stack (choose what fits your team):

**Option 1: React + TypeScript**
```typescript
// Service status hook
const useServices = () => {
  const [services, setServices] = useState([]);

  useEffect(() => {
    const eventSource = new EventSource('/api/events');
    eventSource.onmessage = (e) => {
      const data = JSON.parse(e.data);
      setServices(data.services);
    };
    return () => eventSource.close();
  }, []);

  return services;
};
```

**Option 2: Vue 3 + TypeScript**
```typescript
// Composable for conversation control
export const useConversation = () => {
  const status = ref({});

  const dispatch = async (room: string, agent?: string) => {
    const response = await fetch(`/api/conversation/dispatch?room=${room}`, {
      method: 'POST'
    });
    return response.json();
  };

  return { status, dispatch };
};
```

**Option 3: Svelte + TypeScript**
```typescript
// Store for real-time updates
export const servicesStore = readable([], (set) => {
  const eventSource = new EventSource('/api/events');
  eventSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    set(data.services);
  };
});
```

### State Management

Recommended approach:
- Use SSE for real-time state (services, conversation)
- Local state for UI-only concerns (selected tab, modal open/closed)
- No need for complex state management (Redux/Vuex) - SSE keeps UI in sync

### API Client

Create a typed API client for consistency:

```typescript
// api.ts
export class RobotSupervisorAPI {
  private baseURL = 'http://localhost:8000';

  async startAllServices() {
    const res = await fetch(`${this.baseURL}/api/services/start-all`, {
      method: 'POST'
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  async dispatchConversation(room: string, agentImplementation?: string) {
    const params = new URLSearchParams({ room });
    if (agentImplementation) params.set('agent_implementation', agentImplementation);

    const res = await fetch(
      `${this.baseURL}/api/conversation/dispatch?${params}`,
      { method: 'POST' }
    );
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  // ... other methods
}

export const api = new RobotSupervisorAPI();
```

### TypeScript Types

Generate types from API responses:

```typescript
// types.ts
export interface Service {
  name: string;
  state: 'stopped' | 'starting' | 'running' | 'stopping' | 'error';
  display_name: string;
  pid?: number;
  uptime_seconds?: number;
}

export interface ConversationStatus {
  state: 'idle' | 'dispatching' | 'engaged' | 'wrapping' | 'error';
  room?: string;
  job_id?: string;
  uptime_seconds?: number;
  last_error?: string;
}

export interface Agent {
  name: string;
  module: string;
  path: string;
}
```

---

## Testing

### Manual Testing Checklist

Services Tab:
- [ ] Start all services → All show running
- [ ] Stop a service → Status updates
- [ ] Restart a service → Brief stopping → running
- [ ] Stop all services → All show stopped

Conversation Tab:
- [ ] Dispatch conversation → Status changes to engaged
- [ ] Wrap conversation → Status changes to idle
- [ ] Try dispatch during active conversation → Shows already engaged
- [ ] Try agent selection during conversation → Disabled with tooltip
- [ ] Select agent when idle → Selection updates
- [ ] Quick reset: wrap → dispatch → Fast transition

Error Handling:
- [ ] Dispatch without services running → Shows error message
- [ ] Concurrent dispatch calls → Second returns "operation in progress"
- [ ] Backend offline → Graceful error display

Real-time Updates:
- [ ] Service status updates automatically
- [ ] Conversation uptime increments every second
- [ ] Button states update based on current state

---

## Deployment

### Development

```bash
# Terminal 1: Start backend
cd robot_supervisor_v2
python run_api.py --reload

# Terminal 2: Start frontend dev server
cd frontend
npm run dev
```

### Production

```bash
# Build frontend
cd frontend
npm run build

# Serve via Nginx/Apache
# Backend: uvicorn with gunicorn workers
# Frontend: Static files from dist/
```

---

## Support & Resources

- **API Documentation**: [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
- **Interactive API Docs**: http://localhost:8000/docs
- **Backend Code**: `robot_supervisor_v2/app/`
- **Example Configs**: `robot_supervisor_v2/config.example.yaml`

---

## Summary

**Key Points for Frontend Developers**:

1. **Two-phase lifecycle**: Services (slow) vs Conversations (fast)
2. **Use SSE** for real-time updates instead of polling
3. **Handle 409 conflicts** gracefully (operations already in progress)
4. **Disable controls** based on current state (e.g., agent selection during conversation)
5. **Emphasize quick reset** workflow - this is the main value proposition
6. **Non-blocking notifications** for user feedback
7. **Comprehensive error messages** to guide users

The backend is complete and ready for integration. Focus on creating a responsive, real-time UI that makes service management and conversation control feel instant and intuitive!
