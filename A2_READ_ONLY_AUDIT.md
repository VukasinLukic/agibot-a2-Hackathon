# A2 read-only audit za TitanSudija

Datum: 2026-09-27. Robotov sat: `2026-09-27T08:10:18+08:00`, Asia/Shanghai, `synchronized:false`, `ntp_active:false`.
Dokaz: `GET /api/system-clock`.

## Kako je rađeno

- Sve je rađeno sa laptopa, samo `GET` zahtevima ka `http://192.168.2.50:8070`. Nije bilo POST/PUT/PATCH/DELETE.
- Nije bilo komandi u robotovom shell-u. Claude se ne prijavljuje SSH-om sa lozinkom.
- Sve što traži shell ima status `NIJE TESTIRANO`. Komanda za to je u dodatku A.
- Kod iz sekcija 4–6 pročitan je iz **lokalne** kopije repoa, ne sa robota. Supervisor na robotu radi iz drugog foldera (vidi 2), pa isti kod nije dokazan.
- Tajne su redigovane.

## 1. Identitet robota

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| Hostname, IP, PC2 | NIJE TESTIRANO | nema shell-a | `hostname -I` | ispis iz dodatka A | ako se ne proveri, može se pogrešno raditi na PC1 |
| SSH host ključ | PASS | `ssh-keyscan` daje `SHA256:5xSjsXk2r1ORLQpku2I1Jj80e614b9hycWLl3407S5M`, isti kao u planu | ssh | — | — |
| OS, A2 verzija, ROS, arhitektura, Python, disk, RAM/GPU | NIJE TESTIRANO | nema shell-a | dodatak A | ispis | — |
| Model u Supervisor config-u | FAIL | `/api/status`: `"robot":{"id":"unitree-g1-edu-01","platform":"unitree","model":"unitree_g1_edu"}` | `robot_supervisor_v2/config.yaml` blok `robot:` | treba `agibot` / `agibot_a2_ultra` | temperature monitor gleda Unitree topic `rt/lowstate`; ostale funkcije koje zavise od modela nisu provereno ispravne |
| Internet | PASS (za Supervisor proces) | `/api/system-clock/drift`: `"reference":"https://www.google.com","round_trip_ms":339,"ok":true` | — | — | voice-agent log od 2026-09-26 ima `could not connect to ElevenLabs` |
| Vreme i zona | PASS | `timezone: Asia/Shanghai`, UTC+8, bez NTP-a, odstupanje 3,4 s | `/api/system-clock` | — | logovi su u UTC+8, a mi smo UTC+2 |

## 2. Robot Supervisor

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| Radi | PASS | `GET /api/health` → `{"status":"ok"}`; `/api/status` → `system.version 2.0.0`, 12 servisa | :8070 | — | — |
| Radni direktorijum | PASS | putanje u odgovorima: `/agibot/data/home/agi/Desktop/CT/a2-team4/...` (`/api/lidar/costmap/status`, `/api/nav/live/status`, `/api/speech/config`) | — | **nije `/agibot/humanoid-platform`** kao u našim docs | deploy u pogrešan folder ne menja Supervisor |
| Korisnik, komanda, tmux | NIJE TESTIRANO | nema shell-a | `ps`, `tmux ls` (dodatak A) | ispis | — |
| Port | PASS | odgovara na 192.168.2.50:8070 sa LAN-a | — | — | nema auth-a, izložen celom LAN-u |
| OpenAPI rute | PASS | `/openapi.json` (150 KB): `nav/*`, `gestures`, `conversation/*`, `services/*`, `vision*`, `audio-bridge/*`, ... | — | **nema nijedne `/api/table-tennis/*` rute** | — |
| Logovi | PASS | `/api/services/<ime>/logs` → `log_path: robot_supervisor_v2/logs/<ime>.log` | GET logs | — | — |
| Greške u logovima | FAIL | `rag-service` je `failed` i restartovan 6 puta 01:46:15–01:46:32. `voice-agent.log` (2026-09-26 17:46 UTC): `could not connect to ElevenLabs`, `Face API returned HTTP 500`. `livekit.log` ima Go stack trace pre starta u 08:05:32. | logs | uzrok rag pada nije u tail-u | govor ne mora da radi |
| Env fajl | FAIL | `/api/environment`: `env_file .../.envs/dev.env`, `env_file_exists:false` (vrednosti redigovane) | — | — | — |

Stanje servisa (`/api/services`):
- `livekit`: running
- `rag-service`: failed
- stopped: voice-agent, audio-bridge, camera-bridge, vision-controller, gesture-bridge, video-recording-service, teleimager-server, inspire-hands, xr-teleop, robot-temperature-monitor

## 3. TitanSudija kod

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| Backend rute u Supervisoru | FAIL | `GET /api/table-tennis/health` → 404 `API endpoint ... not found`; OpenAPI nema `table-tennis` | `include_table_tennis` u `app/api/main.py` | kod na robotu i/ili `TABLE_TENNIS_ENABLED=1` + `TT_AUTH_MODE=token` + restart | UI tab postoji, ali backend ne odgovara |
| Frontend tab | PASS (samo build) | `/assets/index-BZ-eZyKO.js` ima `stoni-tenis` (1×) i `table-tennis` (5×) | `/#/stoni-tenis` | backend | tab puca bez backend-a |
| Putanja paketa, import, zavisnosti (numpy, cv2, onnxruntime, fastapi, rclpy) | NIJE TESTIRANO | nema shell-a | dodatak A | ispis | — |
| `.env`, imena `TT_*` | NIJE TESTIRANO | nema shell-a | dodatak A (samo imena) | ispis | — |
| Režim mock/real, adapteri, SQLite, runtime logovi | NEPOZNATO | ruta ne postoji, pa ni runtime | — | — | — |
| Da li `mode: real` može da se napravi | FAIL (iz koda) | `table_tennis/robot/a2_adapters.py` i dalje ima samo `_dry_run_transport`; `dry_run=False` bez transporta odbija start (linije 101–119) | — | pravi transport za ekran, gest i navigaciju | — |

## 4. Ekran (lokalni kod)

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| Ekran | NIJE TESTIRANO | nijedan poziv nije poslat | `robot_services/screen_manip/add_custom_message.py` | fizički test uz mentora | ide preko PC1 |

Odgovori iz koda:
- **Javna funkcija:** `show_message(message, subtitle="", *, duration_s=2.0)` → `controller.flash_text(...)` (`add_custom_message.py:95`).
- **Trajanje:** vremenski ograničeno. Posle `time.sleep(duration_s)` sledi `restore_default_face()` (`emoticon_screen.py:161–182`). Klip se sam od sebe vrti beskonačno (`emoticon_screen.py:100`), a vremensko ograničenje daje tek restore.
- **Promena teksta:** novi `flash_text` renderuje novi mp4 u isti slot. Stariji flash vidi `superseded` i ne vraća lice (`:163–171`).
- **Podrazumevano lice:** `restore_default_face()` pušta default emoticon id (`add_custom_message.py:131`).
- **Blokiranje:** blokira oko `duration_s` plus vreme rendera. Async verzija je `show_message_async` (`asyncio.to_thread`).
- **Globalni slot:** da, `slot_name = "emoticon_ct_message"` (`emoticon_screen.py:72`).
- **Transport:**
  - render ide preko SSH na face host `agi@...` sa ključem `~/.ssh/agibot_rsa` i ControlMaster-om (`:65–94`, `:321–340`);
  - `rc_module` je `http://192.168.100.100:59001`, tj. **PC1**;
  - `ResourceService` je `http://127.0.0.1:51049`.
- **Pad procesa:** klip ostaje zaglavljen na ekranu, jer se sam ne gasi (`:100`).
- **Rezultat do sledećeg poena:** moguć, ali ne kroz `show_message`. Treba `flash_text` bez restore-a, ili duži `duration_s` i ponovni flash. `table_tennis/robot/score_display.py` drži slot, a transport nije spojen.
- **Model:** `_resolve_robot_model_id` uzima `HUMANOID_ROBOT_MODEL`/`ROBOT_MODEL`, a kad nijedan nije postavljen, podrazumeva `agibot_a2_ultra`. Ekran zato ne zavisi od pogrešnog `robot:` bloka iz sekcije 2, osim ako je env postavljen na unitree (`NIJE TESTIRANO`).
- **`robot_enabled()`:** ako je false, flash se preskače sa `robot_disabled` (`:133`).
- **Mapiranje:**
  - `screen.show` → `controller.flash_text(primary, secondary, duration_s=<veliko>)`, ili nova metoda koja pušta klip bez restore-a;
  - `screen.release` → `restore_default_face()`.

## 5. Gestovi i ARM

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| Lista kataloga | PASS | `GET /api/nav/gestures?refresh=true`: `catalog: agibot_a2_ultra`, `safety_pool: safe_only`, 20 gestova, 133 motion-a | `/api/nav/gestures` | — | — |
| Imena za sudiju | PASS (ime postoji) | `wave 7.16`, `point left 6.13`, `point right 18.26`, `nod thanks 9.37`, `cheer 7.73`, `thumbs up 6.59` (`durations_s`) | isto | — | `point right` traje 18,26 s, predugo za gest posle poena |
| `point_left` / `nod` / `thumbs_up` | FAIL (tačan naziv) | nazivi su sa razmakom: `point left`, `point right`, `nod thanks`, `thumbs up` | — | — | — |
| Pokretanje gesta | NIJE TESTIRANO | nije poslat | `POST /api/nav/gestures/play {"gesture": "<ime>"}` (`nav_missions.py:2245`) | mentor, ARM | 70 kg robot |
| Gesture-bridge servis | FAIL | `/api/gesture-bridge/config`: `service_running:false`. `/api/gestures` daje drugi (Unitree) spisak: `release arm`, `clap`, ... | — | — | dva različita kataloga gestova |
| ARM stanje | PASS | `GET /api/nav/arm`: `{"held":false,"rearms":0,"suppressed":false}` | `POST /api/nav/arm` (samo mentor) | — | ARM pali motore nogu (`nav_missions.py:2265`) |
| Prekid, E-stop | NEPOZNATO | kod: `play` je odbijen dok misija radi (409, `:2253`). Uticaj E-stop-a na gest nije dokumentovan u pročitanom kodu | — | `doctor` | — |
| Zaključavanje nav/gest | PASS | `/api/nav/action-types`: `"gesture+turn": "refused: a gesture forces the MC into whole-body servo..."` | — | — | — |

## 6. Navigacija

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| Aktivna mapa | PASS | `current_map_id 1790423599533`, `team_4`, `is_current:true` | `/api/nav/maps` | — | — |
| Waypoint-i na aktivnoj mapi | FAIL | `/api/nav/maps/1790423599533/meta` → `"waypoints":[]` | `/meta` | tačka pored stola | hod nije moguć |
| `referee-spot` | FAIL | nema ga ni na jednoj proverenoj mapi. `Comrade_upstairs` ima 8 tačaka (java, Fun_room, ...), `new_office` ima 2 | `/meta` | — | — |
| Lokalizacija | FAIL | `/api/nav/status`: `"localization_running":false` | — | relokalizacija (mentor, tablet) | — |
| Poza | FAIL | `"pose":null,"stream_running":false` | `/api/nav/pose`; stream pali `/api/nav/live/stream` | tab Navigation otvoren | — |
| Noge | FAIL | `mc_action: McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO`, `can_walk:false` | — | mentorov ARM | — |
| Task, misija | PASS | `pnc_task_id 163034944041965378`, `PncServiceState_IDLE`, `task_canceled`; `/api/nav/run` → `{"active":false}`; `/api/nav/missions` → `[]` | — | — | — |
| Blockeri | FAIL | dva: MC action / arm, i `Localization is not running` | `/api/nav/status.blockers` | — | — |
| E-stop, work_state, kolizija | NIJE TESTIRANO | nema ih u HTTP odgovoru | `a2_nav.py doctor/status` | ispis | — |

Tok iz koda:
- **Misija:** `PUT /api/nav/missions`, pokretanje `POST /missions/{id}/run`, napredak `GET /run`, otkaz `POST /run/cancel`.
- **Direktan hod:** `a2_nav.py goto` → `PlanningNaviToGoal{map_id, target_id}` (`a2_nav.py:751–762`). Otkaz: `cancel_task(task_id, verify=8.0)` (`:630`).

Mapiranje za `A2RobotNavigator` (`table_tennis/robot/a2_adapters.py`):

| Naš poziv | Izvor | Rupa |
|---|---|---|
| `nav.facts` | `/api/nav/status` → `localization_running`, `mc_action`, `can_walk`, `runner.active`, `current_map_id` iz `/maps` | nema `emergency_stop`, `work_enabled`, `collision`, `pose_age_ms` (NavFacts u `readiness.py:28`). Treba `doctor` ili nov read-only izvor |
| `nav.points` | `/api/nav/maps/{map_id}/meta` → `waypoints[].id/name/x/y` | — |
| `nav.request` | `PlanningNaviToGoal` (`a2_nav.py goto`) ili misija sa jednim korakom | vraćanje native `task_id` nije potvrđeno preko HTTP-a |
| `nav.status` | `/api/nav/status` → `pnc_task_id`, `pnc_state`, `pose` | nema `pose_age_ms`, `within_tolerance`, `settled`, `progress_mark`, `emergency_stop` |
| `nav.cancel` | `POST /api/nav/run/cancel` (misija) ili `a2_nav.py cancel` sa pravim `task_id` | — |

Tolerancije za dolazak (distance, settling) nisu u API odgovoru: `NEPOZNATO`.

## 7. Govor i LiveKit

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| LiveKit server | PASS | `livekit` running, pid 4019644; log `starting LiveKit server ... portHttp 7880, bindAddresses 0.0.0.0` | — | — | — |
| Voice agent | FAIL | `stopped` | `POST /api/services/start-speech` (nije pozvano) | start uz dozvolu | — |
| Audio bridge | FAIL | `/api/audio-bridge/status` → `"running":false`; `/api/nav/speech/status` → `"available":false, "the audio bridge is not reachable on http://127.0.0.1:8766"` | — | start | — |
| AIMA mode | PASS (stanje) | `/api/audio-bridge/aima/status`: `current_mode: disabled`, `bridge_mode_stop_apps: [agent, hal_audio]` | — | — | — |
| Aktivna soba | FAIL | `/api/conversation/status`: `"state":"idle","room":null` | — | — | — |
| Endpoint za tekst | PASS (šema) | `POST /api/conversation/command`, body `AgentCommandRequest{text, gesture?, force_gesture, steps[], room?}`, odgovori 200 i 422 | — | stvarni odgovor NIJE TESTIRANO | `gesture` u istom zahtevu pokreće ruku |
| TTS provajder | FAIL | `/api/speech/config`: `provider elevenlabs, eleven_v3, language sr`; voice-agent log: `could not connect to ElevenLabs`. U našoj persona dokumentaciji piše Soniox, a na robotu je ElevenLabs | — | — | govor zavisi od interneta |
| Utišavanje razgovora sa prolaznicima | PASS (stanje) | `/api/vision`: `"enabled":false`, `vision-controller` stopped | `PATCH /api/vision` (nije pozvan) | — | — |

## 8. Kamera i vision

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| Supervisor kamera | FAIL | `/api/devices/video` → `{"devices":[]}`; config `/dev/video10`, 960x540 | — | `camera_share` nije aktivan | — |
| ROS fisheye topic, FPS, rezolucija, encoding | NIJE TESTIRANO | nema shell-a | `scripts/robot_vision.sh check` | ispis | — |
| rclpy / cv2 / numpy / onnxruntime | NIJE TESTIRANO | — | dodatak A | — | — |
| BallNet težine | FAIL | ne postoje na ovom laptopu (pretraga `C:\Users\Tea`) | — | težine | vision na robotu ne može da se pokrene sa `robot_vision.sh` |
| Kalibracija, benchmark | NEPOZNATO / FAIL | na robotu nije proveravano; `07_IMPLEMENTATION_STATUS.md` kaže da benchmark ne postoji | — | — | automatic scoring ostaje isključen |

## 9. Mreža i telefon

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| Supervisor sluša na LAN-u | PASS | laptop 192.168.2.119 dobija 200 sa 192.168.2.50:8070 | — | — | nema auth-a |
| Firewall | PASS za laptop | isto | — | telefon NIJE TESTIRANO | — |
| Wi-Fi robota | NEPOZNATO | `/api/status.network`: `"error":"wifi_status_unavailable"` | — | ruter ili hotspot | telefon ne vidi LAN kabl |
| SSE preko LAN-a | NIJE TESTIRANO | tt rute ne postoje | — | — | — |
| Frontend URL i token | PASS (kod) | `features/table-tennis/config.ts:14–20`: `VITE_TT_API_BASE ?? ''` (isti origin), token `VITE_...` ili podrazumevani `'operator_secret'` | — | — | hardkodovan podrazumevani token |

## 10. Bezbednost

| Podsistem | Status | Dokaz | Interfejs | Nedostaje | Rizik |
|---|---|---|---|---|---|
| E-stop | NIJE TESTIRANO | nije u HTTP odgovoru | `a2_nav.py doctor` | ispis | — |
| ARM | PASS (stanje) | `held:false` | ARM radi mentor | — | pali noge |
| Idle animacija | PASS (stanje) | `/api/nav/idle-motion`: `neck_enabled:true`, `current_motion: 灵动环顾4.mcap`, `MotionCommandStatus_IDLE` | — | — | robot sam okreće struk i glavu |
| Zaključavanje nav/gest | PASS | `/api/nav/action-types` concurrency pravila; `play` → 409 dok misija radi | — | — | — |
| Vlasnik hardvera | NEPOZNATO | ekran ide preko PC1 face host-a; zvuk drži AIMA `agent` kad bridge nije aktivan | — | — | — |

## Zaključak

1. **Dokazano da radi:**
   - Supervisor API na :8070;
   - LiveKit server;
   - čitanje mapa, gestova, ARM stanja i idle stanja;
   - katalog A2 gestova sa četiri potrebna imena;
   - internet sa robota (drift check preko google.com);
   - frontend build sa tabom za stoni tenis.
2. **Postoji samo u kodu:**
   - A2 ekran (`flash_text` / restore);
   - puštanje gesta (`/api/nav/gestures/play`);
   - hod (`PlanningNaviToGoal`, misije);
   - `/api/conversation/command`;
   - ceo `table_tennis` backend (lokalno testiran, ne na robotu);
   - vision live.
3. **Nedostaje:**
   - `/api/table-tennis/*` na robotu (kod i/ili env i restart);
   - pravi transporti u `a2_adapters.py`;
   - waypoint na aktivnoj mapi `team_4`;
   - lokalizacija i poza;
   - BallNet težine;
   - kalibracija;
   - voice-agent i audio-bridge (stopped);
   - ispravan `robot:` model u config-u;
   - `.envs/dev.env`.
4. **Traži fizički test:** jedan tekst na ekranu, jedan gest, jedna rečenica, jedan hod, FPS kamere, vidljivost loptice sa sudijske tačke.
5. **Traži mentora:** relokalizacija, ARM/DISARM, restart Supervisora i izmene `.env`, start speech servisa, bilo kakav pokret, ekran preko PC1, novi waypoint pored stola.
6. **Najkraći bezbedan redosled:**
   1. dodatak A (read-only);
   2. deploy `table_tennis` u `/agibot/data/home/agi/Desktop/CT/a2-team4` uz mentora, env, restart; ceo ručni meč u `mode: mock` preko telefona;
   3. start speech (`voice-agent` + `audio-bridge`), jedna rečenica preko `/api/conversation/command` bez `gesture`;
   4. jedan tekst na ekranu, pa restore;
   5. ARM (mentor), jedan `wave`;
   6. relokalizacija, waypoint, suvi `goto`, hod (mentor).
7. **FULL demo:** **nije moguć danas.** Nema TT backend-a na robotu, nema lokalizacije ni waypoint-a, noge nisu u režimu hoda, govor i bridge su ugašeni, nema BallNet težina, a ekran i gest nemaju pravi transport u `table_tennis`.
8. **Fallback koji mora biti spreman:**
   - `run_demo --mode mock` na laptopu (plan B iz `ROBOT_VISION_KIT.md`) sa telefonom;
   - poeni ručnim dodirom;
   - „robot je već kod stola” (manual arrival);
   - skor na telefonu;
   - govor preko `/api/conversation/command` samo ako se speech servisi podignu.

## Dodatak A: read-only komanda za shell na robotu

Pokreće operater u svom SSH terminalu na PC2. Ne ispisuje vrednosti tajni.

```bash
hostname -I; hostname; uname -m; cat /etc/os-release | grep PRETTY; ls /opt/ros; \
python3 --version; /usr/bin/python3 --version; df -h / /agibot/data | tail -n +1; free -h; \
cat /proc/device-tree/model 2>/dev/null; echo; \
ps -eo user,pid,args | grep -E 'run_api.py|robot_supervisor' | grep -v grep; tmux ls 2>/dev/null; \
D=/agibot/data/home/agi/Desktop/CT/a2-team4; ls -d $D/table_tennis /agibot/humanoid-platform 2>&1; \
git -C $D log --oneline -1; test -f $D/.env && grep -oE '^[[:space:]]*(TT_[A-Z_]+|TABLE_TENNIS_[A-Z_]+|ROBOT_MODEL|HUMANOID_ROBOT_MODEL|ROBOT_ENABLE)' $D/.env; \
grep -A4 '^robot:' $D/robot_supervisor_v2/config.yaml; \
for P in /usr/bin/python3 $D/.venv/bin/python; do echo "== $P"; (cd $D && $P -c "
import importlib
for m in ['table_tennis','numpy','cv2','onnxruntime','fastapi','pydantic','rclpy','sensor_msgs.msg']:
    try: x=importlib.import_module(m); print('ok',m,getattr(x,'__version__',''))
    except Exception as e: print('FALI',m,type(e).__name__)
"); done; \
ls -la $D/table_tennis/var/vision 2>&1 | head; ls $D/table_tennis/var/*.sqlite 2>&1; \
cd $D && python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py status; \
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py doctor
```
