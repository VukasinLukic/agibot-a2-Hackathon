# Claude Code context: TitanSudija A2 integracija

Ovaj dokument je paket konteksta za agenta koji treba da poveže postojeći
`table_tennis` ugovor sa stvarnim A2 servisima. On nije dozvola da se robot
pokrene. Svaki fizički test zahteva mentora, potvrđen E-stop i eksplicitnu
potvrdu da je robot bezbedan za kretanje.

## Šta je već gotovo

- Backend, storage, API, SSE, auth i mock adapteri rade.
- React feature se builduje u `robot_supervisor_v2/dist` i Supervisor ga servira
  zajedno sa `/api/table-tennis/*`.
- `A2ScoreDisplay`, `A2GestureOutput` i `A2RobotNavigator` trenutno imaju dry-run
  transport. Dry-run sme da simulira, ali ne sme da pozove hardver.
- Automatic scoring je blokiran dok ne postoji vision benchmark.
- Fizički A2 nije testiran.

## Šta znači transport

Transport je konkretan most između našeg porta i postojećeg servisa:

| Naš port | Postojeći servis koji treba proučiti | Dokaz koji mora postojati |
|---|---|---|
| `ScoreDisplay.render/close` | `robot_services/screen_manip/emoticon_screen.py` | head-screen slot, provisioning, show/release i robot-enabled ponašanje |
| `GestureOutput.present_point` | `robot_services/gestures/motion_player.py`, katalog `robot_services/gestures/catalogs/agibot_a2_ultra.py` | stvarni motion ID/name za `wave`, `point left`, `point right`, cancel/status |
| `RobotNavigator.request/get_status/cancel` | `robot_supervisor_v2/app/api/nav_missions.py`, `robot_services/autonomous_navigation/testing_controls/a2_nav.py` | map ID, waypoint, preflight, native `task_id`, polling i cancel sa pravim ID-em |
| `SpeechOutput.speak` | `robot_services/audio/audio_bridge_manager.py`, Supervisor speech services | LiveKit URL/room/identity, audio bridge, TTS provider i latency |
| vision process | `table_tennis/vision/*`, camera bridge/ROS2 capture | camera device/topic, calibration JSON, `camera_ready`, benchmark rezultati |

## Pravila za agenta

1. Prvo čitaj kod i postojeće testove; ne izmišljaj endpoint, waypoint, motion ID, mapu ili token.
2. Ne kopiraj tajne u Git, prompt, log ili novi fajl. `.env` i lokalni config su samo runtime input.
3. Ne menjaj `contracts`, `core` ili `storage` da bi se prilagodili vendor API-ju. Vendor kod ide iza adaptera.
4. Mock mora ostati bez hardverskih importa/poziva. `mode: real` sme raditi samo kada je transport proverljiv.
5. Pre fizičkog poziva napravi fake/spy test koji proverava redosled i payload.
6. Za navigaciju nikada ne šalji `task_id=0`; cancel je potvrđen tek posle stvarnog statusa. Programski cancel nije E-stop.
7. Ne tvrdi da je hardver spreman na osnovu HTTP `200`, `isRunning` ili konfiguracije.
8. Posle svake promene pokreni contract check, fixtures check, backend testove i frontend build.

## Informacije koje treba prikupiti na robotu

Ovo se radi read-only, sa mentorom:

- PC2 hostname/IP i Supervisor port;
- `map_id` aktivne kancelarije i tačan waypoint pored stola;
- da `/tf` daje svež `map -> base_link` pose;
- da su localization i walk action stvarno aktivni;
- ko drži screen slot i audio bridge tokom termina;
- stvarni A2 motion nazivi/ID-jevi;
- camera device/topic, resolution, FPS i `calibration_id`;
- da je E-stop dostupan i ko odobrava svaki hod.

Read-only početak:

```bash
hostname -I
cd /agibot/humanoid-platform
git status --short
git log --oneline -1
aima em doctor
```

Za LiveKit se proveravaju samo prisustvo i konfiguracija, bez ispisivanja vrednosti:
`LIVEKIT_URL`, `LIVEKIT_ROOM`, `LIVEKIT_AGENT_NAME`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`, audio bridge status i testna rečenica.

## Vision benchmark koji nedostaje

Osoba 1 mora predati reproducibilan rezultat za približno 50 ručno označenih razmena:
broj svih razmena, validnih predloga, tačnih/netečnih predloga, abstention, precision
i coverage, uz verziju modela, kamere i kalibracije. Bez tog artefakta
`automatic_scoring` ostaje false; assisted ostaje operator-confirm.

## Minimalni redosled implementacije

1. Read-only discovery izveštaj; bez motion/nav komandi.
2. Screen adapter iza postojećeg porta i spy test.
3. Gesture adapter i provera live kataloga, bez kretanja tela u prvom testu.
4. Navigation adapter: preflight → start → poll → cancel, timeout, stale-pose i unknown-outcome.
5. Speech adapter i audio test bez robota u hodu.
6. Vision capture, kalibracija, `camera.ready.set` i benchmark.
7. Jedan kontrolisani fizički smoke test uz mentora.

## Prihvatni kriterijumi

- `mode: mock`: bez mrežnog/hardverskog poziva; svi postojeći testovi prolaze.
- `mode: real`: startup odbija nedostajući transport; nema tihog fallback-a.
- Screen: jedan vlasnik fizičkog slota, latest-revision wins, explicit release.
- Gesture: stvarni motion je potvrđen kao accepted/completed; failure je vidljiv.
- Navigation: preflight sprečava hod, native `task_id` se pamti, cancel je potvrđen.
- Speech: output/error/latency su vidljivi, bez tajni u logovima.
- Vision: stale/missing/calibration mismatch odbijaju predlog; benchmark artefakt postoji.
- Nijedan dokument ili manifest ne tvrdi `hardware_tested=true` pre mentorskog testa.

## Copy/paste prompt za Claude Code

```text
You are integrating TitanSudija table-tennis with a real Agibot A2, but the robot
must not move without an explicit mentor-approved test step. Read these files first:
table_tennis/AGENTS.md, table_tennis/robot/AGENTS.md,
docs/table_tennis_plan/05_SHARED_CONTRACT.md,
docs/table_tennis_plan/06_REUSE_AUDIT.md,
docs/table_tennis_plan/07_IMPLEMENTATION_STATUS.md,
docs/table_tennis_plan/12_POKRETANJE_NA_ROBOTU.md, and
docs/table_tennis_plan/13_CLAUDE_CODE_A2_INTEGRATION_CONTEXT.md.

Then inspect, without guessing APIs, the existing vendor implementations in:
robot_services/screen_manip/emoticon_screen.py,
robot_services/gestures/motion_player.py,
robot_services/gestures/catalogs/agibot_a2_ultra.py,
robot_supervisor_v2/app/api/nav_missions.py,
robot_services/autonomous_navigation/testing_controls/a2_nav.py,
robot_services/audio/audio_bridge_manager.py, and table_tennis/vision/.

Produce a discovery report before editing: exact callable/API, required config,
read-only evidence, unknowns, safety blockers, and proposed adapter boundary.
Do not print or commit secrets. Do not run a robot motion, gesture, screen write,
or audio command until the report is reviewed and a mentor explicitly approves.

Implement only behind the existing ports. Preserve the mock path and add spy/unit
tests first. For navigation, preserve preflight, stale pose checks, native task_id,
polling, timeout, cancellation confirmation, and unknown outcome after restart.
For screen, preserve exclusive physical-slot ownership and explicit release. For
vision, require camera_ready, calibration_id and assignment_version. Automatic
scoring stays disabled until a benchmark artifact is present.

Run and report:
python -m table_tennis.contracts.generate --check
python -m table_tennis.sim.fixtures --check
python -m pytest tests/table_tennis -q
cd robot_supervisor_v2/frontend && npm run build

Do not claim hardware success. End with a separate list of what is proven in code,
what is proven by a read-only robot check, and what still requires a mentor-led
physical test.
```
