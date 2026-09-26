# Navigacija, termin od 2 sata — plan za LLM koji nije bio u prethodnom chatu

Ovo je jedini dokument koji treba da pratiš dok je robot tu. Popunjavaš
`table_tennis/robot/mentor_podaci.md`. U ovaj fajl ne upisuješ žive vrednosti.

Osoba 3 drži navigaciju, ekran glave i jedan gest po dolasku. Govor, vizija, poeni,
RAG i telefon nisu ovaj termin.

## Pravila za LLM

Radiš na laptopu koji je na mreži robota. Nisi na robotu. SSH je samo cev za
read-only komande. Svaku komandu ispod smeš da pokreneš sam. Sve što nije na
listi „smeš“ ne pokrećeš, čak i ako bi time „završio proveru“.

Pre svake komande proveri da li je na listi smeš. Ako nije, stani i reci osobi
pored robota šta bi komanda uradila. Čekaj da mentor kaže da sme.

Ne izmišljaj `map_id`, ime tačke, `point_id` ni metre. Prazno polje ostaje
prazno. `referee-spot` upisuješ samo ako se tačno tako zove na listi sa robota.

Ne menjaš kod, ne commituješ, ne pushuješ. Ne diraš `contracts/`, `core/`,
`api/`, `vision/`, `persona/`, frontend, `a2_nav.py`, `nav_missions.py`,
`motion_player.py`, `screen_manip`, `humanoid_platform/`.

Ne ulaziš u `tmux` sesiju `robot_supervisor`. Ctrl+C unutra gasi Supervisor.

Lozinka za SSH nije u git-u. Ne traži je da je upišeš u fajl. Ako je host ključ
drugačiji od `SHA256:5xSjsXk2r1ORLQpku2I1Jj80e614b9hycWLl3407S5M`, prekini i
reci osobi da pita mentora. Ne prihvataj novi ključ sam.

### Smeš (samo čitanje)

Na robotu, posle provere da si na PC2:

```bash
hostname -I
git -C /agibot/humanoid-platform log --oneline -1
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py status
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py doctor
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/status
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/pose
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/maps
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/arm
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/run
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/idle-motion
curl -sS --max-time 15 "http://127.0.0.1:8070/api/nav/gestures?refresh=true"
```

`status` i `doctor` pokrećeš iz `/agibot/humanoid-platform` ako je checkout
tamo. Ako `git -C` kaže da direktorijum ne postoji, nađi checkout koji
Supervisor stvarno izvršava i koristi taj put. Ne pretpostavljaj da je to
`main` sa GitHuba.

Sa laptopa, isti HTTP, druga adresa: `http://192.168.2.50:8070`. Port 8080 ne
koristi. Ako 8070 ne odgovori, zapiši to i stani. Ne pokreći Supervisor.

Ako je poza `null` a stream radi, smeš jednom da pročitaš kraj slam loga:

```bash
tail -n 40 /agibot/log/slam/running_slam.log
```

Tražiš reč `localization init failed`. Ne menjaš log i ne relokalizuješ sam.

Šta ekran trenutno vrti, samo ako mentor kaže da smeš da pogledaš PC1. Ovo je
čitanje procesa, ne paljenje klipa. Ključ je na robotu, ne u git-u:

```bash
ssh -i ~/.ssh/agibot_rsa -o StrictHostKeyChecking=yes agi@192.168.100.100 \
  "ps -eo args --no-headers | grep aimmaster.face/3rd_party/ffmpeg | grep -v grep"
```

Ako ključa nema ili SSH na PC1 traži nešto novo, preskoči ekran i ostavi polja
prazna.

### Ne smeš

Ove stvari pale noge, šalju cilj, puštaju ruku ili menjaju lice. Mentor ih radi
rukom ako dođu na red. Ti ih ne kucaš.

- `a2_nav.py arm --execute`
- `a2_nav.py goto … --execute`
- `a2_nav.py` bilo šta osim `status` i `doctor`
- `POST /api/nav/arm`, `/api/nav/prepare`, `/api/nav/disarm`
- `POST /api/nav/gestures/play`
- `POST /api/nav/idle-motion`
- `POST /api/nav/missions/.../run` i `POST /api/nav/run/cancel`
- `POST /api/nav/maps/.../waypoints` (pravi novu tačku)
- `show_message`, `PlayerEmoticon`, bilo koji curl ka `51048`, `51049`, `59001`
- `aima em stop-app`, `start-app`, `reset-app`
- gasenje balansa, motora, LiDAR-a, inflation-a, JetPack-a
- pokrivanje LiDAR-a ili kamera
- `PATCH` vizije, meč, govor, RAG

`goto` bez `--execute` je suvi pregled i sme samo u poslednjih 30 minuta, i
samo ako su kapije iz koraka B zelene. I dalje ne dodaješ `--execute`.

## Šta „ispravno“ znači

Dve provere. Obe radiš. Ne mešaš ih.

**Robot javlja usklađeno.** Dva izvora kažu isto, a lažna spremnost je
prepoznata. Izvori su `a2_nav.py status` / `doctor` i `GET /api/nav/status`.

**Mi smo prepisali doslovno.** Polje u `mentor_podaci.md` je isti string kao u
ispisu, iz istog reda. Prazno je ispravno dok podatak nije viđen. Popunjeno
polje kojeg nema u ispisu je greška prikupljanja. Ti je ispravljaš brisanjem
izmisljenog, ne dogovaranjem.

Ovo ne možeš da potvrdiš ni iz jednog ispisa: da tačka gleda u sto, metri od
ivice, da je mesto van zamaha reketa, da je hodnik na mapi, da kamera vidi
loptu, da je put prazan. To piše mentor na podu. Ako ti ne kaže, polje ostaje
prazno.

## Redosled (120 minuta)

Ne preskači na hod zato što je rano gotovo čitanje. Hod je poslednji i često
otpada.

| Minut | Šta | Ako padne |
|---|---|---|
| 0–10 | A. Identitet računara | Pogrešan PC ili ključ: kraj termina za komande. |
| 10–35 | B. Lokalizacija, mapa, noge, zadatak | Crvena kapija: nema hoda. Ostale provere i dalje smeju da se čitaju. |
| 35–50 | C. Lista tačaka i prepis u belešku | Ime se ne poklapa: ne diraj `config.local.yaml`. |
| 50–60 | D. Supervisor već nešto drži | Aktivan `run` ili tuđi ARM: ne traži gest ni hod. |
| 60–75 | E. Imena gestova | Imena nema u katalogu: u belešku ide `nema`. |
| 75–90 | F. Šta lice trenutno vrti | SSH na PC1 ne radi: preskoči, polja prazna. |
| 90–120 | G. Suvi `goto`, pa hod samo uz mentora | Bilo koja kapija iz B crvena: ne šalji cilj. |

Posle svakog bloka upiši u `mentor_podaci.md` samo ono što si video, i u chat
reci: prolaz, pad, ili prazno. Ne čekaj kraj da prepišeš.

## A. Identitet

```bash
ssh agi@192.168.2.50
hostname -I
```

Prolaz: u ispisu postoji `192.168.100.110`. To je PC2, Orin, jedini računar na
kom se ovo radi.

Pad: vidi se `192.168.100.100` a nema `192.168.100.110`. To je PC1. Izlaziš.
`whoami` i `hostname` ne razlikuju PC1 od PC2. Ne nastavljaš „samo da vidiš“.

Zatim:

```bash
git -C /agibot/humanoid-platform log --oneline -1
```

Taj hash upisuješ u zaglavlje `mentor_podaci.md`. Robot izvršava taj checkout,
ne commit sa laptopa.

## B. Kapije pre hoda

Tab Navigation u browseru (`http://192.168.2.50:8070`) mora biti otvoren pre
čitanja poze. Poza živi na `/tf` `map` → `base_link` i stiže samo dok je live
stream upaljen. Supervisor je baca iz keša posle 5 sekundi. Zato prisutna poza
jeste sveža. `status` starost poze ne ispisuje.

HTTP `TransFormService/GetTransFormation` ne zovi. Na ovom build-u vraća prazno
telo i zna da visi oko 10 s.

Pokreni `status`, `doctor`, `/api/nav/status` i `/api/nav/pose`.

| ID | Prolaz | Lažna spremnost, zapiši i ne hodi |
|---|---|---|
| B1 | `localization running : True` i `localization_running: true` | Samo jedan izvor kaže true, ili je jedan `unavailable` / `pnc_error` / `mc_error`. Ne popunjavaj iz onog koji jeste odgovorio. |
| B2 | `pose` nije `null` i `stream_running` je true. U belešku: `fresh_pose: da` | `localization running : True` bez poze. `isRunning` znači da sesija radi, ne da se uhvatila. Može da ostane u `localization init failed`. |
| B3 | Stream nije upaljen, pa je poza `null` | Ovo nije pad lokalizacije. Otvori tab Navigation i ponovi B2 jednom. I dalje `null` uz `stream_running: true` jeste pad: relokalizacija je sa tableta, radi je mentor. |
| B4 | `current working map` nije prazan i nije `0`, i isti id ima `<-- current` | Mapa postoji u spisku, ali nije trenutna. Hod ide na trenutnu. |
| B5 | `doctor`: E-stop clear | Bilo koji flag `wired`, `wireless` ili `software`. `status` E-stop ne ispisuje. |
| B6 | `can_walk` true. Akcija u imenu ima `LOCOMOTION` ili `NAVIGATION`. `work_state` je `McWorkState_ENABLED` | `McAction_DEFAULT`. Planer i dalje primi cilj i javi `RUNNING` dok robot stoji. Posle E-stop-a puštanje dugmeta vrati režim rada, a akciju ne vrati. Vraćanje akcije je mentorov `arm`, ne tvoj. |
| B7 | `doctor`: leg telemetry `live` | `FROZEN`. `is_walking: true` uz `FROZEN` je stara vrednost od trenutka kad je magistrala presečena. |
| B8 | `is_collisioned` nije true. Robot nije `HANGING` | Sudar ili robot na visilici. Ne šalji cilj. |
| B9 | `/api/nav/status` polje `blockers` prazno | Bilo koji blocker. Tekst blockera prepiši u belešku. Ne „popravljaj“ ga POST-om. |

`doctor` staje na prvom fatalnom uzroku. Ako je B5 ili B6 crveno, redove ispod
ne tumači kao „robot može da krene“.

U `mentor_podaci.md`: lokalizacija `da` samo ako B1 prolazi. `fresh_pose: da`
samo ako B2 prolazi. B1 bez B2 ostavlja `fresh_pose` prazan.

## C. Tačke i prepis

Iz `status` prepiši svaki red `target_id=… name=… xy=(…)`. Iz `GET /api/nav/maps`
proveri da je trenutna mapa ista.

Prolaz liste: bar jedna tačka ima `target_id` različit od 0, neprazan `name` i
oba broja u `xy`. Manje od dve tačke zapiši kao upozorenje: od tačke do tačke
nema šta da se pokaže. Ne pravi novu tačku.

Osoba pored robota i mentor izaberu jedan red pored stola. Ti proveriš da su
`name`, `point_id` i `x`/`y` u belešci tačno taj jedan red. `point_id` u
belešci je `target_id` sa ekrana, ne broj koji si sam dodelio.

`config.local.yaml` nije u git-u (`.gitignore`). Ako fajl već postoji na
robotu, pročitaj ga i proveri da pored `table-1` stoji samo taj `name`. Ako
unutra videš `map_id`, `point_id` ili metre, reci osobi. Ne briši tuđe izmene
dok ne kaže. Ako fajla nema, ne pravi ga u ovom terminu osim ako osoba izričito
kaže da upišeš jednu liniju imena. Primer u `table_tennis/config.example.yaml`
ne popunjavaš živim vrednostima.

`guide_line_id` ne tražiš dok hod bez njega ne padne. Podrazumevano ostaje 0.

Polazak nije tačka. Ne upisuj živi položaj kao ime waypointe. Pitaj mentora
usmeno: da li je robot u već mapiranoj kancelariji, i da li je prostor ispred
kancelarije na ovoj mapi. Ako drugo nije, probni hod kreće iz kancelarije.
Mapiranje hodnika nije ovaj kod.

Metre ne računaš iz `xy`. Mentor meri na podu. 1,5–2 m je ideja, ne upis. Dok
ne izmeri, `stop_distance_m`, „van putanje“ i „van zamaha“ ostaju prazni.

## D. Da li Supervisor već drži robota

```bash
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/run
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/arm
curl -sS --max-time 5 http://127.0.0.1:8070/api/nav/idle-motion
```

Prolaz: `run` nije aktivan. Niko drugi nema ARM. Idle sme da bude uključen dok
ne dođe gest; ne gasiš ga.

Pad: misija već hoda, ili je ARM tuđi. Ne traži gest i ne traži hod. Zapiši
`hold` / `runner` kako su vraćeni.

`pnc last task`: prepiši `task_id`, `state`, `info` dok robot miruje.

| Šta vidiš | Šta upišeš |
|---|---|
| `task_id` 0 ili prazan | `0` ili prazno. To nije misija i nije dokaz da je stigao. |
| `RUNNING` ili `PncServiceState_RUNNING` bez našeg id-a | Nije dolazak. Ako je B6 crveno, ovo je planer koji javlja hod u prazno. |
| `SUCCESS` uz pravi `task_id` različit od 0 | Kandidat za „stigao“, i to tek posle hoda koji smo sami poslali. Pre hoda je tuđi zadatak. |
| `CANCELED`, `CANCELLED`, `FAILED`, `FAILURE`, `IDLE`, `TIMEOUT` | Zaustavljeno stanje. Prepiši ime tačno. |

Prihvaćen RPC nije dolazak.

## E. Gestovi, samo imena

```bash
curl -sS --max-time 15 "http://127.0.0.1:8070/api/nav/gestures?refresh=true"
```

U nizu `gestures` traži tačno: `wave`, `point left`, `point right`, `nod thanks`.
`refresh=true` čita živu biblioteku sa robota, ne keš.

Za svako ime: ako je u nizu, prepiši ga i `durations_s` pored njega. Ako ga
nema, u belešku ide `nema`. Ne zamenjuj brojem iz stare beleške
(wave 1, nod thanks 8, point left 13, point right 14 su stari nalaz od
2026-08-03, ne dokaz da ime danas postoji).

`handshake` ne upisuješ u spisak koji šaljemo, čak i ako je u biblioteci.
`panel explanation` se ne koristi.

Levo i desno su sa strane robota, ne igrača. To ne proveravaš iz JSON-a. Samo
zapiši da je ime nađeno.

Trajanje: `durations_s` je reklamirana dužina. `wave` zna da bude duži od 6 s
zajedno sa povratom u neutralan stav. Ne skraćuj to u belešci na 6.

Puštanje gesta (`POST /api/nav/gestures/play`) nije ova provera. Ako mentor
kaže da sme jedan pokret: mentor drži E-stop, prostor oko ruke je prazan,
mentor pali ARM, jedna komanda, jedna kretnja. Ti tu komandu ne kucaš dok
osoba doslovno ne kaže „pošalji ovu“. Posle pokreta zapiši da li se ruka
pomerila. Ako Supervisor primi gest a ruka stoji, zapiši ARM i da treba pogled
u `/agibot/log/pnc_arm/pnc_arm.log`. Ne šalješ drugi gest da „prođe“.

Posle ~10 s mira robot sam okreće struk i glavu. To nije naš gest. Ne gasiš
idle da bi to sprečio, osim ako mentor to uradi.

## F. Ekran, samo šta već stoji na licu

Lice nema API koji crta string. Vrti mp4 preko `PlayerEmoticon`. Klip se vrti
dok ga neko izričito ne zameni podrazumevanim licem. Zato se u ovom terminu ne
pušta probni kadar osim ako mentor stoji pored i posle njega sam vrati lice.

Read-only prolaz je komanda iz dela „Smeš“ ka PC1. Prepiši putanju mp4 ako je
ffmpeg uopšte pokrenut. To je odgovor na „ko drži slot“: ako putanja nije naša,
slot je tuđi. Ne preuzimaj ga.

Slot koji nas zanima zove se `emoticon_ct_message`. Ako u procesu ne vidiš to
ime, ne zaključuj da je slobodan. Pitaj ko ga drži (sat, kviz, agent) i upiši
odgovor. Ako niko ne zna, polje ostaje prazno.

Jedan kadar, ako mentor pusti, nije tvoj curl. Posle njega lice mora da se
vrati. Ako ostane tuđi klip, zapiši i prestani. Ne zovi `51049` ni `59001`.

## G. Poslednjih 30 minuta

Ulaziš ovde samo ako su B1–B9 prolaz, C ima izabran red, D nema tuđu misiju, i
mentor kaže da je put slobodan i da drži E-stop.

Prvo suvo, bez hoda:

```bash
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py goto TARGET_ID
```

`TARGET_ID` je `target_id` iz izabranog reda, ne ime. Ako ispis ne liči na
suvi pregled ili traži potvrdu koja šalje cilj, prekini. Ne dodaj `--execute`.

`--execute` kuca mentor, ne ti. Posle zaustavljanja ponovi `status` i dopuni
korak o stanju zadatka u `mentor_podaci.md`: koji `target_id` je poslat, koji
`task_id` se vratio (0 ostaje 0), koji `state` je bio dok hoda i koji kad je
stao.

Dolazak za našu aplikaciju, kad jednom bude spojena, traži sve ovo odjednom:
isti `task_id` koji smo sačuvali, pozu mlađu od 5 s, i da je transport javio
`within_tolerance` i `settled`. Danas aplikacija taj hod ne šalje. Termin samo
beleži šta robot sam ispiše. Ne proglašavaj `ready` iz `RUNNING`.

Ako E-stop bude pritisnut usred hoda: ne šalješ ništa da „vratiš“ hod.
Pustiš mentor. Posle toga očekuješ `McAction_DEFAULT` dok mentor ručno ne
vrati akciju. To upiši kao da/ne, ne kao kvar koda.

## Kako da popuniš belešku

Fajl: `table_tennis/robot/mentor_podaci.md`.

- Svako polje ili ostaje prazno ili dobija string iz komande iznad.
- U chat posle bloka navedi ID provere (A, B1…B9, C, D, E, F, G) i jednu od
  reči: prolaz, pad, prazno.
- Na kraju nalepi kratak rezime: koje kapije su zelene, da li sme hod sledeći
  put, koje ime ide u dugme, šta je ostalo prazno jer nije mereno na podu.

U `config.example.yaml` ne ide ništa sa ovog termina.

## Šta svesno ostaje za drugi dan

Ovo ne pokušavaš da „stigneš“ u ova dva sata:

- spajanje `table_tennis` na `a2_nav` (`mode: real` i dalje nema transport);
- dugmad u browseru „Put je slobodan“ i „Robot je stigao“;
- meč, poeni, govor, vizija, RAG;
- mapiranje prostora ispred kancelarije;
- više od jednog gesta i više od jednog hoda.
