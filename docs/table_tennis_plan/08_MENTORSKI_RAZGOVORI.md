# Četiri mentorska razgovora za A2 sudiju

Prvi termin traje 45 minuta; ukupno imate četiri takva termina u 24 sata. Cilj prvog je dokazati pristup i osnovnu fizičku integraciju, proveriti ulaz kamere i dogovoriti uslove demonstracije. Ne pokušavati da svih 40 rezervnih pitanja postanu agenda prvog razgovora.

## Brzi početak — pročitaj timu pre termina

Otvaranje od 30 sekundi:

> Pravimo sudiju za stoni tenis. Imamo lokalnu osnovu za rezultat i događaje. Želimo danas da sa našeg računara dođemo do razvojnog PC2, vidimo kameru i reprodukujemo jedan poen: rezultat na ekranu, kratka izgovorena rečenica i jedan gest. Potrebne su nam tačne komande i ograničenja ovog robota. Možemo li prvo potvrditi kriterijume hakatona, pa ovo praktično pokazati?

Dva mentora: pitajte ko najbolje poznaje mrežu/Supervisor/audio, a ko kameru/pokrete/navigaciju. Prilagodite podelu njihovom stvarnom znanju.

Četiri člana: backend vodi pitanja o procesu, porukama i auth-u; navigation izvršava proverene radnje sa mentorom; vision beleži kameru i snima uz dozvolu; persone beleži govor, kriterijume i odluke. Jedna osoba vodi sat. Ako se radi paralelno sa dva mentora, samo jedna osoba ima kontrolu nad robotovim pokretima i servisima.

## Šta stvarno imamo pre razgovora

Pregledano lokalno: backend grana, zajednička osnova commit 15ff3b6. Postoje i nedovršene lokalne backend izmene; tokom pripreme razgovora nisu menjane niti testirane.

- table_tennis ima modele, engine/API/storage, simulator i fake izlaze.
- robot/a2_adapters.py i persona/speech.py su dry-run mesta za integraciju, ne proverena robot veza.
- 07_IMPLEMENTATION_STATUS.md navodi da React feature i pytest suite nisu bili deo osnovne isporuke; postoje naknadni lokalni test fajlovi, ali nisu provereni ovom pripremom.
- config.example.yaml podrazumeva mock i 127.0.0.1:8099; real režim nije gotov.
- Supervisor feature hook traži TABLE_TENNIS_ENABLED i token auth; to ne znači da štiti sve stare Supervisor rute.
- Postojeći A2 kod nudi ekran, govor, gesture katalog i nav_missions, ali njihove hardware mogućnosti ovde nisu potvrđene.

Mentorima ne govoriti da imate gotovu robot integraciju samo zato što fake log pokazuje „gesture”.

## Prvi termin — raspored 45 minuta

| Minuti | Tema | Opipljiv rezultat |
|---|---|---|
| 0–4 | kriterijumi, dozvoljena oprema, pristup između termina | jasni uslovi demonstracije |
| 4–12 | laptop -> PC2, Supervisor, checkout, env, mreža | stvarna komanda za ponovno povezivanje i proveren host |
| 12–22 | kamera i položaj prema stolu | živi kadar, efektivni FPS ili termin merenja, snimak ako je dozvoljen |
| 22–34 | tekst na licu, govor i levi/desni gest | najmanje jedan ponovljiv fizički izlaz, idealno sva tri |
| 34–40 | waypoint, readiness i otkazivanje | odluka o izvodljivosti; kretanje samo ako mentor proceni da je spremno |
| 40–45 | sažetak, sledeći termin i vlasnici problema | komande, odgovorne osobe, rokovi, potrebni resursi |

Ako povezivanje uzme više vremena, na 12. minutu tražite da mentor pokaže poznato radno okruženje i zabeleži šta nedostaje vašem. Ako jedan problem traje preko pet minuta bez novog traga, dajte mu vlasnika i nastavite sledeću demonstraciju. Ne trošite 20 minuta termina na instalaciju biblioteka koju možete uraditi kasnije.

## Deset pitanja koja moraju dobiti odgovor

### 1. Po čemu se ocenjuje i šta sme biti spolja?

> Da li imate zvaničnu rubriku i težine kriterijuma? Koliko se vrednuju autonomija, tehnička izvedba, primena i sam demo? Da li su dozvoljeni spoljna fiksna kamera, laptop za obradu i operator za potvrdu nejasnih poena? Koliko traje finalni nastup i mora li sve biti uživo?

Zašto: tri odgovora određuju da li investirate u kameru, navigaciju ili pouzdan celovit demo. Ako mentori ne odlučuju o pravilima, tražite izvor/organizatora koji može potvrditi. Ne tražite podatke o drugim timovima.

Zabeležiti: rubric link, live/offline uslove, dozvolu za spoljni računar/kameru, dozvoljeni fallback.

### 2. Kako se ponovo povezujemo kada vi odete?

> Možemo li sada sa našeg laptopa da se ulogujemo na PC2? Koja je aktuelna mreža, ulazni host, jump putanja i adresa Supervisora? Da li VPN/Wi-Fi izoluje klijente? Hoćemo li imati pristup robotu između termina i da li ga delimo sa drugim timovima?

Tražite demonstraciju i tekst komande bez tajni. Autentifikacione podatke preuzeti predviđenim privatnim putem; ne zapisivati ih u ovaj Git dokument. Proveriti host, radni direktorijum i putanju aktivnog Python interpreter-a.

Zabeležiti: SSH alias/put, Supervisor URL, časovi pristupa, osoba za problem veze.

### 3. Gde tačno pokrećemo naš backend?

> Da li preporučujete poseban proces na PC2 ili uključivanje u postojeći Supervisor? Koji checkout i commit se sada stvarno izvršavaju, koji venv koriste Supervisor, vision i voice, koji port smemo koristiti i kako bezbedno restartujemo samo naš servis?

Backend traži i sledeće:
- da li je 8099 slobodan ili treba drugi port;
- kako telefon pristupa web UI-ju dok je PC2 iza PC1;
- da li je port-forward dozvoljen i ko ga konfiguriše;
- koji korisnik/folder čuva SQLite i logove;
- da li Git pristup sa robota radi ili treba drugi deploy način;
- kako se vraća poslednja radna verzija.

Najvažnija odluka: jedan autoritativni backend za demo. Četiri programera ne smeju paralelno imati četiri procesa koji šalju fizičke komande.

### 4. Možemo li stvarno videti lopticu ovom kamerom?

> Pokažite izvor najbolje kamere za sto: tačan topic/device, rezoluciju, stvarni broj novih kadrova u sekundi i kašnjenje. Možemo li snimiti 30–60 sekundi ping-ponga iz položaja gde će sudija stajati? Možemo li kontrolisati ekspoziciju i da li je dozvoljena spoljna kamera ako ugrađena ne daje dovoljno detalja?

Ne prihvatati samo „kamera podržava 30 fps”. Potrebni su novi dekodirani kadrovi, timestamp/sequence i vidljivost loptice. U robot_services/vision/h264_decoder.py postoji komentar o ~22 fps u ranijem testu; nije izmeren limit trenutne kamere.

Potpitanja: da li dva procesa mogu deliti stream, da li gest pomera sliku, šta radi automatska ekspozicija, ko može obezbediti nosač i svetlo. Ako nema stola sada, uzeti primer sa lopticom u kadru i rezervisati konkretan termin na stolu.

### 5. Kako držimo rezultat na licu tokom celog meča?

> Možete li sada prikazati „ANA 3 : 2 MARKO”, zatim „3 : 3”? Postojeći show_message_async vraća default face: koji podržani put možemo koristiti da rezultat ostane dok ga ne zamenimo? Ko još menja ekran i kako mu privremeno prepustimo ekran našem meču?

Zabeležiti: javna Python funkcija/RPC, provisioning korak, prava pristupa, stvarno trajanje ažuriranja, način restore-a, ograničenje teksta. Pitati sme li se koristiti postojeća resource/SSH integracija uz opšte pravilo razvoja na PC2.

Demonstracija: dve uzastopne promene i potvrda da posle nekoliko sekundi nije vraćen pogrešan prikaz.

### 6. Kako naš potvrđeni događaj postaje govor?

> Pokažite najkraći postojeći poziv koji izgovori tačno „Poen Ana, tri prema dva” bez slobodnog LLM prepričavanja. Koji proces mora već raditi, koja soba/agent se koristi i postoji li potvrda da je izgovor završen? Kako prekidamo zastarelu najavu posle korekcije?

Dodatno: da li postoje Soniox/ElevenLabs kredencijali i kvote za tim, kakav je internet, postoji li lokalni/pripremljeni audio fallback, kako sprečiti da detekcija ljudi ponovo pokrene razgovor i da LLM sam gestikulira.

Tražite da mentor pokaže koji native Agibot audio servisi se zaustavljaju i kako se vraćaju. Ne gašenje „svih AIMA procesa”.

### 7. Koji gestovi rade na ovom uređaju?

> Da li point left i point right stvarno rade u trenutnom live katalogu i koliko traju? Možete li ih pokazati u bezbednom položaju? Šta znači accepted, šta started i kako znamo da je pokret završen? Možemo li ukloniti gest koji čeka ako uradimo undo?

Posebno potvrditi perspektivu leve/desne ruke i kretanje trupa/kamere. Pitati da li je handshake prikladan za fizički kontakt ili je samo animacija ruke. Rukovanje je bonus ako zauzima vreme; mahanje dovoljan početni pozdrav.

### 8. Da li dolazak do stola radi danas u ovom prostoru?

> Imate li već testiranu mapu i waypoint pored ovog stola? Možete li pokazati jedan uspešan dolazak i kontrolisano otkazivanje, ili nam reći šta konkretno nedostaje? Koji status potvrđuje stvarni dolazak, a ne samo prihvaćen RPC?

Ako znaju kod, otvoriti nav_missions.py:
- start poziva hold.engage; koji preflight oni koriste;
- kako se proverava odgovarajući native task_id;
- ko održava fresh pose kada nema otvorenog UI-ja;
- koji watchdog/timeout i recovery su provereni.
Ne tražiti da ad hoc menjaju planner ili zaštite radi rokova.

### 9. Koji su najčešći stvarni kvarovi i postupak oporavka?

> Koja tri problema timovima najčešće potroše sat vremena: prihvaćena komanda bez pokreta, kamera bez slike, zauzet audio, izgubljena lokalizacija? Za svaki pokažite gde gledate status/log i kojim redosledom dijagnostikujete. Ko sme da izvrši oporavak posle E-stop-a?

Tražite razliku između aima em doctor kao pregleda i stop/reset kao promene. Ne pretpostavljati da restart aplikacije, oslobađanje E-stop-a ili HTTP 200 vraća ispravno stanje.

Zabeležiti jedan dokaz/log po kvaru i bezbedan kontakt za recovery; ne zapisivati neproverene shell sekvence kao proceduru.

### 10. Šta da uradimo pre sledećeg termina?

> Koji jedan integracioni dokaz želite da donesemo na drugi termin? Možemo li do tada koristiti robot ili samo simulaciju? Ko može potvrditi rezervaciju stola/kamere i gde šaljemo konkretan log problema ako zapnemo?

Na izlazu pročitati mentoru zapisane odluke: host/process, kamera, ekran, govor, gest, navigacija, ograničenja, sledeći termin.

## Šest dodatnih pitanja posebno za backend

Ovo su pitanja za mentora koji poznaje Supervisor; ostatak tima može paralelno proveravati kameru.

1. „Koji API/funkciju preporučujete kao stabilnu granicu za nas: Supervisor ili direktan vendor RPC? Možete li pokazati minimalni request i stvarni response za govor/ekran/gest?”
2. „Ko je trenutno jedini vlasnik robota i postoji li zaštita od dva procesa koji šalju nav/gest komande? Možemo li rezervisati test period?”
3. „Ako isti request stigne dva puta zbog Wi-Fi retry-a, da li servis deduplikuje ili ponavlja fizičku radnju? Postoji li request/task ID koji možemo sačuvati?”
4. „Ako HTTP timeout nastane posle slanja komande, kako saznamo da li je radnja počela? Da li je bezbedno ponoviti request ili moramo prvo pročitati status?”
5. „Da li komanda za govor/gest blokira, samo se stavi u queue ili ima completion event? Koja je izmerena latencija pri toplom sistemu?”
6. „Koji auth i portovi su stvarno aktivni u ovom deployment-u? Naš novi feature traži tokene; kako da ga povežemo sa telefonom/UI-jem bez otvaranja motornih komandi drugim klijentima?”

Ovo su backend integracione nepoznanice. SQL šemu, scoring unit testove i izbor SSE/REST tim može rešavati samostalno posle termina.

## Pitanja ako ostane vremena ili za naredne termine

### Vizija

- Imate li već snimke sa ovom kamerom i ovim stolom, i smemo li ih koristiti?
- Koji ugao daje oba kraja stola bez zaklanjanja i da li može ostati isti do finala?
- Postoji li fizička mogućnost direktnog camera stream-a sa manje baferovanja?
- Da li metapodaci nose stvarno capture vreme ili samo decode/receive vreme?
- Koliko GPU/RAM-a je slobodno dok voice/vision/SLAM rade zajedno?
- Koje instalirane PyTorch/TensorRT/JetPack verzije da zadržimo?
- Može li obrada raditi na laptopu, a robot primati samo evente?
- Da li CV prepoznavanje ljudi automatski dispatchuje razgovor i možemo li to isključiti nezavisno od video izvora?

### Govor i iskustvo

- Kako sprečiti da aplauz/udarci loptice prekidaju najavu poena?
- Možemo li najave score-a slati bez STT/LLM poziva, a razgovor uključiti samo u pauzi?
- Da li provider pravilno izgovara srpska imena i rezultat 10:10, bez tumačenja kao satnice?
- Možemo li unapred pripremiti kratke rečenice za offline demo?
- Da li postoje pravila organizatora za šaljivu personu koja favorizuje funkciju u firmi?
- Koliko veliki tekst na glavi publika realno vidi; sme li mirror scoreboard na laptopu/TV-u?

### Robot i događaj

- Gde je predviđena pozicija sudije izvan kretanja igrača i zamaha reketa?
- Postoji li idle animacija koja pomera kameru/ruke i kako se pravilno upravlja njom?
- Kako se spremnost posle prekida proverava bez automatskog arming-a?
- Da li nav/audio/gestovi mogu raditi zajedno na postojećem setup-u?
- Koliko traje baterija u tom režimu i ko planira punjenje pre finala?
- Koliko traje vraćanje poznate radne konfiguracije ako deploy zakaže?
- Postoji li dozvoljen video rezervne demonstracije i kako se obeležava?

## Šta tražiti kao dokaz, ne samo odgovor

Za svaki izlaz zabeležiti:
- konkretan računar i radni folder;
- funkciju/endpoint i mali payload bez tajni;
- stvarni response;
- šta smo fizički videli/čuli;
- latenciju ako je merena;
- ograničenje i recovery;
- ime osobe koja potvrđuje ili preuzima sledeći korak.

Ne morate danas imati potpuno povezan naš engine. Ako vreme istekne, tri nezavisna ponovljiva poziva koja su mentori pokazali vrede više od usmenog obećanja o integraciji. Na drugom terminu spojite ih sa istim potvrđenim score događajem.

## Raspored četiri razgovora

Vremena su okvir; rezervišite stvarne termine sada.

| Termin | Predlog položaja u 24h | Šta donosimo | Šta radimo s mentorima | Izlaz |
|---|---|---|---|---|
| 1 | sada | mock osnova, ovaj spisak, laptop | pristup, kamera, tri izlaza, izvodljivost nav | ponovljive komande i ograničenja |
| 2 | za ~5–7h | ručni/asistirani meč, konkretni logovi, jedan CV klip | jedan stvarni poen kroz backend -> ekran/glas/gest, razrešenje blokera | ceo osnovni robot tok |
| 3 | za ~12–16h | CV benchmark, undo i recovery test, call adapter | razmene sa kamerom, odobrena ruta, opterećenje i failure | odluka automatic/assisted, bez većeg novog obima |
| 4 | ~2–3h pre finala | zamrznuta kandidat verzija i demo skripta | puna proba od poziva do pobednika + fallback, reset i baterija | finalni poznati radni commit i plan oporavka |

Četvrti termin čuvajte za probu, ne za prvo povezivanje kamere ili zamenu modela. Na kraju svakog termina dogovorite najviše tri zadatka za tim i konkretan zahtev mentorima.

## Odluke posle prvog termina

| Ako utvrdimo | Odluka za razvoj |
|---|---|
| Spoljna kamera dozvoljena i dostupna | testirati fiksni ugao, zadržati robot kao izvršioca i govornika |
| Samo onboard kamera, slab snimak loptice | asistirani režim; snimiti materijal; smanjiti automatski opseg prema dokazima |
| Nema pristupa robotu između termina | fixture development na laptopovima; spremni mali probe zahtevi za termin 2 |
| Govor radi samo uz cloud | kratki template tekstovi, proverene kvote/internet i odobren fallback |
| Nav nije testirana u prostoru | MVP ručni dolazak jasno označen; odvojen test navigacije u terminu 2/3 |
| Ekran auto-vraća lice | osoba 3 radi persistent scoreboard adapter i ownership |
| Auth/network sprečava telefon | mentor potvrđuje podržani pristup; lokalni operator UI kao privremeni demo |
| Final zahteva punu autonomiju | odmah uskladiti scope i opremu; assisted ne predstavljati kao ispunjen uslov |

## Obrazac beleški

Za svaki red upisati VERIFIED (pokazano), REPORTED (rečeno), UNKNOWN ili BLOCKED.

| Stavka | Status | Odgovor / komanda bez tajni | Vlasnik | Rok / sledeći test |
|---|---|---|---|---|
| Kriterijumi i trajanje finala | UNKNOWN | | | |
| Spoljna kamera / laptop / assisted dozvoljeni | UNKNOWN | | | |
| Ponovno povezivanje na PC2 | UNKNOWN | | | |
| Deploy folder, commit i venv | UNKNOWN | | | |
| API port i pristup telefonu | UNKNOWN | | | |
| Kamera: source, novi FPS, kašnjenje | UNKNOWN | | | |
| Testni klip i kalibracioni položaj | UNKNOWN | | | |
| Stalni score na licu | UNKNOWN | | | |
| Govor i completion/cancel | UNKNOWN | | | |
| Levi/desni gest i njegov završetak | UNKNOWN | | | |
| Mapa, waypoint, pose/task status | UNKNOWN | | | |
| Recovery i ko ga izvršava | UNKNOWN | | | |
| Pristup između termina | UNKNOWN | | | |
| Termin 2/3/4 i kontakt | UNKNOWN | | | |

## Istraživanje i zašto su pitanja ovako poređana

1. [TTNet rad](https://arxiv.org/abs/2004.09927) koristi OpenTTGames snimke od 120 fps i zasebno obrađuje detekciju loptice i vremenske događaje. To podržava pitanje o stvarnom video ulazu; objavljeni rezultati nisu garancija tačnosti na našem robotu ili dokaz da je 120 fps obavezan minimum.
2. [NVIDIA Jetson Linux 36.3 exposure API](https://docs.nvidia.com/jetson/archives/r36.3/ApiReference/struct__v4l2__argus__exposure__timerange.html) dokumentuje exposure range kontrolu. Zato pitamo da li je konkretni A2 sensor/driver izlaže; postojanje API-ja ne dokazuje da je promena dostupna na našem stream-u.
3. [LiveKit govor iz alata](https://docs.livekit.io/agents/logic/tools/definition/) navodi session.say i generate_reply. [Text/transcriptions](https://docs.livekit.io/agents/multimodality/text/) razlikuje tekstualne tokove i transkripcije. Naš zaključak: tražiti provereni put za tačan score tekst i prekid najave u instaliranoj verziji, umesto pretpostavke da bilo koja tekst poruka automatski postaje govor.
4. [Audit lokalnog koda](06_REUSE_AUDIT.md) je osnova pitanja o gesture queue-u, flash ekranu, nav runner-u i zastarelim dokumentima.
5. [Stanje implementacije](07_IMPLEMENTATION_STATUS.md), [README osnove](../../table_tennis/README.md), [A2 adapteri](../../table_tennis/robot/a2_adapters.py) i [speech adapter](../../table_tennis/persona/speech.py) pokazuju granicu između našeg mock-a i tek potrebne integracije.
6. [Head screen dokumentacija](../agibot/head_screen.md) i [AIMA audio manager](../agibot/aima_em_service_manager.md) objašnjavaju vlasništvo resursa koje mentor treba da potvrdi na uređaju.

Web dokumentacija opisuje opštu mogućnost. Mentori i demonstracija na vašem primerku potvrđuju šta stvarno možete koristiti za hakaton.

