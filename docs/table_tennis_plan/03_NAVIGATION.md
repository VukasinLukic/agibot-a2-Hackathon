# Osoba 3 — poziv, navigacija, ekran i gestovi na A2

Grana: `navigation`. Tvoja komponenta daje fizički oblik sudiji: prihvata poziv do poznatog stola, javlja spremnost, prikazuje rezultat i pokazuje osvajača poena.

Pročitaj [contract](05_SHARED_CONTRACT.md), [audit](06_REUSE_AUDIT.md), root AGENTS.md i njime navedene robot dokumente. Postojeći kod ima prednost nad zastarelim tvrdnjama u dokumentaciji, ali postojanje koda nije potvrda da funkcija radi na trenutnom robotu.

## Vlasništvo

`table_tennis/robot/`, robot adapter testovi i namenski robot call router. Osoba 2 poseduje registraciju u Supervisor main entrypoint-u; usaglasi taj mali edit da izbegnete konflikt.

Predloženi fajlovi:
- navigator.py: RobotNavigator adapter i call lifecycle;
- readiness.py: provere i objašnjivo ready/busy/failed;
- score_display.py: trajni scoreboard i red najnovije revizije;
- gesture_output.py: player-to-side, dedup i lifecycle;
- coordinator.py: zajedničko vlasništvo kretanja/gesta;
- fake.py: dry-run implementacije;
- README.md: platform configuration i rezultat stvarnih provera.

Osloni se na postojeći nav_missions.py, a2_nav.py, gesture_api.py/motion_player.py, screen_manip i humanoid_platform. Ne kopiraj Unitree locomotion/arm skripte za A2.

## Faza 0 — samostalan start

1. Napravi navigation granu od zajedničke osnove.
2. Pokreni fake adaptere i pošalji potvrđen fixture point.
3. Log mora pokazati match_id, event_id, revision, winner_id, robot-side i score.
4. Simuliraj busy, failed, moving, ready i cancellation.
5. Proveri da import i pokretanje u mock modu ne pravi SSH, HTTP-RPC, AIMA ili ROS poziv.

ROBOT_ENABLE=0 pomaže postojećim izlazima, ali mock ne treba ni da konstruiše real adapter. HEAD_SCREEN_ENABLED nije dovoljan globalni safety gate za direktan poziv screen modula. To proveri testom sa spy/failing transportom.

Prvog sata možeš završiti adapter port i fake demo potpuno bez robota.

## Faza 1 — mapa postojećih servisa i konfiguracija

Razvojni proces radi na PC2. PC1 je kontroler/host ekrana: dopuštene uske integracije postojećih servisa i dokumentovane resource putanje; tamo ne instalirati novu aplikacionu bazu niti menjati motor konfiguraciju.

Utvrdi:
- model agibot_a2_ultra preko registry-ja;
- aktivnu mapu i sačuvani waypoint sudijske pozicije;
- kako teku pozicija i njen timestamp;
- koji gestovi se stvarno razrešavaju u live katalogu;
- trenutno vlasništvo ekrana i AIMA audio servisa;
- ko sme pokrenuti/cancelovati jedinu aktivnu misiju.

Dokumentuj konfiguraciju bez tajni. Sama vrednost localization isRunning=true ne potvrđuje ispravnu lokalizaciju; potreban je svež validan pose i stanje lokalizacije.

## Faza 2 — ekran kao prvi fizički izlaz

Postojeći A2 ekran reprodukuje MP4. show_message_async je flash API i nakon trajanja vraća default face. Za sudiju rezultat treba da ostane do sledeće potvrđene promene.

Implementiraj poseban scoreboard režim adaptera:
1. Proveri/provision-uj reusable resource slot van trenutka poena.
2. Jedan screen worker prima snapshot-e.
3. Latest revision wins za isti match; zastareli render se odbacuje pre playback-a.
4. Skor traje do narednog rezultata ili završetka meča; reset face radi se namerno na izlazu.
5. Undo ažurira score i briše čekajuće zastarele render zadatke.
6. Novi match resetuje lokalni revision watermark.
7. API update potvrđuje upis stanja odvojeno od potvrde prikaza na fizičkom ekranu.

Default tekst: skraćena imena + veliki rezultat + ko servira. Za imena sa č/ć/š/ž/đ proveri renderer/sanitizer; dogovorena transliteracija na ekranu dozvoljena, puno ime ostaje u UI/glasovnom kontekstu.

Postojeći agent flash sata/quiz-a može prepisati scoreboard. Tokom meča uvedi eksplicitno vlasništvo ekrana/lease i koordinaciju sa drugim pozivima. Ne dozvoli dva nekontrolisana procesa za isti slot.

Testovi: dve brze revizije, duplikat, undo, promena meča, failure rendera, default-face restore samo pri release. Unit test koristi fake transport; fizički smoke test proverava i vidljivost sa stola.

## Faza 3 — gestovi za poen i pozdrav

Koristi A2 curated katalog: point left/right, wave, nod thanks; proveri da konkretni resource IDs postoje. Preset ime u kodu nije dokaz da isti preset postoji na drugom firmware-u.

Gest je objašnjiv signal „igrač na ovoj strani dobio je poen”; postojeći pointing preset nije automatski identičan zvaničnom sudijskom podizanju podlaktice. Tačan novi pokret je bonus i zahteva posebnu validaciju.

winner_id -> robot_side_by_player -> gest. Ovo mapiranje operater potvrđuje na mestu. Ako igrači zamene strane, assignment_version i oba mapiranja moraju se ažurirati.

Postojeći gesture API može vratiti accepted dok je pokret samo u redu. Dodaj adapter status i ne predstavljaj accepted kao completed. Queue sa starim gestovima ne sme se odigravati za nekoliko poena unazad.

Predlog:
- jedan kratak gest između razmena;
- dedup event_id+gesture;
- TTL i provera aktualnog match/revision pre izvršenja;
- undo poništava pending gest; započeti fizički gest se ne „poništava” inverznim pokretom;
- tokom navigacije i aktivne razmene gestovi se ne pokreću;
- povratak u neutralan položaj; ne držati podignutu ruku duže vreme.

Rukovanje je opciona pozdravna interakcija. Naziv handshake u katalogu ne garantuje force-controlled hvatanje; ne praviti custom stisak prstiju. Mentor proverava preset i prostor pre ljudskog kontakta. Wave ostaje funkcionalna zamena.

## Faza 4 — poziv iz aplikacije

POST robot/calls prima named waypoint iz allowlist-e, table_id i command_id. Ne prima proizvoljne koordinate, shell ili naziv motor režima iz javnog UI-ja.

Životni ciklus:
requested -> validating -> moving -> arrived -> ready
ili failed / cancel_requested -> cancelled.

Svaki poziv ima call_id i stvarni native task_id gde postoji. Duplicate command vraća isti call. Jedan robot može imati samo jednu aktivnu misiju. Drugi poziv dobija busy sa opisom.

Aplikacija može se zatvoriti, a server i dalje vodi lifecycle. Nemoj staviti petlju navigacije u browser. Cancel endpoint znači zahtev; cancelled tek kada je stanje potvrđeno. Programski cancel nije hardverski E-stop.

Dolazak nije isto što i RPC success. Proveri identitet zadatka, svežu pozu, toleranciju položaja/orijentacije i stabilno zaustavljanje pre robot_ready=true. Ako pouzdanu telemetriju nije moguće dobiti, prijavi need_operator_confirmation.

## Faza 5 — navigacija na postojećem kodu

Audit je pronašao konkretne razloge za dodatni adapter:
- MissionRunner.start trenutno može armirati hod pre svih ostalih provera;
- _wait_terminal nema definisani timeout i prati global state;
- fresh pose zavisi od ROS sidecar-a koji ne sme zavisiti od otvorenog browser taba;
- stari a2_nav uvodni komentar i noviji nav_missions kod imaju različit nivo tvrdnji o live verifikaciji.

Zato redosled treba da bude:
1. Validiraj zahtev, waypoint i ekskluzivno vlasništvo robota.
2. Proveri emergency state, motor action, lokalizaciju, svežu pozu i potrebne servise.
3. Operator potvrđuje da je ruta slobodna pre fizičkog testa; humanoid ne kreće samo zato što je stigao neovlašćen HTTP zahtev.
4. Koristi postojeće podržano upravljanje misijom; nemoj nezavisno pokretati drugi runner.
5. Drži pose sidecar lease tokom cele misije.
6. Zapamti task_id i prati njegov stvarni napredak; global RUNNING nije dovoljan.
7. Postavi ukupni timeout, stale-pose timeout i proveru da napredak postoji.
8. Na failure/cancel zatvori task/lease/pollere uz potvrdu stanja. Ne isključuj balans/motore stojećem robotu kao „cleanup”.
9. Posle E-stop-a ne pokušavaj automatski da vratiš walking mod; potrebna je provera i kontrolisan postupak oporavka.

Tačne udaljenosti/tolerancije određuje se sa mentorom prema prostoru. Ranijih 1.5–2 m je ideja, ne univerzalna bezbedna granica. Robot ne sme biti u putanji igrača ili zamaha reketa; vidljivost kamere se rešava bez smanjenja safety envelope-a.

Ne menjati LiDAR zaštite, inflation radius ili JetPack radi demo-a.

## Faza 6 — kompletan robotski ciklus

Fake/real adapteri imaju isti contract. Tok:
call -> confirmed arrival -> operator ready -> pozdrav -> match start -> score display -> point gesture/announcement -> ready for rally -> finished -> release screen.

Backend čuva rezultat čak i ako robot output padne. UI pokazuje screen_unavailable ili gesture_failed; ne lažira izvršenje.

Fallback:
- navigacija nedostupna: ručno postavljanje i eksplicitna oznaka manual arrival;
- gesture nedostupan: rezultat na ekranu i glas;
- ekran nedostupan: web scoreboard i glas;
- robot offline: ceo software demo nastavlja sa fake adapterom i jasnom oznakom.

## Testovi i isporuka

Mock testovi:
- nijedan hardware transport poziv;
- player-side mapiranje;
- dedup/stale output i undo;
- robot busy;
- native task mismatch;
- pose stale i navigation timeout;
- cancel status razlikuje accepted od completed;
- screen držanje rezultata i release.

Hardware smoke protokol sa mentorom:
- read-only readiness;
- kratak poznati screen sadržaj;
- jedan odobren gest na slobodnom prostoru;
- odvojeno testirana ruta do waypointa;
- cancel i ponovno uspostavljanje ready stanja;
- ceo workflow u poznatim uslovima.

Zabeleži datum, robot/firmware ako je dostupan, commit, šta je stvarno testirano, šta je ostalo mock. Isporuka nije dokaz automatske bezbednosti u bilo kojoj prostoriji.

## PR redosled i handoff

PR1 fake adapters i readiness; PR2 screen; PR3 gestures; PR4 call lifecycle + fake navigacija; PR5 real nav integracija i live test zapis. Fizičke funkcije iza eksplicitnog flag-a.

Osobi 2 daješ readiness/call status i svoje rute za registraciju. Osobi 4 status izvršenja, potrebnu perspektivu levo/desno i ograničenja dužine najave. Osobi 1 javljaš da li se kamera pomera dok robot izvodi pokrete.

