# Podaci sa robota — sudijsko mesto

Popuniti na terminu, čitanjem sa robota. Prazno polje ostaje prazno.
Ne upisivati primer `referee-spot` kao ime sa mape.

Popunjen zapis ide u `table_tennis/config.local.yaml` (nije u git-u).
U dugme ulazi samo `waypoint.name`, pored stola `table-1`.
`map_id`, `point_id` i metri ne idu u poziv iz browsera.

Model koji kod očekuje: `agibot_a2_ultra`.
Hod: postojeći `PlanningNaviToGoal` (`map_id` + `target_id` + `guide_line_id`).
Poza: ROS2 `/tf`, `map` → `base_link`, starija od 5 s se baca.
Ekran: slot `emoticon_ct_message`. Gestovi se traže po `display_name_en`, ne po broju.

Datum:
Robot / firmware:
Commit:

## 1. Mapa

Aktivna mapa (ne nagađati; `current working map` sa robota):

- map_id:
- map_name:
- da li je ovo mapa na kojoj robot trenutno radi: da / ne
- da li mapa pokriva samo kancelariju: da / ne

## 2. Polazak

Polazak nije waypoint u kodu. To je živi položaj u trenutku poziva.

- da li tačka ispred kancelarije uopšte postoji na ovoj mapi: da / ne
- ako ne postoji: robot odatle ne može da krene; meč počinje ručnim postavljanjem

## 3. Sudijska tačka pored stola

Sa `GetTopoMsgs` za map_id iznad. Hod šalje `point_id` kao `target_id`, ne ime.

- name (jedino ovo ide u dugme):
- point_id:
- x:
- y:
- da li tačka gleda u sto, i na koju stranu:
- guide_line_id ako hod bez nje ne prolazi (inače 0):

`referee-spot` u `config.example.yaml` je primer liste. Zameniti ga ovim `name` tek kad je pročitano sa mape.

## 4. Koliko daleko sme da stane

Ranijih 1,5–2 m je ideja, ne granica. Upisati ono što mentor odmeri u sali.

- stop_distance_m od ivice stola:
- da li je van putanje igrača: da / ne
- da li je van zamaha reketa: da / ne
- tolerancija položaja (m) pre nego što kažemo da je stigao:
- tolerancija ugla, ako je bitna:

## 5. Položaj u trenutku termina

`isRunning=true` nije dokaz. Treba svež `map` → `base_link`.

- localization isRunning: da / ne
- `/tf` šalje map → base_link: da / ne
- starost te poze (ms):
- da li je poza na mapi, a ne u praznom: da / ne

## 6. Gestovi

Tačan `display_name_en` sa živog `GetMotion` spiska, ili „nema”.

| Namena | Tražimo u kodu | Nađeno ime |
|---|---|---|
| Mah jednom po dolasku | wave / Wave hand_right hand | |
| Poen igraču sa robotove leve | point left / Direction_point to the left | |
| Poen igraču sa robotove desne | point right / Direction_point to the right | |
| Zahvalnost na kraju meča | nod thanks / Nod head | |

`handshake` se ne šalje. Nije hvatanje reketa.

## 7. Ko drži ekran, zvuk i jedinu misiju

- ko drži slot `emoticon_ct_message` dok traje meč:
- da li agent (sat, kviz) sme da ostane uključen: da / ne
- ko drži AIMA zvuk:
- ko sme da pusti i otkaže jedinu misiju:
- ko gleda da je put slobodan pre hoda (kod traži operatera):

## 8. Šta na robotu znači „stao je”

Prihvaćen RPC nije dolazak. `task_id=0` nije misija. Globalni `RUNNING` nije dolazak.

- naziv statusa zadatka kad je cilj stvarno dostignut:
- naziv statusa dok još hoda:
- da li posle E-stop-a hod ostaje ugašen dok ga neko ručno ne vrati: da / ne

## 9. Kamera sa tog mesta

Grudi se pomeraju kad robot maše ili pokazuje. Javiti osobi 1.

- bolje oko sa sudijske tačke: CHEST_LEFT_FISHEYE / CHEST_RIGHT_FISHEYE
- da li se lopta vidi sa te tačke, bez primicanja u zamah: da / ne

## 10. Redosled provere na robotu

Ništa od ovoga se ne radi dok polja iznad nisu pročitana. LiDAR, inflation i JetPack se ne diraju. Balans i motori se ne gase kao čišćenje.

1. Samo čitanje: mapa, poza, E-stop, da li sme da hoda.
2. Kratak poznat kadar na ekranu, pa povratak lica tek na release.
3. Jedan odobren gest na praznom prostoru.
4. Jedna ruta do `point_id`, posle potvrde da je put slobodan.
5. Otkaz te rute, pa ponovo spreman robot.
6. Ceo krug: poziv, dolazak, mah, meč, skor, gest poena, kraj, puštanje ekrana.
