# 10 TitanSudija: zajednički kontekst robota za ceo tim

Robot: Agibot A2 Ultra, fizičko ime **Titan**. Naša aplikacija / persona: **TitanSudija**
(sudija za stoni tenis). Ovo je zajednički dokument za sva četiri člana tima i za AI asistente
na svim granama. Ne sadrži lozinke ni tokene.

| Grana | Vlasnik | Šta iz ovog dokumenta najviše treba |
|---|---|---|
| `comp-vision` | osoba 1 | kamere (sekcija 3, Kamera), vision controller i detekcija osoba |
| `backend` | osoba 2 | Supervisor, pokretanje, portovi, integracija `table_tennis` |
| `navigation` | osoba 3 | ekran, gestovi, ARM/idle, navigacija, bezbednost pokreta |
| `persone` | osoba 4 | govor, voice agent, auto-razgovor, frontend u Supervisoru |

Sve grane se spajaju u timski `main`; na robot ide samo provereni `main`.
Odgovori iz koda na otvorena pitanja: `11_ODGOVORI_IZ_KODA.md`. Postupak konačnog pokretanja: `12_POKRETANJE_NA_ROBOTU.md`.

Legenda: **[UŽIVO]** potvrđeno na robotu 2026-09-26, **[DOC]** iz docx/repo dokumentacije,
**[TIM]** izjava tima, **[PITATI]** mentor mora potvrditi.

---

## 0. Bezbednost prvo (Agibot Safety Guide, obavezno)

Pravila koja važe za SVAKOGA dok radimo sa Titanom:

1. Titan ima ~70 kg. I sa sigurnosnim sistemima može da povredi čoveka. Svako pomeranje robota
   najavljujemo mentorima i oni nadgledaju svaku akciju robota. [DOC Safety 1.5]
2. **Idle animacija**: posle ~10 s neaktivnosti robot sam rotira struk i glavu. Ta animacija NE staje
   kada je neko blizu. Uvek računati da može krenuti. Isključuje se dugmetom **ARM** u Navigation delu
   Supervisora, ali to radi mentor. [DOC Safety 1.5]
3. Nikada prste/ruke/delove tela u robota, između zglobova ili blizu motora. Ako nešto zapne, zove se mentor. [DOC Safety 1.5]
4. Paljenje (dugme ispod baterije), setup preko tableta, gašenje, punjenje i zamena baterije rade
   **isključivo mentori**. Tokom startup sekvence LiDAR/MC reakcija NIJE aktivna; robot mora biti na gantry-ju.
   Gašenje dok robot nije okačen = robot pada. [DOC Safety 1.1-1.4]
5. Tokom setup-a robot pomera noge: držati razmak. [DOC Safety 1.2]
6. LiDAR se nikad ne gasi i ne pokriva; kamere se ne gase, ne pokrivaju, ne oštećuju bez dozvole mentora. [DOC A2]
7. Zglobovi ne smeju dugo da drže poziciju van neutralne (pregrevanje). [DOC A2]
8. Gestovi: samo 20 animacija označenih kao bezbedne u Supervisoru; ništa što prelazi preko grudi/glave
   (kamere, LiDAR). Robot ne sprečava sudar sa samim sobom. [DOC A2]
9. Velocity komande (Level 3) nemaju reakciju na prepreke: zabranjeno bez mentora. [DOC A2]
10. E-stop: mentor objašnjava gde je pre bilo kakvog fizičkog izlaza; oporavak posle E-stop-a radi samo mentor.

**Naša radna pravila na LAN-u:** samo read-only komande dok mentor ne kaže drugačije; jedna osoba kuca u
SSH terminalu; niko ne restartuje Supervisor, ne radi `aima em stop-app/start-app/reset-app`, `sudo`,
`apt`, mrežne izmene, navigaciju, gestove, govor ni ekran bez izričite dozvole mentora.

---

## 1. Hardver i računari

| Računar | Šta je | Uloga | Mi |
|---|---|---|---|
| PC1 | x86 | motor kontroler, mrežna kartica/Wi-Fi hub, senzori, ekran (face app) | NE diramo |
| PC2 | NVIDIA Jetson Orin AGX 64 GB, JetPack 6.0, Ubuntu 22.04 | razvoj, kamere i LiDAR podaci, Supervisor, AimDK nav | ovde radimo |

[DOC] Interne adrese: PC1 `192.168.100.100`, PC2 `192.168.100.110`. PC se prepoznaje po `hostname -I`
(`whoami` nije koristan).

Senzori [DOC]: 3 kamere na grudima (2 fisheye levo/desno + centralna RGB, najbolja slika), kamera ispod
vrata (~45° nadole), kamera u karlici (~45° nadole); RealSense driver (`hal_d415`); Livox Mid-360 LiDAR u vratu.
Ekran 95x54 mm na glavi, mikrofonski niz u grudima, RGB svetla (plavo normalno, crveno slaba baterija, zeleno punjenje).

---

## 2. Kako se povezujemo (LAN)

**[UŽIVO] Radi ovako:**

- Laptop (Windows, Realtek GbE): statički IPv4 `192.168.2.119`, maska `255.255.255.0`, bez gateway-a.
  Windows prikazuje "Unidentified network / No internet": to je očekivano.
- `ssh agi@192.168.2.50` iz običnog `cmd`/PowerShell (Windows OpenSSH radi).
- Stiže se DIREKTNO na **PC2**: prompt `agi@ubuntu-orin`, kernel `5.15.136-rt-tegra aarch64`,
  sistem `orin-TZA2-v1.3.12`, aplikacija `A2_ULTRA-v2.0.22-0-g8286b819d-2602251650`,
  ROS `release-humble-20241205`. Aarch64 + tegra = Orin = PC2. Jump preko PC1 NIJE potreban.
- Host key: ED25519 `SHA256:5xSjsXk2r1ORLQpku2I1Jj80e614b9hycWLl3407S5M`. Ako se ikad promeni: prekinuti i pitati mentora.
- Auth: lozinka (od mentora, ne zapisuje se u repo).
- Kod na robotu: `/agibot/humanoid-platform` (repo Supervisora). Neko se već logovao sa `192.168.2.117`,
  dakle i drugi (mentor/tim) koriste isti PC2: ne gaziti tuđe procese.
- **Supervisor UI radi na `http://192.168.2.50:8070/`** [UŽIVO, tim ga koristi].

Prva provera posle login-a (samo čitanje):

```
hostname -I      # treba da sadrži 192.168.100.110 (PC2); ako vidiš 192.168.100.100, odmah exit
whoami
pwd
ls
```

Wi-Fi alternativa [DOC]: na A2 je PC1 Wi-Fi hub, pa Wi-Fi put ide `ssh -J agi@<wifi_PC1> agi@192.168.100.110`.
Mi to NE koristimo bez mentora, jer prolazi kroz PC1.

VS Code Remote-SSH (opciono): host `agi@192.168.2.50` direktno preko LAN-a. Ne menjati fajlove van našeg foldera.

---

## 3. Softver na PC2

### Supervisor (Comtrade)

- Repo na robotu: `/agibot/humanoid-platform`. Pokreće se `./run_robot_supervisor_v2.sh`.
- Skripta: pravi tmux sesiju `robot_supervisor`, aktivira `.venv` (Python 3.12), pokreće
  `python robot_supervisor_v2/run_api.py --host 0.0.0.0 --port 8070` (`ROBOT_SUPERVISOR_PORT`, default 8070),
  čita `.env`. Ako sesija već postoji, NE pokreće ništa novo.
- Boot log: `/agibot/data/home/agi/Desktop/CT/humanoid-platform/robot_supervisor_boot.log`.
  Živi izlaz: tmux sesija `robot_supervisor` (gledati samo uz mentora; `tmux attach` pa `Ctrl+b d` za izlaz,
  NIKAD `Ctrl+C` unutra, to gasi Supervisor).
- Supervisor nema svoj auth i sluša celu mrežu: nikad ne izlagati van LAN-a.
- Config: `robot_supervisor_v2/config.yaml` (lokalni, nije u Git-u). Blok `robot:` bira platformu/model;
  za Titana treba `platform: agibot`, `model: agibot_a2_ultra`. [PITATI koji je `robot.id`/`name` sad]
- Runtime env DEV/UAT/PROD za Azure/Truebar u `.envs/`, izbor u `robot_supervisor_v2/state/environment.json`.

### Moduli Supervisora (servisi)

| Servis | Šta radi | Gde u kodu |
|---|---|---|
| voice-agent + livekit | razgovor: STT (Soniox/Truebar) -> Azure OpenAI (+RAG) -> TTS (Soniox/ElevenLabs) | `livekit-client/agent_main.py`, `app/services/voice_agent.py` |
| audio-bridge | mic/zvučnik na PC2; AIMA EM gasi `agent` i `hal_audio` pre starta | `robot_services/audio/`, `docs/audio_bridge.md` |
| vision-controller | YOLO detekcija ljudi -> `POST /api/vision/person_detected` / `person_left` | `robot_services/vision/detection/main.py`, `app/services/vision_controller.py` |
| camera-bridge / video-recording | kamera u LiveKit / snimanje | `robot_services/vision/camera_bridge.py`, `app/services/video_recording.py` |
| gesture-bridge | animacije iz kataloga (20 bezbednih) | `robot_services/gestures/`, `docs/gesture_bridge.md` |
| nav_missions | mape, waypoint-i, ARM, navigacija | `app/api/nav_missions.py` |
| screen_manip | tekst na licu (render mp4 + PlayerEmoticon) | `robot_services/screen_manip/`, `docs/agibot/head_screen.md` |

**Vision controller i osobe [TIM + kod]:** vision controller se pali/reaguje kada detektuje osobu. Kada
"zaključa" najbližu osobu (distance gate), šalje `person_detected` Supervisoru, i to pokreće razgovor
(voice agent, a LLM može sam da gestikulira). Posledice za TitanSudiju:
- igrači za stolom će stalno biti "detektovane osobe" i mogu neželjeno pokretati razgovor/gestove tokom meča;
- detekcija loptice ne sme zavisiti od ovog okidača; vision za lopticu treba da bude poseban proces/tok;
- [PITATI] kako privremeno isključiti auto-engagement tokom meča bez gašenja LiDAR-a/kamera.

### Kamera

- Supervisor servisi čitaju `/dev/video10` (deljena kamera preko `camera_share/camera_share.sh`),
  primer 960x540 @ 15-30 fps. [DOC primer, PITATI za Titana]
- Ranije meren ~22 fps dekodiranja (`robot_services/vision/h264_decoder.py` komentar), nije limit ove kamere.
- Naš plan: `docs/table_tennis_plan/01_COMP_VISION.md`, lokalni alati `camera_testing/`, `camera_demo.py`.

### Ekran (lice)

- Nema "draw text" API-ja; tekst = renderovan 800x480 mp4 klip koji se pušta po ID-ju (`PlayerEmoticon`).
- Lanac ide preko **PC1** (`rc_module` 192.168.100.100:59001, face app na x86) i `ResourceService` na Orin (127.0.0.1:51049).
- Postojeći `show_message()` SSH-uje na PC1 da prepiše klip. To je PC1 radnja: koristimo samo preko
  postojećeg koda i samo uz dozvolu mentora. Klip se vrti u petlji dok se ne pusti default lice.
- Default lice: emoticon 31 (wake word prompt), 11 idle.

### Govor

- Kroz voice agent / Supervisor "manual speech" deo (zadati tekst bez LLM-a). [PITATI tačan endpoint za
  fiksnu rečenicu tipa "Poen Ana, tri prema dva"]

### AimDK (Agibot)

- HTTP-RPC JSON: `http://<ip>:<port>/rpc/aimdk.protocol.<Service>/<Method>`, obavezan `{"header":{}}`.
- Na A2 gateway `51056` na PC2 (nav/mapiranje); motori na PC1. "resource not found" = pogrešan PC.
- AimDK često vrati uspeh prijema, ne uspeh akcije.
- `aima em doctor`: pregled svih EM aplikacija (read-only, ali pokretati uz mentora).
- ROS domain 232.
- Mapa: `/agibot/data/var/MapManagerModule/map.db`.

---

## 4. TitanSudija aplikacija (`table_tennis/`)

Stanje: mock backend radi lokalno bez robota; robot/ekran/govor/gest su fake i označeni SIMULATED.
Ništa nije testirano na Titanu.

- Standalone: `python -m table_tennis.run_demo --mode mock` -> `127.0.0.1:8099` (`/docs` za API).
- U Supervisoru: `robot_supervisor_v2/app/api/main.py` zove `include_table_tennis(app)`, aktivno samo sa
  `TABLE_TENNIS_ENABLED=1`, `TT_AUTH_MODE=token`, `TT_OPERATOR_TOKEN=...`.
- Ugovor: `05_SHARED_CONTRACT.md`; stanje: `07_IMPLEMENTATION_STATUS.md`.
- Pravila: CV samo predlaže poen (`point.propose`), poen dodeljuje operator; jedan proces je jedini writer.

### Kako ćemo je pokrenuti na kraju (plan, svaki korak uz mentora)

Faza A, laptop (bez robota, bezbedno sada):
1. `py -3 -m venv .venv-tt`, `.\.venv-tt\Scripts\Activate.ps1`, `pip install -r table_tennis\requirements-dev.txt`
2. `python -m table_tennis.run_demo --mode mock`, pa simulator `python -m table_tennis.sim.run --scenario manual-game --api http://127.0.0.1:8099`
3. UI/telefon test na laptopu.

Faza B, PC2 read-only (mentor prisutan):
1. SSH, `hostname -I` (mora 192.168.100.110).
2. Pitati: koji commit izvršava `/agibot/humanoid-platform` (`git log --oneline -1`, read-only) i ko je vlasnik checkout-a.

Faza C, kod na PC2 (mentor odobrava):
1. Naš kod je paket `table_tennis/` (plus frontend feature u `robot_supervisor_v2/frontend/src/features/table-tennis/`)
   u timskom repou `a2-hackathon` (remote `teodorajovanovac/a2-hackathon`). Sve četiri grane
   (`comp-vision`, `backend`, `navigation`, `persone`) spajaju se u `main`, i na robot ide samo `main`.
   To je isti repo kao Supervisor, pa na robotu kod ide u `/agibot/humanoid-platform/table_tennis`.
   Nema posebnog foldera.
2. `/agibot/humanoid-platform` je checkout koji Supervisor stvarno izvršava. Mentor odlučuje kako stiže naš
   kod: `git pull` timskog `main`-a u taj checkout ili kopiranje samo `table_tennis/`. Pre toga zabeležiti
   trenutni commit (`git log --oneline -1`) da bi mogao da se vrati.
3. Ne instalirati root `requirements.txt` (ROS/Jetson freeze); samo `table_tennis/requirements-dev.txt`, uz mentora.
4. Dva načina pokretanja:
   - Samostalno: `python -m table_tennis.run_demo --mode mock` na `127.0.0.1:8099` (default iz
     `table_tennis/config.example.yaml`). Sa laptopa preko tunela: `ssh -L 8099:127.0.0.1:8099 agi@192.168.2.50`,
     pa `http://127.0.0.1:8099/docs`.
   - U Supervisoru: `TABLE_TENNIS_ENABLED=1`, `TT_AUTH_MODE=token`, `TT_OPERATOR_TOKEN=...`; rute idu pod
     `http://192.168.2.50:8070/api/table-tennis`. Traži restart Supervisora, radi ga mentor.

Faza D, pravi izlazi (jedan po jedan, mentor ima E-stop):
1. Ekran: tekst "TitanSudija 0 : 0" preko postojećeg `screen_manip`.
2. Govor: jedna fiksna rečenica.
3. Gest: jedan iz liste bezbednih (npr. mahanje), idle animacija isključena (ARM) od strane mentora.
4. Tek tada `TABLE_TENNIS_ENABLED=1` u Supervisoru. Restart Supervisora radi mentor.

Ime **TitanSudija**: u persona/prompt konfiguraciji (`prompt_config.yaml` `robot_name`, `robot.name` u
config-u) na našoj kopiji; na robotu menjati samo uz dogovor sa mentorom jer je config zajednički.

---

## 5. Otvorena pitanja za mentore (odgovori iz koda: `11_ODGOVORI_IZ_KODA.md`)

1. ~~Da li je `192.168.2.50` uvek PC2?~~ Rešeno: da, preko Etherneta je uvek ista.
2. Ko trenutno "poseduje" Supervisor na 8070 i sme li naš proces paralelno?
3. Kako naš `table_tennis/` stiže u `/agibot/humanoid-platform` (pull timskog `main`-a ili kopija) i da li radi samostalno na 8099 ili u Supervisoru?
4. Koja kamera gleda sto (centralna RGB?), device/topic, rezolucija, stvarni FPS; sme li spoljna kamera?
5. Kako isključiti auto-razgovor na detekciju osobe tokom meča?
6. Tačan poziv za fiksni govor, trajni rezultat na ekranu i bezbedne gestove (point left/right?).
7. Gde su logovi AimDK/EM i vision/audio servisa?
8. Da li sme `aima em doctor` i ko radi ARM za idle animaciju?
9. Ko pali/gasi robota i menja bateriju tokom hakatona (1.5-3 h autonomije)?
