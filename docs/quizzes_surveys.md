# Quizzes And Surveys

Quiz and survey content is local file-backed YAML. The robot can run from
tracked examples on a clean checkout, while the supervisor only manages local
user-created content.

## Runtime And Supervisor Split

Runtime builders use examples as fallbacks:

```text
livekit_config/quiz_builder.py
livekit_config/survey_builder.py
```

Supervisor APIs instantiate their services with `include_examples=False`, so:

- Example quizzes and surveys are not listed in the supervisor.
- Example quizzes and surveys are not editable through the supervisor.
- Creating or editing content writes only to local runtime YAML.
- The robot still has default quiz/survey content if no local files exist.

This keeps repository defaults separate from operator-owned event content.

## Files

Tracked defaults:

```text
livekit_config/quizzes.example.yaml
livekit_config/surveys.example.yaml
```

Ignored local runtime catalogs:

```text
livekit_config/quizzes.yaml
livekit_config/surveys.yaml
```

Survey run archives are ignored local JSON files:

```text
robot_supervisor_v2/state/survey_runs/*.json
```

## Quiz Catalog Shape

`livekit_config/quizzes.yaml` has one active quiz slug and a list of quiz
definitions:

```yaml
active: petrol_station_quiz
quizzes:
  - slug: petrol_station_quiz
    title: Petrol Station Quiz
    description: Short spoken quiz for Petrol service-station interactions.
    max_attempts: 2
    questions:
      - question_key: fuel_tank
        question: Kam pravilno natocis gorivo v avtomobil?
        options:
          - motor
          - prtljaznik
          - rezervoar za gorivo
          - pnevmatike
        correct_answer: rezervoar za gorivo
        hint: To je del vozila, namenjen shranjevanju goriva.
        explanation: Gorivo se toci v rezervoar za gorivo.
        order_index: 0
```

Quiz rules:

- `slug` is the stable quiz identifier.
- `question_key` is the stable question identifier inside a quiz.
- `options` must contain at least two unique values.
- `correct_answer` must match one option exactly.
- `max_attempts` must be at least `1`.
- Questions are delivered by ascending `order_index`.

The runtime quiz payload consumed by `livekit-client/quiz_flow.py` is:

```yaml
quiz_id: petrol_station_quiz
max_attempts: 2
questions:
  - id: fuel_tank
    question: Kam pravilno natocis gorivo v avtomobil?
    options:
      - motor
      - prtljaznik
      - rezervoar za gorivo
      - pnevmatike
    correct_answer: rezervoar za gorivo
    hint: To je del vozila, namenjen shranjevanju goriva.
    explanation: Gorivo se toci v rezervoar za gorivo.
```

## Survey Catalog Shape

`livekit_config/surveys.yaml` has one active survey slug and a list of survey
definitions:

```yaml
active: petrol_survey
surveys:
  - slug: petrol_survey
    title: Petrol Survey
    description: Short spoken survey for Petrol service-station interactions.
    questions:
      - question_key: favorite_part
        question: Kaj vam je bilo pri danasnjem obisku najbolj vsec?
        response_kind: free_text
        options: []
        allow_skip: true
        order_index: 0
      - question_key: visit_frequency
        question: Kako pogosto obiscete bencinski servis?
        response_kind: single_choice
        options:
          - Vsak dan
          - Veckrat na teden
          - Enkrat na teden
          - Redko
        allow_skip: true
        order_index: 1
```

Supported `response_kind` values:

```text
single_choice
multiple_choice
free_text
ranking
```

Survey rules:

- `slug` is the stable survey identifier.
- `question_key` is the stable question identifier inside a survey.
- `free_text` questions ignore `options`.
- `single_choice`, `multiple_choice`, and `ranking` require at least two unique
  options.
- `allow_skip` must be a boolean.
- Questions are delivered by ascending `order_index`.

The runtime survey payload consumed by `livekit-client/survey_flow.py` is:

```yaml
survey_id: petrol_survey
title: Petrol Survey
description: Short spoken survey for Petrol service-station interactions.
questions:
  - id: favorite_part
    question: Kaj vam je bilo pri danasnjem obisku najbolj vsec?
    response_kind: free_text
    options: []
    allow_skip: true
```

## Active Selection

Quiz active selection:

```yaml
active: petrol_station_quiz
```

Survey active selection:

```yaml
active: petrol_survey
```

If `active` is empty and a local or fallback catalog has items, the first item is
used defensively. The supervisor update endpoints write the chosen slug back to
the local runtime file.

## Survey Run Archive

Completed survey snapshots are archived as JSON under:

```text
robot_supervisor_v2/state/survey_runs/
```

Only finished survey snapshots are archived. The archive stores:

- run ID
- survey slug and title
- response counts
- snapshot hash
- raw survey snapshot
- flattened answer rows
- source snapshot timestamp
- creation timestamp

Duplicate archive writes are avoided by matching `started_at` for the same
survey, falling back to a snapshot hash when no start time is available.

The supervisor list/export/delete survey-run endpoints read and write these JSON
files directly.

## API Notes

Quiz endpoints:

```text
GET    /api/quizzes/options
GET    /api/quizzes/active
POST   /api/quizzes/update
GET    /api/quizzes
POST   /api/quizzes
PUT    /api/quizzes/{quiz_id}
DELETE /api/quizzes/{quiz_id}
GET    /api/quizzes/{quiz_id}/questions
POST   /api/quizzes/{quiz_id}/questions
PUT    /api/quizzes/questions/{question_id}
DELETE /api/quizzes/questions/{question_id}
GET    /api/quizzes/export
GET    /api/quizzes/runtime/{slug}
```

Survey endpoints:

```text
GET    /api/surveys/options
GET    /api/surveys/active
POST   /api/surveys/update
GET    /api/surveys
POST   /api/surveys
PUT    /api/surveys/{survey_id}
DELETE /api/surveys/{survey_id}
GET    /api/surveys/{survey_id}/questions
POST   /api/surveys/{survey_id}/questions
PUT    /api/surveys/questions/{question_id}
DELETE /api/surveys/questions/{question_id}
GET    /api/surveys/export/catalog
GET    /api/surveys/runtime/{slug}
GET    /api/surveys/export/status
GET    /api/surveys/export/latest
POST   /api/surveys/archive/latest
GET    /api/surveys/runs
GET    /api/surveys/runs/export
GET    /api/surveys/runs/{run_id}
GET    /api/surveys/runs/{run_id}/export
DELETE /api/surveys/runs/{run_id}
DELETE /api/surveys/runs
```

Path parameters named `quiz_id`, `survey_id`, and `question_id` accept the
derived compatibility ID or the stable slug/key. Numeric `id` fields in API
responses are derived compatibility IDs, not database rows. Use slugs and
question keys for durable references.

## Git Policy

Commit changes to `*.example.yaml` only when the shipped defaults should change.
Do not commit local runtime catalogs or survey run archives.
