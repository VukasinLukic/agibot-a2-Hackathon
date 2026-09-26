# 11 Odgovori na pitanja za mentore, pronađeni u kodu i dokumentaciji

Zajednički dokument za ceo tim (grane `comp-vision`, `backend`, `navigation`, `persone`).
Najbitnije po grani: vision 4 i 5, backend 2, 3 i 7, navigation 6 (ekran, gestovi) i 8, persone 5 i 6 (govor).

Izvor: lokalni repo (isti kod kao `/agibot/humanoid-platform` na Titanu, uz moguće razlike u verziji)
i docx dokumentacija. Na robotu ništa nije pokretano. Uz svaki odgovor: koliko je siguran i šta mentor
još treba samo da klimne glavom.

## 1. Da li je 192.168.2.50 uvek PC2?

- POTVRĐENO (tim): preko Etherneta je uvek `192.168.2.50`, i tako se povezujemo.
- UŽIVO: `ssh agi@192.168.2.50` daje `agi@ubuntu-orin`, aarch64, tegra kernel = Orin = PC2.
- `docs/A2_Ultra_Robot_Reference.md` tvrdi da je .50 "PC1's exposed address". To je u suprotnosti sa
  onim što vidimo uživo; verujemo živom izlazu.
- Network docx: robot ima "static IP ports" za Ethernet. Dakle adresa bi trebalo da ostane ista posle restarta.
- Mentor samo potvrđuje: statička je, ne DHCP. Provera posle svakog restarta: `hostname -I` mora da sadrži `192.168.100.110`.

## 2. Ko poseduje Supervisor na 8070, sme li naš proces paralelno?

- Supervisor pokreće `run_robot_supervisor_v2.sh` u tmux sesiji `robot_supervisor` (port 8070); skripta
  ima "cron/boot mode", tj. pali se automatski pri boot-u. Vlasnik je Comtrade tim (mentori).
- Kod: jedan Supervisor po robotu; on drži LiveKit, audio (gasi AIMA `agent` i `hal_audio`), vision, gestove.
- Paralelni proces koji samo čita (naš backend koji ne šalje komande robotu) ne smeta. Proces koji šalje
  gestove/nav mimo Supervisora SMETA (konflikt vlasništva nad MC akcijom, vidi nav_missions "hold" logiku).
- Preporuka: naš backend sme paralelno, ali fizičke izlaze (govor, gest, ekran) šalje KROZ Supervisor API.
- Mentor potvrđuje: da smemo paralelno i da ne restartujemo njihov Supervisor.

## 3. Gde je naš kod i na kom portu radi

- Naš kod: `table_tennis/` + `robot_supervisor_v2/frontend/src/features/table-tennis/` u timskom repou
  `a2-hackathon` (zajednička osnova `15ff3b6`; četiri grane se spajaju u `main`).
  Isti repo sadrži i Supervisor, pa na robotu naš paket stoji u `/agibot/humanoid-platform/table_tennis`.
  Poseban folder na robotu NIJE potreban i nije napravljen.
- Port: **8099**, bind `127.0.0.1` (`table_tennis/config.example.yaml`; mreža samo uz `auth.mode=token`).
  8099 ne koristi nijedan drugi servis u repou. Zauzeti su: 8070 (Supervisor na Titanu), 8766/8767 (audio
  manager), LiveKit, 5xxxx (AimDK).
- Ili bez posebnog porta: unutar Supervisora sa `TABLE_TENNIS_ENABLED=1` -> `:8070/api/table-tennis` (restart = mentor).
- Mentor odlučuje: kako timski `main` stiže u `/agibot/humanoid-platform` (git pull ili kopija) i da li
  pokrećemo samostalno (8099) ili u Supervisoru.

## 4. Kamera

- A2 kamere u repou (`camera_demo.py`, proverene aliasi za A2):
  - `/dev/video0` = CHEST_MAIN (centralna RGB)
  - `/dev/video2` = **CHEST_FISHEYE_L (levi fisheye)**
  - `/dev/video4` = CHEST_FISHEYE_R
  - ROS2 topic levog fisheye-a: `/aima/hal/fish_eye_camera/chest_left/color` (ROS_DOMAIN_ID=232)
  - ostali topici: `/aima/hal/camera/interactive/color`, `/aima/hal/rgbd_camera/head_front/color`, `/aima/hal/rgbd_camera/waist_front/color`
- Supervisor vision/camera/recording čitaju **`/dev/video10`**: v4l2loopback koji puni `camera_share/camera_share.sh`
  (ffmpeg, default izvor `/dev/video6`, **960x540 @ 30 fps**, mjpeg ulaz). Više potrošača deli isti stream i
  moraju tražiti istu rezoluciju.
- Za levi fisheye to znači: ili se `camera_share` prebaci na `CAMERA_SRC=/dev/video2` (menja kameru SVIM
  Supervisor servisima = mentor), ili naš proces čita ROS2 topic / `/dev/video2` direktno (samo ako niko drugi
  ne drži uređaj ekskluzivno).
- FPS: nije izmeren za fisheye. Stari komentar u `h264_decoder.py`: ~22 fps dekodiranja.
  Prva merna radnja: `python camera_demo.py --camera CHEST_LEFT_FISHEYE` (read-only stream) uz mentora.
- Napomena za lopticu: fisheye ima veliko izobličenje na ivicama i manju rezoluciju po stepenu; za malu
  brzu lopticu centralna RGB (`/dev/video0`) ili spoljna kamera je verovatno bolja. Spoljna kamera: nigde
  zabranjena u docs, a `08_MENTORSKI_RAZGOVORI.md` je već predviđa kao pitanje.
- Mentor potvrđuje: da li je `/dev/video2` stvarno levi fisheye na Titanu i da li smemo da ga čitamo paralelno.

## 5. Kako ugasiti auto-razgovor kada detektuje osobu

- Tok: `robot_services/vision/detection/main.py` (YOLO) zaključa najbližu osobu -> `POST /api/vision/person_detected`
  -> `vision_controller.queue_person_detected` -> Supervisor startuje razgovor. Docs: dok je Vision Controller
  aktivan, ručna aktivacija je isključena i robot sam započinje razgovor.
- **Isključenje bez gašenja servisa (operator toggle):**
  `PATCH http://192.168.2.50:8070/api/vision` sa telom `{"enabled": false}`.
  `set_enabled(False)` briše prisutnu osobu, čekajuće događaje i card/face capture. Detektor i kamera rade dalje,
  ali razgovor se ne pokreće. Isto postoji kao prekidač u Supervisor UI (Vision). Vraćanje: `{"enabled": true}`.
  Stanje: `GET /api/vision`.
- Jače: zaustaviti servis `POST /api/services/vision-controller/stop` (gasi i detektor).
- Za lopticu: detekcija loptice treba da bude NAŠ proces, ne ovaj vision-controller (on je za ljude).
  Ako nam treba detekcija igrača, koristimo njegov izlaz, ali sa toggle-om `enabled=false` tokom meča.
- Mentor potvrđuje: da smemo da prebacimo toggle (to je PATCH koji menja stanje Supervisora).

## 6. Govor, ekran, gestovi

**Govor (fiksna rečenica, bez LLM prepričavanja):**
`POST http://192.168.2.50:8070/api/conversation/command`
```json
{"text": "Poen Ana, tri prema dva", "gesture": null}
```
Agent (`livekit-client/agent_main.py`) to izgovara preko `session.say(text, allow_interruptions=False)`.
Uslov: voice-agent i audio-bridge moraju da rade i postoji aktivna LiveKit soba. Kombinovano sa gestom:
`{"text": "...", "gesture": "point left"}`; sekvenca: `"steps": [{"text":"...","gesture":"...","pause_after_ms":500}]`.
CLI ekvivalent: `robot_supervisor_v2/testing_scripts/send_agent_command.py --text "..."`.
Nema completion događaja; odgovor znači "poslato".

**Gestovi (A2 katalog, `robot_services/gestures/catalogs/agibot_a2_ultra.py`, svih 20 SAFE_ONLY):**
wave(1), greeting bow(2), cheer(3), thumbs up(4), welcome gesture(5), goodbye wave(6), presentation point(7),
nod thanks(8), handshake(9), v sign(10), fist bump(11), finger heart(12), **point left(13)**, **point right(14)**,
no(15), ok sign(16), raise hand(17), panel explanation(18-20, dugi 22-29 s).
Point left/right su "Direction_point to the left/right" iz AgiBot kataloga (verifikovano 2026-08-03 na ovom A2).
Pokreće se kroz `/api/conversation/command` sa `gesture`, ili `/api/nav/...` gesture ruta. Trajanje kapirano na 6 s.
Leva/desna su iz perspektive ROBOTA (proveriti uživo jednom).

**Ekran (rezultat koji ostaje):**
`robot_services/screen_manip`: `show_message(text, subtitle, duration_s)` drži poruku `duration_s` pa VRAĆA
default lice. Klip se inače vrti u petlji zauvek (`docs/agibot/head_screen.md`), pa za trajni rezultat treba
pustiti naš klip i NE zvati restore do kraja meča (adapter `table_tennis/robot/a2_adapters.py` već planira
"latest revision wins" + `screen.release`). Nema Supervisor HTTP rute za ekran; poziva se Python funkcijom
na PC2, a ona SSH-uje na PC1 face host (192.168.100.100) sa ključem `~/.ssh/agibot_rsa`.
Mentor potvrđuje: da smemo da koristimo ovaj put (dira PC1).

## 7. Logovi

- Supervisor servisi: `/agibot/humanoid-platform/logs/` (po servisu), arhiviraju se pri gašenju.
  Preko API-ja (read-only): `GET http://192.168.2.50:8070/api/services/<ime>/logs?lines=200`
  i live: `/api/services/<ime>/logs/stream`. Imena: `vision-controller`, `voice-agent`, `audio-bridge`,
  `camera-bridge`, `livekit`, `gesture-bridge`... Spisak: `GET /api/services`.
- Supervisor boot: `/agibot/data/home/agi/Desktop/CT/humanoid-platform/robot_supervisor_boot.log` + tmux `robot_supervisor`.
- AimDK motor/ruke (Gate 2/3 greške): `/agibot/log/pnc_arm/pnc_arm.log`.
- Ostali AimDK moduli: pod `/agibot/log/<modul>/` (isti obrazac; potvrditi `ls /agibot/log`).
- Sistemski servisi: `journalctl -u <servis>` (npr. `agibot_resource_server`, vidi `camera_testing/output.txt`).

## 8. `aima em doctor` i ARM

- `aima em doctor` je čisto prikaz stanja (lista EM aplikacija, PID, state). Bezbedan je; `stop-app/start-app/reset-app`
  NISU. Supervisor ima i API: `POST /api/audio-bridge/aima/doctor` (vraća izlaz).
- ARM: `POST /api/nav/arm` (dugme ARM u Navigation tabu). Šta radi (`nav_missions.py`): stavlja MC u
  walking akciju, **napaja noge** i zaustavlja idle animaciju, i drži to petljom jer skillpilot na ~60 s
  vraća idle. `POST /api/nav/disarm` vraća idle ponašanje. `GET /api/nav/arm` = status (read-only).
- Pošto ARM menja stanje motora, uključuje ga mentor (Safety guide preporučuje baš ARM protiv idle animacije).
