# 05 Plan testiranja kada dođete do robota

Pravilo za svaku fazu: **prvo read-only, pa jedan izlaz po jedan, pa sve zajedno.** Sledeća faza
počinje tek kad prethodna prođe. Sve što pokreće proces na robotu, menja Supervisor ili pravi
pokret radi se **uz mentora**. Detaljne komande su u `docs/table_tennis_plan/12_POKRETANJE_NA_ROBOTU.md`;
ovde je redosled i šta se meri.

## Bezbednost (iz Agibot Safety Guide, A2 Ultra)

- Robot ima oko 70 kg. Pri paljenju i gašenju mora da visi na gantry-ju. Tokom startne sekvence
  LiDAR i reakcija motora **nisu aktivni**, pa se ne prilazi.
- Tablet i setup drži mentor. Tokom setup-a robot pomera noge: držati razmak.
- **Idle animacija** kreće posle ~10 s mirovanja (okreće struk i glavu) i **ne staje kad se neko
  približi**. Pre bilo kakvog rada pored robota mentor uključuje **ARM** (Navigation → ARM).
- Ne stavljati ruke u zglobove i ne dirati robota bez mentora. E-stop je kod mentora.
- Gašenje: hanging mode preko tableta, podizanje gantry-jem, pa dugme. Ako robot ne visi, pašće.
- Tokom meča igrači su na 1,5–3 m. Gestove rukom dozvoliti samo kad niko nije na dohvat ruke.

## Faza 0: dan pre (laptop, bez robota)

- [ ] Ispravke iz 01 (bar greške 1, 2 i 3) spojene u `main`; `pytest tests/table_tennis`
      bez greške; `contracts.generate --check` prolazi.
- [ ] Ceo gem u mock režimu na laptopu, sa telefonom na istoj mreži.
- [ ] Vision proces pušten nad snimkom umesto kamere, protiv mock backenda na laptopu:
      `python -m table_tennis.vision.live --clip snimak.ttclip --match-id <id> --calibration table.json --config vision.yaml`.
- [ ] Tokeni generisani (`python -c "import secrets;print(secrets.token_urlsafe(24))"`, po jedan za
      operator, vision, robot i persona) i preneti privatno.
- [ ] Zapisan SHA commita koji ide na robota.
- [ ] Pitanja za mentora spremna (04, sekcija 3).

## Faza 1: read-only na robotu (10–15 min)

1. `ssh agi@192.168.2.50`; `hostname -I` mora sadržati `192.168.100.110`.
2. `cd /agibot/humanoid-platform && git status --short && git log --oneline -1`: zapisati trenutno
   stanje, da bi moglo da se vrati.
3. `aima em doctor` (samo pregled) i `curl -s http://127.0.0.1:8070/api/health`.
4. Kamera, uz mentora:
   - `ros2 topic list | grep fish_eye`
   - `ros2 topic hz /aima/hal/fish_eye_camera/chest_left/color` → **zapisati fps**
   - `ros2 topic echo --once /aima/hal/fish_eye_camera/chest_left/color --no-arr` →
     **zapisati rezoluciju i encoding**
   - postoji li `camera_info` za taj topic (intrinsics)?
   - `python camera_demo.py --camera CHEST_LEFT_FISHEYE` i jedan snimak ekrana kadra.
5. Robot na mestu sudije pored stola: da li ceo sto, sa oba kraja, staje u levi fisheye?

## Faza 2: snimanje i merenje vision-a (30–45 min)

1. Snimiti 3–5 minuta prave igre sa levog fisheye-a (`ros2 bag record` ako mentor dozvoli, ili
   snimanje kroz `camera_demo.py`; `live` još nema opciju za snimanje). Snimci ostaju na robotu / laptopu, van gita.
2. Pustiti pipeline nad snimkom na **samom Orinu** i zapisati:

| Merenje | Vrednost |
|---|---|
| fps kamere (`ros2 topic hz`) | |
| rezolucija / encoding | |
| p50 / p95 po kadru: kandidati, mreža, tracker | |
| fps obrade u live `--dry-run` | |
| CPU jednog jezgra (`tegrastats` / `top`) | |
| veličina loptice u px, blizu i daleko | |
| % kadrova sa lopticom u letu gde je nađena | |

3. Ako je veličina loptice ispod ~6 px na radnoj širini: raditi ROI u punoj rezoluciji (02, tačka 2).
4. Snimak odneti na laptop, označiti razmene i doučiti BallNet (`table_tennis/var/vision/train/`).

## Faza 3: backend i telefon, bez pokreta (20 min)

1. Na robotu: `.env` sa `TABLE_TENNIS_ENABLED=1`, tokenima i `TT_API_URL=http://127.0.0.1:8070`.
   Restart Supervisora radi mentor.
2. `curl -s -H "Authorization: Bearer $TT_OPERATOR_TOKEN" http://127.0.0.1:8070/api/table-tennis/health`
3. Telefon na Wi-Fi-ju koji vidi robotov LAN (03, sekcija 2): otvoriti aplikaciju, uneti token.
4. Ceo ručni gem sa `mode: mock` i fake ekranom/gestom: poen, undo, pauza, 10:10, 12:10, prekid
   Wi-Fi-ja telefona pa povratak (SSE resync), restart Supervisora usred meča (rezultat mora ostati).

## Faza 4: izlazi jedan po jedan, uz mentora (30 min)

1. **Govor:** `TT_ADAPTER_SPEECH=livekit`, `TT_SPEECH_LIVE=1`; pokrenuti speech servise; ručni poen.
   Izmeriti vreme od dodira do prvog zvuka (štopericom ili iz loga `SPEECH sent in ... ms`).
2. **Ekran:** tek kad postoji `show_text`. Prvo jedna ručna komanda (`show_message("TEST", "", 3)`),
   pa pravi rezultat. Izmeriti vreme od dodira do promene na ekranu.
3. **Gest:** mentor uključi ARM, prostor oko ruku je slobodan. Jedan `point left`, pa jedan
   `point right` preko `/api/conversation/command`; proveriti da je „levo" levo iz ugla robota.
   Tek onda gest uz poen.
4. `PATCH /api/vision {"enabled": false}`, da robot ne započinje razgovor sa igračima.

## Faza 5: vision uživo (30 min)

1. Kalibracija: kadar → četiri ugla i mreža → `table.json` → `calibration.set` u aplikaciji.
2. Proba bez uticaja na pravi meč: `live --device CHEST_LEFT_FISHEYE` usmeriti na poseban mock backend
   (`run_demo` na `:8099`, sa probnim mečem), a pravi meč voditi ručno u Supervisoru. Operater
   zapisuje pravog pobednika i poredi ga sa predlozima u probnom meču. Cilj: nema predloga usred razmene.
3. Tek onda `live --base-url http://127.0.0.1:8070` na pravi meč, režim `assisted`: kamera predlaže,
   operater potvrđuje ili ispravlja.
4. Zapisati za bar 20 razmena: predlog tačan / pogrešan / nema predloga i vreme od kraja razmene do
   predloga na telefonu.

## Faza 6: generalna proba demoa

Novi meč → „ručni dolazak" (ili poziv robota ako navigacija radi i mentor potvrdi put) → pozdrav →
ceo gem u `assisted` režimu → proglašenje pobednika → release ekrana. Za svaki deo proveriti
fallback iz `12_POKRETANJE_NA_ROBOTU.md` §7: vision pao → ručni poeni; ekran pao → telefon + glas;
govor pao → ekran + telefon.

## Faza 7: vraćanje robota

1. Završiti meč u aplikaciji (vraća podrazumevano lice na ekranu).
2. `tmux kill-session -t tt_vision`.
3. `PATCH /api/vision {"enabled": true}`.
4. Mentor: DISARM i gašenje po Safety Guide-u (hanging mode, gantry, dugme).
5. Po dogovoru ukloniti `TABLE_TENNIS_*` iz `.env` i vratiti checkout na zapisani SHA.

## Šta poneti

- laptop sa Ethernet adapterom (`192.168.2.119/24`) i punim `.venv-tt` + `.venv-vision`;
- mali Wi-Fi ruter ili plan za hotspot;
- dve ping-pong loptice iste boje kao na snimcima za učenje;
- telefon operatera i telefon za gledaoce;
- odštampanu ovu listu i tabelu merenja iz faze 2.
