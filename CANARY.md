# A2 English voice canary

The canary is disabled by default. When disabled, the Supervisor launches the
voice agent with its own `.venv` interpreter and the existing Serbian
Soniox/ElevenLabs/Azure path remains active.

## Reply language: bilingual, not English-only

`VOICE_LANGUAGE` / `VOICE_LOCALE` select which **authoring set** the agent uses —
`prompts/*.en.yaml` plus `locales/en-US` — and which STT keyterms are seeded.
They are **not** a reply-language policy. In both the canary and non-canary path
the robot answers in whichever language the visitor speaks, Serbian or English;
that rule lives in the `Language and speaking style` block of `prompts/base.en.yaml`
and `Stil komunikacije` in `prompts/base.yaml`.

Three settings must stay as below or bilingual breaks, each in a different way:

| Setting | Bilingual value | What pinning it to English does |
| --- | --- | --- |
| `STT_LANGUAGE_HINTS` / `SONIOX_LANGUAGE_HINTS` | `sr,en` | Soniox forces Serbian audio into English words, so the model never sees Serbian |
| `SONIOX_LANGUAGE_HINTS_STRICT` | `false` | Same as above, enforced hard |
| `TTS_LANGUAGE` | `auto` | Sends `language_code`, so Serbian text is read with English phonetics |
| `TTS_ENGLISH_PRONUNCIATION` | `false` | Applies English respellings (`TITAN`→`TIE-tan`, `AI`→`A I`) to Serbian output |

`TTS_LANGUAGE=auto` (also `multi`, `any`, `none`, or unset) sends no language code
at all and lets the provider detect per utterance. A single-language experiment arm
still pins a language by setting `TTS_LANGUAGE` to a real code such as `en`.

The initial greeting and the wrap-up goodbye are fixed strings from the active
persona/context, spoken before the visitor's language is known, so they stay in the
authoring-set language. Only the conversation itself mirrors the visitor.

SSML parsing is controlled by `TTS_SSML_PARSING` (default `true`), separately from
`TTS_ENGLISH_PRONUNCIATION`.

## Common configuration

Set these values in the existing secret/environment mechanism. Do not commit API
keys. `TTS_VOICE_ID` intentionally remains empty until the physical-speaker blind
audition selects a voice.

```dotenv
VOICE_CANARY_ENABLED=true
VOICE_AGENT_PYTHON=.venv-voice-canary/bin/python
VOICE_LANGUAGE=en
VOICE_LOCALE=en-US
AZURE_LLM_DEPLOYMENT=gpt-5.4-nano
VOICE_METRICS_ENABLED=false
```

The Supervisor validates the canary interpreter before launching the agent. With
`VOICE_CANARY_ENABLED=false`, `VOICE_AGENT_PYTHON` is ignored.

## Control arm: English Soniox + ElevenLabs

```dotenv
STT_PROVIDER=soniox
SONIOX_LANGUAGE_HINTS=sr,en
SONIOX_LANGUAGE_HINTS_STRICT=false
STT_MODEL=stt-rt-v4
TURN_DETECTION=vad
TTS_PROVIDER=elevenlabs
TTS_MODEL=eleven_flash_v2_5
TTS_VOICE_ID=
TTS_LANGUAGE=auto
TTS_STABILITY=0.8
TTS_STYLE=0.0
TTS_SPEED=0.93
TTS_USE_SPEAKER_BOOST=true
TTS_ENGLISH_PRONUNCIATION=false
```

To run a deliberately English-only arm instead, set `SONIOX_LANGUAGE_HINTS=en`,
`SONIOX_LANGUAGE_HINTS_STRICT=true`, `TTS_LANGUAGE=en`,
`TTS_ENGLISH_PRONUNCIATION=true`, and add an English-only instruction back to
`prompts/base.en.yaml`. All four are needed; changing only the prompt leaves STT
translating Serbian into English before the model ever sees it.

When `TTS_VOICE_ID` is blank, the ElevenLabs control uses the existing
`ELEVENLABS_VOICE_ID`; no new audition result is implied.

## STT arms

Change STT only; keep TTS, LLM, prompts, RAG, and audio unchanged.

Deepgram Flux requires `DEEPGRAM_API_KEY`:

```dotenv
STT_PROVIDER=deepgram_flux
STT_MODEL=flux-general-en
TURN_DETECTION=stt
DEEPGRAM_FLUX_EAGER_EOT=0.4
DEEPGRAM_FLUX_EOT=0.7
DEEPGRAM_FLUX_EOT_TIMEOUT_MS=3000
STT_KEYTERMS=Comtrade,TITAN,AgiBot
```

AssemblyAI requires `ASSEMBLYAI_API_KEY`:

```dotenv
STT_PROVIDER=assemblyai
STT_MODEL=universal-streaming-english
TURN_DETECTION=vad
ASSEMBLYAI_MIN_TURN_SILENCE_MS=400
ASSEMBLYAI_MAX_TURN_SILENCE_MS=3000
STT_KEYTERMS=Comtrade,TITAN,AgiBot
```

## TTS arms

First select a blind-audition voice, then set `TTS_VOICE_ID`. Change TTS only and
keep the winning STT, LLM, prompts, RAG, and audio unchanged.

Cartesia requires `CARTESIA_API_KEY`. Version 1.6.4 supports Sonic 3 as its newest
declared model:

```dotenv
TTS_PROVIDER=cartesia
TTS_MODEL=sonic-3
TTS_VOICE_ID=
TTS_LANGUAGE=en
TTS_SPEED=0.93
```

Deepgram Aura-2 requires `DEEPGRAM_API_KEY`. Its voice is encoded in the model
name; after audition, `TTS_VOICE_ID` may contain the selected complete Aura-2 model
name.

```dotenv
TTS_PROVIDER=deepgram_aura2
TTS_MODEL=aura-2-andromeda-en
TTS_VOICE_ID=
TTS_LANGUAGE=en
```

The Speech tab stores and shows the actual `{provider, model, voice_id, label,
language}` contract. A legacy `tts_tag` is migrated only to provider `truebar` and
is retained separately for explicit rollback; it is never reused as another
provider's voice ID.

## Metrics

Set `VOICE_METRICS_ENABLED=true` only during measurement. Each completed turn emits
one JSON record keyed by a random turn ID. Records contain timings and counters,
never transcript text, credentials, or biometric data. Disabled mode installs no
event listeners and adds no awaits to the hot path.

## Experiment order

1. Establish the English Soniox + ElevenLabs + `gpt-5.4-nano` control.
2. Compare one STT arm at a time.
3. Freeze STT and compare one TTS/voice at a time.
4. Keep STT/TTS fixed before testing another confirmed Azure deployment.
5. Apply/measure English localization.
6. Audio buffers are out of scope for this canary and must be tested separately.

The Supervisor owns the interpreter selection and reads `.env` at startup. After
changing the canary flag or an experiment arm, restart the Supervisor while the
conversation is idle:

```bash
tmux kill-session -t robot_supervisor 2>/dev/null || true; ./run_robot_supervisor_v2.sh
```

Exact one-line rollback:

```bash
sed -i 's/^VOICE_CANARY_ENABLED=.*/VOICE_CANARY_ENABLED=false/' .env && (tmux kill-session -t robot_supervisor 2>/dev/null || true) && ./run_robot_supervisor_v2.sh
```
