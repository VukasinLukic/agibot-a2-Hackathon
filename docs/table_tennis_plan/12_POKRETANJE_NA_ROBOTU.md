# 12 Pokretanje TitanSudije na robotu (ciljni postupak kada je sve gotovo)

Zajednički dokument za ceo tim. Opisuje **najbolji slučaj**: sve četiri grane (`comp-vision`, `backend`,
`navigation`, `persone`) su spojene u timski `main`, testovi prolaze, pravi adapteri postoje i jednom su
provereni sa mentorom. Ako neki deo nije gotov, preskoči njegov korak i koristi fallback iz sekcije 7.

Svaki korak koji pokreće proces na robotu, menja Supervisor ili pravi fizički izlaz radi se **uz mentora**.
Pravila bezbednosti: `10_TITANSUDIJA_ROBOT_CONTEXT.md`, sekcija 0.

## 0. Kako izgleda sistem kada radi

```
laptop / telefon (operater)                     Titan PC2 (192.168.2.50, Orin)
  browser -> http://192.168.2.50:8070  ---->  Supervisor (tmux robot_supervisor, :8070)
                                                ├─ React UI (dist/) + tab "Table tennis"      [persone]
                                                ├─ /api/table-tennis/* (TABLE_TENNIS_ENABLED) [backend]
                                                │    ├─ SQLite: table_tennis/var/*.sqlite
                                                │    ├─ ekran/gest/navigacija adapteri        [navigation]
                                                │    └─ govor -> /api/conversation/command    [persone]
                                                ├─ voice-agent + livekit + audio-bridge (govor)
                                                └─ vision-controller (ljudi), enabled=false tokom meča
                                              TitanSudija vision proces (lopta)              [comp-vision]
                                                kamera -> detektor -> POST point.propose (TT_VISION_TOKEN)
```

Tok jednog poena: kamera vidi poen -> vision šalje `point.propose` -> operater potvrdi u UI (`point.confirm`)
ili sam dodeli (`point.award`) -> backend upiše poen -> ekran prikaže rezultat, Titan izgovori rezultat i
pokaže levo/desno ka igraču koji je dobio poen.

## 1. Preduslovi (dan pre / pre termina)

- [ ] Sve četiri grane spojene u timski `main`; `python -m pytest tests/table_tennis -q` prolazi.
- [ ] `python -m table_tennis.contracts.generate --check` i `python -m table_tennis.sim.fixtures --check` prolaze.
- [ ] Ceo demo odigran lokalno u mock režimu (sekcija 2).
- [ ] Zabeležen SHA commita koji ide na robot: `git log --oneline -1`.
- [ ] Mentor je odobrio: način deploy-a, port/Supervisor režim, kameru, ARM, isključenje auto-razgovora.
- [ ] Tokeni (`TT_OPERATOR_TOKEN`, `TT_VISION_TOKEN`, `TT_ROBOT_TOKEN`) generisani i preneti privatno, nikad u Git:
      `python -c "import secrets;print(secrets.token_urlsafe(24))"` (po jedan za svaki).

## 2. Generalna proba na laptopu (bez robota)

```powershell
cd C:\Users\Tea\OneDrive\Dokumenti\a2-hackathon
.\.venv-tt\Scripts\Activate.ps1
python -m table_tennis.run_demo --mode mock
# drugi terminal:
python -m table_tennis.sim.run --scenario manual-game --api http://127.0.0.1:8099
python -m table_tennis.sim.run --scenario disputed-point --api http://127.0.0.1:8099
```

UI proba: `cd robot_supervisor_v2\frontend`, `npm install`, `npm run dev`, otvoriti tab Table tennis.
Proveriti: novi meč, ručni poen, CV predlog i potvrda, undo, deuce, kraj meča, reconnect.

## 3. Priprema robota (mentor prisutan)

1. Titan upaljen, na gantry-ju, setup preko tableta završio mentor. E-stop kod mentora.
2. LAN: laptop `192.168.2.119/24`, `ssh agi@192.168.2.50`.
3. Provera da smo na PC2 (read-only):
   ```bash
   hostname -I                    # mora sadržati 192.168.100.110
   cd /agibot/humanoid-platform
   git status --short && git log --oneline -1    # zapiši trenutni commit (za povratak)
   aima em doctor                 # stanje modula, samo pregled
   ```
4. Supervisor radi: `http://192.168.2.50:8070` se otvara, `GET /api/health` vraća ok.

## 4. Deploy koda (mentor odlučuje način)

Varijanta A, git (ako robot ima pristup remote-u i mentor dozvoli):
```bash
cd /agibot/humanoid-platform
git fetch <timski-remote> main
git switch --detach <SHA>        # tačno provereni commit, bez menjanja njihove grane
```

Varijanta B, kopija samo našeg koda sa laptopa (PowerShell):
```powershell
scp -r table_tennis agi@192.168.2.50:/agibot/humanoid-platform/
scp -r robot_supervisor_v2\frontend\src\features\table-tennis agi@192.168.2.50:/agibot/humanoid-platform/robot_supervisor_v2/frontend/src/features/
```
(plus registracija feature-a u frontendu i hook u `app/api/main.py`, ako već nisu u checkout-u na robotu)

Zavisnosti (samo ako fale, u Supervisor `.venv`, uz mentora):
```bash
source /agibot/humanoid-platform/.venv/bin/activate
python -c "import table_tennis, pydantic, fastapi; print('ok')"
# ako fali: pip install -r table_tennis/requirements-dev.txt   (NIKAD root requirements.txt)
```

Frontend build (na robotu ako ima Node 18+, inače build na laptopu pa kopirati `robot_supervisor_v2/dist/`):
```bash
cd /agibot/humanoid-platform/robot_supervisor_v2/frontend
npm install && npm run build      # izlaz ide u robot_supervisor_v2/dist, Supervisor ga servira na :8070
```

## 5. Konfiguracija

Na robotu, lokalni config van Git-a: `/agibot/humanoid-platform/table_tennis/config.local.yaml`
```yaml
mode: real
server:
  host: 127.0.0.1
  port: 8099
storage:
  db_path: table_tennis/var/titansudija.sqlite
features:
  automatic_scoring: false        # assisted: CV predlaže, operater potvrđuje
auth:
  mode: token
robot:
  tables:
    table-1: [referee-spot]       # waypoint koji je mentor snimio pored stola
```

U `/agibot/humanoid-platform/.env` (van Git-a) dodati:
```env
TABLE_TENNIS_ENABLED=1
TABLE_TENNIS_CONFIG=table_tennis/config.local.yaml
TT_AUTH_MODE=token
TT_OPERATOR_TOKEN=<privatno>
TT_VISION_TOKEN=<privatno>
TT_ROBOT_TOKEN=<privatno>
```

Persona i ime: `robot_name: TitanSudija` u lokalnom `livekit_config/prompt_config.yaml` (runtime fajl, nije u Git-u).

## 6. Pokretanje, redom

1. **Restart Supervisora** (radi mentor, jer čita `.env` samo pri startu):
   ```bash
   tmux attach -t robot_supervisor     # Ctrl+C samo ovde, uz mentora, pa ponovo:
   ./run_robot_supervisor_v2.sh        # iz /agibot/humanoid-platform
   # izlaz iz tmux-a: Ctrl+b pa d
   ```
2. **Provera feature-a:**
   ```bash
   curl -s http://127.0.0.1:8070/api/table-tennis/health -H "Authorization: Bearer $TT_OPERATOR_TOKEN"
   ```
   Treba `mode: real` i status adaptera (ekran, gest, govor, navigacija, vision).
3. **Govor spreman:** u Supervisor UI pokrenuti speech servise (`livekit`, `voice-agent`, `audio-bridge`) ili
   `POST /api/services/start-speech`. Test: `POST /api/conversation/command` sa `{"text":"TitanSudija je spreman."}`.
4. **Isključiti auto-razgovor na osobe** (igrači su stalno u kadru):
   `PATCH /api/vision` sa `{"enabled": false}` (ili prekidač Vision u UI). Stanje: `GET /api/vision`.
5. **ARM** (mentor): Navigation tab, dugme ARM (`POST /api/nav/arm`). Gasi idle animaciju i drži robota spremnog.
6. **Vision proces za lopticu** u posebnoj tmux sesiji:
   ```bash
   tmux new -s tt_vision
   cd /agibot/humanoid-platform && source .venv/bin/activate   # ili vision venv koji odredi comp-vision
   export TT_VISION_TOKEN=<privatno>
   python -m table_tennis.vision.<ulazna_tacka> --api http://127.0.0.1:8070/api/table-tennis --camera <kamera>
   # Ctrl+b d
   ```
   `<ulazna_tacka>` i `<kamera>` upisuje grana `comp-vision` kada su gotovi (npr. levi fisheye `/dev/video2`
   ili ROS2 `/aima/hal/fish_eye_camera/chest_left/color`).
7. **Otvoriti UI** na laptopu/telefonu: `http://192.168.2.50:8070`, tab Table tennis, uneti operator token.

## 7. Tok demonstracije

1. Novi meč: imena igrača, strane, persona (regular / corporate), `best_of 1`.
2. Poziv robota do stola (`referee-spot`) ili, ako je navigacija isključena, ručno postavljanje i
   "manual arrival". Mentor potvrđuje da je put slobodan.
3. Pozdrav: govor + gest (`wave`), ekran "TitanSudija 0 : 0".
4. Razmene: operater armira razmenu, vision predlaže poen, operater potvrđuje; Titan izgovara rezultat,
   pokazuje `point left` / `point right`, ekran se ažurira.
5. Korekcija: undo poslednjeg poena ako treba (ekran i glas se ispravljaju; izveden gest se ne poništava).
6. Kraj gema (11, razlika 2): proglašenje pobednika, gest `cheer` ili `thumbs up`, ekran ostaje sa konačnim rezultatom.
7. Kraj: release ekrana (vraća default lice).

Fallback ako nešto padne (poen se nikad ne gubi, backend ga čuva):
- vision ne radi: operater dodeljuje poene ručno (`point.award`);
- navigacija ne radi: robot ručno postavljen, "manual arrival";
- gest ne radi: rezultat na ekranu + glas;
- ekran ne radi: web scoreboard + glas;
- govor ne radi: ekran + gest + web scoreboard.

## 8. Gašenje i vraćanje robota

1. U UI završiti meč; release ekrana.
2. `tmux kill-session -t tt_vision` (samo naša sesija).
3. `PATCH /api/vision` sa `{"enabled": true}` (vraća auto-razgovor kako je bio).
4. Mentor: DISARM (`POST /api/nav/disarm`) ili ostavlja robota po svom planu.
5. Ako mentor traži: ukloniti `TABLE_TENNIS_*` iz `.env`, vratiti checkout na zapisani commit
   (`git switch --detach <stari SHA>`), restart Supervisora radi mentor.
6. Laptop: Ethernet adapter vratiti na prethodna podešavanja.

## 9. Brza provera kada nešto ne radi

| Simptom | Gde gledati |
|---|---|
| Nema taba Table tennis | frontend build (`robot_supervisor_v2/dist`), registracija feature-a |
| `/api/table-tennis/*` daje 404 | `TABLE_TENNIS_ENABLED=1` i token u `.env`, Supervisor restartovan; log Supervisora |
| 401/403 | pogrešan token ili actor (vision ne sme `point.award`) |
| Nema govora | `GET /api/services`, log `voice-agent` i `audio-bridge` (`/api/services/<ime>/logs`) |
| Robot sam započinje razgovor | `GET /api/vision` treba `enabled: false` |
| Gest prihvaćen a ništa se ne desi | ARM stanje `GET /api/nav/arm`, `/agibot/log/pnc_arm/pnc_arm.log`, neko blizu (LiDAR blokira) |
| Ekran se ne menja | log našeg ekran adaptera; face host ide preko PC1, pitati mentora |
| Vision ne šalje predloge | tmux `tt_vision`, kamera zauzeta ili pogrešan device |
| Idle animacija se vraća | ARM nije aktivan (skillpilot vraća idle na ~60 s) |
