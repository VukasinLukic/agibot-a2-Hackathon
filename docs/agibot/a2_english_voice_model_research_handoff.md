# A2 English voice-stack research handoff

Date: 2026-08-17

## Purpose

This document is the system handoff for researching and selecting an English-first
voice stack for the AgiBot A2 Ultra deployment. The next researcher should compare
current and alternative STT, LLM, TTS, and voice choices, then return a measured,
implementation-ready recommendation.

This is a research brief, not approval to change the live robot. Do not modify the
active runtime, credentials, prompts, indexes, or supervisor state during research.
Use isolated canary configuration and change one pipeline component at a time.

The target is English-only operation for the first rollout. English should be
explicitly configured end to end, rather than relying on automatic language
detection or on a prompt that switches between Serbian and English.

## Executive system snapshot

The active A2 voice path is:

```text
A2 microphone array
  -> local PortAudio audio bridge (48 kHz mono LiveKit track)
  -> local LiveKit server
  -> Silero VAD
  -> Soniox streaming STT
  -> prompt / tools / local RAG decision
  -> local RAG API + local Qdrant when knowledge is needed
  -> Azure OpenAI LLM
  -> ElevenLabs streaming TTS
  -> LiveKit audio track
  -> local PortAudio audio bridge
  -> A2 speaker
```

Current provider/model configuration observed in code, environment, and runtime
logs:

| Stage | Current implementation | Current value |
| --- | --- | --- |
| VAD | Silero through LiveKit | prewarmed at worker startup |
| Turn detection | LiveKit fixed VAD endpointing | min 0.5 s, max 3.0 s |
| Preemptive generation | LiveKit `AgentSession` | disabled in the active `agent_main.py` |
| STT | `livekit-plugins-soniox` | `stt-rt-v4`, hints `sr,en` |
| RAG embedding | local Hugging Face BGE-M3 | CUDA configured and confirmed in prior RAG service logs |
| RAG vector store | local Qdrant | `127.0.0.1:6333` |
| RAG API | local FastAPI | `127.0.0.1:8098` |
| LLM | LiveKit OpenAI plugin using Azure | `gpt-5.4-nano` deployment/configuration |
| TTS | `livekit-plugins-elevenlabs` | `eleven_flash_v2_5` |
| TTS voice | ElevenLabs direct plugin | voice ID configured through `ELEVENLABS_VOICE_ID` |
| LiveKit | local server | `ws://127.0.0.1:7880` |

No credentials or complete endpoint secrets should be copied into a research
report. State only which credential would be required for a canary.

## Runtime and hardware boundary

The A2 development computer is an NVIDIA Jetson Orin-class ARM64 machine:

- OS architecture: `aarch64`;
- kernel: Linux `5.15.136-rt-tegra`, PREEMPT_RT;
- Python environment used by the configured RAG service: `.venv-310`;
- PyTorch: NVIDIA Jetson build `2.4.0a0+...nv24.07`, CUDA 12.2 build;
- Transformers: `4.47.1`;
- LiveKit Agents: `1.6.4`;
- LiveKit OpenAI plugin: `1.6.3`;
- LiveKit Soniox plugin: `1.6.4`;
- LiveKit ElevenLabs plugin: `1.6.3`.

The RAG service log has previously reported `device=cuda`. A shell probe in the
current restricted execution context could not open the Jetson NVIDIA memory
manager and reported CUDA unavailable. Treat the service log as evidence of the
previous successful service runtime, but repeat `torch.cuda.is_available()` from
the exact supervisor-launched RAG process before any new benchmark. Do not infer
GPU health from a different shell, container, or Python environment.

Cloud STT, LLM, and TTS inference do not run on the Jetson GPU. GPU selection only
affects local workloads such as BGE-M3 and vision. For cloud voice providers, the
important variables are provider region, network path, connection reuse, model
latency, endpointing, and audio streaming behavior.

## A2 audio boundary that candidates must support

The local bridge currently uses:

- an AIUI-USB-MC microphone array exposed as 8 capture channels;
- configured live microphone channels 2, 3, 4, and 5 mixed to mono;
- capture/output pipeline at 48 kHz;
- 10 ms internal LiveKit/APM frames (`480` samples at 48 kHz);
- 100 ms PortAudio callback blocks (`4800` samples);
- bounded capture and reverse-audio queues;
- playback backlog capped at approximately 500 ms;
- stale playback buffers cleared on release/interruption;
- AEC through the LiveKit audio processing path;
- RNNoise disabled because it is an additional CPU-heavy pass.

The selected STT may internally resample from 48 kHz to its preferred input rate.
The selected TTS may return 16, 22.05, 24, 44.1, or 48 kHz audio. The integration
must record any resampling step and its CPU and latency cost. Do not choose a model
from provider TTFB alone if its output creates unstable playback or an expensive
conversion on ARM.

The 100 ms PortAudio block is a separate latency-tuning candidate. It must not be
changed during provider comparison. After the provider winner is selected, test
smaller blocks independently while measuring underruns, callback load, AEC quality,
and first-audible latency.

## Networking state and measurement rules

The machine previously attempted an unreachable/slow DNS resolver first, causing
roughly 5-10 second stalls before cloud requests. The persistent resolver path was
changed to the `systemd-resolved` local stub, with a fast public primary resolver
and a separate fallback. Post-change observations were:

- repeated DNS resolution: approximately 0-60 ms;
- full DNS + TCP + TLS connection setup to current providers: approximately
  0.25-0.44 s.

Provider research must be measured from the A2 host, not from a laptop or a vendor
web playground. Report cold and warm requests separately:

1. DNS lookup;
2. TCP connect;
3. TLS/WebSocket setup;
4. connection acquisition/reuse;
5. provider inference time;
6. first transcript/final transcript or first audio byte;
7. first audible speaker frame.

Use persistent streaming connections where the official SDK supports them. A
provider that has excellent warm latency but reconnects for every turn is not an
acceptable winner.

## Current code integration points

### STT and TTS factory

`livekit_config/truebar_config.py` is the active factory despite its legacy name.
It currently imports Soniox and ElevenLabs directly:

- `prepare_truebar_stt()` always constructs `soniox.STT`;
- `prepare_truebar_tts()` always constructs `elevenlabs.TTS`;
- Soniox model and language hints are environment-driven;
- ElevenLabs model and voice ID are environment-driven;
- the ElevenLabs constructor does not currently pass an explicit language code or
  provider voice settings.

Changing from Soniox or ElevenLabs to another provider is therefore a code change,
not only an `.env` edit. The preferred implementation after research is a small
provider factory with explicit values such as:

```text
VOICE_LANGUAGE=en
STT_PROVIDER=soniox|deepgram|assemblyai|...
STT_MODEL=...
TTS_PROVIDER=elevenlabs|cartesia|deepgram|rime|...
TTS_MODEL=...
TTS_VOICE_ID=...
```

Provider-specific options should remain in provider-specific configuration rather
than leaking conditionals through `agent_main.py`.

### LLM construction

`livekit-client/agent_main.py` constructs the LLM inline with
`openai.LLM.with_azure(...)`. The configured model is `gpt-5.4-nano`. Research must
first confirm which deployments are actually available on the configured Azure
resource/region. Do not recommend a public model name that cannot be mapped to an
available Azure deployment.

The LLM comparison should focus on voice-agent behavior:

- warm TTFT and p95 TTFT;
- tool-call correctness;
- RAG grounding and refusal behavior;
- concise spoken English;
- correct names, acronyms, and directions;
- response streaming behavior;
- cost per representative conversation.

Long-form benchmark scores are secondary to first-audio latency and reliable tool
use in this application.

### Supervisor Speech UI mismatch

The current Supervisor Speech UI and `speech_config.py` still model Truebar
`tts_tag` voices and fetch Truebar voice choices. The active runtime, however, uses
ElevenLabs directly and reads `ELEVENLABS_VOICE_ID` from the environment. Therefore,
selecting a voice in the current Speech UI does not reliably select the active
ElevenLabs voice.

The implementation plan must include replacing the legacy `tts_tag` contract with
a provider-aware voice option containing at least:

```json
{
  "provider": "elevenlabs",
  "model": "...",
  "voice_id": "...",
  "label": "...",
  "language": "en"
}
```

Do not silently reinterpret old Truebar tags as IDs for a different provider.
Migration and rollback must be explicit.

## English localization is broader than STT/TTS

The active system is not yet English-first. These locations must be included in
the eventual English canary:

1. `SONIOX_LANGUAGE_HINTS` is currently `sr,en`; an English-only arm should use
   `en` and disable automatic bilingual ambiguity unless code-switching is a stated
   requirement.
2. `livekit_config/prompts/base.yaml` is written in Serbian and explicitly says to
   default to Serbian.
3. The active persona, office context, and reception phase are written in Serbian.
   LLM instructions can technically be Serbian while producing English, but a
   native English prompt is easier to review and removes translation ambiguity.
4. Default greeting and goodbye values are Serbian.
5. `face_identity_flow.py` contains Serbian default spoken lines for known visitor,
   enrollment, consent, capture, failure, and forget flows. They can be overridden
   through environment variables, but a structured locale resource is preferable.
6. Quiz and survey flows explicitly default to Serbian, and much of their current
   content is Serbian.
7. Pronunciation replacements in `speech_config.py` contain Serbian/Slovenian
   phonetic spellings and can damage native English TTS output. Build a separate
   English dictionary for `Comtrade`, `TITAN`, employee names, acronyms, room names,
   and product names.
8. RAG documents may remain Serbian. BGE-M3 is multilingual, but the evaluation
   must test English questions against Serbian documents and English answer
   generation. If cross-language retrieval recall is insufficient, compare adding
   English documents or indexed English translations rather than changing all RAG
   parameters at once.
9. Room and participant identifiers still include legacy `g1` names. They do not
   change spoken language but should be documented rather than copied into a clean
   A2 English deployment unnoticed.

Use an explicit `VOICE_LOCALE=en-US` or `en-GB` decision. Generic `en` is adequate
for STT/TTS API routing, but the chosen accent, spelling, vocabulary, and voice
persona must be intentional.

## Current baseline versus research candidates

The current Soniox + ElevenLabs + Azure stack is the control arm. Do not discard it
without a same-host English benchmark; it may already be competitive after changing
Soniox hints to English and selecting an English-native ElevenLabs voice.

The following is a research shortlist, not a predetermined winner. Verify current
model availability, SDK compatibility, regions, retention policy, and pricing from
the providers' official documentation at research time.

### STT shortlist

| Arm | Why it belongs in the test | Specific questions |
| --- | --- | --- |
| Soniox current control | Already installed and integrated; streaming connection is proven | Does English-only configuration improve finalization, proper-name recall, and EOU? Can key terms be supplied in the installed plugin version? |
| Deepgram Flux | English conversational model with phrase/semantic turn detection support in LiveKit | Is `STTv2` compatible with the pinned LiveKit 1.6.x stack? Does STT-driven turn detection beat fixed VAD endpointing in the A2 room? |
| Deepgram Nova-3 | Mature streaming STT with English and keyterm support | Compare WER/entity recall and finalization latency against Flux; measure noisy far-field mic behavior |
| AssemblyAI Universal Streaming | English streaming model with configurable turn silence | Measure EOU behavior, proper names, connection reuse, and regional RTT from A2 |
| Speechmatics Enhanced | Broad official LiveKit support and a relevant accuracy candidate | Verify streaming latency, English accent robustness, diarization overhead, pricing, and closest region |

Optional candidates should only be added when they have native streaming in Python,
an official or well-maintained LiveKit integration, and a plausible low-latency
network route from the A2.

### TTS shortlist

| Arm | Why it belongs in the test | Specific questions |
| --- | --- | --- |
| ElevenLabs current control | Already installed and working with direct provider billing | Confirm the provider's currently recommended low-latency English model, because the currently configured Flash v2.5 is shown as deprecated in the current LiveKit Inference catalog; measure direct-plugin status separately |
| Cartesia Sonic family | Official LiveKit streaming support, English voices, speed/emotion controls, pronunciation dictionaries | Compare Sonic 3/3.5 and the current low-latency option; test first-byte, first-audible, sample-rate conversion, and brand pronunciation |
| Deepgram Aura-2 | Official LiveKit integration and English-specific voices | Measure TTFB/naturalness and whether using one vendor for STT+TTS improves operational simplicity without sacrificing quality |
| Rime Arcana/Mist family | English-focused voice choices and explicit WebSocket streaming mode | Test WebSocket mode, sentence versus immediate segmentation, pronunciation controls, and real p95 latency |

If Claude finds a newer official English model from these providers, it should be
added as a dated candidate and compared with the control. Do not replace a working
streaming model based only on a vendor quality claim.

### LLM shortlist rules

Keep the current Azure `gpt-5.4-nano` arm as control. Claude should identify no more
than two additional deployments that are actually available to this Azure resource
or can be provisioned in an approved nearby region. Candidate selection should
optimize:

1. TTFT and streaming stability;
2. instruction and tool-call reliability;
3. RAG grounding;
4. concise natural English;
5. cost.

Speech-to-speech realtime models may be evaluated as a separate architecture arm,
not mixed into the first STT/LLM/TTS comparison. They must preserve transcripts,
RAG injection, supervisor tools, gestures, quiz/survey state, interruption handling,
and deterministic safety boundaries before they can replace the cascaded pipeline.

## Voice persona and listening test

The English TITAN voice should sound like a professional embodied host, not a radio
advertisement or a generic phone bot. The researcher should return three voice
finalists, ideally spanning:

- one warm, calm male voice;
- one warm, confident female voice;
- one neutral alternative with a different English accent.

For every voice report:

- provider, model, voice ID/name, locale/accent, and license/usage constraints;
- speaking rate and supported controls;
- streaming TTFB and first-audible latency on A2;
- pronunciation of `TITAN`, `Comtrade`, `AgiBot`, employee names, acronyms, numbers,
  URLs, room names, and mixed technical phrases;
- intelligibility through the physical A2 speaker at reception volume;
- naturalness across greeting, short answer, long answer, question, apology,
  direction-giving, and interruption;
- stability across repeated synthesis of the same sentence.

Use blind listening labels such as Voice A/B/C. At least three listeners should
score clarity, warmth, authority, naturalness, fatigue, and perceived response speed
from the physical robot, not only headphones.

## Mandatory benchmark protocol

### Test material

Build a versioned English evaluation set with at least:

- 10 short social/reception turns;
- 10 factual RAG questions, including people and Comtrade facts;
- 5 wayfinding/direction questions;
- 5 tool or gesture requests;
- 5 interruptions and stop commands;
- 5 noisy/far-field utterances;
- proper names, acronyms, numbers, email-like strings, and accented English;
- several deliberate pauses inside a sentence to expose premature endpointing.

Use the same recorded microphone inputs for every STT arm where provider terms allow
it. Also run live far-field trials because recorded injection does not reproduce AEC,
speaker echo, or the microphone array's room acoustics.

### Metrics

Report cold and warm p50/p95, sample count, and failures for:

- end-of-user-speech to STT final;
- interim transcript latency;
- WER or normalized transcript accuracy;
- named-entity accuracy for local names and terms;
- false endpoint and missed endpoint counts;
- RAG search time and Recall@K for English questions;
- LLM TTFT and completion duration;
- TTS connection acquisition/reuse;
- TTS TTFB;
- first TTS audio frame received;
- first audible speaker frame;
- end-of-user-speech to first audible response;
- inter-sentence gap;
- barge-in stop latency;
- audio underruns, queue drops, and maximum queue depth;
- request error and reconnect rate;
- estimated cost per 100 representative turns.

The active `agent_main.py` does not currently emit a complete per-turn latency table.
Add optional metrics instrumentation in the eventual canary branch before claiming
a winner. Correlate stages with one turn/speech ID and do not log transcript contents,
credentials, or biometric data.

### Controlled experiment order

1. Freeze current audio bridge, RAG, prompt content, LLM, and TTS. Compare STT only.
2. Freeze the winning STT, RAG, prompt, LLM, and audio bridge. Compare TTS/voices only.
3. Freeze STT/TTS/voice and compare available LLM deployments.
4. Apply English prompt/localization changes and rerun the winning stack.
5. Only then test endpointing, preemptive generation, TTS chunk scheduling, or
   PortAudio block-size changes one at a time.

Do not compare a new STT with different endpointing, a new LLM, a new voice, and a
smaller audio buffer in the same arm; the latency/quality gain would be impossible
to attribute.

## Acceptance gates

The research recommendation must satisfy all of these before implementation:

- native English STT and TTS configuration, not accidental auto-detection;
- streaming STT and streaming TTS;
- no worse RAG factual accuracy than the English control;
- no regression in stop/barge-in behavior;
- no new audio underruns or unbounded queues;
- demonstrably better p95 end-of-speech-to-first-audible latency, or a clearly
  justified quality gain with no material latency regression;
- stable warm connection reuse;
- explicit data retention/privacy statement for audio and transcripts;
- known region/network path and measured A2 RTT;
- compatible Python/ARM64/LiveKit integration;
- rollback to the current Soniox/ElevenLabs/Azure stack through configuration;
- secrets stored only in the existing environment/secret mechanism;
- supervisor UI capable of displaying the actual provider, model, locale, and voice.

Use the current English-configured stack to establish numerical thresholds. As a
design objective, target warm p95 end-of-speech-to-first-audible below 1.5 seconds,
but do not hide quality, reconnect, or false-endpoint regressions merely to meet that
number.

## Expected Claude deliverable

Claude should return one Markdown report containing:

1. a dated compatibility table of current and candidate STT/TTS/LLM models;
2. official documentation links for every material availability or capability claim;
3. provider region, network, privacy/retention, price, and credential requirements;
4. the exact current-control configuration;
5. a primary recommended stack and one fallback stack;
6. three English voice finalists with IDs and a blind listening plan;
7. estimated implementation changes by file;
8. a canary configuration with all changes default-off;
9. benchmark commands/scripts and the required metrics table;
10. risks, rollback, and unresolved questions.

No recommendation should be presented as final without measurements from the A2
host and physical A2 speaker.

## Copyable task prompt for Claude

```text
Read docs/agibot/a2_english_voice_model_research_handoff.md and inspect every local
file it identifies before recommending models. Research the latest official
LiveKit and provider documentation for English streaming STT, low-latency LLMs,
streaming TTS, and production voices compatible with Python and this A2 stack.

Do not change the active runtime. Produce the single Markdown research report
specified in the handoff. Keep Soniox stt-rt-v4 + Azure gpt-5.4-nano + ElevenLabs
eleven_flash_v2_5 as the control. Recommend one primary and one fallback English
stack, three voice finalists, an A2-only controlled benchmark plan, exact integration
points, feature flags, privacy/region/cost considerations, and rollback. Distinguish
facts verified in official documentation from hypotheses that require live A2
measurement. Do not expose secrets.
```

## Official LiveKit starting points checked for this handoff

- Models overview: https://docs.livekit.io/agents/models/
- STT overview: https://docs.livekit.io/agents/models/stt/
- TTS overview: https://docs.livekit.io/agents/models/tts/
- Deepgram STT/Flux: https://docs.livekit.io/agents/models/stt/deepgram/
- AssemblyAI STT: https://docs.livekit.io/agents/models/stt/assemblyai/
- Cartesia TTS: https://docs.livekit.io/agents/models/tts/cartesia/
- ElevenLabs TTS: https://docs.livekit.io/agents/models/tts/elevenlabs/
- Deepgram TTS: https://docs.livekit.io/agents/models/tts/deepgram/
- Rime TTS: https://docs.livekit.io/agents/models/tts/rime/
- Fallback strategies: https://docs.livekit.io/agents/logic/fallback-strategies/

These links and model catalogs are time-sensitive and must be rechecked when the
research is performed.
