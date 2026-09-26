# Prompt za Claude Code — zajednička osnova za četiri člana

Ovaj prompt je spreman za Claude Code sesiju u lokalnom repozitorijumu, sa modelom koji tim izabere. Sadrži implementacione zadatke, zajedničke ugovore i proverljiv kriterijum završetka.

Prvo u sesiju priložite ovaj paket planova. Pokrenite ga jednom kao integrator, pre kreiranja četiri radne grane. Plan je da Claude implementira zajedničku osnovu, a članovi nastave na njoj. Ovo nije zahtev da Claude odmah implementira sve četiri funkcije ili kontroliše fizički robot.

## Prompt za kopiranje

Od sledeće linije do kraja odeljka „Kraj prompta” kopiraj tekst:

---

Radi u repozitorijumu:

`C:\Users\Tea\OneDrive\Dokumenti\a2-hackathon`

Pravimo A2 robota sudiju za stoni tenis sa četiri paralelne grane:
- comp-vision: kamera, kalibracija, loptica, predlozi poena;
- backend: pravila, rezultat, API, događaji, persistence;
- navigation: poziv robota, navigacija, ekran, gestovi;
- persone: aplikacija, glas, neutralna i korporativna persona.

Tvoj zadatak sada je da IMPLEMENTIRAŠ ZAJEDNIČKU OSNOVU koja radi lokalno bez robota, kamere i plaćenih API ključeva. Osnova treba da omogući svakoj osobi da radi od prvog sata.

### Obavezno pročitaj

1. Root i ugnježdene AGENTS.md koji važe za menjane fajlove.
2. docs/table_tennis_plan/README.md.
3. docs/table_tennis_plan/05_SHARED_CONTRACT.md — autoritativni contract v1.
4. docs/table_tennis_plan/01_COMP_VISION.md, 02_BACKEND.md, 03_NAVIGATION.md, 04_PERSONE.md.
5. docs/table_tennis_plan/06_REUSE_AUDIT.md — mapiranje već implementiranih funkcija i poznatih rupa.
6. Postojeće Supervisor API/frontend entrypoint-e i relevantne testove pre dodavanja integracije.

Planirane putanje i API nazivi u planu su specifikacija novih delova, ne tvrdnja da već postoje. Proveri stvarnu strukturu. Ako se razlikuje, napravi minimalno prilagođavanje i zabeleži ga; nemoj menjati ugovor bez obrazloženja i ažuriranja svih fixtures/tipova.

### Prvo proveri Git i okruženje

Pročitaj status, remote i trenutnu granu. Očekivani origin je teodorajovanovac/a2-hackathon; stariji folder Downloads je drugi repo. Sačuvaj korisničke promene i nepraćeni Dokumenti/. Nemoj prebacivati origin, čistiti worktree, force-pushovati ili automatski kreirati/pushovati grane. Kreiranje četiri grane dolazi posle zajedničkog proverljivog commit-a, od istog SHA.

Radi lokalne izmene i proveri ih. Ne commituj/pushuj bez eksplicitnog zahteva u sesiji. Ako Git stanje ne dozvoljava rad, prvo istraži bezbedan način koji čuva postojeće izmene.

### 1. Napravi paket i vlasništvo

Dodaj table_tennis/ sa podpaketima:
contracts, core, storage, api, sim, vision, robot, persona.

Napravi:
- README sa konkretnim Windows PowerShell i Linux komandama;
- config.example.yaml bez tajni;
- minimalne requirements-dev.txt i odvojeni vision requirements ako je potreban;
- feature flag i mode=mock kao default;
- tests/table_tennis/ podeljen po komponentama;
- imenovane konfiguracione opcije umesto hardkodovanih tokena/IP adresa.

Nemoj instalirati ceo root requirements.txt: on sadrži postojeće platform-specifične pakete. Za mock demo dovoljne su lake backend/test zavisnosti; GPU, ROS i LiveKit se ne uvoze pri običnom importu paketa.

### 2. Contract i primeri

Implementiraj Pydantic modele, enumeracije i diskriminisane payload unije prema 05_SHARED_CONTRACT.md:
Player, MatchConfig, MatchSnapshot, PointProposal, VisionObservation, RobotCall, RobotStatus, CommandEnvelope, EventEnvelope, CommandResult, ErrorResponse i CreateMatchRequest.

Ne koristi left/right kao identitet igrača. Čuvaj stabilne p1/p2, court_end_by_player, robot_side_by_player i assignment_version. Uključiti schema_version, UUID identifikatore, UTC vreme, expected_revision, kalibraciju i rally_id.

Komande i događaji su različiti tipovi. CV šalje point.propose; samo backend emituje point.confirmed. source/actor validira server.

Iz modela izvezi JSON Schema/OpenAPI. Generiši TypeScript tipove za React u feature/generated/. Dodaj reproduktivan generator i test/komandu za proveru da generisani fajlovi nisu zastareli. Ne održavaj ručno dve verzije istog ugovora.

Napravi validne fixture fajlove/scenarije iz ugovora. Fixture mečeve generiši kroz legalne komande, ne proizvoljnim score JSON-om.

### 3. Minimalni stvarni engine

Implementiraj mali RefereeEngine i RefereeService za jedan singl gem:
- kreiranje, start i rally.arm;
- ručno point.award i let;
- point.propose i eksplicitni point.confirm u assisted režimu;
- rezultat do 11 uz dva razlike, servis posle dva poena i posle svakog na 10:10;
- pause/resume sa ugovorenim poništavanjem aktivne razmene;
- undo poslednjeg aktivnog poena, uključujući završni;
- persona change između razmena i bez score promene;
- mapiranje strana i kalibracije u pauzi;
- readiness;
- best_of različit od 1 eksplicitno odbiti.

Nema heuristike „loptica nestala pa poen”. Automatic scoring flag je isključen i u bootstrap verziji vrati jasnu grešku ako ga neko uključi; osoba 2 ga kasnije uvodi tek uz benchmark osobe 1.

Minimalni core neka bude mali i testiran; složene CV, navigacione i LLM implementacije ostaviti za vlasnike grana.

### 4. Događaji i trajnost

SQLite transaction čuva ulaznu komandu, emitovane događaje, snapshot i outbox. Dodaj:
- isti command_id/sadržaj daje isti rezultat i ne duplira poen;
- isti command_id/drugi sadržaj je conflict;
- provera command idempotency pre expected_revision;
- najviše jedna finalna odluka po rally_id;
- redosled po revision/server cursor-u;
- oporavak iz event log-a;
- undo kao novi događaj, bez brisanja istorije;
- jedan writer; thread/process strategiju jasno ograniči na podržan deployment.

Side effects ne blokiraju score transakciju. Fake display pamti najnoviji snapshot; fake govor i gest beleže event_id i revision. Stari outbox gestovi se ne ponavljaju pri restartu. Za nepoznat ishod stvarnog fizičkog pokreta ne obećavaj exactly-once.

### 5. Pokretljiv demo API

Koristi isti feature router u:
1. samostalnom mock app-u bez startovanja Supervisor hardvera;
2. tankoj, podrazumevano isključenoj Supervisor integraciji.

Implementiraj rute iz contract-a pod /api/table-tennis. Fake navigator vraća call_id, moving/ready/failed/busy/cancel stanja; simulacija jasno označena. Nema slanja zahteva na robot.

SSE šalje snapshot i događaje bez rupe između snapshot-a i pretplate; reconnect podržava cursor ili resync. Duplikat event_id ne izvršava ponovo izlaz. Ne menjaj semantiku postojećeg globalnog Supervisor /api/events.

Mock server bind 127.0.0.1. Za real mrežnu integraciju koristi provereni postojeći auth, ili jasno odbij pokretanje na javnoj adresi bez konfiguracije. CV actor sme samo predlagati, UI operator sme potvrđivati; persona nema upis rezultata.

### 6. Protocol-i i fake adapteri

Napravi konkretne interfejse i radne fake implementacije za:
VisionProducer, RobotNavigator, ScoreDisplay, GestureOutput, SpeechOutput, EventStore, Clock, IdGenerator.

Vision stub prihvata fixture i emituje pravi validirani point.propose. Robot stub prikazuje koji call/gest/rezultat bi izvršio. Persona stub koristi determinističke replike bez cloud poziva.

Scaffold robot/persona adaptera treba da bude mali: konstruktor, Protocol implementacija, lažni izlaz, test i obeleženo mesto za real implementaciju. Prazan pass nije prihvatljiv kao završen mock.

### 7. Minimalni frontend

Koristi postojeći React/Vite projekat. Dodaj features/table-tennis/ sa:
- tipizovanim API klijentom;
- izborom mock/live endpointa kroz konfiguraciju;
- unosom imena, prvog servera, persone i scoring mode (manual/assisted);
- scoreboard i server marker;
- call robot i statusom;
- start, rally.arm, poen p1/p2, confirm predlog, let, undo, pause/resume;
- jasnim prikazom asistiranog i simuliranog režima;
- reconnect/stale/error indikatorima.

Prikaz koristi samo backend snapshot. Ne računaj servis u React-u. Dugmad koriste command_id za retry iste namere; 409 dovodi do resync-a, ne do slepog ponovnog poena.

Ovo je funkcionalna osnova za osobu 4, bez finalnog dizajna ili svih sportsko-humorističkih komentara.

### 8. Lokalni alati za četiri člana

Obezbedi i dokumentuj stvarno proverene komande, ciljano ovih oblika:
- python -m table_tennis.run_demo --mode mock
- python -m table_tennis.sim.run --scenario manual-game --api http://127.0.0.1:8099
- python -m table_tennis.sim.run --scenario disputed-point --api http://127.0.0.1:8099
- python -m pytest tests/table_tennis
- komanda za regeneraciju schema/TS tipova.

Ako promeniš CLI oblik, ažuriraj sva uputstva i proveri nove komande. Ciljni port 8099 je podesiv, a zauzet port treba prijaviti.

Dodaj kratke AGENTS.md instrukcije u nove module sa vlasništvom:
- osoba 1 vision;
- osoba 2 contracts/core/storage/api/sim;
- osoba 3 robot;
- osoba 4 persona i frontend feature.
Zajednički contracts i Supervisor registraciju menja integrator.

### 9. Provere

Obavezni testovi:
- 0:0, servis sekvenca, 10:10, 11:10 nije kraj, 12:10 jeste;
- duplicate command, duplicate rally proposal sa drugim command_id;
- confidence van opsega i nepoznat igrač;
- zastarela revizija, kalibracija i strana;
- proposal ne menja rezultat;
- let ne menja rezultat/server;
- undo poslednjeg/finalnog poena i zastareli event posle undo;
- istovremene komande: samo jedna prihvaćena za istu reviziju;
- sqlite restart vraća stanje, ali ne replay-uje govor/gest;
- SSE reconnect ne duplira izvršenja i ne gubi promenu na snapshot granici;
- nema hardverskih import-a/poziva u mock modu;
- contract fixtures i generisani TS konzistentni;
- frontend typecheck/build.

Kreni od runnabilnog vertikalnog prolaza i dodaj slojeve; nemoj ostaviti ceo scaffold na nepovezanim praznim klasama.

### 10. Definition of done

Bez robota i API ključeva mogu:
1. pokrenuti mock backend i UI;
2. kreirati meč;
3. pozvati fake robota;
4. početi meč, armirati rally i dodeliti poen;
5. videti isti rezultat u API-ju, UI-ju i fake screen log-u;
6. dobiti regularni ili korporativni komentar sa tačnim rezultatom;
7. poslati fake CV predlog i potvrditi ga;
8. ponoviti komandu bez drugog poena;
9. uraditi undo i 10:10 scenario;
10. restartovati proces i dobiti tačno stanje bez ponovljenih gestova.

Završi izveštajem: kreirani fajlovi, komande za pokretanje, stvarno izvršeni testovi i rezultat, šta je mock, šta je ostavljeno za svaku od četiri grane i koji commit tim treba da napravi pre grananja. Nemoj tvrditi da nešto radi na A2 bez fizičkog testa.

---

## Kraj prompta

## Kako tim prihvata osnovu

Integrator proverava demonstraciju iz Definition of done. Tek nakon toga pravi zajednički commit i deli SHA. Četiri osobe granaju od tog SHA prema README-u. Svaka dobija svoj dokument i može Claude Code-u dati završnu instrukciju: „Implementiraj fazu 1 iz mog plana, koristi postojeći contract, nastavi dok testovi te faze ne prođu.”

Posebno proveriti da scaffold nije uvezao GPU/ROS u core i da nije automatski uključio pravi robot. To omogućava lokalni razvoj na četiri računara.
