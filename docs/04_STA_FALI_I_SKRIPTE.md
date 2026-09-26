# 04 Šta fali da bi radilo na robotu i šta sa skriptama

## 1. Šta fali, po prioritetu za demo

| # | Šta | Zašto | Vlasnik | Procena |
|---|---|---|---|---|
| 1 | **Tokeni**, regenerisan `openapi.json`, ruta i dugmad za put i dolazak robota | 01, greške 1 i 4 | osoba 2 + 4 | 2–3 h |
| 2 | **Capture bez praznog hoda + detekcija ugašene kamere** | 01, greška 2 | osoba 1 | 2–3 h |
| 3 | **Vision proces otporan na pad backenda** + `camera.ready false` pri gašenju | 01, greška 3 | osoba 1 | 1–2 h |
| 4 | **Kalibracija sa slike** | operater i dalje kuca `calibration_id`, a niko ne klikće uglove na fisheye kadru | osoba 1 + 4 | 0,5 dan |
| 5 | **BallNet težine učene na fisheye kadrovima** | mreža je učena na snimku telefona | osoba 1 | snimanje 30 min + označavanje |
| 6 | **Pravi ekran** (`show_text` bez restore) | `A2ScoreDisplay` je i dalje dry-run | osoba 3 | 2–4 h |
| 7 | **Gest uz govor** (`gesture` u istoj komandi) | `A2GestureOutput` je i dalje dry-run | osoba 3/4 | 1–2 h |
| 8 | Trajna LiveKit konekcija za govor | kašnjenje (01, greška 5) | osoba 4 + mentori | 2–4 h |
| 9 | Auto `rally.armed` posle poena | jedan dodir po poenu | osoba 2 | 2 h |
| 10 | Logika sudije za servis, aut i dupli odskok | proširenje; `missed_return` je urađen (02) | osoba 1 | 1 dan + benchmark |
| 11 | `undistortPoints` za fisheye | samo ako merenje na robotu pokaže grešku | osoba 1 | 2–4 h uz robota |
| 12 | Pravi transport navigacije ka `a2_nav` | logika je urađena, transport je dry-run; za demo je dovoljan ručni dolazak | osoba 3 | posle demoa |
| 13 | Živi zvuk sa mikrofona | nema ulaza, bridge drži mikrofon | osoba 1 | posle demoa |

### Živi vision proces: urađeno, šta još dodati

`python -m table_tennis.vision.live --match-id <id> --calibration table.json --device CHEST_LEFT_FISHEYE --base-url http://127.0.0.1:8070 [--config vision.yaml]`
sada postoji. Na robotu `--base-url` mora biti `:8070` (podrazumevano je `:8099`), a `--config`
mora da navede `ballnet_path` ili HSV boju, inače tracker ne startuje. Korisno bi bilo dodati:

- `--dry-run`: sve radi, ali ništa se ne šalje backendu, samo se loguje „poslao bih predlog".
  To je prvi režim na robotu.
- `--record`: čuva sirove kadrove u TTCLIP format (koji `FileCapture` već čita, a `overlay.py` već ume da piše) za učenje i
  benchmark. Snimci ostaju u `table_tennis/var/` (van gita).
- `--match latest`, da ne mora ručno da se kopira ID meča preko ssh-a.
- `camera.ready.set false` kad nema kadra N ms (01, greška 2).
- Na svakih 5 s loguje: fps kamere, fps obrade, p50/p95 po koraku (`pipeline.last_ms`), broj
  kandidata i duplikata.

### Detalj za #4: kalibracija sa slike

Vision proces jednom sačuva JPEG kadra (npr. `GET /vision/frame.jpg` ili fajl koji se `scp`-uje).
U aplikaciji operater dodirne četiri ugla i mrežu redom kao u `calibration.py` (`end_a_0`,
`end_a_1`, `end_b_0`, `end_b_1`), a `CalibrationGate` proveri i upiše `table.json`. Tek onda
operater šalje `calibration.set` sa istim ID-jem. Brži privremeni put: skripta na laptopu otvori
sačuvani JPEG u OpenCV prozoru, klikovi idu u JSON, pa se JSON `scp`-uje na robota.

## 2. „One skripte": šta sa njima

Pod skriptama sam pregledao dve grupe. Ako ste mislili na neke treće, javite koje.

### a) Skripta za slanje na hakaton granu

`deploy/sync-to-robot.ps1` je samo na grani `claude/funny-babbage-xfmshs` i nije u `main`-u. Ako
želite da je koristite, spojite je u `main` (PR), pa je pokrenite sa `-DryRun` pre prvog pravog
slanja. Napomena: u deploy folderu je upravo urađen `git pull nas main` direktno na
`a2-hackathon-team4`. To je u redu dok se ne pushuje; skripta će takav nepushovan merge
prepoznati, odbaciti ga i napraviti ponovo.

### b) Fajlovi u korenu repoa

Sve ovo će završiti u hakaton repou koji vide organizatori i drugi timovi.

| Fajl / folder | Šta je | Predlog |
|---|---|---|
| `camera_demo.py` | čita A2 kamere, uključujući `CHEST_LEFT_FISHEYE` | **zadržati**, to je prvi test kamere na robotu (05, faza 1) |
| `test.py` | MJPEG server za gledanje kamere iz browsera | zadržati, ali premestiti u `tools/` i dati mu ime (npr. `tools/mjpeg_preview.py`) |
| `locomotion_test.py` | Unitree G1 SDK (`unitree_sdk2py`) | **ukloniti**: nije za A2 i pokreće hodanje |
| `pada`, `rade`, `takođe` | prazni fajlovi (0 B) | ukloniti |
| `yolo26n.pt` (5,3 MB) | težine modela | ukloniti iz gita (AGENTS: modeli ne idu u git) |
| `tt.tgz`, `sample.mp3`, `test.wav`, `demo_metrics_*.csv` | privremeni fajlovi | ukloniti ili premestiti van repoa |
| `hall_of_fame_kb (1).jsonl` | duplikat `hall_of_fame_kb.jsonl` | ukloniti duplikat |
| `camera_testing/*.mp4, *.raw, *.jpg` | test snimci | ukloniti (snimci ne idu u git), zadržati `.txt` beleške |
| `Dokumenti backup staro/` (24 MB) | vendor `.docx` + stara kopija plana | pitati mentore da li vendor dokumentacija sme u deljeni repo; plan je već u `docs/table_tennis_plan/` |
| `vendor_wheels/` (1,5 MB) | wheel paketi | proveriti da li su potrebni za Orin; ako jesu, ostaviti uz README |

Brisanje radite u vašem repou, kao običan PR. Ja ništa od ovoga nisam dirao.

## 3. Rizici koje treba rešiti sa mentorima (nisu u kodu)

- Da li fisheye topic sme da se čita paralelno sa Supervisor vision-om i da li ga objavljuje PC2.
- Da li `robot_services/screen_manip` sme da dobije `show_text` (zajednički kod).
- Da li Wi-Fi ruter sme na robotov LAN (telefon operatera).
- ARM i gestovi: uključuje ih samo mentor.
- Tokeni i `.env` na robotu: ko ih upisuje i kako se vraća stanje posle demoa.
