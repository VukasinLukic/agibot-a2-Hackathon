# vision (osoba 1)

Praćenje loptice i jedan predlog poena po razmeni. Paket ne piše rezultat i ne bira servera. Backend (`point.confirm` ili `point.award`) jedini menja skor.

Šalje se samo:

- `camera.ready.set` (actor `vision`)
- `point.propose` sa razlogom `service_fault`, `out_after_hit`, `double_bounce` ili `missed_return`

`confidence` je skor modela, ne kalibrisana verovatnoća. Predikcija i nestanak loptice sami nisu poen. `benchmark.AUTOMATIC_ENABLED` ostaje false.

Jedan proces pokreće jednog producera. `MatchVisionProducer` je živi put. `FixtureVisionProducer` u `stub.py` čita fixture i ne računa lopticu. Ne pokreću se oba.

## Zavisnosti

Mock backend ih ne traži. OpenCV i numpy idu u poseban venv, iz korena repozitorijuma:

```powershell
py -3 -m venv .venv-vision
.\.venv-vision\Scripts\Activate.ps1
pip install -r table_tennis\requirements-vision.txt
```

```powershell
python -m unittest discover -s tests/table_tennis/vision -v
```

## Kadar

Lokalni ulaz je sirovi TTCLIP, ne mp4. Capture čita kadar po kadar, dodeljuje `frame_seq` i `capture_monotonic_ns`, i ne crta i ne šalje HTTP. Isti piksel-bafer ne dobija novi `frame_seq`.

```python
from table_tennis.vision.capture import FileCapture
from table_tennis.vision.overlay import write_overlay_clip
from table_tennis.vision.synthetic import write_moving_circle

write_moving_circle("clip.ttclip")
with FileCapture("clip.ttclip", "file-cam") as capture:
    write_overlay_clip(capture, "marked.ttclip")
    print(capture.stats)
```

Na robotu, samo na PC2 gde ROS vidi kameru, dozvoljena su dva sirova chest fisheye topica:

- `CHEST_LEFT_FISHEYE` (`/aima/hal/fish_eye_camera/chest_left/color`)
- `CHEST_RIGHT_FISHEYE` (`/aima/hal/fish_eye_camera/chest_right/color`)

Podrazumevani je levi. Interactive H.264 i svaki topic koji se završava na `/h264` se odbijaju: red od oko 120 kadrova kasni i do pet sekundi. Kamera se proglašava izgubljenom tek posle tri uzastopna prazna `read()`. Tada `camera_missing` postaje istinit, `camera.ready.set` ide na false i predlozi staju.

```python
from table_tennis.vision.a2 import A2FisheyeCapture

with A2FisheyeCapture("CHEST_LEFT_FISHEYE") as capture:
    for frame in capture:
        if capture.table_in_frame(corners_and_net):
            break
```

## Kalibracija

Operater klikne četiri ugla, pa mrežu. Redosled: `end_a_0`, `end_a_1`, `end_b_0` (uz `end_a_1`), `end_b_1`. To nisu leva i desna strana robota. Sto je 1525 × 2740 mm. Homografija je ravan stola i ne ispravlja fisheye, pa sto treba da stoji u sredini kadra.

Sva četiri ugla i mreža moraju biti bar 8 px od ivice. Četvorougao koji se seče, površina ispod 2% kadra, ili mreža van 12% od sredine dužine, ostaju nevažeći i ne upisuju se. Posle odbijenog klika čeka se nov kadar i `camera_moved()`.

```python
from table_tennis.vision.calibration import CalibrationGate, mark_ends, write_calibration, write_marked_ppm

gate = CalibrationGate()
calibration = gate.submit(frame, corners, net)
if calibration.ready:
    write_calibration("table.json", calibration)
    write_marked_ppm("table.ppm", mark_ends(frame.image, calibration))
```

`calibration.set` šalje operator, ne vision. Promena kamere, rezolucije ili položaja robota poništava staru kalibraciju. Tačka u vazduhu projektovana na sto nije odskok.

## Loptica

HSV u `config.example.yaml` je prazan dok se ne izmeri na fisheye kadru. Bez boje i bez `model_path`, `BallTracker` ne kreće. Živi producer traži samo unutar kalibrisanog stola, plus pojas iznad njega.

OpenCV put: `inRange`, razlika tri kadra, `connectedComponentsWithStats`. Izdužen trag zamućenja ostaje. Položaj je sredina traga, debljina traga je prečnik. Ako je belo telo u boji deblje od `max_diameter`, odbija se. Kad ima više kandidata, uzima se najbliži predikciji, unutar kapije. Kratak jaz je `predicted`, duži je `missing`. Nijedan nije odskok. `proves_bounce` ostaje false.

BlurBall (cogsys-tuebingen/blurball, MIT) je naučeni lokator za zamućenje. Težine nisu u git-u:

https://cloud.cs.uni-tuebingen.de/index.php/s/6Z8TpM3sXRKHzGC

`model_path` pokazuje na checkpoint. Njihov paket traži `TT_BLURBALL_ROOT` i CUDA. Dok toga nema, `locate` vraća prazno i kadar ostaje na OpenCV putu. Prag skora je 0.7. Torch se ne uvozi na importu paketa i nije u korenskom `requirements.txt`.

## Predlog

`RallyJudge` predlaže samo jasan promašen povratak: lopta je viđena na obe polovine, van pojasa mreže (8% dužine), pa track pređe u `missing`. Pobednik je igrač koji nije na prijemnoj strani (`court_end_by_player`). Isti pikseli prate strane stola, ne sliku levo/desno.

`MatchVisionProducer` prvo šalje `camera.ready.set`, pa najviše jedan `point.propose` po rally-ju, i samo dok je `scoring_mode` jednak `assisted`. Polovina se računa iz odskoka, ne iz položaja u vazduhu. Servis čeka odskok na strani servera pa na strani primaoca. U igri jedan odskok na protivničkoj polovini i udarac (ili prelaz mreže koji nije odskok) menjaju napadača. Kraj razmene: loš servis (`service_fault`), lopta posle udarca ne padne na protivničku polovinu (`out_after_hit`), drugi odskok na istoj polovini bez udarca (`double_bounce`), ili odskok na protivničkoj polovini pa nestanak preko tog kraja (`missed_return`). Nestanak mora da traje pola sekunde i ne sme biti iznad sredine stola. `confidence` je najslabiji skor viđene loptice u toj razmeni. Rally, `calibration_id` i `assignment_version` dolaze iz trenutnog snapshot-a. Isti `command_id` ostaje pri ponovnom slanju. Posle 409 predlog se baca i ne šalje se ponovo sa novim `expected_revision`.

Živi proces je `python -m table_tennis.vision.live`. On drži poslednji snapshot sa SSE toka `/events` (dok tok nije stigao, jednom pita `GET`), i šalje komande na `POST /api/table-tennis/matches/{id}/commands` sa `TT_VISION_TOKEN`. Telo ne imenuje actora. `--match-id latest` uzima poslednji meč. `--dry-run` samo loguje komande. `--record` piše TTCLIP u `table_tennis/var/`. `--grab still.jpg` sačuva prvi kadar i stane; uglove onda bira `python -m table_tennis.vision.mark_table still.jpg -o table.json`. Pad backenda ne gasi proces: predlog se pošalje još jednom sa istim `command_id`, a izlaz šalje `camera.ready.set false`. Na svakih 5 s ispisuje fps i p50/p95.

Zvuk, ako je prosleđen, mora prvo da zaključi. Predlog ide samo kad zvuk i slika imaju istog pobednika i isti razlog. Kontakt čiji je `last_contact_ns` pre početka ove razmene se ignoriše. Demo ostavlja zvuk isključen. Detalj signala je u `sound/README.md`. Fixture sa poljima komande: `fixtures/missed_return.json`.

## Benchmark

`benchmark.evaluate` meri finalni skup odvojeno od tuning snimaka. Bar je 50 razmena sa stvarnog A2 stola, oznake `net`, `occlusion`, `fast_ball` i `stop`, precision bar 95% i coverage jednostavnih razmena bar 80%. Sintetički snimak taj bar ne otvara. Ispunjen bar ne uključuje `AUTOMATIC_ENABLED`.

## BallNet put

Kad je `ballnet_path` postavljen, `BallTracker` ne zove HSV ni BlurBall (`model_path` i `ballnet_path` se isključuju). Kadar ide kroz `pipeline.BallNetPipeline`:

1. `candidates.py`: kadar širi od `ballnet.work_width_px` (960) se smanjuje. Prethodna dva kadra se poravnaju na trenutni (LK tok na 320 px, affine RANSAC; `compensate_motion: false` to gasi). Kandidat je ono što je svetlije od oba poravnata kadra (prag 18). Površine i patch se skaliraju sa radnom širinom. ROI (sto plus pojas, ili `roi`) seče pretragu.
2. `ballnet.py`: mala CNN nad patch-om 32 × 32 (B, G, R, diff), ceo kadar u jednom batch-u. `ballnet.onnx` ide kroz OpenCV DNN (oko 2 ms za 40 kandidata na laptopu), `.npz` (`c0w … l1b`) kroz čist numpy (oko 11 ms). Isti izlaz, razlika ispod 1e-6. Torch se ne uvozi.
3. `mht.py`: više hipoteza (mht3). Skor traga: logit, neto brzina, kazna za jedan pogodak, ivicu i sitan okvir; histereza `thr_new`/`thr_conf`. Samo potvrđen trag ide u `predicted`. Pikseli važe na 960 × 540, vreme u kadrovima od 30 fps (iz `capture_monotonic_ns`). Parametri su u `TrackerParams`; `thr_new` se bira ponovo za svaku novu težinu.

Izlaz je isti `TrackSample`, u pikselima originalnog kadra. `confidence` je skor mreže za taj kandidat. `coast` nije duži od `missing_frames - 1`. Prazan kadar posle poslednje loptice ostaje `predicted` dok ne prođe `missing_frames`, pa tek onda `missing`. Jedan slab kadar usred razmene nije promašen povratak.

Offline prolaz kroz snimak, sa CSV-om, overlay-em i vremenom po koraku:

```powershell
python -m table_tennis.vision.run_video snimak.mov --ballnet C:\tezine\ballnet.npz --overlay table_tennis\var\vision\snimak.mp4
```

CSV bez `--csv` ide u `table_tennis/var/vision/` (van gita).

Merenje na `IMG_5844.MOV` (telefon iz ruke, 30 fps, 505 kadrova sa lopticom ručno označeno). Mreža učena na jednoj polovini snimka, merena na drugoj:

| | stari HSV tracker | BallNet + MHT |
|---|---|---|
| loptica u letu nađena | 0 % | 88–96 % |
| izlaz na pogrešnom objektu | – | 2–6 po polovini |
| izlaz kad loptice nema | – | 6–19 od ~200 kadrova |
| vreme po kadru (laptop CPU, 960 px) | 238 ms | 19 ms |

Jedan snimak, jedna kamera i jedna loptica. Na A2 fisheye kadru brojevi nisu dokazani: snimiti, označiti i doučiti (`table_tennis/var/vision/train/`).

## Odskok i udarac

`rally_events.RallyEventDetector` čita `TrackSample` redom. Odskok je oštar obrt brzine po y (dole pa gore), udarac je obrt brzine po x. Treba mu tri uzastopna posmatranja, pa događaj kasni jedan kadar. Sa kalibracijom odskok mora pasti na sto (± 60 mm) i dobija polovinu (`end_a` / `end_b`); odskok u ruci ili na podu se odbacuje. Na neviđenim polovinama snimka nađeno je 23 od 26 označenih odskoka. Udarac je često zaklonjen reketom: nedostatak udarca znači „ne znam”.

`RallyJudge` predlaže poen samo ako je poslednji takav događaj odskok na prijemnoj polovini, pa track pređe u `missing`. `TrackSample.proves_bounce` i dalje ostaje False: odskok je poseban događaj, ne polje uzorka.

## Granice

- Vision ne pomera robota, ne bira servera i ne piše skor.
- Snimci i težine ne idu u git.
- Head, waist i svaki `/h264` topic nisu ulaz.
