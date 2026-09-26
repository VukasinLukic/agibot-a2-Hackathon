# Plan: kako da vizija stvarno broji poene

Stanje 27.9.2026, posle testova na `IMG_0382.mov` i `IMG_0385.mov` (demo sto).

## 1. Zašto je radilo na starom snimku, a ne radi na novom

„Model“ nije jedna stvar, nego lanac od pet koraka. Samo jedan od njih je učen:

| Korak | Šta radi | Kako je nastao |
|---|---|---|
| 1. Kandidati (`candidates.py`) | nalazi sve što je svetlije od prethodna dva kadra | ručno podešeni pragovi (18 svetline, površina ≥ 6 px na 960) |
| 2. BallNet (`ballnet.py`) | za svaki kandidat kaže „loptica / nije“ | **učen** na jednom snimku |
| 3. Praćenje MHT (`mht.py`) | spaja detekcije u putanju | ručno podešeno: kapije, gravitacija, očekivana veličina, brzina u px |
| 4. Odskoci (`rally_events.py`) | nalazi odskok (obrt brzine po y) | pravilo, traži lopticu u 3 uzastopna kadra |
| 5. Logika poena (`point_logic.py`) | iz niza odskoka i udaraca zaključuje poen | pravila |

Sve je podešavano i mereno na **jednom** snimku (`IMG_5844`: telefon iz ruke, izbliza, velika
loptica). Merenje je rađeno na drugoj polovini **istog** snimka, pa je rezultat (88–96%)
bio preoptimističan: ista kamera, isto svetlo, ista pozadina. Na demo stolu se sve menja
odjednom:

- **Loptica je mnogo manja** (oko 5 px na radnoj širini 960, umesto 15+). Koraci 1 i 3 imaju pragove u pikselima.
- **Pozadina je teška**: bela šrafura na staklu, beli slušalice, šareni džemper, beli stolovi. Mnogo belih pokretnih kandidata.
- **Ugao je drugačiji**: kamera je odozgo sa strane, pa je odskok mali obrt po y i korak 4 ga lako promaši.

Šta smo izmerili 27.9. na `IMG_0385` (stabilizovan, tačna kalibracija):

- Kad je kandidat stvarno loptica, BallNet mu u 76% slučajeva daje ocenu > 0.5. **Učeni deo nije glavni krivac.**
- Loptica je potvrđena u samo 20% kadrova, a u celom snimku je nađeno 10 odskoka. Razmena se raspadne pre zaključka.
- Doučavanje BallNet-a na 24 pregledane putanje: nije merljivo bolje. Niži pragovi MHT-a: više kadrova sa lopticom, ali ne i više odskoka ni poena.
- **Glavni problem: nemamo tačne oznake** (gde je loptica, gde su odskoci, ko je dobio poen). Zato svaka promena ide naslepo i ne može da se dokaže da je bolja. Tako je prošao ceo današnji pokušaj.

## 2. Da li čekati snimak sa robota?

**Delimično.** Robotova fisheye kamera izgleda drugačije od telefona, pa se konačno
podešavanje i učenje rade **samo na snimcima sa robota**. Deo posla ne zavisi od kamere, i
taj može odmah:

| Sada, na telefonskim snimcima | Tek sa robotovim snimcima |
|---|---|
| alat za obeležavanje i merenje po koracima (tačka 3A) | konačne oznake i merenje |
| pragovi zavisni od razmere → računati iz veličine stola u kalibraciji | doučavanje BallNet-a |
| zvuk kao detektor odskoka (ne zavisi od kamere) | podešavanje MHT-a i odskoka |

## 3. Plan

### A. Merenje pre svega (pola dana rada, plus 1–2 sata obeležavanja)

1. **Alat za obeležavanje**: prolaz kroz snimak, klik na lopticu na svakih nekoliko kadrova, interpolacija između klikova; oznaka kadra odskoka; na kraju razmene ko je dobio poen. Detektor predlaže, čovek ispravlja.
2. **Merenje po koracima** na obeleženom snimku, u jednoj tabeli:
   - Kandidati: da li je loptica uopšte među kandidatima? (recall)
   - BallNet: ocene na pravoj loptici i na lažnim kandidatima.
   - MHT: koliko kadrova sa lopticom je potvrđeno.
   - Odskoci: koliko od stvarnih je nađeno, koliko lažnih.
   - Poeni: koliko je tačno predloženo, koliko pogrešno, koliko puta je vizija pitala.
3. Obeležiti 1 minut sa `IMG_0385` za početak, a posle robota 3–5 minuta sa robota.

Tek sa ovom tabelom vidimo **koji korak gubi lopticu** i da li je neka promena stvarno bolja.

### B. Popravke, redom od najslabijeg koraka po tabeli

1. **Pragovi zavisni od razmere**: veličina loptice i brzine u px zavise od udaljenosti kamere. Iz kalibracije znamo dužinu stola u px, pa pragove (min. površina kandidata, `size_ref`, kapije i gravitacija u MHT-u) treba računati iz toga, umesto fiksnih brojeva za jedan snimak. To važi za svaku kameru, pa i za robota.
2. **Mala loptica**: ako kandidati propuštaju lopticu, raditi kandidate u punoj rezoluciji, ali samo u kalibrisanom pojasu stola (plan iz `docs/05`: „ROI u punoj rezoluciji ako je loptica ispod ~6 px“).
3. **BallNet**: doučiti na oznakama iz više snimaka (`IMG_5844` + `IMG_0385` + robot), uz teške lažne primere (šrafura, slušalice, džemper). Meriti na **drugom** snimku, ne na polovini istog.
4. **Odskoci**:
   - zvuk (`table_tennis/sound/`, već postoji detekcija udarca o sto) kao drugi, nezavisan izvor odskoka. Zvuk tačno kaže *kada*, a slika *na kojoj polovini*. To je upravo korak koji je danas najslabiji;
   - pre toga proveriti mikrofon robota i kašnjenje zvuka.
5. **Logika poena**: tek kad su odskoci pouzdani. Pragovi (0.5 s nestanka, 3 s za pitanje) se podešavaju na obeleženim razmenama.

### C. Robot

1. Na terminu: 3–5 minuta prave igre sa `--record` (po 60 s, vidi `docs/ROBOT_VISION_KIT.md`), plus `--dry-run` za fps.
2. Posle termina: obeležiti taj snimak (A), izmeriti, uraditi popravke B na njemu, pa novi snimak i nova provera.

## 4. Realna očekivanja

- Bar za automatsko suđenje iz `vision/README.md` (50 razmena sa pravog stola, preciznost 95%, pokrivenost 80%) je daleko i neće se dostići za nekoliko dana.
- Realan cilj za demo: vizija sama i tačno predloži deo poena, pita „Ko je dobio poen?“ kad vidi da je razmena gotova, a ostalo operater dodeli dodirom. To već radi, samo sa malo predloga.
- Bez merenja (A) ne treba menjati težine ni pragove. Promena koja se ne može izmeriti može isto tako da pogorša stvar.

## 5. Ko radi

Po dogovoru, koraci 1–5 lanca (`vision/*.py` osim `live.py`, `calibrate.py`, `preview.py`, `video.py`) i
težine su kolegin deo. Alat za obeležavanje i merenje (A) može da bude zajednički, jer ne dira
postojeći kod. Sve popravke iz B idu kroz njega ili uz njegov dogovor.

Postojeći alati: `training/ballnet/` (`diag.py`, `stabilize.py`, `dump_cands.py`, `tracklets_auto.py`, `finetune_0385.py`) i težine u `models/ballnet/`.
