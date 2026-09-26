# Robot Supervisor V2

Production-ready robot supervisor with unified service management, conversation control, real-time transcripts, and device enumeration.

## Architecture Overview

### Service Wrapper Pattern

All services inherit from `BaseService` which provides:
- **Lifecycle management**: start, stop, restart
- **Health checks**: Automatic health monitoring with configurable checks
- **Device enumeration**: Discover audio/video devices
- **Configuration management**: Runtime configuration updates
- **Log management**: Unified log access and streaming

### Service Implementations

1. **LiveKit Server** (`livekit_server.py`)
   - Manages LiveKit media server process in dev mode
   - Runs `livekit-server --dev` command
   - Process-alive health check
   - No configuration needed

2. **Voice Agent** (`voice_agent.py`)
   - Directory-based agent discovery
   - Enumerates all Python files with "agent" in the name (`*agent*.py`)
   - Selectable agent at runtime
   - Configuration: agents directory, selected agent
   - **Credentials**: Reads `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` from environment

3. **Camera Bridge** (`camera_bridge.py`)
   - Video device enumeration (Linux: `/dev/video*`)
   - Configuration: device path, resolution, framerate

4. **Audio Bridge** (`audio_bridge.py`)
   - Separate input/output device enumeration
   - Uses `sounddevice` library for device discovery
   - Multi-mode support (external, robot, local, jabra)
   - Configuration: input/output device indices, sample rate, mode

5. **Gesture Bridge** (`gesture_bridge.py`)
   - HTTP health check to `/health` endpoint
   - Configuration: port, script path

6. **Robot Temperature Monitor** (`robot_temperature_monitor.py`)
   - Uses the top-level `robot.platform` / `robot.model` config to select support
   - Currently supports `unitree_g1_edu` through Unitree SDK2 `rt/lowstate`
   - Fails cleanly and remains optional for unsupported robot models
   - See [`../docs/temperature_monitor.md`](../docs/temperature_monitor.md)

### Service Registry

- **ServiceRegistry**: Factory for creating service instances
- **ServiceManager**: Manages all service lifecycle, health monitoring, configuration

## Directory Structure

```
robot_supervisor_v2/
├── app/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py              # FastAPI application with all endpoints
│   ├── controllers/
│   │   ├── __init__.py
│   │   └── manual.py            # Manual conversation controller
│   ├── models/
│   │   └── conversation.py      # Transcript data models
│   ├── services/
│   │   ├── __init__.py
│   │   ├── base.py              # BaseService abstract class
│   │   ├── livekit_server.py    # LiveKit server service
│   │   ├── voice_agent.py       # Voice agent with directory discovery
│   │   ├── camera_bridge.py     # Camera bridge service
│   │   ├── audio_bridge.py      # Audio bridge service
│   │   ├── gesture_bridge.py    # Gesture bridge service
│   │   └── registry.py          # ServiceRegistry and ServiceManager
│   ├── utils/
│   │   ├── __init__.py
│   │   └── logging.py           # Logging utilities
│   ├── transcript_store.py      # In-memory transcript storage
│   └── transcript_manager.py    # LiveKit transcript capture
├── frontend/                    # React + TypeScript frontend
│   ├── src/
│   │   ├── api/                 # API client and types
│   │   ├── components/          # React components
│   │   ├── contexts/            # React context providers
│   │   └── App.tsx              # Main application
│   └── dist/                    # Built frontend assets
├── logs/                        # Service logs directory
├── config.example.yaml          # Example configuration
├── run_api.py                   # API server entrypoint
└── README.md                    # This file
```

## Configuration

See `config.example.yaml` for a complete configuration example.

For DEV/UAT/PROD runtime environment setup, including Azure OpenAI, Azure AI Search, and Truebar STT/TTS variables, see [`ENVIRONMENT_SETUP.md`](./ENVIRONMENT_SETUP.md).

Prompt, quiz, and survey content is stored in local YAML files under
`livekit_config/`. Tracked `*.example.yaml` files provide robot/runtime
fallbacks, while supervisor edits create ignored local runtime files. See
[`../docs/prompts_personas.md`](../docs/prompts_personas.md) and
[`../docs/quizzes_surveys.md`](../docs/quizzes_surveys.md).

Key configuration sections:
- `services`: List of service definitions with type and config
- `health_monitoring`: Health check interval settings
- `api`: API server settings
- `engagement`: Conversation management settings
- `automation`: Automation mode settings

### Environment Variables

The following environment variables are required:

- **`LIVEKIT_URL`** - LiveKit server URL (e.g., `ws://localhost:7880`)
- **`LIVEKIT_API_KEY`** - LiveKit API key (default in dev mode: `devkey`)
- **`LIVEKIT_API_SECRET`** - LiveKit API secret (default in dev mode: `secret`)

These are automatically provided by `livekit-server --dev` and will be inherited by voice agent processes.

## Testing

Run the test script to verify service definitions:

```bash
cd /Users/bjorn/Repos/HumanoidLivekit
python robot_supervisor_v2/test_services.py
```

This will:
1. Load configuration
2. Register all services
3. Enumerate devices (audio, video)
4. Discover voice agents
5. Display configuration parameters
6. Show health check configurations

## Voice Agent Directory Discovery

The voice agent service discovers agents by scanning the configured directory for Python files with "agent" in the name:
- Pattern: `*agent*.py`

Example directory structure:
```
livekit-client/agents/
├── customer_service_agent.py
├── technical_support_agent.py
└── general_agent.py
```

All three agents will be discovered and available for selection at runtime.

## Device Enumeration

### Audio Devices

Audio devices are enumerated separately for input and output:

```python
devices = await audio_service.list_devices()

# Returns Device objects with:
# - type: "audio_input" or "audio_output"
# - name: Device name
# - is_default: Whether this is the system default
# - metadata: channels, samplerate, hostapi
```

### Video Devices

Video devices are discovered from `/dev/video*` (Linux):

```python
devices = await camera_service.list_devices()

# Returns Device objects with:
# - type: "video"
# - name: Friendly name (e.g., "Camera 0 (video0)")
# - id: Device path (e.g., "/dev/video0")
# - is_default: True for first device
```

## Health Checks

Services implement health checks appropriate to their type:

- **HTTP**: GET request to endpoint (LiveKit, Gesture API)
- **Process**: Check if process is alive (Camera, Audio, Voice Agent)

Health checks are performed:
1. At service startup (wait_for_ready)
2. Periodically in background (when health monitoring enabled)

## Features

### ✅ Implemented

1. **Service Management**
   - Start/stop all services or individual services
   - Health monitoring with automatic status updates
   - Service configuration management
   - Real-time log streaming per service

2. **Conversation Control**
   - Manual dispatch/wrap conversation workflow
   - LiveKit agent job management
   - Real-time conversation status updates
   - Fast conversation reset (<500ms)

3. **Real-time Transcripts**
   - Captures LiveKit agent transcription events
   - Role-based transcript display (agent, user, system)
   - Live transcript updates via SSE
   - In-memory storage with session management

4. **Web Frontend**
   - React + TypeScript + Tailwind CSS
   - Real-time status updates via Server-Sent Events
   - Service controls with visual status indicators (🟢🟡🔴🔵)
   - Conversation controls with transcript viewer
   - Service log streaming viewer

5. **API**
   - FastAPI with automatic OpenAPI docs (`/docs`)
   - RESTful endpoints for all operations
   - Server-Sent Events for real-time updates
   - Comprehensive error handling

### 🚧 Future Enhancements

- Agent selection UI
- Automation mode for hands-free operation
- Persistent transcript storage
- Transcript export functionality
- Advanced service configuration UI

## Setup Instructions

### Prerequisites

- **Python 3.9+** with pip
- **Node.js 18+** with npm
- **LiveKit server** binary (or use `--dev` mode)

#### Installing Node.js (if not already installed)

**Ubuntu/Debian:**
```bash
# Install Node.js 20.x (LTS)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Verify installation
node --version  # Should be v20.x or higher
npm --version   # Should be 10.x or higher
```

**macOS:**
```bash
# Using Homebrew
brew install node

# Or download from https://nodejs.org/
```

**Alternative - Using nvm (recommended for multiple Node versions):**
```bash
# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash

# Restart terminal or source your profile
source ~/.bashrc  # or ~/.zshrc

# Install Node.js
nvm install 20
nvm use 20
```

### First-Time Setup on New Environment

#### 1. Clone Repository

```bash
git clone <repository-url>
cd HumanoidLivekit
```

#### 2. Python Environment Setup

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# OR
.venv\Scripts\activate     # Windows

# Install Python dependencies
cd robot_supervisor_v2
pip install -r requirements.txt
```

#### 3. Environment Configuration

Create a `.env` file in the project root:

```bash
cd /path/to/HumanoidLivekit
cp .env.example .env  # If example exists, or create manually
```

Add required environment variables to `.env`:

```env
# LiveKit Configuration (required for transcript capture)
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret

# Default room and required LiveKit dispatch name
LIVEKIT_ROOM=g1-lab
LIVEKIT_AGENT_NAME=your_livekit_dispatch_name
```

**Note**: When using `livekit-server --dev`, these default values are automatically provided.

#### 4. Frontend Setup

**Important**: Make sure Node.js and npm are installed (see Prerequisites above).

```bash
cd robot_supervisor_v2/frontend

# Verify Node.js is available
node --version  # Should be v18.0.0 or higher
npm --version   # Should be v9.0.0 or higher

# Install Node.js dependencies (this may take a few minutes)
npm install

# Build frontend for production
npm run build
```

This creates optimized files in `frontend/dist/` that are served by the API server.

**Common Issues:**
- If `npm install` fails with permission errors, do NOT use `sudo`. Fix npm permissions or use nvm instead.
- If you see "ENOENT: no such file or directory", make sure you're in the correct directory.
- If you see dependency warnings, they're usually safe to ignore unless the build fails.

#### 5. Configuration File (Optional)

Copy and customize the config file:

```bash
cd robot_supervisor_v2
cp config.example.yaml config.yaml
# Edit config.yaml to match your environment
```

### Running the System

#### Option 1: Direct Python Execution

```bash
# From the project root
cd robot_supervisor_v2
python run_api.py

# Or with auto-reload for development
python run_api.py --reload
```

#### Option 2: Using the Shell Script (Linux/Robot)

```bash
# From the project root
./run_robot_supervisor_v2.sh
```

This will:
- Start the API server in a tmux session
- Load environment variables from `.env`
- Run on port 8080 by default (override with `ROBOT_SUPERVISOR_PORT`)
- Log to `/home/unitree/robot_supervisor_boot.log`

Access the system at:
- **Frontend**: http://localhost:8000
- **API**: http://localhost:8000/api/
- **Interactive Docs**: http://localhost:8000/docs

### Frontend Development Mode

For active frontend development with hot reload:

```bash
cd robot_supervisor_v2/frontend
npm run dev
```

Frontend dev server: http://localhost:5173

After making changes, rebuild for production:

```bash
npm run build
```

### Updating Frontend on Existing Deployment

If you only need to update the frontend on an already-running system:

```bash
cd robot_supervisor_v2/frontend
npm run build
```

The API server will automatically serve the new built files (no restart needed).

## Documentation

- **[API_DOCUMENTATION.md](API_DOCUMENTATION.md)** - Complete API reference
- **[FRONTEND_GUIDE.md](FRONTEND_GUIDE.md)** - Frontend implementation guide
- **[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)** - Feature implementation status
