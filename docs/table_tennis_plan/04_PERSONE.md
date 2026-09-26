# Osoba 4 — aplikacija, glas i dve persone

Grana: `persone`. Ti praviš iskustvo od poziva sudije do proglašenja pobednika. Korisnik treba da razume stanje i ima brzu korekciju, a komentari treba da budu usklađeni sa tačnim rezultatom.

Pročitaj [contract](05_SHARED_CONTRACT.md), [audit](06_REUSE_AUDIT.md) i [Git pravila](README.md).

## Vlasništvo

`table_tennis/persona/`, namenski prompt/template fajlovi, `robot_supervisor_v2/frontend/src/features/table-tennis/`, frontend feature registracija i pripadajući testovi. Generisane tipove dobijaš od osobe 2.

Ne praviš lokalni autoritativni score u browseru. Prikazuješ MatchSnapshot. UI left/right label mapira na stable p1/p2 preko contract-a, a glas koristi imena.

Predložene celine:
- components/Setup, Scoreboard, OperatorControls, PendingDecision, RobotCallStatus;
- api/client, api/events;
- persona/templates, persona/commentator, persona/speech_output;
- mocks/feature preview zasnovan na zajedničkim fixtures.

## Faza 0 — prvih 60 minuta

Pokreni minimalni UI i mock backend iz scaffold-a. Otvori new_match, pending_proposal, deuce i finished fixtures. Napravi početni ekran i scoreboard bez zavisnosti od kamere.

Testiraj jedan ručni poen -> novi snapshot -> ekran sa istom revision. Vežbaj reconnect i 409 odmah, da ne razviješ UI koji optimistično uvećava skor bez potvrde.

Na kraju prvog sata pokaži: dva imena, rezultat, marker servera, dugmad poen/undo/pauza i izbor persone.

## Faza 1 — setup i poziv

Setup:
- imena igrača;
- prvi server;
- mapiranje end_a/end_b i robot left/right;
- regular/corporate persona;
- manual/assisted režim;
- sto/named waypoint;
- opciona ručno uneta titula za humor;
- dugme Pozovi sudiju sa call statusom;
- fallback Robot je ručno postavljen;
- spremnost igrača/operatora.

Ne tražiti stvarne HR pristupne podatke. Za demo je dovoljna dobrovoljno uneta uloga. Rank ne pretpostavljati iz izgleda, glasa ili imena.

Nakon poziva prikazati requested/validating/moving/ready/failed/busy, sa Cancel. Ne prikazuj „stigao” odmah posle HTTP 202. Native telemetry ostaje u debug sekciji; igrač vidi „Dolazim”, „Spreman”, „Potrebna pomoć”.

## Faza 2 — operaterski ekran

Scoreboard sadrži imena, velike poene i server marker. U dnu stanje i poslednja potvrđena odluka. Odvojeno prikaži predlog: „Predlog: Ana je osvojila poen — potvrdi”.

Dugmad:
- Započni;
- Spremni / rally.arm;
- Poen Ana i Poen Marko;
- Potvrdi predlog;
- Let / ponovi razmenu;
- Undo prethodnog poena;
- Pauza / nastavi;
- Završi.

Na malom ekranu moraju imati razmak i smislen disabled state. Score u podešenom redosledu imena; fizičku stranu obeleži tekstom ako može biti dvosmislena.

Spremi command_id na početku namere; retry iste namere zadržava ID. Disable dugmeta smanjuje grešku, ali backend idempotency je prava zaštita. Kod 409 preuzmi snapshot i pokaži korisniku da ponovi odluku na aktuelnom stanju. Ne prenosi stari winner automatski na novi rally.

Undo navodi koji poen poništava. Ako je odluka već promenjena, prikaži konflikt. Ne lokalno oduzimati rezultat pre odgovora.

Assisted ne postaje automatski samo zato što je countdown istekao. UI jasno označava mock, manual ili assisted. Ne prikazuj vrednost 0.91 kao „91% garantovano tačno”; može biti samo model score.

## Faza 3 — realtime i offline stanja

Koristi tipizovani API klijent i SSE stream iz contract-a. Prvo dobijaš snapshot/cursor, zatim događaje. Reconnect resync ne treba da animira sve stare poene ili ponovi stare replike.

Stanje UI:
connecting, live, reconnecting, stale, error.
Zadnji rezultat može ostati vidljiv uz „Veza prekinuta”; tasteri za mutacije disabled dok nema aktuelne revizije. Ne čuvaj offline niz point komandi koji će iznenada biti poslat kasnije.

Neznatan SSE heartbeat nije promena poena. Ista score revizija može imati point.confirmed i match.finished — ne duplirati poen ni najavu.

U mrežnom režimu auth mora raditi i za SSE. Sa bearer tokenom koristi fetch streaming ili postojeći podržani session pristup; ne stavljaj token u query URL.

## Faza 4 — govor bez cloud zavisnosti

Najpre napravi determinističke šablone koji rade sa fake speech adapterom:
- match start;
- point confirmed;
- server change;
- deuce;
- uncertain/pending;
- score correction;
- match won;
- navigation ready/failed.

Rezultat i server sastavljaj iz snapshot-a. Najbolje je odvojiti tačnu obaveznu score rečenicu od slobodnog komentara, tako da generisanje humora ne može promeniti broj.

Primeri:
| Događaj | Regularna persona | Korporativna persona |
|---|---|---|
| Poen Ani, 4:3 | „Poen Ana. Četiri prema tri.” | „Ana osvaja poen. Ovaj potez ide u kvartalni izveštaj. Četiri prema tri.” |
| Marko nadigra direktora | „Poen Marko.” | „Poen Marko. Nadam se da ovo neće uticati na sledeći performance review.” |
| 10:10 | „Deset prema deset. Servis se menja posle svakog poena.” | „Deset prema deset. Potrebna je odluka upravnog odbora — i dva poena razlike.” |
| Nejasan poen | „Nisam siguran ko je osvojio poen. Molim potvrdu.” | „Organizaciona šema mi je jasna, ova loptica nije. Molim potvrdu poena.” |
| Undo | „Ispravka. Rezultat je pet prema četiri.” | „Revizija je završena. Zvanično: pet prema četiri.” |

Kratak komentar ide između razmena; dug monolog prekida ritam igre. Početno ograniči humor na povremene poene i prelome, a skor izgovori redovno. Gledaoci treba da razlikuju poen od šale.

## Faza 5 — dve persone

Regular:
- neutralan ton;
- isti odnos prema igračima;
- jedna kratka score najava;
- objašnjenje na zahtev.

Corporate:
- eksplicitno izabran zabavni režim;
- teatralno dodvoravanje višoj unetoj funkciji;
- dobronamerne replike bez vređanja pojedinca;
- tačan rezultat ostaje autoritativan.

Favorizovanje u ovom planu odnosi se na komentar, ton i pohvalu. Ako tim odluči da šala treba stvarno da menja bodove, to je posebna vidljivo označena parodijska scoring politika koju implementira osoba 2, ne skrivena LLM instrukcija. U v1 nema takve politike.

Imena/role su nepoverljiv korisnički sadržaj, ne sistemske instrukcije. Ograniči dužinu, renderuj ih kao tekst u React-u i ne interpoliraj u shell, SSML ili ffmpeg expression. U promptu ih prenesi kao podatke.

## Faza 6 — povezivanje postojećeg LiveKit glasa

Reuse: agent_main command stream, postojeći audio/TTS stack i prompt servis. Tačne adapter ulaze proveri u auditu i aktuelnim zvaničnim LiveKit dokumentima pre menjanja SDK koda, kako traži AGENTS.md.

Feature-gated referee mode treba da:
- ima jednog vlasnika govora;
- ućutka opšti startup/person-detected pozdrav i neželjene RAG/quiz radnje tokom meča;
- isključi nezavisno automatsko gestikuliranje iz LLM-a, jer osoba 3 kontroliše sudijske pokrete;
- ne dozvoli agent time/quiz flash-u da prepiše scoreboard;
- dobije samo događaj i snapshot koji su potrebni za govor;
- podrži cancel_pending na undo i preskoči zastarele komentare.

LLM opcionalno generiše kratak komentar iz fakata. Deadline/budget definisati konfiguracijom; ako kasni, odmah koristi template. Na izlazu meča zaustavi referee mode i vrati prethodno agent ponašanje.

Brojeve za TTS formatiraj postojećim normalizerom ili deterministički; testiraj „jedanaest prema devet” i srpska imena. Screen i govor ne moraju deliti isti transliterisani tekst.

Glasovna korekcija je operaterska namera koja traži potvrdu uz konkretan rally/event. Slobodan razgovor ili rečenica „daj mi poen” ne smeju direktno menjati rezultat.

## Testovi

Frontend:
- autoritativni snapshot jedini izvor skora;
- pending kandidat ne povećava rezultat;
- dupli klik/timeout koristi isti ID;
- 409 vraća resync;
- offline dugmad disabled, stanje označeno;
- strane se promene, skor prati igrača;
- mobile layout, čitljiv server marker;
- TypeScript build i basic component/integration provere.

Persona:
- svi template primeri sadrže tačan score i pobednika;
- corporate ne menja snapshot;
- nepoznat rank ne izaziva grešku;
- imena sa prompt-like tekstom ostaju podaci;
- stale event posle undo/restart-a ne govori;
- LLM timeout aktivira lokalni fallback;
- komentari ne počinju dok je rally aktivan.

## Definition of done i PR-ovi

Moguće je odraditi ceo meč iz aplikacije sa fake robotom, videti predlog i ispravku i čuti dve različite persone kada je audio adapter dostupan. Bez LLM ključa šabloni i scoreboard rade. Stvarni hardware status ne prikazuje se kao potvrđen ako je samo mock.

PR1 UI feature sa fixtures; PR2 typed API/SSE i kontrole; PR3 templates/fake speech; PR4 LiveKit adapter i izolacija referee mode-a; PR5 demo UX i failure scenarios.

Osobi 2 predaješ tačne komande i eventualne contract predloge. Osobi 3 predaješ redosled pozdrav/gest/govor i potrebu za screen ownership. Osobi 1 daješ panel u kom se vide poslednji kandidat i njegova evidencija, bez lažnog obećanja automatske tačnosti.

