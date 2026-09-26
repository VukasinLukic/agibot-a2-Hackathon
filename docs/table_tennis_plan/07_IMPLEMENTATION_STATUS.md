# 07 Stanje implementacije zajedničke osnove

Datum: 26. septembar 2026. Osnova je u `table_tennis/` (uputstvo: `table_tennis/README.md`).
Ništa nije testirano na fizičkom A2. Po dogovoru tima, pytest suite i React feature
nisu deo ove isporuke.

## Šta postoji

| Deo | Stanje |
|---|---|
| Ugovor v1 (`contracts/`) | Pydantic modeli, diskriminisane unije komandi i događaja, generator JSON Schema + OpenAPI + TS (`--check` za zastarelost). TS tipovi su u `robot_supervisor_v2/frontend/src/features/table-tennis/generated/contract.ts`. |
| Pravila i engine (`core/`) | Jedan singl gem do 11 uz 2 razlike, servis po formuli iz ugovora (i posle 10:10), manual i assisted režim, propose/confirm, award, let, undo poslednjeg poena (i završnog), pause/resume, sides/calibration u pauzi, persona između razmena, readiness. `best_of != 1` i `automatic` se eksplicitno odbijaju. Event sourcing: replay log-a daje isto stanje. |
| Trajnost (`storage/`) | SQLite: komanda, događaji, snapshot i outbox u jednoj transakciji; idempotency pre revizije; UNIQUE po reviziji i po `rally_id`; oporavak posle restarta bez ponavljanja govora/gesta. |
| API (`api/`) | Sve rute iz ugovora pod `/api/table-tennis`, SSE sa cursor/resync i heartbeat-om, actor auth (local/token), standalone mock app (`run_demo`), Supervisor hook iza `TABLE_TENNIS_ENABLED=1` (ne menja `/api/events`). |
| Fake adapteri | Ekran (latest revision wins, transliteracija), gest (p1/p2 -> robot left/right), navigator (requested -> ready, busy, cancel, failed), govor sa determinističkim srpskim replikama u regular i corporate personi, vision fixture producer. Sve je zabeleženo u `table_tennis/var/fake_outputs.log` i na `GET /debug/outputs`. |
| A2/LiveKit scaffold | `robot/a2_adapters.py`, `persona/speech.py`: dry-run, sa `REAL:` oznakama; bez importa hardverskih modula. |
| Simulator (`sim/`) | 14 scenarija iz odeljka 12 ugovora + `manual-game` i `disputed-point`; HTTP i in-process drajver; generator fixtures. |

## Ostalo po granama

- **comp-vision (osoba 1):** capture sa `frame_seq`/timestamp, kalibracija stola, detektor/tracker, događaji, benchmark. Polazište `vision/stub.py`.
- **backend (osoba 2):** pytest suite (matrica iz 02_BACKEND.md, konkurentnost, SSE granica, restart), output orchestration sa pravim adapterima, automatski režim tek posle benchmark-a.
- **navigation (osoba 3):** pravi ekran/gest/navigacija u `robot/a2_adapters.py` na postojećem A2 kodu, readiness provere, hardverski smoke test sa mentorom.
- **persone (osoba 4):** React feature `features/table-tennis/` (setup, scoreboard, kontrole, predlog, poziv robota, SSE klijent sa reconnect-om) na generisanim tipovima; LiveKit govor; opcioni LLM komentar sa template fallback-om.

## Pre grananja

Integrator pokreće mock backend i oba simulator scenarija, pregleda diff (bez `table_tennis/var/`, baza i logova) i pravi jedan commit, npr. `feat(table_tennis): shared foundation v1 (mock)`. Taj SHA dele sva četiri člana.

## Backend grana: Faza 1 (pravila) završena

- Test matrica iz 02_BACKEND.md i pravila servisa/pobede: `tests/table_tennis/core/test_tt_rules.py`,
  `tests/table_tennis/core/test_tt_engine_phase1.py`; svi scenariji iz `sim/scenarios.py`:
  `tests/table_tennis/test_tt_scenarios.py`. Pokretanje: `python -m pytest tests/table_tennis -q`.
- Ispravke engine-a posle pregleda: `point.award`/`rally.let` za zatvoren ili poništen rally uvek
  daju `stale_rally` (provera rally ID-a pre statusa); `persona.set` je odbijen i u pauzi dok postoji
  aktivan/pending rally; `match.paused` posle undo beleži stvarni `previous_status`.
- Fixtures (`tests/table_tennis/fixtures/*.json`) generisane i determinističke (`python -m table_tennis.sim.fixtures --check`).

## Backend grana: Faza 2 (race uslovi i trajnost) završena

- `tests/table_tennis/storage/test_tt_concurrency.py`: istovremene komande za istu reviziju (tačno jedna prihvaćena),
  operator vs CV trka, isti command_id iz više niti, konflikt ID-a, idempotency pre revizije, UNIQUE odluka po
  rally-ju u bazi, drugi writer na istoj bazi dobija 409 bez oštećenja, dozvole actor-a.
- `tests/table_tennis/storage/test_tt_recovery.py`: restart vraća isto stanje i replay log-a daje isto stanje; govor i
  gest se ne ponavljaju posle restarta (`skipped_restart`, `unknown_restart`); undo otkazuje zastarele izlaze; stari
  komentar se preskače kad počne nova razmena; ekran latest-wins; greška adaptera ne gubi poen; robot poziv prekinut
  restartom postaje `failed` (ishod nepoznat).
- Nije bilo potrebe za izmenom koda: postojeća implementacija je prošla sve provere.

## Backend: ishod poziva robota kao događaj meča

- Kad poziv koji ima `match_id` završi, robot adapter šalje `robot.ready.set` (actor `robot`) i backend emituje
  `readiness.changed` sa `component: "robot_ready"` i razlogom:
  `robot_arrived` (value true), `robot_call_failed` (false), `robot_call_cancelled` (false).
  Izveštaj robota stvara događaj i kad se vrednost ne menja (npr. neuspeh dok robot nije bio spreman).
- `readiness.changed` je dodat u govorni outbox (`SPEECH_EVENT_TYPES` u `core/service.py`). Tekst najave je na
  personi (osoba 4): `Commentator.line_for` za `readiness.changed` + `robot_ready` (urađeno na grani `persone`,
  uključujući i `manual_arrival`); ostale komponente ne izgovaraju ništa.
- Mock: waypoint-i iz `robot.fail_waypoints` (`broken-spot`) automatski su dozvoljeni, da bi UI mogao da proba neuspeh.
- Testovi: `tests/table_tennis/robot/test_tt_robot_call_events.py`.

## Backend grana: Faza 3 (API i SSE) završena

- `tests/table_tennis/api/test_tt_http_api.py`: sve rute preko HTTP-a, kodovi 401/403/404/409/422 u ErrorResponse
  formatu, ceo ručni tok, robot pozivi, token režim (bez tokena 401 i na GET/SSE, token u URL-u se ne prihvata,
  vision token ne može da dodeli poen), validacija config-a i bind-a, Supervisor hook (isključen, bez tokena se ne
  montira, sa tokenom traži auth), `run_demo` za zauzet port i javni host.
- `tests/table_tennis/api/test_tt_sse.py`: snapshot + događaji, dva događaja iste revizije, reconnect preko
  `?last_event_id=` i `Last-Event-ID` bez duplikata i bez gubitka, resync za nepoznat ID, događaj baš na granici
  snapshot/pretplata stiže tačno jednom, spor klijent ne blokira engine, heartbeat, pravi uvicorn server.
- Bez izmena koda: postojeći API je prošao sve provere.

## Backend grana: Faza 4 (koordinacija izlaza) završena za backend deo

- Izbor adaptera konfiguracijom (`adapters:` u config-u ili `TT_ADAPTER_DISPLAY/GESTURE/SPEECH/NAVIGATOR`):
  `fake` ili pravi razredi (`a2`, `livekit`). U mock modu pravi razredi rade kao dry-run; `mode: real`
  odbija fake adaptere i ne startuje dok osobe 3 i 4 ne ubace pravi transport (bez tihog fallback-a).
- `GET /health` sada za ekran, govor i gest pokazuje adapter, dry-run, broj uspešnih/neuspelih/preskočenih i
  poslednju grešku; `available=false` dok poslednji pokušaj nije uspeo. Poen ostaje sačuvan.
- Gubitak kamere u assisted režimu odbija nove CV predloge, ručni poen ostaje dostupan, režim se ne menja.
- `python -m table_tennis.manifest`: SHA, grana, dirty, režim, adapteri, hash ugovora, da li su generisani
  fajlovi i fixtures ažurni, poslednji meč, poznate granice; `hardware_tested` je uvek false.
- Testovi: `tests/table_tennis/integration/test_tt_phase4_outputs.py`, `tests/table_tennis/test_tt_manifest.py`.
- Preostalo: pravi transporti (osoba 3: ekran/gest/navigacija, osoba 4: LiveKit govor) na već postojećim
  `REAL:` mestima; Faza 5 čeka benchmark osobe 1; Faza 6 traži robota i mentora.

## Persone grana (osoba 4): aplikacija i dve persone, mock

- **Replike** (`table_tennis/persona/`): regular (vedar, neutralan sudija) i corporate (dobronamerno zezanje po
  poziciji u firmi, nasumičan „miljenik” po meču, samo u rečima). Robot se predstavlja kao Titan; za igrače samo
  rodno neutralni oblici. Rezultat uvek prvi i iz snapshot-a, šala posle; na važnim trenucima uvek replika, inače
  otprilike svaki treći poen. Izbor varijante je stabilan hash (isti događaj = ista rečenica, i posle restarta).
  Najave dolaska robota (`robot_arrived`, `manual_arrival`, `robot_call_failed`, `robot_call_cancelled`).
  Pozicije: `persona/roles.py` (isti spisak u frontend `roles.ts`). Bez LLM-a i bez cloud poziva.
- **Aplikacija TitanSudija** (`robot_supervisor_v2/frontend/src/features/table-tennis/`): za telefon na
  `/#/stoni-tenis` i kao tab „Stoni tenis” u Supervisoru (registracija u `main.tsx` i `App.tsx`). Setup (imena,
  pozicija, persona, prvi servis, strana robota, režim), poziv Titana ili „već je kod stola”, sto odozgo gde dodir
  polovine daje poen, predlog kamere sa potvrdom, poništi/ponovi/pauza, promena persone, kraj meča, pridruživanje
  drugog telefona. Skor samo iz backend snapshot-a; bez veze dugmad su isključena. U mock režimu prikazuje tačan
  tekst koji bi Titan izgovorio. Stil je ograničen na `.tt` (`table-tennis.css`), ostatak Supervisora nije diran.
- **Testovi:** `tests/table_tennis/persona/test_commentator.py` (tačan rezultat u svakoj najavi, iste brojke u obe
  persone, nema korporativnih šala u regularnoj, imena kao podaci, nepoznata pozicija, najave robota). Frontend:
  `tsc -b` i eslint čisti; tok proveren u headless pregledaču na veličini telefona.
- **Pravi glas** (`persona/speech.py`, `LiveKitSpeechOutput`): rečenicu šalje na Supervisor
  `POST /api/conversation/command`, agent je izgovara doslovno (`session.say`, Soniox TTS, bez LLM-a).
  Na početku meča/promeni persone šalje `__REFEREE_ON__:<persona>`, na kraju `__REFEREE_OFF__`.
- **Lične šale (LLM, opciono)** (`persona/joke_bank.py`): na početku meča jedan poziv Azure OpenAI napiše
  šale za ove igrače (imena, pozicije, tajni miljenik); tokom igre se samo biraju, pa poen ne čeka LLM.
  Filtrira brojeve i rezultat; ako LLM kasni ili padne, ostaju ručno pisane. Keš: `table_tennis/var/jokes/`.
- **Razgovor sa Titanom tokom meča** (`livekit-client/referee_mode.py` + 38 dodatih linija u `agent_main.py`):
  persone `titan_sudija` i `titan_korporativni_sudija` u `livekit_config/prompts/personas.yaml` (vide se i u
  Supervisor prompt manageru); alat `get_table_tennis_match` čita rezultat (samo čitanje); agent ćuti dok je
  poen u toku i ne pokreće gestove sam. Van meča agent radi kao pre.
- **Testovi:** `tests/table_tennis/persona/` (replike, pravi transport sa lažnim Supervisorom, banka šala sa
  lažnim LLM-om, referee režim).

### Pokretanje glasa na robotu (PC2, uz mentora)

Preduslov: Supervisor, voice-agent i audio-bridge rade (Titan govori na „manual speech” iz Supervisora).
Agent treba restart da učita `referee_mode.py`.

```bash
TT_ADAPTER_SPEECH=livekit TT_SPEECH_LIVE=1 TT_SUPERVISOR_URL=http://127.0.0.1:8070 TT_LLM_JOKES=1 python -m table_tennis.run_demo --mode mock
```

- `TT_SPEECH_LIVE=1` pušta samo glas uživo dok su ekran/gest/navigacija još simulirani (`mode=real` traži sve
  prave adaptere). `/health` i dalje piše `dry-run` za govor; stvarno slanje se vidi u logu (`SPEECH sent`).
- `TT_LLM_JOKES=1` koristi `AZURE_OPENAI_*` iz `.env`; bez toga idu samo ručno pisane šale.
- Agent čita backend na `TT_API_URL` (podrazumevano `http://127.0.0.1:8099`).
- Izmeriti: vreme od dodira „+ poen” do glasa (Supervisor otvara LiveKit sobu po rečenici).

- **Preostalo:** redosled pozdrav/gest/govor sa osobom 3; gašenje automatskog razgovora na detekciju osobe
  tokom meča (Vision Controller, Supervisor); provera na A2.
