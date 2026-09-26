# 01 Greške koje moramo da popravimo

Pregledan je `main` @ `1f2f48f` (posle PR #14 i #15 „navigation" i commita „fixed bugs regarding
vision and sound pipelines"). Ništa nije menjano. U listi su samo greške koje su i dalje u kodu i
koje su proverene (pokretanjem ili čitanjem koda). Stavke koje su popravljene ili nisu greške su
na dnu.

## Šta je pokrenuto

| Provera | Rezultat |
|---|---|
| `python -m pytest tests/table_tennis` | **326 passed, 1 skipped, 1 failed** |
| Pao test | `test_run_demo_port_and_host` visi dok ga timeout ne prekine (greška 1) |
| Preskočen | `test_track_onnx.py`: nema lokalnih BallNet težina (očekivano) |
| `python -m table_tennis.contracts.generate --check` | **pada**: `openapi.json` je zastareo (greška 4) |
| `python -m table_tennis.sim.fixtures --check` | prolazi |

---

## 1. Podrazumevani tokeni su javni (VISOKO, bezbednost)

**Gde:** `table_tennis/config.py:45-58`; `robot_supervisor_v2/frontend/src/features/table-tennis/config.ts:20`;
`livekit-client/referee_mode.py:88`.

`Tokens` ima ugrađene vrednosti `operator_secret`, `vision_secret`, `robot_secret`, `sim_secret` i
`persona_secret`, a `AuthSettings.mode` je podrazumevano `"token"`. Aplikacija ugrađuje
`operator_secret` u build.

**Zašto je greška:**
- `table_tennis/api/supervisor.py:44` treba da montira feature samo kad su tokeni podešeni. Sada
  je dovoljno `TABLE_TENNIS_ENABLED=1`, i Supervisor na `0.0.0.0:8070` prihvata `operator_secret`
  od bilo koga na mreži. Operater sme da pozove robota na waypoint, a to je pokret robota od 70 kg.
- `run_demo --host 0.0.0.0` stvarno pokreće server na svim interfejsima. Zato test
  `test_run_demo_port_and_host` visi.
- Komande iz `table_tennis/README.md` ne rade: `sim.run` bez `--token` dobija **401**, a sa
  `--token operator_secret` scenario `disputed-point` dobija **403** na `camera.ready.set`.
  Obe greške su proverene pokretanjem.

**Popravka:** vratiti `mode="local"` i `None` tokene kao podrazumevane, a tokene za robota upisati
u `.env`. Ako zero-config mora da ostane: odbiti poznate podrazumevane tokene kad host nije
loopback i kad feature radi u Supervisoru; simulatoru dati poseban `--vision-token`.

## 2. Capture sa fisheye-a vrti CPU i ne primeti kad kamera stane (VISOKO)

**Gde:** `table_tennis/vision/a2.py:97-113` i
`robot_services/vision/detection/ros2_capture.py:294` (`read`).

`Ros2VideoCapture.read()` se ne blokira i posle prvog kadra **uvek** vraća `ok=True` sa kopijom
poslednjeg kadra. Poslednja izmena (`_READ_FAILURES = 3`) broji neuspela čitanja, ali takvih posle
starta nema. Zato:

- kada je obrada brža od kamere, petlja se vrti i svaki put pravi dve pune kopije kadra i poredi
  ih bajt po bajt (`a2.py:109`). Na 1920×1080 to je oko 7 ms po okretaju na x86, a na Orinu više,
  uz zauzeto jezgro i GIL;
- kada kamera prestane da šalje, `camera_missing` se nikad ne postavi. Petlja zauvek preskače
  duplikate, `camera.ready` ostaje `true`, a predlozi tiho prestanu;
- vreme kadra je vreme čitanja (`a2.py:113`, `now_ns()`), a ne vreme snimanja. Iz njega tracker i
  detektor odskoka računaju brzinu.

**Popravka:** u `Ros2VideoCapture` čuvati brojač kadrova i `header.stamp` i dodati blokirajući
`read(timeout)` preko `threading.Condition`. `A2FisheyeCapture` onda poredi brojač umesto bajtova,
a posle N ms bez novog kadra postavlja `camera_missing`.

## 3. Vision proces pada kad backend nije dostupan i ostavlja „kamera spremna" (SREDNJE)

**Gde:** `table_tennis/vision/live.py:105-117` (`_request`) i `table_tennis/vision/events.py:208`
(`MatchVisionProducer.run`).

- `_request` hvata samo `HTTPError`. Kad backend ne radi, na primer tokom restarta Supervisora,
  `urlopen` baca `URLError` (proveren je `Connection refused`) i ceo vision proces pada sa
  traceback-om. Isto važi za timeout. Snapshot se čita za **svaki kadar**, pa je dovoljan jedan
  kratak prekid.
- `run` nema `try/finally`. Kad se proces zaustavi (Ctrl+C) ili padne, `camera.ready.set false` se
  ne pošalje. Aplikacija i dalje pokazuje kameru kao spremnu, a operater čeka predloge koji neće
  doći.

**Popravka:** u `_request` hvatati `URLError`/`OSError`/`TimeoutError`; za čitanje snapshot-a
koristiti poslednji dobar i posle nekoliko neuspeha ponovo pokušati; za predlog jedan retry sa
istim `command_id`. U `run` dodati `finally` koji pokuša da pošalje `camera.ready.set false`.

## 4. Rute za robota: zastareo OpenAPI i potvrda dolaska bez rute (SREDNJE, kad navigacija bude prava)

**Gde:** `table_tennis/contracts/schema/openapi.json`, `table_tennis/api/router.py`,
`table_tennis/robot/call_service.py:145`, `table_tennis/robot/a2_adapters.py:467`.

- `POST /robot/calls/{id}/route` postoji u routeru, ali nije u `openapi.json`
  (`generate --check` pada). U aplikaciji nema dugmeta za tu rutu, pa poziv iz telefona ostaje u
  `route_not_confirmed`.
- Nova metoda `RobotCallService.confirm_arrival` (operater potvrđuje da je robot stigao) **nema
  HTTP rutu**. Pravi navigator bez `task_id` završava u stanju `arrived` sa razlogom
  `need_operator_confirmation`. `arrived` nije završno stanje, pa taj poziv ostaje aktivan i svaki
  novi poziv dobija `robot_busy` dok se ovaj ne otkaže.

**Popravka:** dodati `POST /robot/calls/{id}/arrival` (samo operator), regenerisati ugovor
(`python -m table_tennis.contracts.generate`) i dodati u aplikaciju dva dugmeta: „Put je slobodan"
i „Robot je stigao".

## 5. Svaka izgovorena rečenica otvara novu LiveKit sobu (SREDNJE, kašnjenje)

**Gde:** `robot_supervisor_v2/app/utils/agent_commands.py:82-110`.

Za svaku komandu radi se `rtc.Room()`, `connect`, slanje i `disconnect`. `LiveKitSpeechOutput`
šalje po jednu komandu za svaku rečenicu, a na početku meča i dve. Povezivanje na sobu je obično
nekoliko stotina ms pre nego što TTS krene, pa je govor posle poena primetno zakašnjen. To je u
suprotnosti sa zahtevom za malo kašnjenje iz `AGENTS.md`.

**Popravka:** jedna trajna konekcija u Supervisoru za slanje komandi agentu, ili bar spojiti
`__REFEREE_ON__` i prvu rečenicu u jednu komandu sa `steps`. Ovo je Supervisor kod, pa ga
najaviti mentorima.

---

## Nije greška u kodu, ali se mora izmeriti na robotu

Ovo se ne može potvrditi bez kamere na robotu. Proveriti u fazi 2 plana testiranja (dokument 05):

- **Izobličenje fisheye-a:** kalibracija je čista homografija bez `undistort`
  (`vision/calibration.py`). Ako su krajevi stola blizu ivice kadra, polovina odskoka može biti
  pogrešna. Popravka ako zatreba: `cv2.fisheye.undistortPoints` samo za tačke.
- **Veličina loptice:** pragovi (`candidates.py` `min_area=6`, `patch_min_side=24`) su podešeni
  na snimku telefona. Na fisheye-u loptica na dalekom kraju može imati samo nekoliko piksela.
- **Zaklon posle odskoka:** nova logika sudije predlaže `missed_return` kad je poslednji događaj
  odskok na strani primaoca i loptica nestane na ~7 kadrova (`missing_frames: 8`). Ako reket
  primaoca zakloni lopticu duže od toga u trenutku udarca, predlog će biti pogrešan. Izmeriti
  koliko često se to dešava na snimku sa robota.

## Popravljeno od prošle analize

- **Nema živog vision procesa:** sada postoji `python -m table_tennis.vision.live`
  (`--device CHEST_LEFT_FISHEYE` ili `--clip`), sa učitavanjem kalibracije (`read_calibration`).
- **Sudija predlaže usred razmene / pogrešan pobednik kod auta:** predlog sada traži da poslednji
  događaj pre nestanka bude odskok na strani primaoca (`RallyEventDetector` je povezan). Nestanak
  važi tek posle `missing_frames` kadrova, a ne posle prvog praznog kadra. Poverenje više nije
  fiksno 0,8.
- **Navigacija:** pravi tok sa E-stop-om, otkazivanjem koje čeka da robot stane, proverom
  telemetrije i zaštitom od hodanja na praznom snapshot-u; dobro pokriveno testovima
  (`test_real_flow.py`).

## Izbačeno iz liste (nisu greške)

- Snapshot po kadru preko HTTP-a: izmereno oko 2–3 ms na lokalu, prihvatljivo.
- Vite dev server na `0.0.0.0`: namerno, zbog telefona; opasno je samo zajedno sa greškom 1.
- Rečenica koja ne uspe se ne ponavlja: svesna odluka, sledeći poen kaže novi rezultat.
- Higijena repoa (fajlovi u korenu): nije greška u kodu, vidi dokument 04.
