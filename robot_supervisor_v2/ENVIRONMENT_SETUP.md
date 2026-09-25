# Runtime Environment Setup

Robot Supervisor supports a three-way runtime environment switch:

```text
DEV | UAT | PROD
```

The switch currently applies to:

- Azure OpenAI
- Azure AI Search
- Truebar STT/TTS authentication and endpoint settings

It does not switch LiveKit, audio devices, camera devices, gesture bridge, or local RAG service settings.

## File Layout

Keep shared service/runtime values in the root `.env` file:

```text
.env
```

Move switchable Azure and Truebar values into:

```text
.envs/common.env
.envs/dev.env
.envs/uat.env
.envs/prod.env
```

The `.envs/` directory is git-ignored. Do not commit secrets.

The selected environment is persisted here:

```text
robot_supervisor_v2/state/environment.json
```

That state directory is also git-ignored.

## What Stays In Root `.env`

Leave non-switchable supervisor and service settings in root `.env`, for example:

```env
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
LIVEKIT_AGENT_NAME=
LIVEKIT_ROOM=

AUDIO_BRIDGE_IDENTITY=
AUDIO_BRIDGE_*
LIVEKIT_CAMERA_*
GESTURE_*
RAG_EXTERNAL_*
ROBOT_SUPERVISOR_*
```

If a value should be the same for DEV, UAT, and PROD, it can stay in root `.env` unless it is one of the managed Azure or Truebar variables below.

## `.envs/common.env`

Use `common.env` for shared Azure and Truebar defaults that are the same across DEV, UAT, and PROD:

```env
OPENAI_API_VERSION=2025-01-01-preview
CHOSEN_COMPLETION_MODEL=gpt-4.1
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
CHOSEN_EMB_MODEL=text-embedding-ada-002
VECTOR_FIELD=embedding

TRUEBAR_CLIENT_ID=truebar-client
TRUEBAR_ENABLE_INTERIMS=true
TRUEBAR_ENABLE_DIARIZATION=false
TRUEBAR_SEND_TOKEN_IN_URL=false
TRUEBAR_TTS_ENABLE_SSML=true
TRUEBAR_TTS_SSML_RATE=+110%
TRUEBAR_TTS_NLP_ST_TAG=VIT:sl-SI:*:1.0.0
TRUEBAR_TTS_NLP_ST_PARAMETERS={"processSsml": true}
TRUEBAR_TTS_NLP_TN_TAG=VIT:sl-SI:*:*
TRUEBAR_TTS_NLP_TN_PARAMETERS={}
TRUEBAR_TTS_NLP_G2A_TAG=VIT:sl-SI:*:*
TRUEBAR_TTS_NLP_G2A_PARAMETERS={"fastEnabled": true}
TRUEBAR_TTS_SINGLE_CONFIG=1
```

Values in `dev.env`, `uat.env`, or `prod.env` override `common.env`.

## Environment-Specific Files

Each environment file must define the required Azure and Truebar values.

Example `.envs/dev.env`:

```env
AZURE_OPENAI_BASE=https://your-dev-openai.openai.azure.com/
AZURE_OPENAI_API_KEY=your-dev-openai-key
AI_SEARCH_ENDPOINT=https://your-dev-search.search.windows.net
AI_SEARCH_ADMIN_KEY=your-dev-search-key
INDEX_NAME=your-dev-index

TRUEBAR_USERNAME=your-dev-truebar-user
TRUEBAR_PASSWORD=your-dev-truebar-password
TRUEBAR_AUTH_URL=https://your-dev-auth.example.com/realms/truebar/protocol/openid-connect/token
TRUEBAR_API_BASE_URL=https://your-dev-api.example.com
TRUEBAR_STT_WS_URL=wss://your-dev-api.example.com/api/pipelines/stream
TRUEBAR_TTS_WS_URL=wss://your-dev-api.example.com/api/pipelines/stream
TRUEBAR_ASR_TAG=your-dev-asr-tag
TRUEBAR_TTS_TAG=your-dev-tts-tag
```

Repeat the same shape for:

```text
.envs/uat.env
.envs/prod.env
```

## Required Managed Variables

The environment switch validates these required values:

```env
AZURE_OPENAI_BASE=
AZURE_OPENAI_API_KEY=
OPENAI_API_VERSION=
AI_SEARCH_ENDPOINT=
AI_SEARCH_ADMIN_KEY=
INDEX_NAME=

TRUEBAR_USERNAME=
TRUEBAR_PASSWORD=
TRUEBAR_CLIENT_ID=
TRUEBAR_AUTH_URL=
TRUEBAR_API_BASE_URL=
TRUEBAR_STT_WS_URL=
TRUEBAR_TTS_WS_URL=
```

Optional managed values:

```env
CHOSEN_COMPLETION_MODEL=
AZURE_OPENAI_DEPLOYMENT=
CHOSEN_EMB_MODEL=
VECTOR_FIELD=
TRUEBAR_ASR_TAG=
TRUEBAR_TTS_TAG=
TRUEBAR_*
```

All `TRUEBAR_*` values are passed through to the voice agent for the selected environment.

## Migration Steps

1. Create or fill `.envs/common.env`.
2. Create or fill `.envs/dev.env`, `.envs/uat.env`, and `.envs/prod.env`.
3. Start Robot Supervisor.
4. Use the top-bar `DEV | UAT | PROD` toggle to select an environment.
5. Confirm the selected environment in `robot_supervisor_v2/state/environment.json`.
6. Once verified, remove managed Azure and Truebar values from root `.env` to avoid confusion.

Remove these from root `.env` after migration:

```env
AZURE_OPENAI_BASE=
AZURE_OPENAI_API_KEY=
OPENAI_API_VERSION=
CHOSEN_COMPLETION_MODEL=
AZURE_OPENAI_DEPLOYMENT=
CHOSEN_EMB_MODEL=
AI_SEARCH_ENDPOINT=
AI_SEARCH_ADMIN_KEY=
INDEX_NAME=
VECTOR_FIELD=

TRUEBAR_USERNAME=
TRUEBAR_PASSWORD=
TRUEBAR_CLIENT_ID=
TRUEBAR_AUTH_URL=
TRUEBAR_API_BASE_URL=
TRUEBAR_STT_WS_URL=
TRUEBAR_TTS_WS_URL=
TRUEBAR_ASR_TAG=
TRUEBAR_TTS_TAG=
TRUEBAR_*
```

## Runtime Behavior

- Switching is blocked while a conversation is active or transitioning.
- If `voice-agent` is running and the conversation is idle, changing environment restarts `voice-agent` automatically.
- If `voice-agent` is stopped, the selected environment is used the next time it starts.
- The API returns masked secrets only. Passwords and API keys are not exposed in full.

## Verification

Run the environment tests:

```bash
.venv/bin/python -m unittest robot_supervisor_v2.testing_scripts.test_runtime_environment
```

Build the frontend:

```bash
cd robot_supervisor_v2/frontend
npm run build
```

After switching environments, check the voice-agent log:

```text
robot_supervisor_v2/logs/voice-agent.log
```

Look for:

```text
=== Environment: DEV ===
```

or:

```text
=== Environment: UAT ===
=== Environment: PROD ===
```

The environment toggle tooltip also shows the active Azure and Truebar hosts.

