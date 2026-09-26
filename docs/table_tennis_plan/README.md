# Plan implementacije A2 sudije

Ovaj paket omogućava četiri paralelna toka razvoja od prvog sata. Prvo jedna osoba pokreće zajednički scaffold pomoću Claude Code prompta; zatim svi granaju isti provereni commit. Svako može razvijati svoj modul bez robota, kamere ili tuđeg nedovršenog servisa.

Dokumenti predstavljaju plan i instrukcije za buduću implementaciju. Predloženi paketi, endpointi, testovi i CLI komande još nisu implementirani ovim paketom. Postojeći kod i stanje potvrđeni su lokalnim pregledom; fizički robot nije testiran.

## Redosled čitanja

1. [00 Claude Code prompt za zajedničku osnovu](00_CLAUDE_CODE_PROMPT.md) — kopirati ceo blok u Claude Code.
2. [05 Zajednički ugovor](05_SHARED_CONTRACT.md) — obavezno za sve četiri osobe; autoritativna specifikacija.
3. Svako uzima svoj plan: [01 Vizija](01_COMP_VISION.md), [02 Backend](02_BACKEND.md), [03 Navigation](03_NAVIGATION.md), [04 Persone i aplikacija](04_PERSONE.md).
4. [06 Audit postojećeg koda](06_REUSE_AUDIT.md) — rezultat zasebnog subagenta sa dokazima i ograničenjima.
5. [07 Stanje implementacije](07_IMPLEMENTATION_STATUS.md): šta je zajednička osnova stvarno isporučila i šta ostaje po granama. Komande: `table_tennis/README.md`.

## Repozitorijum i četiri grane

Pregledana lokacija: `C:\Users\Tea\OneDrive\Dokumenti\a2-hackathon`.
Zatečeno: `main`, commit `519ce20`, origin `https://github.com/teodorajovanovac/a2-hackathon`. Folder `Dokumenti/` već je bio nepraćen i pripada korisniku.

Ovo nije ranija Downloads kopija sa originom `leksaas/a2-hackathon` i granom `a2-hackathon-team2`. Plan pretpostavlja da novi timski fork koristi `main` za integraciju. Organizatorski main se ne menja. Ako organizatori zahtevaju predaju na team granu, integrator predaje finalni provereni commit tamo kao odvojen završni korak.

| Osoba | Grana | Vlasništvo |
|---|---|---|
| 1 | `comp-vision` | `table_tennis/vision/`, snimci/kalibracija, vision testovi |
| 2 | `backend` | contracts, core, api, storage, simulator i integracija |
| 3 | `navigation` | `table_tennis/robot/`, adapteri za poziv, navigaciju, ekran, gestove |
| 4 | `persone` | `table_tennis/persona/`, namenski promptovi, frontend feature |

Razmak nije dozvoljen u Git nazivu grane, pa je „comp vision” standardizovan na `comp-vision`. „Osoba 2” koristi `backend`. Nazivi `navigation` i `persone` prate zahtev.

## Zajednička osnova pre razdvajanja

Osoba 2 je integrator i prvi pokreće prompt iz dokumenta 00. Rezultat treba da bude pokretljiv mock demo: pravila, ručni poeni, API, SSE, fixture događaji, fake robot i fake govor, osnovni UI, testovi i uputstva. Scaffold nosi 0–0 do kraja jednog gema, ali ne donosi automatske CV odluke.

Integrator pregleda i sačuva osnovu u jedan provereni commit na timskom main-u. Delite taj SHA sa sva četiri člana. Tek onda nastaju četiri grane. Ovo je kratka zajednička priprema; kamera, pravi robot i produkcioni govor nisu preduslov.

Ako scaffold nije gotov na početku, paralelno radite: osoba 1 snima kameru, osoba 2 završava ugovor, osoba 3 čita adaptere i pravi dry-run, osoba 4 razrađuje ekran i replike koristeći primer snapshot-a.

## Git postupak za svakog

Komande se izvršavaju u sopstvenoj kopiji repozitorijuma, posle objavljivanja osnove:

```bash
git status --short --branch
git remote -v
git fetch origin
git switch --no-track -c comp-vision origin/main
git push -u origin comp-vision
```

Osobe 2–4 zamenjuju naziv sa `backend`, `navigation` ili `persone`. Ako grana već postoji na originu, koristi `git switch --track origin/comp-vision`; ako postoji lokalno, samo `git switch comp-vision`. Najpre sačuvaj svoj nezavršen rad; ne forsiraj switch.

Tok rada:

```bash
git status
git add konkretan/fajl
git diff --cached
git commit -m "feat: opis male završene celine"
git fetch origin
git merge origin/main
git push
```

Svako otvara PR iz svoje grane u main timskog forka. Integrator proverava testove i spaja male celine. Nema force-push-a na zajedničke grane. Osobe ne menjaju isti checkout istovremeno; koristite svoje klonove ili zasebne worktree foldere. OneDrive sinhronizacija nije način deljenja aktivnog Git checkout-a.

Ne commitovati .env, tokene, SSH ključeve, video-snimke, face baze, venv, modele ili generisane logove. Maleni sintetički fixtures i šabloni bez tajni ulaze u Git. Dokumentaciju dodavati po eksplicitnoj putanji, ne celim `Dokumenti/` folderom.

## Integracija bez konflikata

- Osoba 2 održava `table_tennis/contracts/`, generisani OpenAPI/JSON Schema i TypeScript tipove.
- Osoba 3 dodaje svoj router, ali osoba 2 poseduje promenu zajedničkog Supervisor entrypoint-a.
- Osoba 4 poseduje frontend navigaciju i feature registraciju.
- Izmenu postojećeg gesture/screen/LiveKit koda najaviti integratoru; održati prethodno ponašanje za druge funkcije.
- Nema paralelnog ručnog menjanja generisanih TS tipova.
- PR sa ugovorom se spaja pre PR-a koji ga koristi. Aditivni opcioni field je poželjniji od promene postojećeg značenja.
- Svaki PR navodi: šta radi, kako se pokreće lokalno, koje fixture koristi, testove i poznato ograničenje.

## Kontrolne tačke

Vremena su predlog za jedan intenzivan razvojni dan; zavise od opreme i termina pristupa robotu.

| Period | Zajednički dokaz napretka |
|---|---|
| Priprema / prvi sat | isti contract v1; pokretljiv mock; četiri grane; svi dobijaju isti snapshot |
| Sledeća 2–3 sata | UI šalje ručni poen, backend čuva, fake ekran i glas prikazuju istu reviziju |
| Sredina razvoja | kandidat poena iz snimka; pravi ekran i kratki gest nezavisno provereni |
| Integracija | operator potvrđuje CV predlog; lokalno rešenje radi bez LLM veze |
| Pred demo | jedan ceo gem, undo, izgubljena kamera, promena persone, poziv robota ili jasno označen ručni dolazak |

Automatsko suđenje uključiti tek posle merenja na snimcima sa vašeg stola. „Niko nije kliknuo” nije potvrda poena. Ručni ili asistirani režim mora biti vidljiv u aplikaciji i demonstraciji.

## Završni zajednički test

Poziv -> robot ready -> kalibracija -> start -> poen P1 -> duplikat istog poena -> jedan rezultat -> undo -> sledeći poen -> prekid SSE -> ponovna sinhronizacija -> nerešen rezultat 10:10 -> završetak 12:10. Nakon restartovanja prikazuje se poslednje stanje bez ponavljanja rukovanja, gestova i starih komentara.

Ovaj paket ne kreira grane, ne pushuje i ne pokreće robota. Komande su spremne za tim nakon generisanja zajedničke osnove.
