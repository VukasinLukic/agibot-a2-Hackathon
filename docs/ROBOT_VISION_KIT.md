# Robot vision kit: loptica na A2 chest fisheye kameri

Kratak postupak da vision za lopticu proradi na robotu i predlaže poene u aplikaciju
sudije. Komande se kopiraju redom. Posle svake faze proveri da izlaz liči na „OK”, pa tek
onda pređi na sledeću. Pozadina: `table_tennis/vision/README.md`, `docs/05_PLAN_TESTIRANJA_NA_ROBOTU.md`,
`docs/table_tennis_plan/12_POKRETANJE_NA_ROBOTU.md`.

Alati:

- `scripts/robot_vision.sh`: na robotu. Samo omotač oko `table_tennis.vision.live` i `calibrate`.
  Pre pokretanja ispiše tačnu komandu. Ne pomera robota i ne dira Supervisor.
- `scripts/laptop_vision.ps1`: na laptopu. Kopira fajlove preko scp/ssh i otvara prozor za klik kalibracije.

## Bezbednost (Agibot Safety Guide, A2 Ultra)

- Robot ima oko 70 kg. Pri paljenju i gašenju visi na gantry-ju. Dok traje startna sekvenca,
  LiDAR i reakcija motora nisu aktivni, pa se robotu ne prilazi.
- Tablet, setup i **E-stop drži mentor**. Tokom setup-a robot pomera noge, pa držite razmak.
- **Idle animacija** kreće posle oko 10 s mirovanja. Okreće struk i glavu i **ne staje kad se neko
  približi**. Pre svakog rada pored robota mentor uključuje **ARM** (Navigation → ARM). Za vision je
  to dvostruko važno: pokret struka pomera i kameru na grudima i kalibracija više ne važi.
- Ne stavljajte ruke u zglobove i ne dirajte robota bez mentora. **Bez mentora nema nikakvog pokreta.**
- Gašenje: hanging mode preko tableta, podizanje gantry-jem, pa dugme. Ako robot ne visi, pašće.
- Ovaj kit ne šalje nijednu komandu za kretanje. Restart Supervisora i izmene `.env` radi mentor.

## Faza 0: na laptopu, pre polaska (15 min)

Sve komande su iz korena repoa, `C:\Users\Tea\a2-hackathon-1`, u PowerShell-u.

1. **Kod.** `calibrate.py` i nove opcije za `live` postoje samo na grani `persone`, a glavni agent još
   menja `table_tennis/vision/`. Kad on završi, testovi moraju da prođu:
   ```powershell
   .\.venv-vision\Scripts\python.exe -m unittest discover -s tests/table_tennis/vision
   git log --oneline -1      # zapiši SHA koji nosiš na robota
   ```
2. **Težine.** Nove su `table_tennis\var\vision\train\ballnet.onnx` i `.npz`, kad se doučavanje završi.
   Prethodne su zamrznute u `table_tennis\var\vision\kit\ballnet_prev.onnx` i `.npz`
   (md5 `86584fd5…` i `3d86e0e6…`). Proveri da nova nije ista kao stara:
   ```powershell
   Get-FileHash -Algorithm MD5 table_tennis\var\vision\train\ballnet.onnx, table_tennis\var\vision\kit\ballnet_prev.onnx
   ```
   Ako su hash-evi isti, doučavanje još nije upisalo nove težine. Težine ne idu u git.
3. **Pydantic za robota.** Vision na robotu radi na sistemskom Python-u 3.10, jer je ROS Humble
   `rclpy` napravljen za 3.10. Supervisor `.venv` je 3.12 i tu ne može da učita `rclpy`. Ako na 3.10
   nema pydantic-a 2, nosimo wheel-ove (već skinuti u `table_tennis\var\vision\kit\wheels`):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\laptop_vision.ps1 Wheels
   ```
4. **Tokeni.** Vision koristi `TT_VISION_TOKEN`, a telefon operatora `TT_OPERATOR_TOKEN`. Oba
   moraju biti isti kao u robotovom `/agibot/humanoid-platform/.env`. Skripta sama čita `TT_VISION_TOKEN`
   iz tog `.env` i ne ispisuje ga. Ako `.env` nema tokene, Supervisor prima podrazumevane
   (`vision_secret`, `operator_secret`). Tokeni ne idu u git, chat ni screenshot.
5. **Mreža.** Laptop je na `192.168.2.119/24`, a robot na `ssh agi@192.168.2.50`. Svaki `scp`/`ssh` može tražiti lozinku.
6. Odštampaj ili otvori ovaj dokument na drugom ekranu.

## Faza 1: read-only provera na robotu, uz mentora (10–15 min)

1. Kopiranje koda i kita. Tek kad mentor odobri način deploy-a (`12_POKRETANJE_NA_ROBOTU.md` §4):
   ```powershell
   # laptop; PushCode pravi ~/tt_backup_<vreme>.tgz na robotu pre raspakivanja, table_tennis/var ne dira
   powershell -ExecutionPolicy Bypass -File scripts\laptop_vision.ps1 PushCode
   powershell -ExecutionPolicy Bypass -File scripts\laptop_vision.ps1 PushKit
   ```
   `PushKit` stavlja nove težine kao `table_tennis/var/vision/ballnet.onnx|.npz`, stare kao
   `ballnet_prev.onnx|.npz`, `robot_vision.sh` u `scripts/` i wheel-ove u `table_tennis/var/vision/wheels/`.
   Na kraju ispiše md5 obe težine na robotu. Supervisor učitava novi `table_tennis` kod tek posle restarta.
2. Na robotu:
   ```bash
   ssh agi@192.168.2.50            # ako prijava pita za ROS opciju, izaberi 1
   cd /agibot/humanoid-platform
   git status --short && git log --oneline -1   # zapiši stanje, za povratak
   scripts/robot_vision.sh check
   ```
3. OK izlaz iz `check`:

   | Sekcija | OK | Ako nije |
   |---|---|---|
   | python | `Python 3.10.x`, `ok` za `rclpy`, `sensor_msgs.msg`, `numpy`, `cv2`, `pydantic 2.x` | fali pydantic: `scripts/robot_vision.sh pydeps table_tennis/var/vision/wheels`, pa ponovo `check`. Fali `rclpy`: nije 3.10 ili ROS nije učitan, pa probaj `PY=/usr/bin/python3` i ROS opciju 1 |
   | kod | `ok live --device … --match-id`, `ok calibrate.py`, `ok ros2_capture.read_if_new` | na robotu je stari kod, ponovi `PushCode` |
   | ROS topic | `fish_eye` topici u listi, `average rate: ~30`, `height`, `width`, `encoding` | ništa ne stiže: pitaj mentora da li PC2 objavljuje fisheye i da li je `ROS_DOMAIN_ID=232` |
   | kadrovi kroz capture | `kadar WxH`, `fps kamere kroz capture: ~30`, `camera_missing=False` | `Timed out waiting for first ROS 2 camera frame`: isto kao gore |
   | BallNet | `ok OnnxBallNet: … ms` za `ballnet.onnx` i `ballnet_prev.onnx` | `.onnx` se ne učitava (stari OpenCV u `jetson_deps`): koristi `.npz` (`BALLNET=…/ballnet.npz`), sporije je |
   | backend | `GET /api/health -> 200`, `GET /api/table-tennis/matches (vision token) -> 200` | 404 na `/matches`: feature nije uključen (vidi „Backend” ispod). 401: token nije isti kao u `.env` Supervisora |

   **Zapiši:** rezoluciju (`WxH`), fps topica, encoding i broj jezgara. Rezolucija i fps fisheye kamere
   se ne znaju dok se ne izmere ovde.
4. **Backend.** Feature radi samo ako Supervisor ima `TABLE_TENNIS_ENABLED=1` i `TT_AUTH_MODE=token`
   u `.env`, i ako je posle toga restartovan (`12_POKRETANJE_NA_ROBOTU.md` §5–6). Upis i restart
   radi **mentor**. Ako to danas ne ide, koristi plan B na kraju dokumenta.

## Faza 2: kadar i kalibracija (10 min)

Robot stoji sa strane stola kod mreže, između igrača, i gleda ceo sto. Mentor je uključio **ARM**.
Od ovog trenutka robot i kamera se ne pomeraju. Posle svakog pomeranja kalibracija se radi ponovo.

1. Kadar sa iste kamere i u istoj rezoluciji koju čita `live`:
   ```bash
   scripts/robot_vision.sh grab
   # OK: "kadar 1920x1080, camera_id …" i "sacuvan …/table_tennis/var/vision/kadar.png"
   ```
2. Tačke se unose ovim redom (i klik i kucanje):
   1. `end_a_0`: jedan ugao kraja A
   2. `end_a_1`: drugi ugao kraja A
   3. `end_b_0`: ugao kraja B na **istoj dugoj ivici kao `end_a_1`**
   4. `end_b_1`: poslednji ugao
   5. i 6. mesta gde mreža dodiruje dve duge ivice

   Uglovi tako idu redom oko stola. Kraj A je kraj za koji aplikacija pita „Na kraju stola end_a stoji”.
   To nije isto što i levo/desno od robota.

   **Put A, klik na laptopu (preporučeno):**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\laptop_vision.ps1 PullFrame
   powershell -ExecutionPolicy Bypass -File scripts\laptop_vision.ps1 Calibrate
   ```
   Otvara se prozor, smanjen na ekran. Klikni 6 tačaka, pa Enter (`u` vraća poslednju, Esc odustaje).
   Skripta upiše `table.json` i `table.png`, ispiše `calibration_id` i pošalje oba fajla na robota.

   **Put B, ukucane tačke (bez prozora):** piksele pročitaj sa `kadar.png` (Paint dole levo pokazuje
   x,y) ili iz linije `tacke: …` koju ispiše `calibrate --image` na laptopu. Onda na robotu:
   ```bash
   scripts/robot_vision.sh calibrate-points "x,y x,y x,y x,y x,y x,y"
   ```
   Ova komanda uzima novi kadar sa kamere. Ako se robot pomerio od `grab`, tačke više ne važe.
   Pregled prebaci na laptop: `scp agi@192.168.2.50:/agibot/humanoid-platform/table_tennis/var/vision/table.png .`
3. OK izlaz: `upisano …table.json i …table.png`, pa `calibration_id: <uuid>`. Kad je kalibracija odbijena,
   ništa se ne upisuje, a razlog je jedan od ovih:
   `neka tacka je na manje od 8 px od ivice kadra`, `uglovi se seku` (pogrešan redosled),
   `sto je premali u kadru`, `mreza nije na sredini izmedju krajeva A i B`.
4. Otvori `table.png`. Slova A i B moraju biti na pravim krajevima, a linija mreže na mreži.
   Homografija ne ispravlja fisheye, pa krajevi stola na ivici kadra mogu malo da odstupaju.
5. **Zapiši** `calibration_id` (na robotu ga vraća `scripts/robot_vision.sh cal-id`) i ime igrača na kraju A.

## Faza 3: snimak prave igre za doučavanje (10 min)

`live` snima samo uz meč. Zato mora postojati bar jedan meč u aplikaciji (može probni) i
`table.json`. Snima se u `--dry-run` režimu, pa ništa ne ide u meč. Snimak je sirov BGR:
**1920×1080 je oko 6,2 MB po kadru, oko 190 MB/s na 30 fps, pa 60 s zauzme oko 11 GB.**
Skripta pre početka proveri slobodno mesto i odbije snimanje ako ga nema dovoljno.

```bash
scripts/robot_vision.sh record 60     # dva-tri puta po 60 s, igrači igraju prave razmene
# OK: "recording …/clips/fisheye-<vreme>.ttclip", fps linije, na kraju
#     "…: 1920x1080, header N frames, file N frames, 30.0 fps"
```

- Snimak se sam prekida posle zadatih sekundi, signalom kao Ctrl+C, pa se zaglavlje ispravno zatvori.
  Ranije se prekida sa Ctrl+C. Ako je proces ubijen i zaglavlje kaže 0 kadrova:
  `scripts/robot_vision.sh fix-clip <fajl.ttclip>`.
- Ako je `file N frames` znatno manje od sekunde×fps, disk ne stiže pa se kadrovi bacaju. Drugo mesto:
  `RECORD_DIR=/agibot/data/... scripts/robot_vision.sh record 60`, uz mentorovo odobrenje.
- Posle svakog snimka ga prebaci na laptop, proveri veličinu, pa obriši na robotu:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\laptop_vision.ps1 PullClips   # u table_tennis\var\vision\clips
  ```
  ```bash
  rm /agibot/humanoid-platform/table_tennis/var/vision/clips/fisheye-*.ttclip
  ```
- Ne snimaj tokom pravog meča: kopija i upis svakog kadra dodaju kašnjenje.

## Faza 4: dry-run protiv backenda (10 min)

1. U aplikaciji napravi meč kao u fazi 5, tačke 1–3: „Kamera, uz potvrdu”, `calibration_id`, kraj A.
2. Na robotu:
   ```bash
   tmux new -s tt_vision
   cd /agibot/humanoid-platform
   scripts/robot_vision.sh dry-run
   # izlaz iz tmux-a bez gašenja: Ctrl+b pa d
   ```
3. OK log:
   ```
   INFO latest match is <match_id>
   INFO dry-run would send camera.ready.set
   INFO fps camera 29.8 process 29.8 p50 14.0ms p95 22.0ms candidates 12 duplicates 0   (na 5 s)
   ```
   Operater pritisne „Servis — kamera gleda”, igrači odigraju razmenu, a posle kraja razmene u logu
   treba da piše `dry-run would send point.propose`. U dry-run-u backend ne dobija `camera.ready`,
   pa aplikacija može da prikaže kameru kao nespremnu. To je očekivano.
4. Šta znače brojevi:
   - `fps camera` i `process`: kadrovi koje je petlja uzela i obradila. Capture uvek daje najnoviji
     kadar, pa su ova dva broja skoro ista. Pravu brzinu kamere daje `ros2 topic hz` iz `check`.
     Kad je `process` ispod fps-a topica, kadrovi se preskaču. Kašnjenje ne raste, ali tracker vidi manje.
   - `p50`/`p95`: vreme obrade jednog kadra (kandidati + mreža + tracker), bez ROS konverzije.
     Na 30 fps budžet je 33 ms, pa `p95` treba da bude ispod oko 30 ms.
   - `candidates`: broj kandidata u poslednjem kadru. Stalno preko ~60 znači šum (ljudi i reketi u ROI).
5. Kad je `process` ispod ~20 fps ili `p95` preko 33 ms:
   - Proveri da ide `.onnx`, ne `.npz` (na laptopu je `.npz` 5–7× sporiji), bez `--record`, bez `--show`.
   - `live.py` već smanjuje sebi prioritet (`nice(5)`) i ograničava OpenCV i OMP na 2 niti
     (`_limit_vision_threads`, `_yield_a_core`). Jezgra se biraju pri pokretanju:
     `TT_VISION_CPUS=6-7 scripts/robot_vision.sh dry-run`. Slobodna jezgra pogledaj u `top` (taster `1`) ili `tegrastats`.
   - Ako CPU troši Supervisor vision-controller (YOLO za osobe), pitaj mentora sme li da se pauzira tokom meča.
     `PATCH /api/vision {"enabled": false}` gasi samo započinjanje razgovora. Da li gasi i detektor, nije provereno.
   - `nvpmodel` i `jetson_clocks` ne menjati bez mentora.
6. Zaustavljanje: `tmux attach -t tt_vision`, pa Ctrl+C.

## Faza 5: pravi meč (uz mentora)

1. Aplikacija (`http://192.168.2.50:8070`, tab Table tennis, operator token) → novi meč: igrači, persona.
2. „Podešavanja stola”: **„Na kraju stola end_a stoji”** = igrač na kraju A sa `table.png`;
   **„ID kalibracije”** = `calibration_id`; **„Poene dodeljuje”** = **„Kamera, uz potvrdu”**.
   Posle kreiranja se režim ne menja. → „Napravi meč”.
3. „Titan je već kod stola” → „Počni meč”.
4. Vision se pokreće **tek kad meč postoji** (`--match-id latest` uzima poslednji meč):
   ```bash
   tmux new -s tt_vision
   cd /agibot/humanoid-platform
   scripts/robot_vision.sh run
   # Ctrl+b pa d
   ```
   OK: `latest match is <isti id kao u aplikaciji>`, fps linije, **bez** `rejected (4xx)`.
5. Za svaki poen: pre servisa operater pritisne **„Servis — kamera gleda”** (`rally.arm`). Vision predlaže samo
   dok je razmena otvorena. Kad se pojavi „Kamera predlaže … Potvrdi poen”, proveri i potvrdi. Ako nije
   tačno, dodirni polovinu stola pravog igrača. Ako predlog ne stigne za 1–2 s posle kraja razmene,
   dodeli poen ručno.
6. Novi meč: proces sam pređe na noviji meč (provera na 5 s, ne usred razmene). Sigurnije je da ga posle
   kreiranja novog meča ugasiš i pokreneš ponovo.
7. Povratak na stare težine, ako nove greše:
   ```bash
   BALLNET=$PWD/table_tennis/var/vision/ballnet_prev.onnx scripts/robot_vision.sh run
   ```
8. Kraj: `tmux attach -t tt_vision`, pa **Ctrl+C**. To pošalje `camera.ready.set false`. Tek onda
   `tmux kill-session -t tt_vision`. `kill` ili `kill-session` bez Ctrl+C ostavi kameru „spremnom” u meču.

## Faza 6: kad nešto ne radi

| Simptom u logu / aplikaciji | Uzrok | Šta uraditi |
|---|---|---|
| `rejected (403): forbidden_actor … actor 'operator' may not send camera.ready.set` | u `TT_VISION_TOKEN` je token drugog actora (npr. operatorov) | `export TT_VISION_TOKEN=<vision token iz .env>`; tokeni moraju biti različiti |
| `RuntimeError: match list failed (401)` / `invalid_token` | token nije isti kao u Supervisorovom `.env`, ili Supervisor nije restartovan posle izmene `.env` | uporedi sa `.env`; restart radi mentor |
| `match list failed (404)` ili curl `404` na `/api/table-tennis/matches` | feature nije uključen: nema `TABLE_TENNIS_ENABLED=1` ili `TT_AUTH_MODE=token`, ili nije bilo restarta | mentor: `.env` + restart; ili plan B |
| `match list failed (200)` | nema nijednog meča | prvo napravi meč u aplikaciji |
| `no proposal: calibration does not match the snapshot` | `calibration_id` u meču nije iz ovog `table.json` | u pauzi „Postavi kalibraciju” sa ID-jem iz `cal-id`, ili nov meč |
| `WARNING calibration is WxH but the camera frame is WxH` | kalibracija sa druge kamere ili rezolucije | `grab` + nova kalibracija sa `--device` kamere |
| `no proposal: camera or calibration is not ready` | meč nema `calibration_id`, ili backend nije primio `camera.ready` | proveri ID u meču; bez `--dry-run` proces sam šalje ready |
| `no proposal: scoring mode is not assisted` | meč je „Mi, dodirom” | nov meč sa „Kamera, uz potvrdu” |
| `no proposal: match is not in a rally` / `rally is not open` | niko nije pritisnuo „Servis — kamera gleda”, ili predlog već čeka | pritisni Servis pre svakog poena; potvrdi predlog koji čeka |
| u dry-run-u ima `would send point.propose`, a u `run` predloga nema | backend je odbio predlog sa 409 (npr. `camera_not_ready`, `stale_calibration`); 409 se u logu ne ispisuje | proveri „Kamera” u aplikaciji i `calibration_id` |
| proces izađe, u aplikaciji kamera nije spremna (`camera_missing`) | tri uzastopna čitanja bez kadra: topic je stao | `scripts/robot_vision.sh check` (sekcije ROS i capture), pa ponovo `run` |
| `Timed out waiting for first ROS 2 camera frame` | ROS nije učitan, pogrešan `ROS_DOMAIN_ID`, ili topic ne postoji | ROS opcija 1 pri prijavi; `check`; pitaj mentora |
| `… is not a raw chest fisheye topic` | `DEVICE` nije `CHEST_LEFT_FISHEYE`/`CHEST_RIGHT_FISHEYE` (H.264 se odbija) | `DEVICE=CHEST_LEFT_FISHEYE` |
| `BallNet weights not found` ili `GRESKA: … nema fajla …ballnet.onnx` | težine nisu kopirane | `laptop_vision.ps1 PushKit` |
| `ModuleNotFoundError: pydantic` / `rclpy` | pogrešan Python ili fali paket | faza 1, tabela „python” |
| predlozi stižu kasno ili ih nema u brzim razmenama | `process` fps nizak | faza 4, tačka 5 |
| nijedan predlog, a fps je dobar | težine ne vide lopticu na ovoj kameri | `BALLNET=…ballnet_prev.onnx`; poeni ručno; snimci iz faze 3 idu u doučavanje |

Fallback je uvek isti: operater dodeljuje poene dodirom (`point.award`). Poen se nikad ne gubi.

## Plan B: backend na laptopu

Kad Supervisor danas ne može da dobije feature (nema restarta), vision na robotu šalje u mock backend na laptopu:

```powershell
# laptop; Windows firewall mora da pusti ulaz na 8099 (pitaj pre nego što menjaš)
.\.venv-tt\Scripts\python.exe -m table_tennis.run_demo --mode mock --host 0.0.0.0 --port 8099
# UI: cd robot_supervisor_v2\frontend; $env:VITE_API_PROXY_TARGET="http://127.0.0.1:8099"; npm run dev
```
```bash
# robot
TT_API_URL=http://192.168.2.119:8099 TT_VISION_TOKEN=vision_secret scripts/robot_vision.sh dry-run
```

Mock prima podrazumevane tokene. Ovako se meri vision, ali to nije pravi demo na Supervisoru.

## Posle termina

1. Vision ugašen sa Ctrl+C, `tmux kill-session -t tt_vision`.
2. Snimci prebačeni na laptop i obrisani sa robota. `table.json`, `kadar.png` i težine ostaju u
   `table_tennis/var/vision/`, van gita.
3. Opciono: `rm -rf /agibot/humanoid-platform/table_tennis/var/pydeps`. Kit ne menja sistemski Python ni `.env`.
4. Ostatak (ARM/DISARM, `.env`, vraćanje checkout-a iz `~/tt_backup_*.tgz` ili git-om, gašenje) ide po
   `05_PLAN_TESTIRANJA_NA_ROBOTU.md`, faza 7, i radi ga mentor.
