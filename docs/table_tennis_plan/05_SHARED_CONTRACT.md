# Zajednički ugovor v1

Autoritet: ovaj dokument. Vlasnik: osoba 2. Sve četiri grane koriste identične tipove. Ovo je specifikacija za scaffold i kasniju implementaciju.

## 1. Granice sistema

`table_tennis/` je novi mali paket u korenu postojećeg repozitorijuma. Poslovna pravila ne uvoze ROS, SDK, OpenCV, LiveKit ni kod Supervisora. Integracija koristi adaptere. Postojeći monolit ne prepisivati.

Predložena struktura:

```text
table_tennis/
  contracts/  core/  storage/  api/  sim/
  vision/  robot/  persona/
  config.example.yaml
  requirements-dev.txt
  run_demo.py
robot_supervisor_v2/frontend/src/features/table-tennis/
  generated/  components/  api/  mocks/
tests/table_tennis/
  contracts/  core/  api/  vision/  robot/  persona/  integration/
```

Pydantic modeli u contracts su izvor istine. Iz njih se generišu JSON Schema/OpenAPI i TS tipovi za frontend. Fixture JSON mora proći iste validatore. Potrebno je eksplicitno definisati request i response modele za svaki endpoint; SSE snapshot koristi isti model kao GET.

## 2. Identitet i vreme

- `player_id` je stabilan `p1` ili `p2`; nikad ne upisuj rezultat samo pod „left”.
- `court_end_by_player` mapira igrača na `end_a` ili `end_b` u koordinatama kalibrisanog stola.
- `robot_side_by_player` mapira igrača na `left` ili `right` iz perspektive robota. To nije nužno leva strana video-snimka.
- `assignment_version` raste pri promeni dodeljenih strana. Stari kandidat se odbija.
- `calibration_id` identifikuje geometriju kamere/stola; promena kamere zahteva novu kalibraciju.
- `command_id`, `event_id`, `match_id`, `proposal_id`, `call_id`: UUID string.
- `rally_id`: UUID koji backend kreira pri `rally.arm`; prihvata se najviše jedna konačna odluka po rally-ju.
- `revision`: rastući integer stanja meča, uključuje pauzu, korekciju, personu i ostale prihvaćene promene. Ne koristi ga kao broj poena.
- `occurred_at`: UTC ISO8601 sa zonom; `received_at` postavlja server. Redosled određuje server, ne sat klijenta.
- Kamera koristi `frame_seq` i `capture_monotonic_ns` iz jednog procesa. Ta vremena nisu uporediva između računara bez sinhronizacije.

## 3. Modeli

| Model | Obavezna polja i semantika |
|---|---|
| Player | id, display_name; opciono ručno uneta `role_label`, `role_rank` za zabavnu personu |
| MatchConfig | target_points=11, win_by=2, best_of=1, first_server_id, scoring_mode=assisted (API model default), persona=regular |
| MatchSnapshot | schema_version, match_id, revision, status, players, score_by_player, first_server_id, server_id, winner_id, assignment_version, court_end_by_player, robot_side_by_player, calibration_id, active_rally_id, active_proposal_id, persona, scoring_mode, ready, updated_at |
| PointProposal | proposal_id, rally_id, winner_id, confidence, reason, calibration_id, assignment_version, capture window, optional evidence_ref |
| VisionObservation | frame_seq, capture_monotonic_ns, detected, x_px/y_px ili null, observation_kind=observed/predicted/missing, confidence, calibration_id |
| RobotCall | call_id, table_id, named_waypoint_id, state, updated_at, reason nullable |
| RobotStatus | call_id nullable, availability, navigation_state, pose_age_ms nullable, ready, reason nullable |
| EventEnvelope | schema_version, event_id, match_id nullable za robot poziv, revision nullable za robot poziv, type, occurred_at, received_at, causation_id, payload |
| CommandEnvelope | schema_version, command_id, expected_revision, type, payload |
| CommandResult | command_id, duplicate, event_ids, snapshot |
| ErrorResponse | code, message, current_revision nullable, details bez tajni |

Confidence je [0,1], ali sirovi model score nije dokaz statističke kalibracije. Razlog je enum `missed_return`, `double_bounce`, `out_after_hit`, `service_fault`, `unknown`; automatski dozvoljeni razlozi biraju se tek nakon testiranja. Mrežica/let i rub ostaju ručna odluka u MVP-u. `evidence_ref` je lokalni identifikator klipa, ne neproverena putanja/URL za čitanje.

`ready` sadrži odvojeno `robot_ready`, `camera_ready`, `calibration_ready`, `operator_ready`. Manual mode sme raditi bez kamere; mock mode jasno označava simulaciju. Assisted/automatic ne smeju prihvatati CV predloge iz nekalibrisane/stale kamere.

UI za novi meč namerno bira bezbedni manual režim („Mi, dodirom“); operator mora pri
kreiranju izabrati assisted ako želi CV predloge. `scoring_mode` je posle kreiranja
nepromenljiv.

## 4. Primer potvrđenog stanja

```json
{
  "schema_version": "1.0",
  "match_id": "11111111-1111-4111-8111-111111111111",
  "revision": 12,
  "status": "between_rallies",
  "players": [
    {"id": "p1", "display_name": "Ana", "role_label": "lead", "role_rank": 3},
    {"id": "p2", "display_name": "Marko", "role_label": "engineer", "role_rank": 2}
  ],
  "config": {"target_points": 11, "win_by": 2, "best_of": 1},
  "score_by_player": {"p1": 3, "p2": 2},
  "first_server_id": "p1",
  "server_id": "p1",
  "winner_id": null,
  "assignment_version": 1,
  "court_end_by_player": {"p1": "end_a", "p2": "end_b"},
  "robot_side_by_player": {"p1": "left", "p2": "right"},
  "calibration_id": "table-1-camera-a-v1",
  "active_rally_id": null,
  "active_proposal_id": null,
  "persona": "regular",
  "scoring_mode": "assisted",
  "ready": {"robot_ready": true, "camera_ready": true, "calibration_ready": true, "operator_ready": true},
  "updated_at": "2026-09-26T12:00:00Z"
}
```

`config` je obavezni podmodel snapshot-a uz polja u gornjoj tabeli. U prvom scaffoldu best_of je samo 1; više gemova se eksplicitno odbija, ne prikazuje kao podržano.

## 5. Ulazne komande

UI šalje backendu komande; CV šalje predloge; samo backend emituje potvrđene score događaje. `source`/identitet pošiljaoca određuje adapter/autentifikacija na serveru, ne poverenje u proizvoljan JSON.

| type | payload | Ko sme |
|---|---|---|
| match.start | {} | operator |
| rally.arm | {} | operator; kasnije kontrolisani orchestrator |
| point.propose | PointProposal | vision adapter ili eksplicitni simulator |
| point.confirm | proposal_id | operator |
| point.award | rally_id, winner_id, reason | operator |
| rally.let | rally_id, reason | operator |
| point.undo | target_event_id, reason | operator |
| match.pause / match.resume | reason | operator |
| match.end | reason | operator |
| persona.set | persona: regular/corporate | operator, između razmena |
| sides.set | obe kompletne mape strana | operator, u pauzi |
| calibration.set | calibration_id | operator, u pauzi |
| robot.ready.set | ready, reason | robot adapter; operator samo za označen ručni dolazak |
| camera.ready.set | ready, reason | vision adapter |
| operator.ready.set | ready | operator |

`scoring_mode` se bira pri kreiranju i ostaje fiksan u MVP-u. Novi režim može zahtevati novi meč; ne uvoditi prećutnu promenu.

Primer CV komande:

```json
{
  "schema_version": "1.0",
  "command_id": "22222222-2222-4222-8222-222222222222",
  "expected_revision": 13,
  "type": "point.propose",
  "payload": {
    "proposal_id": "33333333-3333-4333-8333-333333333333",
    "rally_id": "44444444-4444-4444-8444-444444444444",
    "winner_id": "p1",
    "confidence": 0.91,
    "reason": "missed_return",
    "calibration_id": "table-1-camera-a-v1",
    "assignment_version": 1,
    "capture_start_seq": 420,
    "capture_end_seq": 480,
    "evidence_ref": null
  }
}
```

## 6. Događaji i rezultat

Backend emituje: `match.created`, `match.started`, `rally.armed`, `point.proposed`, `point.confirmed`, `rally.let`, `score.corrected`, `match.paused`, `match.resumed`, `match.finished`, `persona.changed`, `sides.changed`, `calibration.changed`, `readiness.changed`.

Score događaji sadrže winner_id za poen, rally_id, reason, previous_score, new_score i post-commit snapshot. `score.corrected` dodatno sadrži target_event_id. Ovo su potvrđene činjenice za ekran i glas.

Jedna komanda može emitovati više događaja u jednoj reviziji, npr. point.confirmed i match.finished. Potrošač ne odbacuje drugi događaj samo zato što ima istu reviziju: deduplikacija po event_id; zastarelost po revision. Score prikaz je idempotentan upsert snapshot-a.

Robot poziv ima odvojene događaje `robot.call.updated`, payload RobotCall/RobotStatus. Do dolaska nemaju score revision. Nikad ne uvećavati rezultat zbog promenjenog statusa navigacije.

## 7. Pravila i korekcija

MVP je singl, jedan gem do 11 uz dva razlike. Sledeći server za T ukupnih poena:
- pre 10:10: prvi server ako je floor(T/2) paran, inače drugi;
- od 10:10: prvi server ako je (T-20) paran, inače drugi.
Primeri: 0:0 P1, 1:0 P1, 2:0 P2, 3:2 P1, 10:10 P1, 11:10 P2, 11:11 P1. Posle završenog gema nema novog servisa.

`point.undo` u MVP-u poništava samo poslednji još aktivan potvrđeni poen. Negativan rezultat nije moguć. Server i winner ponovo se računaju iz aktivnih odluka; undo završnog poena ponovo otvara gem. Ponovljeni undo istog targeta se odbija ili vraća raniji odgovor za isti command_id.

Ako je sledeći rally već armed, undo ga poništava i match prelazi u paused, bez aktivnog rally-ja. Stari predlozi i stari izvršni zadaci su nevažeći. Novi pokušaj dobija novi rally_id; prethodni se nikad ponovo ne koristi. U istoriji ostaje i original i korekcija.

States: setup -> between_rallies -> rally -> pending_decision -> between_rallies / finished. Pause je zasebno stanje; čuva prethodni status za kontrolisani resume. Pause tokom rally-ja ga prekida: resume vraća between_rallies i traži nov rally.arm. Pause sa pending odlukom čuva predlog samo ako kalibracija/strane nisu menjane; confirm dozvoljen tek nakon resume. Promena strana/kalibracije poništava pending i rally.

Let zatvara trenutni rally bez poena i bez promene servera; sledeći dobija novi ID. Nerešen predlog se ne potvrđuje istekom vremena.

## 8. Transport

Namenski feature router unutar Supervisora, uz standalone mock app koji ga koristi bez startup sporednih efekata ostatka sistema.

| Endpoint | Uloga |
|---|---|
| GET /api/table-tennis/health[?match_id=<id>] | režim mock/real, capability status; vision čita `camera_ready` i `calibration_ready` iz izabranog/najnovijeg meča |
| POST /api/table-tennis/matches | CreateMatchRequest -> snapshot (201) |
| GET /api/table-tennis/matches/{id} | autoritativni snapshot |
| POST /api/table-tennis/matches/{id}/commands | CommandEnvelope -> CommandResult |
| GET /api/table-tennis/matches/{id}/events | SSE live stream i snapshot resync |
| POST /api/table-tennis/robot/calls | table_id, named_waypoint_id, command_id -> RobotCall (202) |
| GET /api/table-tennis/robot/calls/{id} | RobotCall |
| POST /api/table-tennis/robot/calls/{id}/cancel | command_id -> zahtev za otkazivanje (202) |
| GET /api/table-tennis/robot/status | RobotStatus |

Osoba 2 poseduje match/router transport i registraciju; osoba 3 implementira robot servis/poseban router unutar rezervisanih ruta. Osoba 4 pravi UI klijenta.

Idempotency: isti command_id + isti payload vraća sačuvani odgovor čak i ako expected_revision više nije aktuelan. Isti ID sa drugim payloadom -> 409. Tek nakon provere idempotency ide provera revision. Zastareo expected_revision -> 409 uz current_revision; klijent preuzima snapshot i traži novu ljudsku potvrdu za zastarelu odluku. Nema slepog retry-a sa novim ID.

Za POST /matches command_id je deo request-a, a idempotency scope je kreiranje meča, jer ID meča još ne postoji. Robot calls imaju svoj scope. Pogrešan model -> 422; bez pristupa -> 401/403; nepostojeći ID -> 404.

SSE klijent prima početni snapshot sa cursor-om iz istog zaključanog/transactional preseka, zatim događaje posle cursor-a bez rupe. Na reconnect šalje last_event_id; ako nije u retention-u, dobija resync snapshot. Periodični heartbeat. Spor klijent dobija resync, ne zaustavlja engine. UI posle reconnect-a prikazuje trenutno stanje; stare gestove i govor ne pušta ponovo.

Auth: lokalni mock bind 127.0.0.1. Za mrežni demo koristiti postojeći podržani Supervisor auth, proveriti da se primenjuje i na nove rute i SSE. Za SSE sa bearer tokenom koristiti fetch streaming; ne stavljati tajne u URL. Nema CORS '*' sa kredencijalima. CV adapter ima samo pravo predlaganja, operator korekcije; persona nema score-write kredencijale.

## 9. Portovi adaptera i bootstrap

Potrebni Python Protocol ugovori, sa konkretnim fake implementacijama:

- `VisionProducer.run(sink, context_provider)`: observation/point.propose, prekida rad na zatvaranje.
- `RobotNavigator.request_call(request)`, `get_status()`, `cancel(call_id)`.
- `ScoreDisplay.render(snapshot)`, `close()`.
- `GestureOutput.present_point(event, snapshot)`, `cancel_pending(match_id)`.
- `SpeechOutput.announce(event, snapshot)`, `cancel_pending(match_id)`.
- `EventStore.append_transaction(...)`, `load_match(...)`, `read_after(cursor)`.
- `Clock` i `IdGenerator` za determinističke testove.
- `RefereeService.handle(match_id, command, actor)` jedini ulaz za promenu meča.

Fake adapteri ne uvoze hardverske module. Mode=mock je default. Real se eksplicitno bira konfiguracijom i ne sme automatski biti fallback iz mock-a.

## 10. Izvršavanje i oporavak

SQLite čuva komandu, događaje, snapshot i outbox u jednoj transakciji. Jedan writer i lock po meču. Jedan vlasnik fizičkog robota; drugi call dobija busy. Single-process demo ili eksplicitni DB locking; ne predstavljati in-memory lock kao zaštitu za više worker-a.

Outbox:
- ekran je idempotentan, latest snapshot wins;
- govor i gest imaju dedup ključ event_id+kind, TTL i aktuelnu match/revision proveru;
- posle pada procesa ne obećavati exactly-once fizički gest: unknown ishod se ne ponavlja automatski;
- undo otkazuje pending side effects, šalje novi ekran i kratku korekciju; već izveden pokret se ne može poništiti;
- stare redove ne puštati pri startup replay-u; replay samo rekonstruiše stanje;
- score engine ne čeka TTS, SSH, render MP4 ili navigaciju.

Nova razmena se armira tek kada je robot spreman; UI označava kraj najave/gesta. Zastareli komentari se preskaču kada je razmena već počela. Ekran održava poslednji rezultat i dok drugi izlaz ne radi.

## 11. Pouzdanost

Scaffold/manual: samo operator upisuje poene. Assisted: svi CV predlozi traže eksplicitnu potvrdu. Automatic: feature flag podrazumevano isključen, prag i dopušteni događaji odobreni nakon lokalnog benchmark-a. Nestanak loptice sam po sebi nije dokaz pobednika.

Na ~50 ili više ručno označenih razmena meriti precision automatskih odluka, coverage svih razmena, abstention, latency i broj izgubljenih frejmova. Visoka tačnost na malom setu je demo rezultat, ne profesionalna garancija.

## 12. Obavezni fixtures

`new_match`, `manual_point`, `duplicate_command`, `left_right_swap`, `stale_proposal`, `pending_proposal`, `let`, `deuce_10_10`, `finish_12_10`, `undo_finish`, `reconnect`, `robot_busy`, `camera_missing`, `persona_change`.

Sve fixture scenarije generisati legalnim komandama kroz engine, da snapshot, servis i revizije budu konzistentni. Statični primer iz ovog dokumenta je format, ne kompletan fixture log.
