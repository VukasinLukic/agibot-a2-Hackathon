# Osoba 2 — pravila meča, backend i integracija

Grana: `backend`. Ti održavaš zajedničku osnovu i jedini izvor rezultata. Od tvog malog stabilnog API-ja zavise tri nezavisna modula, ali njihov razvoj ne treba da čeka fizički robot.

Prvo koristi [Claude Code prompt](00_CLAUDE_CODE_PROMPT.md), zatim [contract](05_SHARED_CONTRACT.md). Ovaj plan nastavlja scaffold; nemoj ponovo praviti već generisane modele.

## Vlasništvo

`table_tennis/contracts/`, `core/`, `storage/`, `api/`, `sim/`, pripadajući testovi, schema generator i Supervisor registracija. Ti koordiniraš generisane TS izmene sa osobom 4.

Ne obrađuješ video i ne izvršavaš motor komande u engine-u. Domain core treba da radi kao čista funkcija state + command -> events/new state. RefereeService dodaje validaciju identiteta, zaključavanje i storage.

## Faza 0 — osnova za sve

Pre grananja pokreni prompt 00, proveri mock demo i sačuvaj zajednički commit. Članovima pošalji SHA, API adresu, putanju ugovora i komande. Zatim pređi na backend granu.

Prvog sata zajedno potvrdite:
- četiri stable player/side polja i perspektivu robota;
- point.propose/confirm i razliku između komande i događaja;
- jedan gem u MVP-u;
- Pydantic -> schema -> TypeScript;
- protokol robot/persona adaptera;
- mala SQLite baza i jedan API proces.

Ako neko još nema okruženje, daj mu validirani fixture JSON i mock API. Ne uslovljavaj start kamere dostupnošću punog Supervisora.

## Faza 1 — pravila

Pregledaj scaffold implementaciju i dopuni edge cases:
1. P1/P2 stabilni kroz ceo meč.
2. Poeni 0 ili više; target 11; pobeda tek uz dva razlike.
3. first_server i next server po formuli u contract-u.
4. match status setup, between_rallies, rally, pending_decision, paused, finished.
5. Jasni guard uslovi za komande, posebno award tokom pause/finished.
6. Let ne dodaje poen i ne menja servis.
7. Persona ne učestvuje u obračunu pobednika.
8. best_of različit od 1 je unsupported u v1.

Ne podržavati arbitrarnu promenu rezultata „set score” bez istorije. Za MVP undo poslednjeg poena i ponovni tačan award kroz novi rally obezbeđuju korekciju. Ako kasnije uvodiš administrativnu rekonstrukciju, mora imati audit događaj.

Test matrica:
| Ulaz | Očekivanje |
|---|---|
| 10:9 -> poen P1 | kraj 11:9 |
| 10:10 -> P1 | 11:10, nije kraj |
| 11:10 -> P2 | 11:11, nastavak |
| 11:10 -> P1 | kraj 12:10 |
| isti award dva puta | jedan poen |
| dva ID-a komande za isti završen rally | nema drugog poena |
| undo završnog poena | gem ponovo otvoren, server izveden ponovo |
| let na servisu | score/server isti, novi rally mora biti armiran |
| sides.set | score ostaje uz player_id |
| regular -> corporate | identičan rezultat |

## Faza 2 — race uslovi i trajnost

Sve komande idu kroz jedan RefereeService:
- autorizacija aktera;
- lookup command_id i payload hash;
- identičan retry vraća originalni odgovor;
- expected_revision validacija;
- validacija rally/assignment/calibration;
- domain transition;
- jedna transakcija za komandu, događaje, snapshot i outbox.

Jedan gem ne može imati dva prihvaćena poena iz iste revizije kroz istovremene operator/CV pozive. Testiraj konkurenciju, ne samo sekvencu. Dokumentuj single-worker deployment; ako uvodiš više worker-a moraš dodati stvarnu DB zaštitu, ne samo asyncio.Lock.

Undo ne briše originalni poen: target označava neaktivnim kroz događaj, replikuješ aktivnu istoriju i dobijaš tačan server. Novi događaji podignu revision. Aktivni novi rally pri undo se invalidira, a stari CV predlog odbija.

Restart rekonstruiše match state. Event replay nije instrukcija da robot ponovo odigra sve gestove. DB i snimci su van Git-a; test koristi privremenu bazu.

## Faza 3 — API i SSE

Dovrši rute iz contract-a. Jedan prefix, jedan tipizovan klijent. Nemoj dodavati alternativni /score endpoint koji zaobilazi commands.

Za SSE reši race između početnog snapshot-a i registracije subscriber-a. Test koristi događaj emitovan baš na toj granici. Lagging/reconnect klijent dobija cursor replay ili resync snapshot. Event ID i revision imaju različite uloge; ista revizija može imati više događaja.

UI i robot prvo traže snapshot; ne izvode stanje samo iz događaja od trenutka povezivanja. Rezultat potvrdi tek kada je transaction commit završen, pa emituješ SSE.

Error ugovor:
- 409 conflict uz aktuelnu reviziju;
- 422 nevalidan schema/payload;
- 401/403 neovlašćen actor;
- 404 nepoznat match.
Greška transporta ne znači da komanda nije upisana; retry koristi isti command_id.

SSE/GET i command endpoint-i moraju imati doslednu autentifikaciju u mrežnom režimu. Test vision actor -> point.award mora pasti. Public persona content nije credential.

## Faza 4 — koordinacija izlaza

Orchestrator pretvara post-commit događaje u odvojene zadatke:
- scoreboard refresh;
- kratka najava poena;
- dozvoljeni gest;
- poseban komentar po personi.

Screen snapshot može sustići najnoviju reviziju. Govor i gest imaju kraći vek: preskoči stari komentar kada počne sledeća razmena. Undo invalidate pending tasks, zatim isporuči novu score reviziju. Nikad ne blokiraj transaction čekajući SSH/TTS.

Osoba 3 poseduje real robot implementaciju, osoba 4 speech; ti ugovaraš lifecycle, dedup i callback status. Adapter failure pokazati u health/UI; poen ostaje sačuvan.

Robot ready i kamera ready su uslovi režima, ne samo statična polja. Ako kamera otkaže u assisted/automatic, pauziraj prihvatanje CV i prikaži operatoru stanje. Manual score može ostati dostupan prema eksplicitnoj odluci operatora, bez skrivene promene režima.

## Faza 5 — automatizacija

Početni assisted režim: svaki predlog mora biti potvrđen. Operator ne treba da nagađa da li je sistem nešto automatski uradio; snapshot jasno pokazuje režim i pending proposal.

Automatic uvodiš tek nakon benchmark-a osobe 1:
- konfigurisani prag, dopušteni reasons i verzija modela/kalibracije;
- validni aktuelni rally/strane;
- svež capture;
- dovoljni opaženi događaji, ne samo predikcija;
- nema automatskog zaključka nakon timeout-a potvrde.
Prag 0.85 iz starog PRD-a je početna ideja, ne dokaz pouzdanosti.

## Faza 6 — integracija u Supervisor

Postojeći api/main.py je centralni registracioni fajl. Jednom dodaj feature router iza flag-a; standalone demo ostaje upotrebljiv bez Supervisor lifecycle-a i njegovih hardverskih procesa.

Robot call rute popunjava osoba 3 kroz adapter. Persona modul šalje samo output, ne novu score komandu. Existing /api/events je sistemski status i nije dovoljan match event log.

Za stvarni robot koristi postojeće okruženje i odvojene procese za ROS/vision; ne instaliraj platform requirements u mock venv.

## Kriterijum završetka

1. Ručni i asistirani gem rade od starta do finish-a.
2. Test matrica pokriva skor, servis, lifecycle, undo, concurrency i restart.
3. API/fixtures/TS schema su usklađeni.
4. Jedan kompletan demo radi sa fake robot/voice.
5. Pravi adapteri mogu se zameniti fake-ovima konfiguracijom.
6. Greška jednog izlaza ne gubi poen i ne resetuje meč.
7. Commit diff ne sadrži tajne i runtime bazu.

## Handoff i PR redosled

PR1 engine hardening; PR2 storage/replay/concurrency; PR3 transport/auth/SSE; PR4 output orchestration i Supervisor feature wiring; PR5 optional automatic gate.

Integrator spaja male PR-ove ostalih i objavljuje novi main SHA. Tim merge-uje origin/main u svoju granu. Ne prepisuj ugovor privatno na kraju hakatona: svaka promena ide kroz schema generator, fixtures i sva četiri smoke testa.

Pred demonstraciju izvozi manifest: commit SHA, configuration mode, schema/model/calibration version i poznate granice. Ne navoditi da je motor testiran ako je samo prošao fake adapter.

