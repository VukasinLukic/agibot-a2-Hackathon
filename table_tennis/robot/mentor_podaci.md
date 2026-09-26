# Termin sa robotom — šta pokupiti (navigacija)

Redosled, komande i šta sme da pokrene drugi LLM:
`NAVIGACIJA_2H_LLM_PLAN.md` u korenu repozitorijuma.
Ovde se samo upisuju vrednosti. Prazno polje ostaje prazno.

Dva sata. Prvo samo čitanje. Hod, ekran i gest tek uz mentora.
Prazno polje ostaje prazno. Ne upisivati primer `referee-spot` kao ime sa mape.

Posle termina:

- cela ova beleška ostaje ovde;
- u `table_tennis/config.local.yaml` (nije u git-u) ide samo ime tačke, pored `table-1`;
- `map_id`, `point_id` i metri ne idu u dugme.

Datum:
Commit na robotu (`git log --oneline -1` u checkout-u koji robot stvarno izvršava):
Firmware (ako `status` ili mentor kaže):

## 0. Da li smo na pravom računaru

Sa laptopa: `ssh agi@192.168.2.50`.

- `hostname -I` mora da sadrži `192.168.100.110` (PC2). Ako vidiš samo `192.168.100.100`, to je PC1: izađi.
- Ako se host ključ promenio u odnosu na `SHA256:5xSjsXk2r1ORLQpku2I1Jj80e614b9hycWLl3407S5M`, stani i pitaj mentora.

## 1. Jedna read-only komanda

Iz checkout-a na robotu:

```bash
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py status
```

Ništa ne šalje. Prepiši ove redove.

| Red na ekranu | Polje |
|---|---|
| `localization running :` | da / ne. Ako je `False`, waypoint hod ne može. Stani ovde za hod. |
| `current working map  :` | `map_id` |
| stored maps, red sa `<-- current` | `map_name` |
| `MC work_state` | da li je `McWorkState_ENABLED` |
| `MC action` | da li u imenu ima `LOCOMOTION` ili `NAVIGATION`. `McAction_DEFAULT` ne hoda. |
| `walking / collided` | oba |
| `pnc last task` | `task_id`, `state`, `info` |
| svaki red `target_id=… name=… xy=(…)` | cela lista, ne samo jedna tačka |

Ako ispis deluje čudno, druga read-only komanda, i dalje bez hoda:

```bash
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py doctor
```

Zapiši da li je E-stop pritisnut. `status` E-stop ne ispisuje. `doctor` ispisuje.

## 2. Sudijska tačka

Sa liste iz koraka 1 izaberi jednu tačku pored stola, onu koju mentor potvrdi. Ostale ostaju kao komentar, ne kao dugme.

- name (jedino ovo kasnije ide u `config.local.yaml`):
- point_id (na ekranu je `target_id`):
- x:
- y:
- da li gleda u sto, i na koju stranu: da / ne

`referee-spot` nije ime sa mape dok ga neko nije tako sačuvao na tabletu.

## 3. Polazak

Polazak nije tačka u kodu. To je živi položaj u trenutku poziva.

- da li je robot sada unutar već mapirane kancelarije: da / ne
- da li prostor ispred kancelarije postoji na ovoj mapi: da / ne

Ako je drugo `ne`, probni hod kreće iz kancelarije. Mapiranje hodnika je mentorov posao, ne naš kod.

## 4. Metri od stola

`status` ovo ne zna. Mentor meri na podu. 1,5–2 m je samo ideja.

- od ivice stola do mesta na kom robot stoji (m):
- van putanje igrača: da / ne
- van zamaha reketa: da / ne

Ta udaljenost se ne upisuje kao pomeraj u kodu. Mesto na kom robot sme da stane sačuva se kao waypoint (korak 2). Metri su samo tolerancija dolaska, i ostaju u ovoj belešci.

## 5. Da li je poza sveža

`localization running : True` nije sveža poza. `status` ne ispisuje starost poze.

- da li mentor vidi `/tf` `map` → `base_link` mlađi od 5 s: da / ne / nije provereno

Ako je `nije provereno` ili `ne`, hod do tačke se ne šalje. HTTP `GetTransFormation` ne koristiti: na ovom build-u vraća prazno telo i zna da visi.

## 6. Šta na robotu znači „stao je”

Prihvaćen RPC nije dolazak. `task_id=0` nije misija. Globalni `RUNNING` nije dolazak.

Sa reda `pnc last task` dok robot miruje, i ako se u koraku 8 ipak krene, ponovo posle zaustavljanja:

- state dok hoda:
- state kad je cilj stvarno dostignut:
- posle E-stop-a akcija ostaje `McAction_DEFAULT` dok je mentor ručno ne vrati: da / ne

## 7. Ekran, samo ako mentor pusti

Jedan kadar, pa vraćanje podrazumevanog lica. Ne ostavljati klip da vrti.

- ko trenutno drži slot `emoticon_ct_message`:
- da li sme da ostane naš tokom meča: da / ne
- da li agent (sat, kviz) sme da ostane uključen: da / ne

## 8. Jedan gest, samo ako mentor pusti

Mentor drži E-stop i uključuje ARM. Prostor oko ruke je prazan. Jedan pokret, ne serija.

Tražimo ime sa živog spiska, ne broj. Ako imena nema, upiši `nema`.

- wave (mah po dolasku):
- point left (poen igraču sa robotove leve):
- point right (poen igraču sa robotove desne):
- nod thanks (zahvalnost na kraju):

`handshake` se ne šalje.

## 9. Hod, samo ako su koraci 1–5 popunjeni i mentor kaže da je put slobodan

Ne kroz aplikaciju. Aplikacija ovaj hod još ne šalje.

```bash
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py goto TARGET_ID
```

Bez `--execute` ovo je suvi pregled. `--execute` tek kad mentor drži E-stop i put je prazan. `arm --execute` ne pokretati sami.

Posle zaustavljanja ponovo `status` i dopuni korak 6.

- target_id koji je poslat:
- da li je robot stao na tački: da / ne
- task_id koji je robot vratio (ako je 0, zapiši 0, ne izmišljaj drugi):

## 10. Kamera sa tog mesta

Grudi se pomeraju kad robot maše. Ovo je beleška za osobu 1, ne naš hod.

- bolje oko: CHEST_LEFT_FISHEYE / CHEST_RIGHT_FISHEYE / nije gledano
- lopta se vidi sa tačke, bez primicanja u zamah: da / ne / nije gledano

## Šta jedan chat može da proveri

LLM nije na robotu. Radi u ovom chatu, na laptopu. Dok je laptop na mreži robota, chat može da pokrene iste read-only komande preko SSH, ili da pročita ispis koji ti nalepiš. Ne uključuje lokalizaciju, ne pritiska ARM i ne šalje `goto --execute`. Ti ili mentor uključite lokalizaciju i otvorite tab Navigation; chat tek onda čita.

RAG, govor i ostali prekidači na supervisoru nisu ova provera. Za navigaciju važe samo tri čitanja ispod. Chat ih poredi među sobom i sa ovom beleškom. Ako se dva izvora ne slažu, beleška se ne popunjava dok se ne vidi koji je svežiji.

### Tri čitanja

Sva tri su bez hoda.

1. Na PC2, iz checkout-a koji robot izvršava:

```bash
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py status
python3 robot_services/autonomous_navigation/testing_controls/a2_nav.py doctor
```

2. Supervisor već radi na robotu (`http://192.168.2.50:8070`). Tab Navigation mora biti otvoren, jer poza postoji samo dok je live stream upaljen. Chat onda čita:

```bash
curl -sS http://127.0.0.1:8070/api/nav/status
curl -sS http://127.0.0.1:8070/api/nav/pose
```

`127.0.0.1` je sa samog robota. Sa laptopa je ista stvar na `http://192.168.2.50:8070`.

3. Imena gestova, ako mentor pusti korak 8:

```bash
curl -sS http://127.0.0.1:8070/api/nav/gestures
```

### Da li robot dobro javlja

Chat ovo može da kaže iz ispisa. Ne može da vidi sobu.

| Šta gledamo | Prolaz | Robot javlja, ali to nije spremnost |
|---|---|---|
| Računar | `hostname -I` sadrži `192.168.100.110` | Vidi se `192.168.100.100`. To je PC1. „resource not found“ na navigaciji često znači pogrešan računar. |
| Lokalizacija | `status` kaže `True`, a `/api/nav/status` ima `localization_running: true` i prazan `blockers` | `isRunning: true` samo znači da sesija radi. Može da ostane u petlji `localization init failed` i da ne da pozu. |
| Poza | `/api/nav/pose` vraća `pose`, a `stream_running` je true. Keš baca pozu stariju od 5 s, pa prisutna poza jeste sveža. | Stream nije upaljen, pa `pose` je `null`. To nije dokaz da lokalizacija nije uspela. Prvo otvori tab Navigation. Ako je stream upaljen a `pose` i dalje `null`, lokalizacija nije uhvaćena: relokalizacija sa tableta. |
| Mapa | `current working map` nije prazan i nije `0`, i isti `map_id` ima oznaku `<-- current` | Mapa u spisku postoji, ali nije trenutna. Hod ide na trenutnu, ne na onu koju izaberemo iz sećanja. |
| Noge | `doctor`: E-stop clear, `can_walk` true, telemetrija `live`, `work_state` je `McWorkState_ENABLED` | Planer javlja `RUNNING` dok je akcija `McAction_DEFAULT` ili je telemetrija `FROZEN`. Prihvaćen zadatak, robot stoji. `is_walking: true` uz `FROZEN` je stara vrednost, ne korak. |
| Tačke | Lista ima bar jednu tačku sa `target_id`, `name` i `xy` | Manje od dve tačke: od tačke do tačke nema šta da se pokaže. Prazno ime nije ime. `point_id` 0 se ne uzima. |
| Zadatak | `task_id` je broj različit od 0, a `state` se prepiše kako je ispisan | `task_id=0` nije misija. `SUCCESS` bez pravog id-a nije dolazak. `RUNNING` sam po sebi nije dolazak. |
| Dva izvora | `status` i `/api/nav/status` kažu isto za lokalizaciju, mapu i akciju | Jedan kasni ili je jedan poziv pukao (`unavailable`, `pnc_error`, `mc_error`). Beleži se greška, ne izmišlja se vrednost. |

`doctor` staje na prvom fatalnom uzroku. Ako ispiše E-stop ili `MC action is McAction_DEFAULT`, dalji redovi o hodu se ne tumače kao „robot može da krene“.

### Da li mi dobro prepisujemo

Kad su tri čitanja u chatu, a polja u ovoj belešci popunjena, chat poredi belešku sa sirovim ispisom. Prolaz je doslovno poklapanje, ne „otprilike“.

- `map.map_id` je tačno `current working map`, a `map_name` je ime sa reda `<-- current`.
- `localization` je `da` samo ako oba izvora kažu da radi. `fresh_pose` je `da` samo ako je `/api/nav/pose` vratio pozu. `True` na lokalizaciji ne popunjava `fresh_pose`.
- Izabrani red: `name`, `target_id` i `xy` su iz istog reda liste. `point_id` u belešci je taj `target_id`, ne redni broj koji smo sami dodelili.
- Ime u `config.local.yaml` pored `table-1` je to isto `name`, karakter po karakter, uključujući razmake. U taj fajl ne idu `map_id`, `point_id` ni metri.
- `referee-spot` ulazi samo ako se tačno tako zove na listi. Ako ga nema, u dugme ide ime koje jeste na listi.
- Polazak ostaje prazan kao waypoint. Živi položaj se ne zapisuje kao ime tačke.
- Metri, „gleda u sto“ i „van zamaha“ ostaju prazni dok mentor ne izmeri. Chat te tri stvari ne sme da popuni iz `xy`.
- `task_id` 0 ostaje 0. Ako komanda ne vrati id, polje ostaje prazno.
- Gest: upisano ime postoji u `GET /api/nav/gestures`. Ako ga nema, piše `nema`, ne broj iz stare beleške. `handshake` se ne prepisuje u spisak koji šaljemo.
- Commit u zaglavlju je `git log --oneline -1` iz checkout-a na robotu, ne commit sa laptopa.

Chat posle toga kaže šta je prepisano, šta je prazno i šta se ne slaže. Prazno je ispravno stanje dok podatak nije viđen. Popunjeno polje koje nije u ispisu je greška prikupljanja.

### Šta chat ne potvrđuje

- Da tačka gleda u sto, koliko je metara od ivice i da li je van zamaha. To je mera na podu.
- Da je prostor ispred kancelarije na mapi. To kaže mentor gledajući mapu, ne jedan `map_id`.
- Da kamera vidi loptu. To je osoba 1, na licu mesta.
- Da će hod uspeti. Čist `blockers` znači da robot sme da primi cilj, ne da je put prazan. Put i E-stop ostaju na mentoru.

## Šta se ne radi na ovom terminu

- Ne dirati LiDAR, inflation ni JetPack.
- Ne gasiti balans ni motore kao čišćenje.
- Ne pokrivati LiDAR ni kamere.
- Ne puštati meč, govor ni viziju. To je ostatak tima posle ovih podataka.
- Ne kopirati ove brojeve u `config.example.yaml`.
