# Prompts And Personas

This document describes how the humanoid agent prompt is configured after the
content database removal. Prompt content is local file-backed YAML. There is no
Postgres, SQLAlchemy, Alembic, or prompt seeding step in the normal runtime path.

## Runtime Model

The robot builds its system prompt from four selected prompt parts:

1. `core_mode`
2. `persona`
3. `context`
4. `phase`

Those values are stored in:

```text
livekit_config/prompt_config.yaml
```

If that local file does not exist, the robot falls back to:

```text
livekit_config/prompt_config.example.yaml
```

The prompt text itself is loaded from:

```text
livekit_config/prompts/base.yaml
livekit_config/prompts/personas.yaml
livekit_config/prompts/contexts.yaml
livekit_config/prompts/event_parts.yaml
```

Each file has a tracked example fallback:

```text
livekit_config/prompts/base.example.yaml
livekit_config/prompts/personas.example.yaml
livekit_config/prompts/contexts.example.yaml
livekit_config/prompts/event_parts.example.yaml
```

The runtime builder in `livekit_config/prompt_builder.py` uses examples as
fallbacks so the voice agent can start on a clean checkout.

## Supervisor Behavior

The supervisor uses the same service seam, `PromptService`, but instantiates it
with `include_examples=False`.

That means:

- Example prompt files are not shown in the prompt manager.
- Example prompt files are not editable through the supervisor.
- Creating or editing content writes only to local runtime files.
- If no local prompt files exist, the supervisor prompt manager starts empty.
- The robot can still use example prompt defaults until local content is added.

This split is intentional. Examples are shipped defaults for startup safety, not
managed user content.

## File Shapes

Prompt files use stable slugs as keys. A minimal local `base.yaml` can be either
a direct slug-to-text map:

```yaml
standard: |
  You are {robot_name}, a humanoid robot host.
```

or a slug-to-object map:

```yaml
standard:
  title: Standard
  prompt_text: |
    You are {robot_name}, a humanoid robot host.
  initial_greeting: Lepo pozdravljeni.
  goodbye_text: Hvala za obisk.
```

The object form is what the supervisor writes. It supports:

- `title`
- `prompt_text`
- `initial_greeting`
- `goodbye_text`
- `is_archived`
- `order_index` for event moments

Personas live in `personas.yaml` and define the speaking style. Contexts live in
`contexts.yaml` and define location, event, business, and safety context. Event
moments live in `event_parts.yaml` and refine behavior for the current phase of
an event.

Local event moments written by the supervisor are grouped by context:

```yaml
contexts:
  petrol_event:
    opening:
      title: Opening
      prompt_text: |
        Welcome visitors and keep replies short.
      order_index: 0
```

The example fallback also supports the older flat event-parts shape for
compatibility.

## Active Selection

The active prompt config has this shape:

```yaml
active:
  core_mode: standard
  persona: ceremony_host
  context: petrol_planning_conference_2026
  phase: workshop_part
composition_order:
  - base
  - persona
  - context
  - phase
variables:
  company_name: Petrol
  robot_name: Robot
```

The active slugs must exist in the local catalog when changed through the
supervisor. The default example selection is only used by the robot/runtime
builder when no local active config exists.

## Variable Substitution

Prompt text can contain simple placeholders:

```text
{robot_name}
{company_name}
{gesture_policy_prompt}
```

`PromptBuilder` substitutes these at prompt build time. The gesture policy is
derived from the configured gesture safety pool and should remain the boundary
between prompts and concrete robot gesture backends.

In managed supervisor runtime, `{robot_name}` comes from the resolved
`robot.name` in `robot_supervisor_v2/config.yaml`. If that field is missing,
`null`, or blank, the supervisor falls back to the configured robot model's
friendly display name from `humanoid_platform`. Standalone prompt builds read
`HUMANOID_ROBOT_NAME`, then `ROBOT_NAME`, and finally keep the legacy `Primus`
fallback.

## Greetings And Goodbye Text

Initial greeting and goodbye text are resolved in this order:

1. Active event moment
2. Active context
3. Active core mode
4. Built-in fallback text

Personas do not currently provide greeting or goodbye overrides through the
supervisor API.

## API Notes

The prompt REST API accepts stable slugs in path parameters. Numeric `id` fields
in responses are derived compatibility IDs, not database rows.

Main endpoints:

```text
GET    /api/prompts/options
GET    /api/prompts/active
POST   /api/prompts/update
POST   /api/prompts/preview
GET    /api/prompts/modes
GET    /api/prompts/speaking-styles
GET    /api/prompts/event-settings
GET    /api/prompts/event-settings/{event_setting_slug}/event-moments
```

## Git Policy

Tracked defaults:

```text
livekit_config/prompt_config.example.yaml
livekit_config/prompts/base.example.yaml
livekit_config/prompts/personas.example.yaml
livekit_config/prompts/contexts.example.yaml
livekit_config/prompts/event_parts.example.yaml
```

Ignored local runtime files:

```text
livekit_config/prompt_config.yaml
livekit_config/prompts/base.yaml
livekit_config/prompts/personas.yaml
livekit_config/prompts/contexts.yaml
livekit_config/prompts/event_parts.yaml
```

Commit example changes when defaults should change for everyone. Do not commit
local runtime prompt files.
