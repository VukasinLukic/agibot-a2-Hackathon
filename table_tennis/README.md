# table_tennis: zajednička osnova A2 sudije

Mock backend koji radi lokalno bez robota, kamere i API ključeva. Ugovor v1 je u
`docs/table_tennis_plan/05_SHARED_CONTRACT.md`; Pydantic modeli u
`table_tennis/contracts/` su izvor istine.

> Ništa ovde nije testirano na fizičkom A2. Sve robot/ekran/govor/gest izlaze
> u mock modu izvode fake adapteri i jasno su označeni kao SIMULATED.

## Instalacija (samo lake zavisnosti)

Ne instaliraj root `requirements.txt` (platform-specifičan ROS/Jetson freeze).

Windows PowerShell:

```powershell
cd C:\Users\Tea\OneDrive\Dokumenti\a2-hackathon
py -3 -m venv .venv-tt
.\.venv-tt\Scripts\Activate.ps1
pip install -r table_tennis\requirements-dev.txt
```

Linux:

```bash
python3 -m venv .venv-tt
source .venv-tt/bin/activate
pip install -r table_tennis/requirements-dev.txt
```

Python 3.10+.

## Komande

| Šta | Komanda (iz korena repozitorijuma) |
|---|---|
| Mock backend (127.0.0.1:8099) | `python -m table_tennis.run_demo --mode mock` |
| Drugi port / baza | `python -m table_tennis.run_demo --mode mock --port 8100 --db table_tennis/var/b.sqlite` |
| Simulator: ceo ručni gem | `python -m table_tennis.sim.run --scenario manual-game --api http://127.0.0.1:8099` |
| Simulator: sporni poen (CV predlog + potvrda) | `python -m table_tennis.sim.run --scenario disputed-point --api http://127.0.0.1:8099` |
| Lista scenarija | `python -m table_tennis.sim.run --list` |
| Regeneracija schema/OpenAPI/TS tipova | `python -m table_tennis.contracts.generate` |
| Provera da generisani fajlovi nisu zastareli | `python -m table_tennis.contracts.generate --check` |
| Manifest pred demo (SHA, režim, adapteri, verzije, granice) | `python -m table_tennis.manifest` (`--json`, `--out manifest.json`) |
| Mock sa A2/LiveKit adapterima u dry-run režimu | `$env:TT_ADAPTER_DISPLAY="a2"; python -m table_tennis.run_demo` (Linux: `TT_ADAPTER_DISPLAY=a2 python -m ...`) |
| Regeneracija fixtures | `python -m table_tennis.sim.fixtures` (provera: `--check`) |

Za kompletne backend + vision testove instaliraj i dodatke za kameru:

```powershell
pip install -r table_tennis\requirements-vision.txt
python -m pytest tests/table_tennis -q
```

`requirements-dev.txt` je dovoljan za core/API/robot/persona testove; tri vision test
modula se bez NumPy automatski preskaču. Za stvarnu proveru vision algoritama instaliraj
NumPy/OpenCV iz `requirements-vision.txt`.

API dokumentacija dok backend radi: http://127.0.0.1:8099/docs

Zauzet port se prijavljuje porukom i izlaznim kodom 2. Reset stanja: zaustavi
backend i obriši `table_tennis/var/table_tennis_mock.sqlite`.

Stanje posle restarta: backend čita isti SQLite, vraća tačan rezultat i NE
ponavlja stare govore i gestove (pending -> `skipped_restart`, u toku ->
`unknown_restart`).

## Struktura i vlasništvo

| Putanja | Vlasnik | Sadržaj |
|---|---|---|
| `contracts/` | integrator (osoba 2) | modeli, komande, događaji, generator, `schema/` |
| `core/` | osoba 2 | `rules.py`, `engine.py` (event sourced), `service.py`, `outputs.py`, `ports.py` |
| `storage/` | osoba 2 | `sqlite_store.py`: komanda + događaji + snapshot + outbox u jednoj transakciji |
| `api/` | osoba 2 | router `/api/table-tennis`, SSE, auth, standalone app, Supervisor hook |
| `sim/` | osoba 2 | scenariji, drajveri (in-process/HTTP), CLI, fixtures |
| `vision/` | osoba 1 | kadar, kalibracija, tracker, `MatchVisionProducer`; `stub.py` ostaje fixture put |
| `sound/` | osoba 1 | wav, vrhovi, kadar, podloga, sirovi blok, jedan `missed_return` samo uz slaganje sa slikom |
| `robot/` | osoba 3 | `fake.py`, `call_service.py`, `scoreboard.py`, `a2_adapters.py` (dry-run scaffold) |
| `persona/` | osoba 4 | `templates.py`, `commentator.py`, `speech.py` |
| `robot_supervisor_v2/frontend/src/features/table-tennis/` | osoba 4 | `generated/contract.ts` (generisano, ne menjati ručno) |

## Ključna pravila (kratko)

- Igrači su uvek `p1`/`p2`. Strane su posebna mapa (`court_end_by_player`,
  `robot_side_by_player`) sa `assignment_version`.
- CV šalje samo `point.propose`; rezultat menjaju `point.confirm` (operator)
  ili `point.award` (operator). Samo backend emituje `point.confirmed`.
- Actor određuje server: lokalno header `X-TT-Actor` (samo loopback), u mreži
  `Authorization: Bearer` po actor-u. Vision/sim ne sme da dodeli poen (403),
  persona nema prava upisa.
- Idempotency: isti `command_id` + isti sadržaj vraća sačuvan odgovor
  (`duplicate: true`); isti ID, drugi sadržaj -> 409. Provera ide pre
  `expected_revision`. Zastarela revizija -> 409 uz `current_revision`.
- Najviše jedna konačna odluka po `rally_id` (UNIQUE u bazi).
- `best_of != 1` -> 422 `unsupported_best_of`. `scoring_mode=automatic` ->
  422 `automatic_scoring_disabled`; `features.automatic_scoring: true` -> greška pri startu.
- `rally.arm` traži `robot_ready` (poziv robota ili `robot.ready.set` sa
  `reason: "manual_arrival"`).
- UI novi meč podrazumevano kreira u manual režimu; za CV predloge izaberi assisted
  u setupu, jer se `scoring_mode` posle kreiranja ne menja.
- Jedan proces je jedini writer. Više uvicorn worker-a nad istom bazom nije podržano.

## Rute

`GET /health[?match_id=<id>]`, `GET|POST /matches`, `GET /matches/{id}`,
`POST /matches/{id}/commands`, `GET /matches/{id}/events` (SSE),
`POST /robot/calls`, `GET /robot/calls/{id}`, `POST /robot/calls/{id}/cancel`,
`GET /robot/status`, `GET /debug/outputs` (samo mock).

SSE: prvo `event: snapshot` (cursor + snapshot), zatim `event: match_event`
(`id` = event_id) i posle svakog commit-a novi `snapshot`. Reconnect:
`?last_event_id=` ili `Last-Event-ID` header; nepoznat ID -> `resync: true`.
Heartbeat `: heartbeat` svakih 15 s. Postojeći Supervisor `/api/events` nije menjan.

Health vision capability je vezana za `camera_ready`/`calibration_ready` iz izabranog
meča (ili najnovijeg meča ako `match_id` nije prosleđen); ne predstavlja dokaz da je
fizička kamera povezana. `automatic_scoring_enabled` je konfiguracioni feature flag i
ostaje isključen dok tim ne dostavi benchmark.

## Supervisor integracija (isključena podrazumevano)

`robot_supervisor_v2/app/api/main.py` poziva `include_table_tennis(app)`.
Uključuje se samo sa `TABLE_TENNIS_ENABLED=1` i token auth-om
(`TT_AUTH_MODE=token`, `TT_OPERATOR_TOKEN=...`), jer Supervisor nema svoj auth
i sluša mrežu. Runtime se pokreće tek na prvi zahtev.

## Šta je mock, šta je ostalo po granama

Vidi `docs/table_tennis_plan/07_IMPLEMENTATION_STATUS.md`.
