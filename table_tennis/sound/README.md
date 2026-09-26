# sound (osoba 1)

Zvuk meri trenutak udara i, kad je signal jasan, podlogu. Kamera i dalje kaže na kojoj je polovini stola lopta bila. Mikrofon ne bira pobednika i ne piše rezultat. Jedini pošiljalac `point.propose` ostaje `MatchVisionProducer`.

Uvoz paketa ne otvara mikrofon, ne učitava model i ne šalje komandu.

## Tok

1. Snimak je 16-bitni PCM na 48 kHz. `write_clip` / `read_clip` drže wav i sat prvog uzorka u `ime.wav.clock.json` (`start_monotonic_ns`). Vreme uzorka je `start + (index * 1e9) // 48000`, da sat ne sklizne oko milisekunde u minutu.
2. `energy_peaks` radi high-pass na 2 kHz, pa kratku energiju. Prag je obavezan argument. Vrhovi bliži od `min_gap_ns` ostaju jedan događaj, pa kontakt od 1–2 ms ne postane dva. Tišina ne daje vrh.
3. `classify_contact` za jedan vrh meri trajanje i spektralni centroid. Kratak svetao ton je `table`, kratak niži ton je `racket`, duži tup ton je `floor`. Tiho ili neodređeno je `abstain`. Opsezi razdvajaju sintetičke tonove; nisu kalibracija sa robota.
4. `nearest_frame` veže vreme udara za kadar čiji je `capture_monotonic_ns` najbliži, unutar dva perioda kamere. Van prozora vraća prazno. Pri jednakom razmaku ostaje raniji kadar. Ovde se homografija ne računa.
5. `missed_return_proposal` gleda redosled: reket, pa dva odskoka na istoj tuđoj polovini, pa tišina duža od 1,5 s. Pre prvog odskoka lopta mora biti viđena na drugoj polovini; uzima se poslednji takav kadar. Polovina dolazi iz `table_half` (`y_mm` ili `project_to_table_plane`). Predviđena tačka, `abstain` i odskok na sopstvenoj polovini ne daju predlog. Pobednik je igrač koji nije na prijemnoj strani, ista podela kao u `vision/events.py`. Funkcija ne šalje HTTP. Polja komande su kao u `vision/fixtures/missed_return.json`, plus `last_contact_ns` (vreme drugog odskoka) da vizija odbije zaključak iz prethodne razmene. Razlog je `missed_return`. `service_fault` se ne vraća.
6. `clip_from_raw_blocks` spaja uzastopne sirove blokove u jedan snimak. Sat prvog uzorka je `start_monotonic_ns` bloka. Blok koji je već prošao AEC ili noise suppression se odbija. Rupa između blokova se ne popunjava.

`write_impulse` pravi sintetski prasak od 1–2 ms. `write_silence` je poseban fajl nula. Nijedan od ta dva ne označava udarac.

## Predlog poena

`RallyJudge.hear` u `vision/events.py` pamti zaključak zvuka. Kad je zvuk uključen, predlog čeka taj zaključak: dok ga nema, slika ćuti. Predlog se šalje samo kad je `scoring_mode` jednak `assisted`, kamera i kalibracija spremne, i kad zvuk i slika imaju istog pobednika i isti razlog. Zvuk zaključuje samo `missed_return`, pa ostali razlozi slike ne prolaze dok je zvuk uključen. Demo ostavlja zvuk isključen. `last_contact_ns` pre početka razmene se ignoriše. Ako se ne slože, nema predloga. Odgovor 409 baca predlog. `benchmark.AUTOMATIC_ENABLED` ostaje false.

Dozvoljeni razlozi ugovora su `missed_return`, `double_bounce`, `out_after_hit`, `service_fault`, `unknown`. Ovaj folder ne dodaje novi razlog i ne uvodi novog actora. `point.propose` i dalje smeju samo `vision` i `sim`.

## Ulaz

Isti sat kao kadar: `time.monotonic_ns` u `vision/a2.py`. Ista stopa kao `audio_bridge.py`: 48 kHz. Ulaz za udarac je sirovi blok, pre AEC-a, noise suppression-a i high-pass-a. Obrađen govorni signal koji bridge šalje u LiveKit gubi udarac.

Ovaj paket ne otvara ALSA uređaj koji bridge drži i ne menja `audio_bridge.py`. Na laptopu ulaz je wav snimljen dok bridge nije držao mikrofon, ili kopija sirovog bloka. Snimci i skup `tt_sounds` (CC BY-NC) ne idu u git.

## Testovi

```powershell
python -m unittest discover -s tests/table_tennis/sound -v
```
