# 03 Backend za poene, komunikacija sa robotom, ekran na glavi i telefon

## 1. Backend za poene: šta već postoji i šta dodati

Backend (`table_tennis/core`, `storage`, `api`) je najzreliji deo projekta i ne treba ga
prepravljati:

- pravila jednog gema do 11 uz razliku 2, servis i posle 10:10, undo, pauza, let;
- event sourcing u SQLite-u: komanda, događaji, snapshot i outbox idu u jednoj transakciji, a posle
  restarta se ne ponavljaju stari govor i gestovi;
- idempotentne komande (`command_id`), `expected_revision`, najviše jedna odluka po `rally_id`;
- SSE stream sa reconnect-om (`Last-Event-ID`) i heartbeat-om;
- dozvole po actor-u: vision sme samo da predloži, operater odlučuje.

Tok jednog poena danas:

```
operater "Servis" ──► rally.arm ──► rally.armed
vision ────────────► point.propose ──► proposal pending (UI prikazuje "Kamera predlaže")
operater ──────────► point.confirm  ──► point.confirmed  (ili point.award bez kamere)
                                        │
                        outbox ─────────┼──► ekran (A2ScoreDisplay)
                                        ├──► govor (LiveKitSpeechOutput → Supervisor)
                                        └──► gest  (A2GestureOutput: point left/right)
SSE ◄── snapshot + događaj ──► telefon / Supervisor tab / voice agent
```

Predlozi za dodavanje (vlasnik: osoba 2):

1. **Automatski `rally.armed` posle potvrđenog poena** u `assisted` režimu (vidi 02). Tada operater
   ima jedan dodir po poenu.
2. **Istek predloga:** ako operater ne reaguje N sekundi, predlog ostaje na ekranu telefona, ali
   robot ne izgovara ništa. Nikad se ne potvrđuje sam (`„Niko nije kliknuo" nije potvrda`).
3. **Tokeni** (01, greška 1), regenerisan `openapi.json` i ruta za potvrdu dolaska robota (01, greška 4).
4. **Read-only gledalac:** drugi telefon ili TV za publiku treba da vidi rezultat bez operatorskih
   prava. Actor `persona` je već read-only; dovoljna je `?view=1` varijanta aplikacije koja koristi
   poseban read-only token i sakriva dugmad.

## 2. Gde šta radi (preporučena topologija za demo)

```
 Telefon operatera ─┐                        Titan PC2 (Orin, 192.168.2.50)
 Telefon/TV publike ─┤  Wi-Fi ruter           ┌─────────────────────────────────────────┐
                     ├─ (ili laptop hotspot) ─┤ Supervisor :8070                         │
 Laptop tima ────────┘  povezan kablom        │  ├─ React build (aplikacija i tab)        │
                        na robotov LAN        │  ├─ /api/table-tennis  (naš backend)       │
                                              │  │     └─ SQLite  table_tennis/var/       │
                                              │  ├─ /api/conversation/command → LiveKit   │
                                              │  └─ voice-agent, audio-bridge, gesture    │
                                              │                                          │
                                              │ tt_vision proces (tmux)                   │
                                              │  ROS2 fisheye → tracker → sudija ─HTTP──► │
                                              └──────────────┬───────────────────────────┘
                                                             │ ssh (face host)
                                              PC1 x86 192.168.100.100: face app (ekran glave)
```

- **Backend unutar Supervisora** (`TABLE_TENNIS_ENABLED=1`) ima jedan port (8070) i jednu adresu za
  telefon i aplikaciju. Samostalni `run_demo` na 8099 ostaje za laptop.
- **Vision proces odvojeno**, u svom tmux-u i venv-u, na istom PC2 (tamo ROS vidi kameru). Sa
  backendom priča preko `http://127.0.0.1:8070/api/table-tennis`:
  - piše: `POST /matches/{id}/commands` (`camera.ready.set`, `point.propose`) sa vision tokenom;
  - čita: jedna SSE pretplata `GET /matches/{id}/events` drži poslednji snapshot u memoriji.
  Loopback je bez mrežnog kašnjenja, a pad vision procesa ne ruši Supervisor.
- **Telefon do robota:** robot je na žičanom LAN-u (`192.168.2.x`), a telefon je na Wi-Fi-ju.
  Potrebno je jedno od ovoga:
  1. mali Wi-Fi ruter spojen kablom na isti LAN (najčistije; telefon otvara
     `http://192.168.2.50:8070/#/stoni-tenis`);
  2. laptop sa hotspotom i Vite dev serverom na `0.0.0.0:5173` koji proksira `/api` na
     `http://192.168.2.50:8070` (`VITE_API_PROXY_TARGET`). Zbog toga je `host: "0.0.0.0"` i
     dodat u `vite.config.ts`. Radi, ali laptop je onda obavezan deo sistema.
  Ovo proveriti sa mentorom pre dana demoa: da li sme ruter na robotovu mrežu.

## 3. Ekran na glavi robota

Kako ekran radi (`docs/agibot/head_screen.md`): ne postoji API za „nacrtaj tekst". Postoji samo
„pusti klip po id-u". `robot_services/screen_manip` renderuje 800×480 mp4 sa tekstom preko
ffmpeg-a na face hostu (PC1, preko ssh-a) i pušta ga. Klip se vrti u petlji dok se ne pusti drugi.

Stanje u našem kodu:

- `table_tennis/robot/scoreboard.py` pravi dva reda: `ANA 3 : 2 MARKO` i `SERVIS ANA`,
  transliteracija i dužine 24/32 znaka odgovaraju ograničenjima renderera;
- `score_display.py` ima sesiju „najnovija revizija pobeđuje" i drži jedan slot;
- `A2ScoreDisplay` (`a2_adapters.py`) i dalje šalje samo dry-run `screen.show`.

Šta nedostaje za pravi ekran:

1. U `AgibotEmoticonScreenController` postoji samo `flash_text`, koji posle `duration_s` vraća
   podrazumevano lice. Za rezultat koji ostaje treba nova metoda `show_text(primary, secondary)`
   koja samo renderuje i pusti klip (prva polovina `flash_text`, bez `sleep` i `restore`), plus
   postojeća `restore_default_face()` za kraj meča. Ovo je izmena u `robot_services/screen_manip`,
   koji je zajednički kod: najaviti je mentorima.
2. `A2ScoreDisplay` transport: `screen.show` → `show_text`, `screen.release` →
   `restore_default_face`, pozvano u posebnoj niti (render je blokirajući).
3. `provision_screen()` pri startu runtime-a, da prvi rezultat ne čeka hladan ssh (~1,7 s naspram
   ~0,4 s kad je veza topla).

Očekivano kašnjenje od potvrde poena do ekrana: oko 0,2 s render + 0,4 s ssh + pokretanje klipa,
dakle **oko 0,6–1 s**. Ako je to previše, rezultat 0–0 do 11–11 može da se unapred renderuje u
pozadini na početku meča, ali to traži više registrovanih slotova, pa je tek druga faza.

## 4. Govor i gest, redosled izlaza

- Govor već radi kroz Supervisor (`/api/conversation/command`, `session.say` bez LLM-a). Glavna
  mana je nova LiveKit konekcija po rečenici (01, greška 5).
- Gest „point left/right" može da ide **u istoj komandi** kao rečenica:
  `{"text": "Poen Ana, tri dva", "gesture": "point left"}`. Tako se ne šalju dve komande i gest i
  glas su sinhronizovani. Levo i desno su iz perspektive robota i mapiraju se iz
  `robot_side_by_player`, ne iz slike.
- Redosled posle `point.confirmed`: **ekran odmah** (najsporiji je, pa neka krene prvi), zatim
  govor sa gestom. Ne čekati da jedno završi da bi drugo počelo; „primljeno" nije „završeno"
  (ruta nema completion događaj).
- Gest samo dok je robot ARM-ovan, stoji i niko nije na dohvat ruke (Safety Guide 1.5). Za demo
  koristiti samo kratke SAFE_ONLY gestove (`point left/right`, `thumbs up`, `wave`).

## 5. Telefon (frontend)

Aplikacija `features/table-tennis` je već dobra osnova: prikazuje samo backend snapshot (bez
lokalnog brojanja), koristi SSE sa reconnect-om i stabilnim `command_id` po nameri, ima setup,
predlog kamere sa potvrdom, undo, pauzu i ručni dolazak robota.

Dopune:

1. dugmad „Put je slobodan" (`route_not_confirmed`) i „Robot je stigao" (`need_operator_confirmation`) (01, greška 4);
2. read-only prikaz za publiku (tačka 1.4);
3. vidljiv indikator „kamera živa / kalibracija važi / poslednji kadar pre X s", da operater zna
   kad ne treba da čeka predlog;
4. unos kalibracije sa slike umesto kucanja `calibration_id` (vidi 04, stavka 2).
