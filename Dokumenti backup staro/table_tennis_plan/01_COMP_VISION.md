# Osoba 1 — vizija i praćenje loptice

Grana: `comp-vision`. Tvoj proizvod je vremenski niz opažanja loptice i objašnjiv predlog osvajača poena. Backend odlučuje šta postaje rezultat.

Pre rada pročitaj [zajednički ugovor](05_SHARED_CONTRACT.md), [pravila rada](README.md) i [audit koda](06_REUSE_AUDIT.md). Putanje novih modula su plan implementacije; scaffold ih priprema.

## Vlasništvo i isporuka

Menjaš `table_tennis/vision/`, `tests/table_tennis/vision/`, sopstvene primer-konfiguracije i uputstvo za kalibraciju. Ugovor predloži integratoru; ne pravi svoju kopiju tipova. Osoba 2 poseduje izbor automatski prihvatljivih događaja i pragova na backendu.

Predloženi fajlovi:

| Fajl | Uloga |
|---|---|
| capture.py | kamera ili video, timestamp, sequence, health |
| calibration.py | četiri ugla, mreža, end_a/end_b, calibration_id |
| detector.py | candidates loptice |
| tracker.py | kratka putanja, predikcija i missing |
| events.py | izdvajanje događaja iz vremenskog konteksta |
| producer.py | Protocol implementacija i HTTP/fixture sink |
| overlay.py | debugging prikaz |
| benchmark.py | poređenje sa ručnim ground truth-om |
| config.example.yaml | bezbedni lokalni parametri |
| README.md | ulaz, kalibracija, benchmark, ograničenja |

Ne razvijaš brojanje, LLM, novu Supervisor aplikaciju ili kontrolu motora. U postojećem vision folderu detekcija osobe ne predstavlja detekciju ping-pong loptice.

## Faza 0 — prvih 60 minuta

1. Napravi svoju granu od zajedničkog SHA.
2. Pokreni mock API koji scaffold obezbeđuje.
3. Pošalji jedan fixture point.propose; proveri da se pojavio predlog, a rezultat ostao 0:0.
4. Pročitaj snapshot: stable player IDs, assignment_version, calibration_id i active_rally_id.
5. Pripremi 30–60 sekundi snimka ili kameru; izmeri stvarni capture FPS i vidljivost loptice. Ne zaključuj FPS samo iz metadata.
6. Prijavi timu: izvor kamere, rezoluciju, FPS, koliko se često vidi loptica i da li postoji testni video.

Ne čekaš robot. Ako nema kamere, radiš sa lokalnim snimkom i sintetičkom putanjom; jasno označi izvor u debug prikazu.

## Faza 1 — pouzdan video ulaz

Implementiraj jedan capture interfejs za video i živu kameru. Frejm nosi frame_seq i capture_monotonic_ns. Audio, GUI render i HTTP slanje ne smeju blokirati capture.

Drži mali ograničen bafer. Kod preopterećenja meri izgubljene frejmove i povećavaj neizvesnost događaja; proizvoljno odbacivanje frejmova može sakriti odskok. U offline benchmark-u obradi sve frejmove. U live modu ne gomilaj više sekundi kašnjenja.

Ugrađena A2 centralna kamera ide kroz H264/ROS putanju u postojećem camera bridge-u; proveri stvarni format, dekodiranje i vremenske oznake pre reuse-a. Ne koristi retke JPEG snapshot-e namenjene jezičkom modelu kao ulaz za suđenje.

Provera:
- video se čita do kraja i uredno zatvara;
- kamera se isključi bez zaglavljivanja procesa;
- sequence raste, timestamps su monotoni;
- health prelazi u camera_missing i predlaganje staje;
- izgubljeni frejmovi se vide u metrici.

Isporuka: video/overlay sa merenjima, bez score odluka.

## Faza 2 — geometrija stola

Operater bira četiri ugla u definisanom redosledu i mrežu. Sačuvaj resolution, camera_id, corner order, transform, datum i calibration_id. Proveri da uglovi čine nekrižajući četvorougao, da površina nije degenerisana i da region nije van slike.

Definiši end_a/end_b. Osoba 2 mapira p1/p2 na te krajeve; osoba 3 nezavisno mapira na levu/desnu stranu robota. Nije dozvoljeno pretpostaviti da x<centar znači robot-left.

Homografija važi za ravan stola. Pozicija loptice u vazduhu projektovana tom transformacijom nije dokaz da je dotakla sto. Odskok zahteva vremenski dokaz i dodatnu geometriju/signal; ovaj detalj eksplicitno prikaži u confidence logici.

Ako kamera promeni položaj ili rezoluciju, prethodna kalibracija je nevažeća. Robot sa kamerom na grudima može pomeriti kameru pri gestu i balansiranju; gate/recalibration je potreban i kada nije hodao. Fiksna spoljašnja kamera smanjuje ovaj problem ako je dostupna.

Provera: poznate tačke/rubovi mapiraju se u očekivane zone, promena resolution invalidira kalibraciju. Isporuka: calibration JSON i screenshot overlay-a sa end_a/end_b.

## Faza 3 — detector i tracker

Prvi prototip: HSV maska za boju loptice, motion mask i ROI. Filtriraj kandidata po lokalnoj veličini, udaljenosti od predikcije i kontinuitetu. Zadrži više kandidata kad je scena dvosmislena; ne biraj samouvereno svaki narandžasti predmet.

Kalman filter koristi timestamp-derived delta t. Kratak nestanak može dati predicted opservaciju; nju označi kao predicted. Posle većeg prekida resetuj track. Predikcija ne sme proizvesti fiktivan odskok koji vodi do poena.

Rezervni put: specijalizovani temporalni model/finetuning. Generic yolo26n.pt nije dokaz da zna ping-pong lopticu u ovom uglu. Pre promene modela meri osnovu. Ne menjaj JetPack, system CUDA ili globalni PyTorch na robotu; poseban vision env i kompatibilnost po postojećem deployment-u.

Otvori model/repo licencu pre preuzimanja ili ugrađivanja. Zabeleži izvor i hash težina, odvojeno od licence koda. Težine i veliki snimci ne idu u Git.

Provera: false positives na reketu/odeći, brza loptica, nestanak, ponovno pojavljivanje, promenljivi FPS. Isporuka: overlay sa observed/predicted/missing i CSV merenjima.

## Faza 4 — predlog poena

Razdvoji tri pitanja: gde je loptica, da li je razmena završena i ko je osvojio poen. Tačnost detekcije nije automatski tačnost suđenja.

Počni od jednostavnih jasno snimljenih razmena: potvrđen ispravan prelazak/odskok i propušten povratak. Složen servis, dodir mreže, rub i zaklonjen kontakt idu na operatora. Slučaj „loptica je otišla desno” nije dovoljan za winner_id.

Za svaku odluku čuvaj kratku listu opaženih događaja i nedostajućih dokaza. Šalji point.propose samo za aktivni rally_id. backend određuje servera; ne procenjuj ga iz položaja loptice.

Predlog po ugovoru sadrži winner_id, confidence, reason, capture_start_seq/end_seq, calibration_id, assignment_version, proposal_id. U assisted modu svaki kandidat ide na eksplicitnu potvrdu; ne počinji lokalno brojanje.

Jednom poslat predlog ima isti command_id pri transport retry-u. Nakon 409 preuzmi novo stanje; ako rally/strane/kalibracija nisu više isti, odbaci predlog. Ne prepisuj samo expected_revision i ponovo šalji stari zaključak.

Isporuka: snimak -> predlog -> UI pending -> potvrda -> tačan score. Tačka kontakta sa osobom 2: mali skup validnih i nevalidnih fixture događaja.

## Faza 5 — benchmark i odluka o automatizaciji

Ručno označi najmanje približno 50 razmena sa stvarnog postavljanja: winner, end time, reason, uncertain. Uključi primere mreže, zaklanjanja, brze loptice i namernog prekida. Razdvoji tuning snimke od finalnog testa; susedni frejmovi iste razmene ne smeju biti u oba seta.

Izmeri:
- correct automatic decisions / sve automatske odluke (precision);
- broj automatskih odluka / sve razmene (coverage);
- procenat traženih potvrda (abstention);
- pogrešan igrač, dupli kandidat, vreme do kandidata (p50/p95);
- capture/inference FPS, dropped frames i stale input.

Ciljevi za kontrolisani demo su predlog: bar 95% tačnih među automatski prihvaćenim i bar 80% coverage jednostavnih razmena. Ako ne postigneš, isporuči asistirani režim sa poštenim prikazom. Ne biraj prag na finalnom testnom skupu i onda ga predstavljaj kao nezavisnu proveru.

## Testovi i Definition of done

- Modul radi bez A2 i bez UI.
- Sve poruke prolaze contracts validaciju.
- Nema score mutacije iz vizije.
- Missing/predicted tačke ne postaju samostalni dokaz pobednika.
- Stara kalibracija ili zamena strana invalidira kandidata.
- Jedan rally ne proizvodi niz različitih finalnih odluka.
- Snimljeni benchmark i ograničenja dostupni timu; privatni snimci nisu u Git-u.

Pokretanje: koriste se stvarne komande iz scaffold README-a za demo/simulator, a ti dodaješ i proveravaš CLI za video/camera i benchmark. README mora imati primer za lokalni video i za live izvor.

## Handoff i PR redosled

PR1 capture + kalibracija; PR2 detector/tracker + overlay; PR3 proposals + integracioni fixture; PR4 benchmark/konfiguracija. Svaki PR mora raditi samostalno iza feature flag-a.

Osobi 2 šalješ event primer, preporuku supported reasons, metricu i podatak da confidence nije nužno kalibrisana verovatnoća. Osobi 3 šalješ zahtev za položaj/kretanje kamere. Osobi 4 šalješ format debug prikaza bez video buffer-a u globalnom score stream-u.

