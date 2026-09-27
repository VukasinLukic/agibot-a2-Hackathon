# Team4: ponovljiv deployment na A2 PC2

Ovaj dokument je za `leksaas/a2-hackathon`, granu **a2-hackathon-team4**.
Nijedna komanda ovde ne podrazumeva push na main. Ne povezivati se na PC1.
Git push NIJE deploy, a uspešan preflight NIJE potvrda da govor/hod rade na robotu.
Za offline preflight na laptopu: `python -m pip install -r deploy/pc2/requirements-check.txt` u odabranom lokalnom venv-u. Na robotu prvo proveriti postojeći mentorski environment, ne menjati ga ovim uputstvom naslepo.

## Šta je popravljeno

- Linux launcher odbija postojeću `robot_supervisor` sesiju umesto lažne poruke da je nova kopija pokrenuta. Ništa ne ubija i ne restartuje.
- Stvarni A2 `config.yaml` je obavezan: nema tihog fallback-a na Unitree example.
- `.env` čita Python u stvarnom child procesu. Nije shell skript: vrednosti sa razmacima i `$` ne izvršavaju komande.
- Boot log je u aktivnom checkout-u: `robot_supervisor_v2/logs/robot_supervisor_boot.log`.
- Preflight proverava config, flag-ove, portove, ključeve bez ispisa vrednosti, voice interpreter, speech state i frontend build.
- Token se unosi u browseru. Ne ugrađuje se u frontend kroz `VITE_*` ili javni default.
- Nepovezani IGRA/papir-kamen-makaze ekran, koji je rušio TypeScript build, sačuvan je u `legacy/igra/`.
- Windows launcher više ne koristi apsolutnu putanju drugog developera. To je lokalni alat, ne zamena za PC2 launcher.

## 1. Šta ide u Git, a šta NE

| Ide u Git | Ne ide u Git; ostaje privatno/lokalno |
| --- | --- |
| Python/TS kod, prompt/persona fajlovi koji su već tracked | `.env`, `app/.env`, `.envs/`, API/SSH ključevi i tokeni |
| `requirements*.txt`, frontend `package.json` i `package-lock.json` | `.venv`, `.venv-voice-canary`, ROS/camera venv, node_modules |
| `.env.example` sa placeholder-ima i `team4_` RAG imenima | Stvarni mentorski `robot_supervisor_v2/config.yaml` |
| `scripts/pc2_deploy.py`, release builder, Git guard, testovi | `robot_supervisor_v2/state/`, uključujući `speech.json` i `environment.json` |
| Postojeći models/vendor_wheels, prema postojećem repou i licenci | `table_tennis/var/`, kalibracija, snimci i baze sa privatnim podacima |
| Uputstva i bezbedni example konfiguracioni fajlovi | `table_tennis/config.local.yaml`, backup fajlovi i `.deploy-private/` |

`dist/` se ne commit-uje: gradi se iz tačnog commita na laptopu i ulazi u release arhivu. Robot ne mora imati Node.
Stari `tt.tgz` nije izvor istine i izostavljen je iz novih release arhiva; istorija nije prepisivana.
`robot_services/audio/audio_bridge.py` već je tracked uprkos starom ignore pravilu; ulazi u arhivu.

**Zašto pravi config nije objavljen?** U ovom lokalnom checkout-u nema potvrđenog mentorski podešenog A2 config-a. On sadrži stvarne putanje, uređaje i eventualne tokene. Nismo izmislili zamenu koja bi mogla promeniti hardver. Prvo ga sačuvajte sa aktivnog PC2 deployment-a. Posle redakcije i mentorske provere može se napraviti javni A2 profil. Do tada je ovaj fajl obavezan privatni deo deployment-a.

## 2. Bezbedan Git tok na laptopu

```powershell
git branch --show-current
git remote -v
git status --short --branch
# Mora biti a2-hackathon-team4; origin push URL mora biti leksaas/a2-hackathon.git.
git fetch origin a2-hackathon-team4
git merge --ff-only FETCH_HEAD
```

Ako nije fast-forward, stati i pregledati razlike. Bez force-push-a/reset-a.
Ne koristiti `git add .` za privatne fajlove. Stagovati imenovane fajlove, proveriti `git diff --cached --stat`, pa:

```powershell
.\.venv-tt\Scripts\python.exe scripts/check_git_secrets.py
git diff --cached --check
# Posle pregleda i commita:
git push --dry-run origin HEAD:refs/heads/a2-hackathon-team4
git push -u origin HEAD:refs/heads/a2-hackathon-team4
```

`sync-to-robot.ps1` sada radi dry-run po default-u, a `-Push` eksplicitno objavljuje samo ovu granu. Ne spaja sam druge grane i ne šalje ništa na robota.
Guard otkriva privatne putanje i neke poznate formate ključeva. Nije garancija da u čitavoj istoriji nema proizvoljnih vendor ključeva; nove YAML/JSON i arhive pregledati i ručno.

## 3. Napravi release na laptopu

Čist, commitovan checkout na team4; Python 3.11+ i Node/npm na laptopu:

```powershell
python scripts/build_team4_release.py
```

Builder pokreće `npm ci --ignore-scripts`, TypeScript proveru i Vite build. Zatim pravi `artifacts/team4-<commit>-<timestamp>.tar.gz`, ispisuje SHA256 i upisuje `TEAM4_RELEASE.json` u arhivu. U njoj su committed code + izgrađeni `dist`, bez ignorisanih privatnih fajlova.
Frontend `.env*` i procesne `VITE_*` override-e builder odbija da slučajno ne ugradi token ili pogrešan API URL. Ne gasiti ovu proveru; runtime operator token se unosi kroz aplikaciju.

## 4. Pre bilo kakvog menjanja na PC2

Mentor prisutan. U ranijem terminu SSH je bio `agi@192.168.2.50`, a fizički repo `/agibot/data/home/agi/Desktop/CT/a2-team4`.
To su prethodno zabeležene vrednosti, ne dokaz trenutnog stanja.

```bash
hostname -I
readlink -f /agibot/humanoid-platform
readlink -f /agibot/data/home/agi/Desktop/CT/a2-team4
tmux list-panes -a -F '#{session_name} #{pane_pid} #{pane_current_path}'
ss -ltnp | grep -E ':(8070|8080|7880|8098)\b'
curl --max-time 5 -fsS http://127.0.0.1:8070/api/speech/config
```

Mentor potvrđuje PC2 (ranije interni IP `192.168.100.110`), aktivni folder, identitet hosta i vlasnika procesa. Ako ste na PC1 ili niste sigurni, stati. Ne menjati host-key proveru i ne gasiti sisteme da bi se oslobodio port.
Iz speech API-ja zabeležiti `state_file`; to pokazuje iz koje kopije radi. Ne deliti tokene/logove sa privatnim sadržajem.

## 5. Šta tačno ručno sačuvati / preneti

Ako ostajete u ISTOM potvrđenom folderu, privatni fajlovi se obično ne prenose ponovo: **sačuvati ih i ostaviti na mestu**. Git merge ne donosi te fajlove i ne sme da ih zameni example-ima.

| Fajl na PC2 | Zašto je potreban |
| --- | --- |
| `.env` | LiveKit, Azure, Soniox/ElevenLabs, canary flag, TT tokeni i URL-ovi |
| `robot_supervisor_v2/config.yaml` | Stvarni A2 servisi, audio/kamera Python putanje i API port |
| `robot_supervisor_v2/state/speech.json` | Sačuvana lista glasova i aktivni provider/model/jezik; bez njega UI može pokazati samo ElevenLabs |
| `robot_supervisor_v2/state/environment.json` + potrebni `.envs/*.env` | Izbor CT/DEV i pripadajući Azure konfiguracioni sloj |
| `table_tennis/config.local.yaml`, ako se koristi | Lokalni waypoint i izbor adaptera; ne izmišljati `referee-spot` |
| `table_tennis/var/vision/table.json`, `vision_right.yaml` | Kalibracija i vision podešavanja; posle pomeranja robota potrebna je nova kalibracija |
| Dodatni privatni audio/upload/map/RAG podaci koje mentor identifikuje | Nisu obavezni za minimalni ručni meč, ali ih ne brisati pri deploy-u |

Primer **backup-a na laptop**, tek posle potvrde da je ovo aktivan folder (PowerShell):

```powershell
New-Item -ItemType Directory -Force .deploy-private | Out-Null
scp agi@192.168.2.50:/agibot/data/home/agi/Desktop/CT/a2-team4/.env .deploy-private/robot.env
scp agi@192.168.2.50:/agibot/data/home/agi/Desktop/CT/a2-team4/robot_supervisor_v2/config.yaml .deploy-private/config.yaml
scp agi@192.168.2.50:/agibot/data/home/agi/Desktop/CT/a2-team4/robot_supervisor_v2/state/speech.json .deploy-private/speech.json
scp agi@192.168.2.50:/agibot/data/home/agi/Desktop/CT/a2-team4/robot_supervisor_v2/state/environment.json .deploy-private/environment.json
```

Ako neki state fajl ne postoji, zabeležiti; ne izmišljati njegov sadržaj. `.envs` fajlove preneti samo ako se koriste. Čuvajte ovaj backup privatno; `.deploy-private/` je ignorisan.

Ako fajlovi nedostaju na ciljnom PC2 deployment-u, nakon pregleda sa mentorom šaljite ih prvo pod privremenim imenima, ne preko radnih fajlova:

```powershell
scp .deploy-private/robot.env agi@192.168.2.50:/agibot/data/home/agi/Desktop/CT/a2-team4/.env.team4.incoming
scp .deploy-private/config.yaml agi@192.168.2.50:/agibot/data/home/agi/Desktop/CT/a2-team4/robot_supervisor_v2/config.yaml.team4.incoming
```

Mentor napravi timestamp backup postojećih ciljnih fajlova, uporedi konfiguraciju i tek onda odobri zamenu. Speech/environment state analogno vratiti u `state/`, bez spajanja tuđih podataka. Podesiti `.env` i eventualne env fajlove na dozvole `600`, privatne direktorijume na `700`.
Ne kopirati laptopov privatni SSH ključ na robota. `authorized_keys` nije deo projekta niti deployment paketa.

**Venv nije fajl za kopiranje sa Windows-a.** Zadržati postojeća Linux/aarch64 okruženja na PC2. Ako su nestala, mentor ih obnavlja za odgovarajući JetPack/Python, iz postojećih requirements i vendor paketa. Ne raditi naslepo `pip install -r requirements.txt` preko radnog robot okruženja. Canary, Supervisor i ROS kamera mogu koristiti različite interpretere.

## 6. Objavi kod i frontend u jednom potvrđenom folderu

Prvo backup i dogovoreni servisni termin sa mentorom. U aktivnom PC2 repo-u:

```bash
cd /agibot/data/home/agi/Desktop/CT/a2-team4
git status --short --branch
git branch --show-current
```

Ako postoje promene ili nije team4, stati: ne koristiti reset/clean. Kada mentor potvrdi čist team4 checkout:

```bash
git fetch https://github.com/leksaas/a2-hackathon.git a2-hackathon-team4
git merge --ff-only FETCH_HEAD
git rev-parse HEAD
```

Sa laptopa pošaljite tačno izgrađenu arhivu (zameniti ime onim koje je builder ispisao):

```powershell
scp .\artifacts\team4-<commit>-<timestamp>.tar.gz agi@192.168.2.50:/agibot/data/home/agi/Desktop/CT/a2-team4/
```

Mentor na PC2 proverava `sha256sum` arhive i `tar -xOf IME_ARHIVE TEAM4_RELEASE.json`. Commit mora biti ISTI kao `git rev-parse HEAD`; ako je tim u međuvremenu pushovao, izgraditi novu arhivu ili dogovoriti tačan commit, ne mešati verzije.
Sačuvati prethodni `dist` kao timestamp backup. Zatim iz proverene arhive izdvojiti **samo** build i release oznaku, jer je kod već stigao kroz Git:

```bash
tar -xzf IME_ARHIVE robot_supervisor_v2/dist TEAM4_RELEASE.json
bash run_robot_supervisor_v2.sh --check
```

Ne raspakovavati ceo projekat preko nepoznatog radnog checkout-a. Ako nema Node-a na robotu, to je očekivano: build je već u arhivi.

Preflight može prijaviti `app/.env`: mentor mora bezbedno uskladiti njegove vrednosti sa root `.env`, sačuvati backup i skloniti konfliktni override. Ne brisati ga pre pregleda.
U `.env` zadržati postojeće validne ključeve i dopuniti TT promenljive iz `.env.example`; ne prepisivati ceo fajl template-om. Pet TT tokena moraju biti različite nasumične vrednosti, ne `operator_secret` itd. Operatoru se daje samo njegov token.

## 7. Pokretanje i minimalan test

Samo mentor odobrava zaustavljanje postojeće sesije i ponovni start. Restart može uticati na ARM/idle animaciju i audio/AIMA ownership. Launcher sam ne ubija staru sesiju.

```bash
# Tek kada mentor odobri start i postojeći Supervisor više ne radi:
bash run_robot_supervisor_v2.sh
```

Sačuvan je mentorski ROS shell startup tok (`ROS_SELECTION`, default `1`). Ako nema interaktivnog ROS menija, mentor može pozvati `ROS_SELECTION= bash run_robot_supervisor_v2.sh`. SDK/ROS okruženje i dalje mora biti ono koje su mentori pripremili.
Prvo proveriti log i `/api/health`, zatim TT ručni meč i token ekran. Proveriti sačuvane glasove, pokrenuti jednu rečenicu prema mentorskoj speech proceduri. Tek onda postaviti `TT_ADAPTER_SPEECH=livekit` i `TT_SPEECH_LIVE=1`, uz potreban kontrolisan reload/restart.

Ne uključivati automatsko suđenje, gestove ili hod da bi se „videlo da li radi”. `.env.example` namerno drži motion/display mock. Stvarni adapteri, waypoint, ARM i gestovi zahtevaju poseban test sa mentorom. Ne tvrditi da je robot spreman samo zato što je build zelen.

## 8. Poznata ograničenja

Lokalna verifikacija ove izmene: `pytest tests/table_tennis tests/test_pc2_deployment.py -q` — 387 passed, 1 skipped, 7 subtests passed; `npm run build` uspešan; Bash sintaksa i PowerShell parsing provereni. To nisu testovi na fizičkom robotu.

- Fizički robot nije testiran ovim izmenama; preflight ne proverava mrežu cloud provajdera, dozvole ključeva, ALSA, ROS ni balans.
- Provider/jezik mora biti podržana kombinacija. Raniji `unsupported_language` ne rešava se promenom Git grane.
- Ako postoji Docker RAG na 8098, ne startovati paralelni Python RAG. Pregledani launcher ne koristi Compose i ne prosleđuje root `livekit.yaml` LiveKit-u.
- Polazni dependency audit prijavio je 8 frontend nalaza; nije rađen automatski major upgrade usred deployment popravke.
- Git guard nije zamena za specijalizovan audit istorije. Stari privatni podaci, ako su ikada commitovani, zahtevaju rotaciju ključeva i odvojeno dogovoreno čišćenje, ne force-push tokom hakatona.
