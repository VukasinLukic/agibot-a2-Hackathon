# X2 Ultra — Agenda & RAG Architecture (reference for the A2 Ultra port)

**Status:** the described system is complete and running in production on the X2 Ultra
(`humanoid-platform`, LiveKit voice agent + local RAG service + Qdrant).

**Who this is for:** an engineer or coding agent building the equivalent "event Agenda"
capability on the AgiBot A2 Ultra. It documents *how the X2 does it* — the layers, the
contracts between them, the exact data shapes, and the non-obvious defenses that were
added after real failures. Reproduce the structure; do not assume the file paths or
port numbers are identical on your robot.

**How to read it:** Sections 1–3 define the feature and the topology. Sections 4–8 walk
the stack bottom-up (knowledge → retrieval → agent → prompt → operator UI). Section 9 is
a single end-to-end trace. Sections 10–12 are the configuration surface, the failure
modes that shaped the design, and a port checklist.

---

## 1. What "the Agenda" actually is

The Agenda is **not a module**. There is no `agenda.py`, no agenda class, no agenda
endpoint. Searching the codebase for "agenda" returns almost nothing in Python.

The Agenda is a **capability that emerges from five cooperating layers**, each of which
carries one agenda-specific responsibility:

| Layer | Agenda responsibility | Where |
|---|---|---|
| Knowledge | The agenda PDF, isolated in its own Qdrant collection | `rag_service/data/knowledge/<event-slug>/` |
| Retrieval | Hybrid search + schedule-vocabulary defenses so "first panel" never resolves to a person | `rag_service/app.py` |
| Agent | Decides *whether* to retrieve, builds the query, enforces a latency budget, injects the context | `livekit-client/agent_main.py` |
| Prompt | Hard anti-hallucination rules for times, speakers, panel ordinals | `livekit_config/prompts/personas.yaml`, `livekit_config/prompts/runtime.yaml` |
| Operator | Selects which indexes are live for the current event | `robot_supervisor_v2/app/api/main.py` + frontend |

**The core design decision:** the agenda is *data*, not code. Swapping events means
uploading a different PDF into a different index and flipping a persona — no deploy.
Everything that looks agenda-specific in the code is a *guardrail* against a failure
mode that schedule documents provoke, not agenda business logic.

Read that twice before porting. The temptation on A2 will be to write an
`AgendaService` with `get_next_session()`. The X2 deliberately does not, because the
moment you parse the agenda into structured fields you own a parser for every future
PDF layout, and you lose the ability to answer "what's the talk about Power Platform
about?" which is free with retrieval.

### The concrete X2 instance

- Index slug `comtrade-tech-day`, Qdrant collection `robot_knowledge_comtrade-tech-day`
- One document: `Agenda.pdf`, 3 pages, **3 chunks**
- Persona `comtrade_tech_day_agenda` ("Comtrade Tech Day Agenda Guide")
- A second event (Finance Day) is served by the `comtrade_host` persona with a dedicated
  `## 3.1 PITANJA O AGENDI` section — see §7.2, it is the more evolved of the two

The agenda PDF itself is a plain table — `rbr | Tema | Opis | Prezenteri | Vreme` —
extracted to flat text by PyMuPDF. There is no schema. Retrieval works because a
2500-char chunk comfortably holds a dozen contiguous agenda rows, so "who speaks after
lunch" finds lunch and the next row *in the same chunk*.

---

## 2. Process topology

Five processes, all on the robot, all reachable over loopback:

```
┌──────────────────────────────────────────────────────────────────────┐
│ robot_supervisor_v2 (FastAPI + React frontend)   :8080 (operator UI) │
│   proxies /api/knowledge/* ──────────────┐                           │
└──────────────────────────────────────────┼───────────────────────────┘
                                           │
┌──────────────────────────────────┐       │
│ livekit-client / agent_main.py   │       │   (control plane: upload,
│   the voice agent (LiveKit job)  │       │    index CRUD, query-slug
│                                  │       │    selection)
│   on_user_turn_completed()       │       │
│        └─> _run_rag() ───────────┼───────┼──┐
└──────────────────────────────────┘       │  │ (data plane: POST /v1/search)
                                           ▼  ▼
                            ┌──────────────────────────────────┐
                            │ rag_service/app.py   :8098       │
                            │   FastAPI, BGE-M3 in-process     │
                            └───────────────┬──────────────────┘
                                            │
                                     ┌──────▼──────┐
                                     │ Qdrant :6333│
                                     └─────────────┘
```

Key property: **the agent and the RAG service are decoupled by one HTTP contract**
(`POST /v1/search` → `{context, hits}`). The agent never talks to Qdrant, never loads an
embedding model, and never sees a chunk. That is what makes the port tractable — on A2
you can move the agent and keep the RAG service byte-identical, or vice versa.

> `livekit-client/answer_service.py` + `utils/answer_engine.py` are a **separate,
> standalone** question-answering REST service (classify → retrieve → LLM). Nothing in
> the live voice path imports them. Useful as a testing harness; do not port it as if it
> were the agent's RAG path.

---

## 3. The one contract that matters

Everything else is an implementation detail behind this:

**Request** — `POST http://127.0.0.1:8098/v1/search`
```json
{
  "query":          "kada pocinje prvi panel i ko su panelisti",
  "top_k":          4,
  "include_scores": false,
  "budget_ms":      2400
}
```

**Response**
```json
{
  "query":      "<the original, unmodified query>",
  "top_k":      4,
  "elapsed_ms": 137.4,
  "context":    "ENTITY MATCHES: ...\n-----\nIndex: Comtrade Tech Day | Agenda.pdf | 1 | 0 | doc_...\n<chunk text>\n-----\n...",
  "hits":       [ { "rank": 1, "score": 0.81, "source": "Agenda.pdf", "text": "...", "payload": {...} } ]
}
```

The agent consumes **only `context`** — a single pre-assembled, pre-budgeted string. It
does not rerank, does not read `hits`, does not look at scores. All retrieval quality
decisions live server-side.

This is the highest-leverage thing to copy. It means the A2 agent's RAG integration is
~40 lines of HTTP client, and all the hard tuning lives in one service you can test
offline with `curl`.

`budget_ms` is the agent telling the service *"return your best partial answer within
this wall-clock"*. The service honours it by skipping secondary indexes mid-scan rather
than returning nothing (§5.6). Cooperative deadlines, not cancellation.

---

## 4. Knowledge layer — indexes, ingestion, storage

`rag_service/app.py`, class `LocalRAGService`.

### 4.1 The index model

An **index** is a named (slug, title, description, Qdrant collection, storage dir) tuple.

```python
class IndexProfile(BaseModel):
    slug: str                  # "comtrade-tech-day"
    title: str                 # "Comtrade Tech Day"   <- appears in the LLM context!
    description: str           # "agenda, schedule, talks, speakers, rooms, breaks"
    kind: Literal["managed", "external"]
    collection_name: str       # "robot_knowledge_comtrade-tech-day"
    storage_path: str | None   # required when kind == "managed"
    immutable: bool            # "main" is always immutable
```

- `managed` — the service owns the PDFs on disk and can upload/delete/reindex.
- `external` — a Qdrant collection someone else filled; search-only. Auto-discovered
  from `GET /collections` on a 30s TTL cache.
- `immutable` (`RAG_IMMUTABLE_INDEX_SLUGS`, always includes `main`) — write endpoints
  return HTTP 409. This is how the shared company knowledge base is protected from an
  operator who is mid-event and uploading a new agenda.

`title` is not cosmetic: it is emitted as `Index: <title>` on the metadata line of every
context block, so the LLM can tell an agenda chunk from a company-history chunk. Name
indexes for the model's benefit, not the operator's.

### 4.2 Active index vs. query indexes — the important distinction

`rag_service/data/index_state.json` is the single source of truth:

```json
{
  "active_slug": "comtradeinformation",
  "query_slugs": ["main", "comtradeinformation"],
  "indexes": [ ...IndexProfile... ]
}
```

- **`active_slug`** — the *write* target. Uploads, deletes and reindexes hit this one.
- **`query_slugs`** — the ordered *read* set. Every search fans out across all of them.

They are independent, and that is the feature. During an event you set
`query_slugs = ["main", "comtrade-tech-day"]` so the robot answers both "what does
Comtrade do?" and "when is lunch?" from one utterance, while `active_slug` stays on
whichever index the operator is curating.

**Order matters.** Under a `budget_ms` deadline the service walks `query_slugs` in order
and stops after the first index that yields candidates (§5.6). Put the event index
first when the event is the priority.

### 4.3 Ingestion

`POST /knowledge/upload` (multipart, PDF only) →

1. `doc_id = doc_<UTC yyyymmdd_HHMMSS>_<8 hex>`; raw PDF written to
   `<storage_path>/<doc_id>.pdf`
2. **Extract** — PyMuPDF per page, `" ".join(page.get_text().split())` (whitespace
   collapsed to single spaces). A PDF with no extractable text raises — scanned agendas
   need OCR first.
3. **Chunk** — per page, fixed-width sliding window:
   `RAG_CHUNK_CHARS=2500`, `RAG_OVERLAP_CHARS=300`. Page boundaries are hard chunk
   boundaries; chunks never span pages.
4. **Embed** — BGE-M3, batch 16.
5. **Upsert** — point id `uuid5(NAMESPACE_URL, "<doc_id>_p<page>_c<idx>")`, so re-indexing
   the same document is idempotent. Payload:
   ```python
   {"document_id", "chunk_id", "filename", "page_number",
    "chunk_index", "start_pos", "end_pos", "text"}
   ```
6. Sidecar `<doc_id>.json` holds `DocumentInfo{id, filename, chunks, uploaded_at, file_size}`
   — this is what the operator UI lists.
7. Invalidate the lexical cache for that collection; fold any new person names into the
   canonical-name set.

**Why 2500/300 for agendas.** An agenda row is ~150–300 chars. 2500 chars ≈ 10–15
consecutive rows, so "what's after lunch" retrieves lunch *and* its neighbours in one
chunk — adjacency is preserved for free. Overlap 300 means a row split across a chunk
boundary still appears whole in one of the two.

If you shrink chunks on A2 for latency, **you will break temporal adjacency** and the
robot will start answering "what's next" with an unrelated session. Prefer reducing
`top_k`.

### 4.4 Embeddings

BGE-M3 (`bge-m3-local/` on disk, else `BAAI/bge-m3`), loaded in-process via
`transformers.AutoModel`, `max_length=256` tokens, **mean pooling over the attention
mask, then L2 normalize**. Qdrant collections use unnamed vectors, cosine distance,
`size = model.config.hidden_size`.

Guardrails worth copying:
- `RAG_OFFLINE=true` sets `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` and refuses to start
  with a clear error if the local model directory is incomplete. A robot at a customer
  site must never silently try to reach huggingface.co.
- `_validate_collection_vector_size` returns HTTP 409 if an existing collection's vector
  size disagrees with the loaded model, instead of writing garbage.
- Startup warms the model with one dummy embed, and (when the lexical fast path is on)
  pre-loads every query index's lexical cache. First real query is not the slow one.

---

## 5. Retrieval layer — the search pipeline

`LocalRAGService.search()`. This is the part most worth reading line by line.

### 5.1 Pipeline order

```
query
 └─ 1. entity expansion        (dictionary: alias → canonical + related terms)
 └─ 2. fuzzy person alias      (STT misheard a surname → canonical)
 └─ 3. person-query detection  → raises effective top_k
 └─ 4. _search_points()
        ├─ 4a. lexical fast path   (skip embedding entirely if lexical is decisive)
        └─ 4b. hybrid: vector + lexical + priority, per query index
 └─ 5. context assembly under a char budget
 └─ 6. SearchResponse
```

### 5.2 Entity dictionary — `rag_service/data/entity_dictionary.json`

```json
{"entities": [
  {"canonical": "Veselin Jevrosimović", "type": "person",
   "aliases": ["Vesa", "Veso", "Veselin Jevrosimovic", "Veselinu Jevrosimoviću"],
   "related_terms": ["Comtrade Group", "Founder", "osnivač", "predsednik"]},
  {"canonical": "menza", "type": "facility",
   "aliases": ["kantina", "restoran", "cafeteria", "canteen", "lunch"],
   "related_terms": ["prizemlje", "ručak", "radno vreme"]}
]}
```

Matching (`_entity_term_score`) is substring → token-window → `SequenceMatcher`, with a
0.82 threshold, capped at 3 matches, exact person matches preferred over everything.

On a hit the query is **replaced** (not appended) by the canonical + matched terms —
`_compact_query_with_entity_matches`. Dumping every alias and related term into the
query dilutes the embedding; two canonical tokens do not.

The match is also **announced to the LLM** in the context:

```
ENTITY MATCHES: Vesa -> Veselin Jevrosimović. Koristi ove kanonske entitete samo ako
retrieved kontekst sadrži odgovor.
```

Agenda relevance: Serbian is heavily inflected ("panela", "panelu", "panelima") and STT
mangles names. The dictionary is where you encode *this event's* vocabulary — speaker
name variants, room aliases, "pauza"/"break"/"lunch". **This file is the main per-event
tuning surface** and it is data, so it ships with the event, not with a release.

### 5.3 Fuzzy person alias — `_fuzzy_person_alias`

Guards the case where STT gets the first name right and the surname wrong. Requires an
exact-ish first name (≥0.86), a fuzzy surname (≥0.68), compatible initials
(with j/y and v/w treated as equivalent), and a ≥0.10 margin over the runner-up — no
ambiguous rewrite. `_name_part_variants` strips ~18 Serbian case suffixes
(`ovima`, `evom`, `ijima`, `og`, `u`, `a`, `e`, `i`, …) before comparing.

The canonical name set is built at startup by scrolling **every query index** and running
`_PERSON_NAME_RE` (`Capitalized Capitalized`) over each chunk's text and filename, plus
every `type: person` entry in the dictionary.

On a hit the LLM is told, in the context, that a substitution happened and to flag the
assumption — the robot says "assuming you mean Veselin Jevrosimović" rather than
silently answering about someone else.

### 5.4 ⚠️ The agenda-specific defense — `_NON_NAME_TOKENS`

**This is the single most important agenda-specific thing in the retrieval layer, and it
is not obvious. Port it deliberately.**

Agenda documents contain capitalized headings that are indistinguishable from
`Firstname Lastname` to a regex:

```
"Tema Panela 1 je ..."   →  _PERSON_NAME_RE matches "Tema Panela"
```

So "Tema Panela" entered the canonical person-name set. Then a visitor asked
*"kada počinje **prvi panel**?"* — and `_fuzzy_person_alias` matched "prvi panel" against
the bogus person "Tema Panela", rewrote the query into a **person lookup**, and the robot
answered about a nonexistent human.

The fix is a stopword set consulted by `_is_non_name_token()`, applied inside
`_person_name_candidates()` so the poison never enters the index in the first place:

```python
_NON_NAME_TOKENS = {
    "agenda", "agendu", "raspored", "rasporeda",
    "panel", "panela", "panele", "paneli",
    "panelist", "panelista", "paneliste", "panelisti", "panelists",
    "moderator", "moderatora", "moderatorka",
    "sesija", "sesije", "session",
    "govornik", "govornici", "speaker", "speakers",
    "tema", "teme", "temu", "topic",
    "pauza", "pauze",
    "prvi", "prvog", "prvom", "drugi", "drugog", "drugom", "treci", "cetvrti",
    "first", "second", "third",
}
```

Two regression tests pin the behaviour
(`robot_supervisor_v2/testing_scripts/test_rag_hybrid_search.py`):

- `test_agenda_headings_are_not_indexed_as_person_names` — `"Sanja Trbulin"` in,
  `"Tema Panela"` out
- `test_agenda_ordinal_question_does_not_fuzzy_match_a_heading` — even against a *stale*
  index that still holds the bogus name, "prvi panel" must not become a person lookup

**Port both the set and the tests, translated into the A2's language(s).** The class of
bug — schedule vocabulary colliding with name heuristics — will reappear verbatim.
Whatever your agenda's column headers are ("Track", "Session", "Chair"), they belong in
this set.

### 5.5 Hybrid scoring

Three components, merged per candidate across all query indexes (dedup key
`<collection>:<chunk_id|document_id|point_id>`):

```
final = 0.72 * vector_cosine        # RAG_HYBRID_VECTOR_WEIGHT
      + 0.28 * lexical_similarity   # 1 - vector_weight
      + priority_score              # person-profile boost, clamped [-0.10, +0.55]
```

`_lexical_similarity` — normalize (diacritics folded: č→c, š→s, đ→dj), strip
punctuation, drop Serbian stopwords and tokens <3 chars, then per query token take the
best match against the chunk's token set (exact 1.0, prefix 0.78–0.96, else
`SequenceMatcher`). `+0.12` bonus if the whole normalized query appears verbatim.

`_person_profile_priority` boosts chunks whose *filename* or first 700 chars carry the
person's name, extra for founder/president/CEO markers, and **penalizes** low-value
sources (`guideline`, `scope`, `taxonomy`, link-dump pages).

`RAG_HYBRID_MIN_FINAL_SCORE=0.32` — below this, hits are dropped and the response
carries an **empty context**. An empty context is a correct and desirable outcome: §7
makes the model say "I don't have that" rather than improvise. Do not lower this knob to
"get more results".

### 5.6 Two fast paths and the deadline

**Lexical fast path** (`RAG_LEXICAL_FAST_PATH=true`) — before embedding anything, scan
the cached lexical records. If ≥ `RAG_LEXICAL_FAST_PATH_MIN_HITS` candidates score ≥ the
threshold, return them and **never run the model**. That is the whole embedding +
vector-search cost removed from the critical path.

The threshold is raised in `search()` to keep it conservative:
- floor `max(RAG_LEXICAL_FAST_PATH_MIN_SCORE, 0.80)`
- **0.88** when there was no entity match and no fuzzy alias (nothing corroborates the
  query, so demand near-verbatim overlap)

The lexical cache (`_lexical_records`) holds, per collection, every point with its
pre-tokenized text — built once, warmed at startup, invalidated on upsert/delete. This
is why `RAG_HYBRID_LEXICAL_SCAN_LIMIT=700` is affordable on robot hardware.

**Deadline handling** — when `budget_ms` is present, `_search_points` iterates
`query_slugs` and:
- breaks before starting index *n>0* if the deadline has passed
- **breaks after the first index that produced any candidates**
- skips the full lexical collection scan entirely (it cannot be interrupted mid-scroll)

The comment in the source states the philosophy plainly: *voice requests prefer useful
partial recall over waiting for every selected index.* A late perfect answer is worse
than a prompt good one when a human is standing there.

### 5.7 Context assembly

```
[FUZZY NAME MATCH: ...]        ← only if a rewrite happened
[ENTITY MATCHES: a -> b; ...]  ← only if the dictionary matched
-----
Index: Comtrade Tech Day | Agenda.pdf | 1 | 0 | doc_20260806_085210_54af269e
<chunk text>
-----
Index: Main | ...
<chunk text>
```

Metadata line = `Index: <title>` + the non-null `metadata_fields`
(`filename`, `page_number`, `chunk_index`, `document_id`).

Budget = `RAG_CONTEXT_MAX_CHARS` (4200 on X2), enforced cumulatively. The last block that
does not fit is clipped at a word boundary (if past half the remaining space) and
suffixed `…`.

### 5.8 ⚠️ The context-budget floor — `_effective_context_max_chars`

The second agenda-specific bug, and the more insidious one:

```python
_CONTEXT_METADATA_HEADROOM_CHARS = 400

def _effective_context_max_chars(configured: int, chunk_chars: int) -> int:
    return max(configured, chunk_chars + _CONTEXT_METADATA_HEADROOM_CHARS)
```

With `RAG_CONTEXT_MAX_CHARS=2400` and `RAG_CHUNK_CHARS=2500`, the budget was smaller than
a single chunk. Assembly clipped the **best-ranked hit** mid-chunk — and in an agenda
chunk, what follows the session title is exactly the **panelist list**. The robot
confidently gave the time and topic of a panel and then, having had the names truncated
out from under it, produced plausible-sounding invented panelists.

The service now raises the budget and logs a warning at startup. Pinned by
`test_context_budget_never_falls_below_one_chunk`.

**Invariant for A2: `RAG_CONTEXT_MAX_CHARS ≥ RAG_CHUNK_CHARS + 400`.** Assert it at boot.
Silent tail-truncation of a retrieved chunk is a hallucination generator, because the
model sees a syntactically complete context and has no signal that anything is missing.

---

## 6. Agent layer — when and how the robot calls RAG

`livekit-client/agent_main.py`. Entry point: `on_user_turn_completed()` → `_run_rag()`.

RAG is skipped entirely for vision queries (an image is attached) and for explicit
gesture commands.

### 6.1 The client — `livekit-client/rag/external_client.py`

`RAGServiceClient` wraps one `httpx.AsyncClient` (max 4 connections, 2 keepalive, 60s
expiry — a persistent pool, since every TCP handshake is on the critical path).

- `asearch_wrapper(query, budget_s, top_k)` — one search. Sets
  `budget_ms = max(50, budget_s*1000 - 100)`, reserving 100 ms for serialization and the
  loopback round trip, and sets the HTTP timeout to `budget_s + 0.25`. **The server's
  deadline is always tighter than the client's** — the service gets to return a partial
  answer before the client gives up on it.
- `asearch_many_wrapper(queries, ...)` — `asyncio.gather` with
  `return_exceptions=True`, splits each result on `^-----$`, dedups blocks by normalized
  text, labels each group `SUBQUESTION: <q>`, clips the merge at
  `RAG_MULTI_QUERY_CONTEXT_MAX_CHARS` (6000). Raises only if *every* subquery failed.
- `search_wrapper` — a sync `urllib` variant kept for scripts. Not on the voice path.

### 6.2 The gate — `_should_attempt_rag()`

Retrieval costs latency, so most turns must not trigger it. Ordered, and **the early
returns are all `True`** — high-value lookups bypass the restrictive gate:

```
intent != "knowledge" or backchannel            → False   (hard stop)
is_self_identity_question(query)                → True
_contains_probable_person_name(query)           → True    ("Firstname Lastname")
"ko je <X>" / "reci mi o <X>"                   → True
_contains_wayfinding_rag_keyword(query)         → True
not RAG_SELECTIVE_GATE_ENABLED                  → True
len(words) < RAG_QUERY_MIN_WORDS (3)            → False
else                                            → _looks_directed_at_robot(query, intent)
```

Two more layers wrap it in `_run_rag`:
- **GATE_V2** — if the turn-gate classified the utterance as `command` /
  `question_social` / `chitchat` and the intent is not `knowledge`, skip.
- **`_should_skip_rag`** — an exact-match backchannel list (`ok`, `hvala`, `da`, `aha`,
  `dosta`, `super`, …, `RAG_SKIP_BACKCHANNELS`). Cheap and catches the common case.

**Agenda implication.** "Kada je pauza?" is 3 words and reads as directed — it passes.
But a trailing STT fragment like "Pauza za ručak." is 3 words and *not* obviously
directed. The wayfinding-keyword early return exists precisely because STT splits real
questions into fragments. **Put your event's schedule vocabulary into the wayfinding
keyword list** (`livekit_config/locales/<lang>/install_lexicon.yaml`) or agenda fragments
will be silently dropped by the gate. This is the most likely "the Agenda doesn't work"
bug on a fresh port, and it produces no error — just silence.

### 6.3 Query construction — `_build_rag_search_queries()`

1. **`_contextualize_rag_query`** — if `_is_contextual_followup(query)` (≤6 words, starts
   with a contextual opener, or contains a pronoun like `njega`/`tome`/`it`/`there`) and a
   topic is still fresh, prepend the topic: `"Panel 1" + "a ko to vodi?"`.
2. **`_strip_rag_leading_discourse`** — drop a leading acknowledgement before the real
   question ("Da, ko je…" → "ko je…"). Stops "da" from polluting the embedding.
3. **`_expand_common_stt_confusions`** — locale lexicon substitutions.
4. **Known-person override** — if the current turn is about a face-recognized visitor,
   the query is *replaced* with `canonical_name + aliases + "Comtrade"`.
5. **Multi-query split** — `_split_rag_subqueries` splits on `[?;,]` or `and`/`i`/`pa`
   *followed by a question word* (`gde`/`ko`/`šta`/`kada`/`where`/`who`/…), max 3 parts,
   each ≥2 content words.

The **topic memory** is `_last_rag_topic` (TTL `CONTEXTUAL_FOLLOWUP_TIMEOUT_S`). On a
successful retrieval `_commit_rag_topic` prefers a canonical entity parsed out of the
context (`ENTITY MATCHES: x -> y`) over the raw query string — so the follow-up is
anchored to the *resolved* entity, not to what STT heard.

**Agenda implication.** This is what makes the natural agenda dialogue work:

> "Kada počinje prvi panel?" → topic := Panel 1
> "A ko su panelisti?" → contextual follow-up → searched as "Panel 1 a ko su panelisti"

Without topic carry-over, the second question retrieves an arbitrary panel and the model
answers about the wrong one — with perfectly real names, which is worse than inventing
them, because it is unfalsifiable to the listener.

`_is_contextual_followup` has a deliberate exception: a short query that carries a
wayfinding keyword *and* a question word is treated as a **new** lookup, not a follow-up
(coffee → canteen must not inherit the coffee topic).

### 6.4 Adaptive `top_k` — `_rag_top_k_for_query()`

| Case | `RAG_QUALITY_MODE=true` | `false` |
|---|---|---|
| self-identity + known person | `max(top_k, 8)` | `min(top_k, 3)` |
| person lookup | `max(top_k, 12)` | `min(top_k, 3)` |
| everything else (incl. agenda) | `top_k` (4) | `top_k` |

Person answers need many chunks (bio spread across a profile); agenda answers need few
(one chunk holds the neighbourhood). `RAG_QUALITY_MODE` is the global latency/quality
lever.

### 6.5 Cache

LRU keyed on the *normalized* search query, `RAG_CACHE_TTL_S=300`,
`RAG_CACHE_MAX_ITEMS=32`. A cache hit skips the network entirely — logged as
`cache_hit`, still commits the topic and still applies claim grounding.

At an event the same ten agenda questions arrive over and over from different visitors.
The cache is what keeps the twentieth "kada je pauza" as fast as the first.

### 6.6 Budgets and the fallback — `_run_rag()`

```
rag_start
 ├─ cache hit?  → inject, return
 ├─ prefetch matches this query?
 │     await prefetch_task, timeout = RAG_ASYNC_WAIT_BUDGET_S (0.20s)
 └─ else
       task = search(budget_s = max(RAG_SEARCH_BUDGET_S, RAG_FALLBACK_SEARCH_BUDGET_S))
       await shield(task), timeout = RAG_SEARCH_BUDGET_S + RAG_RESPONSE_GRACE_S   # 2.65s

 on TimeoutError:
       # do NOT start a second search — that repeats embedding + vector work
       await the SAME shielded task,
             timeout = RAG_FALLBACK_SEARCH_BUDGET_S + grace - elapsed              # ~4.15s total
       on failure → return (no context; the model answers from persona)
```

Two details worth copying exactly:

- **`asyncio.shield`** — the first `wait_for` timing out must not cancel the in-flight
  request. The fallback re-awaits the *same* task. A naive retry doubles embedding work
  on the critical path at the exact moment you are already slow.
- **The server gets the larger budget up front** (`max(search, fallback)`), so the
  *client's* first deadline is the tight one. The service is never told to give up early
  on work the client may still want.

`RAG_ASYNC` prefetch (`_start_rag_prefetch`) fires speculatively on the final transcript,
before the turn is committed, and is cancelled if the resolved query changes. It is
`false` on X2 — the LLM query-rewrite step made it unprofitable — but the machinery is
intact and correct.

### 6.7 Injection — `_add_rag_message()`

The retrieved context is injected as a **`developer`-role message** with
`created_at = new_message.created_at - 0.001`, so it lands immediately *before* the user
message in the chat context.

`_remove_previous_rag_message()` runs at the top of every `_run_rag` and strips the prior
turn's injected message by id. **Exactly one RAG context exists in the chat context at
any time.** Stale agenda chunks from three turns ago are the classic source of "the robot
answered about the wrong panel", and this is the line of defense.

---

## 7. Prompt layer — the anti-hallucination contract

Retrieval decides *what the model sees*. The prompt decides *what it is allowed to say*.
For an agenda, the prompt layer is where the feature is actually won or lost.

### 7.1 The injected message — `runtime.yaml: rag_protocol.message`

```
RAG_CONTEXT (the source may be in Serbian; the required reply language is '{reply_language}'):
{rag_context}
{identity_context}

Use the context when relevant. If it is empty, answer from persona for small talk, but for
a factual question say you do not have that detail instead of inventing one. Reply entirely
in '{reply_language}', translating relevant facts when necessary; do not mix languages.
{grounding_policy}
```

`{grounding_policy}` is chosen per turn — `grounding_wayfinding` for location/hours
queries, otherwise **`grounding_general`, which names event agendas explicitly**:

> For people, teams, company facts, **and event agendas**, every name, role, organisation,
> date, and time you say must appear in this context.
> Never produce a person name, a job title, an employer, **a session time, or a speaker
> list** that is not written in the context, not even a plausible-sounding one, **and never
> fill a list out to a round number.**
> Copy names exactly as the context spells them; do not translate, guess, or substitute a
> similar name.
> When the question is about one specific item (**a named panel**, session, team, or
> person), use only the lines about that item. **Facts about a different panel or person
> are not an answer, so do not repeat their names.**
> If the context provides only partial support, say the part it supports and say plainly
> that you do not have the rest; **a missing speaker list is answered with "that I do not
> have with me", never with invented names.**

Every clause there is scar tissue. "Never fill a list out to a round number" exists
because the model, shown two panelists, would produce a third to make it feel complete.

### 7.2 The persona rules — `personas.yaml § 3.1 PITANJA O AGENDI`

The Finance Day host persona carries the fullest agenda contract. Translated:

- **No name is spoken unless it is in the context.** *"An invented panelist name is the
  worst mistake you can make in this role — worse than saying you don't know."* Stating
  the failure ranking explicitly works better than another "be accurate".
- **If the panelist list isn't there, say so**, with a scripted example answer:
  *"I can tell you the time and topic right away, but I don't have the panelist list at
  hand — the colleagues at registration have the current schedule."* Giving the model a
  concrete acceptable-failure sentence is what makes abstention feel natural rather than
  like a refusal.
- **Ordinals bind to the same panel.** "prvi panel" / "panel jedan" / "panel 1" / "prvi" /
  "Procena AI-a" are all Panel 1. Do not mix in Panel 2 even if Panel 2 is described in
  more detail in the context.
- **A question about a panel is a question about the whole panel.** "when does the first
  panel start, what's the topic and who are the panelists" — all three parts are about
  Panel 1 even though "first" appears once. Never answer only the first part.
- **Report partial context part by part.** Have the time and topic but not the names? Give
  time and topic, then say the names aren't at hand. **Do not refuse the whole answer over
  one missing piece** — and if the context contains a *different* panel's panelists, those
  names are not an answer, so don't say them.
- **"Next panel" is relative to the current time** — if the time is unknown, ask which
  panel is running or give the whole schedule.
- **Opening remarks, breaks, closing remarks, lunch and networking are not panels.**
  "First panel" means Panel 1, not the opening address that precedes it.
- **`RAG_CONTEXT` is a source of facts, not a source of topic.** If retrieval surfaced
  something unrelated, ignore it — never let a stray chunk steer the conversation.
- Never hedge with "according to my information" / "based on publicly available sources".
  The robot is a host who knows the program.

The dedicated `comtrade_tech_day_agenda` persona is the lighter variant: agenda/timetable
scope, chronological order, brevity, "do not recite the full agenda unless asked",
"say what's happening now or next only when both the schedule and a reliable current time
are available", prefer the newer official agenda on conflict and otherwise **state the
discrepancy rather than choosing silently**.

### 7.3 Claim grounding — a post-generation filter

`_filter_host_grounded_tts_segment` runs **on generated text before TTS** and drops
sentences whose factual claims aren't supported by the retrieved context.

Support test: normalized substring match, **numeric check** (any number in the segment
that is absent from the context fails the segment outright), else ≥50% overlap of content
words ≥4 chars.

Scope is `RAG_CLAIM_GROUNDING_SCOPE` — `wayfinding_only` on X2, `all` also covers person
and host lookups. The source comments call it what it is: a low-latency guardrail, not a
second retrieval pass.

The numeric rule is the agenda-relevant one — a hallucinated "14:30" cannot survive it.
Consider setting `all` for an agenda-heavy deployment on A2, and measure the false-drop
rate before shipping it.

---

## 8. Operator layer — running an event

`robot_supervisor_v2` proxies the RAG service to the tablet UI (`_rag_request`, base URL
`RAG_SERVICE_BASE_URL`, default `http://127.0.0.1:8098`):

| Supervisor endpoint | RAG service | Purpose |
|---|---|---|
| `GET /api/knowledge` | `GET /knowledge` | list documents in the active index |
| `POST /api/knowledge/upload` | `POST /knowledge/upload` | upload a PDF (10 min timeout) |
| `DELETE /api/knowledge/{doc_id}` | `DELETE /knowledge/{doc_id}` | remove a document |
| `POST /api/knowledge/index` | `POST /knowledge/index` | reindex everything |
| `GET /api/knowledge/{doc_id}/file` | `GET /knowledge/{doc_id}/file` | stream the PDF back |
| `GET /api/knowledge/indexes` | `GET /indexes` | list, merged with live Qdrant discovery |
| `POST /api/knowledge/indexes/create` | `POST /indexes/create` | new index |
| `DELETE /api/knowledge/indexes/{slug}` | `DELETE /indexes/{slug}` | delete index + collection + storage |
| `POST /api/knowledge/update` | `POST /indexes/activate` | set the **write** target |
| **`POST /api/knowledge/query-indexes`** | **`POST /indexes/query`** | **set the ordered read set** |
| `GET /api/knowledge/search` | `GET /knowledge/search` | operator-facing debug search |

The supervisor also merges in collections discovered directly from Qdrant that the RAG
service doesn't know about, so nothing is invisible in the UI.

**Runtime RAG toggle.** `__RAG_ON__` / `__RAG_OFF__` command tokens flip
`_rag_runtime_enabled` on the agent (and mirrored supervisor state). Off drops the client
and short-circuits `_run_rag` with `reason="disabled"`. An operator can kill retrieval
mid-event without a restart — which you want the first time the robot says something
wrong in front of an audience.

**The event-day runbook this enables:**

1. Create index `<event-slug>`
2. Activate it (write target), upload `Agenda.pdf`
3. Add this event's speakers / rooms / session aliases to `entity_dictionary.json`
4. Set query slugs to `["main", "<event-slug>"]` — order matters under deadline
5. Select the agenda persona + event context in the prompt UI
6. Smoke-test via `GET /api/knowledge/search` before doors open

No deploy, no restart, no code change.

---

## 9. End-to-end trace

> Visitor: **"Kada počinje prvi panel i ko su panelisti?"**

```
 1. STT final transcript → on_user_turn_completed(turn_ctx, new_message)
 2. not a vision query, no explicit gesture      → _run_rag()
 3. _remove_previous_rag_message()               → last turn's context dropped
 4. _classify_user_intent → "knowledge"
 5. GATE_V2 llm_role not in {command,social,chitchat} → continue
 6. _should_attempt_rag: 7 words, directed       → True
 7. _build_rag_search_queries:
      not a contextual follow-up (no fresh topic)
      multi-query split: "kada pocinje prvi panel" / "ko su panelisti"
      → 2 queries
 8. _rag_top_k_for_query → 4 (not a person lookup: "panelisti" ∈ _NON_NAME_TOKENS)
 9. cache miss
10. asearch_many_wrapper → 2 concurrent POST /v1/search, budget_ms ≈ 2400 each
      server, per query:
        entity expansion    → maybe "Panel 1" canonical
        fuzzy person alias  → None  ⟵ _NON_NAME_TOKENS prevented "Tema Panela"
        lexical fast path   → threshold 0.88 (no entity/alias corroboration); miss
        embed + Qdrant query_points over query_slugs, deadline-aware
        hybrid rank, drop < 0.32
        assemble ≤ 4200 chars   ⟵ ≥ 2500 + 400, so the panelist list is NOT clipped
11. client merges both contexts, dedups on ^-----$, labels SUBQUESTION:, clips at 6000
12. cache put; _commit_rag_topic → "Panel 1"      (anchors the next follow-up)
13. _add_rag_message: developer message at created_at - 0.001,
      grounding_policy = grounding_general        ("event agendas", "never fill a list
                                                    out to a round number")
14. LLM generates under persona § 3.1 (ordinals bind; whole-panel answer;
      partial context reported part by part)
15. claim grounding filter (wayfinding_only → inactive here)
16. TTS
```

Then:

> Visitor: **"A ko to vodi?"**

→ `_is_contextual_followup` (4 words, contextual opener) + fresh topic
→ searched as **"Panel 1 a ko to vodi"** → returns Panel 1's moderator, not Panel 2's.

---

## 10. Configuration reference

### RAG service (`rag_service/app.py`)

| Variable | X2 value | Meaning |
|---|---|---|
| `RAG_QDRANT_URL` | `http://127.0.0.1:6333` | Qdrant |
| `RAG_DATA_ROOT` | `rag_service/data` | state + storage root |
| `RAG_EMBED_MODEL` | `bge-m3-local` | local dir preferred over HF id |
| `RAG_DEVICE` | `cpu` | falls back to CPU with a warning if CUDA is absent |
| `RAG_OFFLINE` | `true` | forces `local_files_only`, no network |
| `RAG_MAX_LENGTH` | 256 | embed token cap |
| `RAG_CHUNK_CHARS` | 2500 | **agenda adjacency depends on this** |
| `RAG_OVERLAP_CHARS` | 300 | must be < chunk |
| `RAG_CONTEXT_MAX_CHARS` | 4200 | **must be ≥ chunk + 400** (§5.8) |
| `RAG_TOP_K` | 4 | default when the client omits it |
| `RAG_HYBRID_VECTOR_WEIGHT` | 0.72 | lexical weight = 1 − this |
| `RAG_HYBRID_CANDIDATE_MULTIPLIER` | 2 | candidate pool = top_k × this, cap 50 |
| `RAG_HYBRID_LEXICAL_SCAN_LIMIT` | 700 | cached, so affordable |
| `RAG_HYBRID_MIN_LEXICAL_SCORE` | 0.56 | lexical candidate floor |
| `RAG_HYBRID_MIN_FINAL_SCORE` | 0.32 | below → empty context (intended) |
| `RAG_LEXICAL_FAST_PATH` | true | skip the model when lexical is decisive |
| `RAG_LEXICAL_FAST_PATH_MIN_SCORE` | 0.68 | raised to 0.80 / 0.88 in `search()` |
| `RAG_PERSON_QUERY_MIN_TOP_K` | 4 | floor for person queries |
| `RAG_QUERY_EMBED_CACHE_SIZE` | 128 | LRU on normalized query |
| `RAG_QDRANT_TIMEOUT_SECONDS` | 1.5 | |
| `RAG_IMMUTABLE_INDEX_SLUGS` | `main` | `main` always included |
| `RAG_ENTITY_DICTIONARY_PATH` | `data/entity_dictionary.json` | |

### Agent client (`livekit_config/voice_settings.py`)

| Variable | X2 value | Meaning |
|---|---|---|
| `RAG_EXTERNAL_ENABLE` | true | master switch |
| `RAG_EXTERNAL_API_URL` | `http://127.0.0.1:8098` | |
| `RAG_EXTERNAL_TOP_K` | 4 | agent-side default |
| `RAG_EXTERNAL_TIMEOUT_SECONDS` | 3.0 | |
| `RAG_EXTERNAL_RETRY_SECONDS` | 1 | client re-init backoff |
| `RAG_SELECTIVE_GATE_ENABLED` | true | the §6.2 gate |
| `RAG_QUERY_MIN_WORDS` | 3 | gate floor |
| `RAG_SEARCH_BUDGET_S` | 2.5 | first deadline |
| `RAG_FALLBACK_SEARCH_BUDGET_S` | 4.0 | second deadline, same in-flight task |
| `RAG_RESPONSE_GRACE_S` | 0.15 | added to both |
| `RAG_QUALITY_MODE` | true | raises top_k for person/self queries |
| `RAG_PERSON_TOP_K` | 12 | |
| `RAG_SELF_IDENTITY_TOP_K` | 8 | |
| `RAG_MULTI_QUERY_ENABLED` | true (default) | |
| `RAG_MULTI_QUERY_MAX_QUERIES` | 3 | |
| `RAG_MULTI_QUERY_CONTEXT_MAX_CHARS` | 6000 | merged-context clip |
| `RAG_CACHE_TTL_S` / `RAG_CACHE_MAX_ITEMS` | 300 / 32 | |
| `RAG_ASYNC` | false | speculative prefetch (machinery is intact) |
| `RAG_ASYNC_WAIT_BUDGET_S` | 0.20 | |
| `RAG_QUERY_REWRITE` | true | fast-LLM query rewrite |
| `RAG_QUERY_REWRITE_TIMEOUT_S` | 0.15 | |
| `RAG_ENGLISH_PARITY` | true | English regex patterns alongside Serbian |
| `RAG_CLAIM_GROUNDING_SCOPE` | `wayfinding_only` | or `all` |
| `RAG_SKIP_BACKCHANNELS` | long CSV | exact-match skip list |

---

## 11. Failure modes this design defends against

Every one of these was a real X2 incident. Assume each will recur on A2.

| # | Failure | Defense | Where |
|---|---|---|---|
| 1 | "prvi panel" fuzzy-matched the heading "Tema Panela" and became a person lookup | `_NON_NAME_TOKENS` blocks schedule vocabulary from the canonical name index | §5.4 |
| 2 | Context budget < chunk size silently clipped the panelist list off the best hit → invented panelists | `_effective_context_max_chars` floor + startup warning | §5.8 |
| 3 | Shown 2 panelists, the model produced a 3rd to round out the list | "never fill a list out to a round number" | §7.1 |
| 4 | Follow-up "a ko to vodi?" retrieved an arbitrary panel → right-sounding, wrong answer | topic memory + `_contextualize_rag_query` | §6.3 |
| 5 | A different panel's names in context were used as the answer | "Facts about a different panel are not an answer" | §7.1/7.2 |
| 6 | Whole answer refused because one field was missing | "report partial context part by part", with a scripted abstention | §7.2 |
| 7 | Stale context from a previous turn steered the answer | `_remove_previous_rag_message`, exactly one at a time | §6.7 |
| 8 | Retrieval timeout → visible dead air | shielded task + two-stage budget, never a second search | §6.6 |
| 9 | Timeout retry doubled embedding cost exactly when already slow | re-await the same in-flight task | §6.6 |
| 10 | STT fragments ("Pauza za ručak.") dropped by the gate — silently | wayfinding-keyword early return in `_should_attempt_rag` | §6.2 |
| 11 | Hallucinated times reaching TTS | numeric claim-grounding filter | §7.3 |
| 12 | Robot tried to reach huggingface.co at a customer site | `RAG_OFFLINE` + hard startup failure | §4.4 |

---

## 12. Port checklist for A2 Ultra

**Reuse unchanged (platform-independent):**
- `rag_service/app.py` in full — no robot-specific code in it
- `livekit-client/rag/` (`external_client.py`, `config.py`)
- `livekit_config/runtime_prompts.py` + `prompts/runtime.yaml` `rag_protocol.*`
- The gate / query-construction / cache helpers in `agent_main.py` (§6.2–6.5)
- `robot_supervisor_v2` knowledge proxy endpoints

**Re-author per robot:**
- Persona and context YAML — the A2's name, voice, venue
- `entity_dictionary.json` — this event's people, rooms, facilities, aliases
- `install_lexicon.yaml` — **wayfinding/schedule keywords for the A2's language(s)** (§6.2)
- `_NON_NAME_TOKENS` — your agenda's column headers and ordinals (§5.4)
- Latency budgets — re-measure on A2 silicon; do not copy 2.5s/4.0s on faith

**Verify before calling it done:**
1. `RAG_CONTEXT_MAX_CHARS ≥ RAG_CHUNK_CHARS + 400` — assert at boot (§5.8)
2. Port both agenda regression tests from `test_rag_hybrid_search.py` (§5.4) and the
   budget-floor test (§5.8)
3. Ask an ordinal question ("first panel") against a **deliberately poisoned** canonical
   name index — it must not become a person lookup
4. Ask a three-part panel question — all three parts must be answered
5. Ask for a panelist list you know is **absent** from the agenda PDF — the robot must
   abstain, naturally, and never produce a name
6. Ask a follow-up with a pronoun — must stay on the same panel
7. Kill the RAG service mid-conversation — the robot must degrade to persona answers, not
   hang or crash
8. `__RAG_OFF__` then `__RAG_ON__` mid-conversation — no restart required
9. Run the whole thing with the network cable pulled

**Do not do:**
- Parse the agenda into structured fields. It is one PDF in one index. (§1)
- Shrink `RAG_CHUNK_CHARS` for latency — it breaks temporal adjacency. Lower `top_k`
  instead. (§4.3)
- Lower `RAG_HYBRID_MIN_FINAL_SCORE` to "get more results". An empty context is a
  designed outcome. (§5.5)
- Let the agent rerank or post-process `hits`. Keep all retrieval quality server-side, or
  you lose the ability to tune with `curl`. (§3)

---

## 13. Source map

| Concern | File |
|---|---|
| RAG service, search pipeline, index CRUD | `rag_service/app.py` |
| Index state | `rag_service/data/index_state.json` |
| Entity dictionary | `rag_service/data/entity_dictionary.json` |
| Agenda document | `rag_service/data/knowledge/comtrade-tech-day/` |
| Async RAG HTTP client | `livekit-client/rag/external_client.py` |
| Client config from env | `livekit-client/rag/config.py`, `livekit_config/voice_settings.py` |
| Gate, query build, budgets, injection | `livekit-client/agent_main.py` (`_run_rag`, `_build_rag_search_queries`, `_add_rag_message`, `_should_attempt_rag`) |
| Injected-message + grounding templates | `livekit_config/prompts/runtime.yaml` (`rag_protocol.*`) |
| Agenda personas and rules | `livekit_config/prompts/personas.yaml` (§3.1), `personas.example.yaml` (`comtrade_tech_day_agenda`) |
| Wayfinding / schedule keywords | `livekit_config/locales/<lang>/install_lexicon.yaml` |
| Prompt composition | `livekit_config/prompt_builder.py`, `livekit_config/runtime_prompts.py` |
| Operator proxy endpoints | `robot_supervisor_v2/app/api/main.py` (Knowledge Management section) |
| Agenda regression tests | `robot_supervisor_v2/testing_scripts/test_rag_hybrid_search.py` |
| Standalone QA service (not the voice path) | `livekit-client/answer_service.py`, `utils/answer_engine.py` |
| A2 migration analysis (prior work) | `docs/agibot/a2_voice_rag_analysis.md`, `docs/agibot/a2_voice_rag_tts_stt_handoff.md` |
