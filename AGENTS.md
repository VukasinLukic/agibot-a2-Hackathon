
## Project Orientation

This repository is being migrated from a Unitree G1 Edu focused LiveKit robot
solution into a multi-platform humanoid stack. The shared core should stay
robot-agnostic where possible: LiveKit agent behavior, supervisor API/UI,
conversation lifecycle, prompts/content, transcripts, RAG, conference scripts,
and command transport.

Robot-specific behavior should be selected through small platform/model
abstractions in `humanoid_platform/`, not by scattering vendor checks through
shared code. Current platforms are `unitree` and `agibot`; current robot models
are `unitree_g1_edu`, `agibot_a2_ultra`, and `agibot_x2_ultra`.

Use the docs before changing platform-specific behavior:

- `ROBOT_PLATFORM_MIGRATION.md` - migration direction and current targets.
- `ROBOT_PLATFORM_ABSTRACTION.md` - platform/model boundary and what is
  intentionally not modeled yet.
- `ROBOT_CONFIG.md` - lean supervisor robot config shape.
- `docs/gesture_bridge.md` - gesture backend/catalog boundary.
- `docs/temperature_monitor.md` - temperature monitor platform boundary.
- `docs/visual_ui.md` - visual UI state abstraction and Unitree LED backend.
- `docs/agibot/head_screen.md` - A2 face display: how to show custom text.
- `docs/agibot/AIMA_EM.md` - current notes on Agibot AIMA/AIMA EM handling.

Agibot A2/X2 should not inherit Unitree-specific SDK behavior by default. If a
robot model has no backend for a capability, the service/runtime should disable
that capability cleanly and remain optional unless the concrete integration is
known.




## LiveKit Documentation

LiveKit is a fast-evolving project. Always refer to the latest documentation. LiveKit provides an MCP server at `https://docs.livekit.io/mcp` with tools for browsing and searching docs. Key tools: `get_docs_overview`, `get_pages`, `docs_search`, `code_search`, `get_changelog`, `get_pricing_info`. Prefer browsing (`get_docs_overview`, `get_pages`) over search, and `docs_search` over `code_search`, as docs pages provide better context than raw code.

## Low Latency

Low latency is critical for this project. Treat end-to-end responsiveness as a primary requirement across the whole solution, including LiveKit agent processing, audio/video capture, streaming, transcription, model calls, tool execution, robot control, and service-to-service communication. Prefer implementation choices that reduce avoidable buffering, blocking work, extra network round trips, cold starts, and unnecessary serialization. When changing latency-sensitive paths, consider the full pipeline impact, measure or log timings where practical, and avoid adding work that can delay real-time interaction unless it is clearly necessary.

Also very important always respect Safety Guide C:\Users\Tea\OneDrive\Dokumenti\a2-hackathon\docs\Agibot Safety Guide.docx


##
