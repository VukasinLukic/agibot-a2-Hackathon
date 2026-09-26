# Konfiguracija navigacije (faza 1)

Bez tajni i bez izmišljenog waypointa. Izvor su postojeći moduli, ne živi robot.
Stavke koje čekaju mentorov termin su na dnu. Ne popunjavati ih nagađanjem.

## Utvrđeno iz koda

### Model

Registry `humanoid_platform` ima `agibot_a2_ultra` (`AGIBOT_A2_ULTRA_MODEL`).
Gestovi idu na `AGIBOT_A2_MOTION_PLAYER`, katalog `agibot_a2_ultra`.
Ekran glave ide na `AGIBOT_EMOTICON_PLAYER` (`PlayerEmoticon`), ne na Unitree LED.
Zvuk u modelu je lokalni bridge na razvojnom računaru (PC2). PC1 se ne koristi
za novu bazu niti za izmenu motorne konfiguracije.

Ovo je model koji kod očekuje. Nije potvrda da je baš taj uređaj uključen u sali.

### Položaj i vreme

Trenutni položaj nije polazna tačka u našem kodu. To je živa poza na mapi.

Poza stiže preko ROS2 `/tf` (`map` → `base_link`) u sidecar `a2_nav_stream`.
`NavRelay.last_pose` čuva poslednju pozu i trenutak prijema. Starija od 5 s
vraća se kao da je nema. Stari HTTP poziv za pozu je prazan i ne koristi se.
Sidecar živi dok ga neko drži; običan hod ga ne drži sam ako niko ne gleda mapu.
`readiness.py` tretira pozu stariju od 5000 ms kao `stale_pose`.
`localization isRunning=true` nije dovoljan dokaz svežeg položaja.

### Gestovi koje kod traži

Ime u katalogu nije vendor ID. Pokret se traži po engleskom nazivu na živom
`GetMotion` spisku. Za sudiju kod očekuje:

| Naša namena | Ime u katalogu | Hint koji kod traži |
|---|---|---|
| Pozdrav po dolasku | `wave` | Wave hand_right hand |
| Poen igraču sa robotove leve strane | `point left` | Direction_point to the left |
| Poen igraču sa robotove desne strane | `point right` | Direction_point to the right |
| Zahvalnost | `nod thanks` | Nod head |

`handshake` postoji kao preset, ali nije hvatanje. Za pozdrav ostaje `wave`.
Katalog je upoređen sa jednim A2 spiskom 3. avgusta 2026. To nije provera ovog termina.

### Ko pokreće i gasi jedinu misiju

Poziv kreće od operatera (`POST /robot/calls`), sa imenom waypointa sa liste.
Server drži jedan aktivan poziv. Drugi dobija `robot_busy`.
Otkazivanje je zahtev; `cancelled` važi tek kad je stanje potvrđeno.
Pravi hod, kad se spoji, mora da pamti `task_id`. `task_id=0` ne zaustavlja robota.
Programski cancel nije E-stop. Posle E-stop-a se hod ne uključuje sam.
Ne pokreće se drugi runner pored postojećeg `MissionRunner`.

Ekran u kodu drži `robot_services/screen_manip` (jedan MP4 slot). Tuđi flash
(agent, sat, kviz) može da ga prepiše. Zvuk drži AIMA `agent` dok se namerno
ne preda našem bridge-u. Ko ih drži u konkretnom terminu nije utvrđeno.

## Faza 2 u kodu

Ekran više nije samo dve linije teksta. `score_display.py` drži jedan slot
`emoticon_ct_message` od kreiranja sesije, ne u trenutku poena. Jedan worker
pusti samo najnoviju reviziju; starija čekanja se odbace pre reprodukcije.
Duplikat se ne pušta ponovo. Undo briše nepuštene kadrove. Novi meč
(`status=setup`) resetuje lokalni watermark. Upis kadra (`accepted`) i
prikaz (`shown`) su odvojeni. Podrazumevano lice se vraća samo na `release`.
Drugi worker na isti slot se odbija.

Fizička provera da li se skor vidi sa stola, i ko u sali drži taj slot dok
agent pali sat ili kviz, i dalje čekaju termin. Ovaj kod ne zove ekran robota.

## Faza 3 u kodu

Gest se kači na backendov `point.confirmed` i `match.finished`. Strana dolazi
iz snapshot-a (`robot_side_by_player`), istog polja koje operator potvrdi u
meču. Persona i dalje izgovara poen. Vizija ne pokreće gest.

Jedan worker pusti samo najnoviji nepušteni gest. Stariji se odbace. Isti
`event_id` se ne ponavlja. Gest stariji od 20 s se ne pušta. Undo briše red.
Započet pokret se ne poništava suprotnim. Dok je poziv u hodu, ili je razmena
aktivna, gest se ne pušta. Kad poziv stigne u `ready`, jednom se maše (`wave`).
Kraj meča je `nod thanks`. `handshake` se ne šalje.

Imena u kodu nisu id-jevi firmware-a. Da li se `wave`, `point left`,
`point right` i `nod thanks` i dalje razrešavaju na robotu, ostaje za termin.

## Čeka termin sa mentorom

Ne raditi ove stavke dok nema robota. Ne upisivati privremena imena kao prava.

- Identifikator aktivne mape kancelarije. Mentor je mapirao kancelariju i sto.
  `map_id` nije prepisan.
- Ime waypointa pored stola na toj mapi. U konfiguraciji `referee-spot` je
  primer iz osnove, ne tačka sa mape. Zameniti ga tek pravim imenom.
- Da li početak van kancelarije uopšte stoji na mapi. Trenutno se ne vidi.
  Ako nije na mapi, robot odatle ne može da krene. Polazak i dalje nije
  waypoint u kodu: to je živi položaj. Cilj je waypoint pored stola.
- Da li je položaj na ovom robotu svež: `/tf` stvarno šalje `map` → `base_link`,
  i da li `isRunning=true` prati ispravnu pozu, ne samo uključen servis.
- Da li se `wave`, `point left`, `point right` i `nod thanks` i dalje razrešavaju
  na firmware-u u sali.
- Ko u terminu drži ekran glave i AIMA zvuk, i da li smeju da ostanu uključeni
  dok robot hoda i pokazuje rezultat.
