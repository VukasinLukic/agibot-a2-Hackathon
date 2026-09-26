# vision (osoba 1)

Praćenje loptice i jedan predlog poena po razmeni. Ovaj paket ne piše rezultat i ne bira servera. Backend (`point.confirm` ili `point.award`) jedini menja skor.

Šalje se samo:

- `camera.ready.set` (actor `vision`)
- `point.propose` sa razlogom `missed_return`

`confidence` je skor modela, ne kalibrisana verovatnoća. Predikcija i nestanak loptice sami nisu poen. Automatsko bodovanje ostaje isključeno (`benchmark.AUTOMATIC_ENABLED`).

Jedan proces sme da pokrene jednog producera. `MatchVisionProducer` je živi put. `FixtureVisionProducer` u `stub.py` je fixture put. Ne pokreći oba.

## Zavisnosti

Mock backend ih ne traži. OpenCV i numpy idu u poseban venv, iz korena repozitorijuma:

```powershell
py -3 -m venv .venv-vision
.\.venv-vision\Scripts\Activate.ps1
pip install -r table_tennis\requirements-vision.txt
```

Testovi ovog paketa, bez kamere:

```powershell
python -m unittest discover -s tests/table_tennis/vision -v
```

## Lokalni snimak

Ulaz je sirovi TTCLIP, ne mp4. Sintetički klip proverava cev, ne sudiju.

```python
from table_tennis.vision.capture import FileCapture
from table_tennis.vision.overlay import write_overlay_clip
from table_tennis.vision.synthetic import write_moving_circle

write_moving_circle("clip.ttclip")
with FileCapture("clip.ttclip", "file-cam") as capture:
    write_overlay_clip(capture, "marked.ttclip")
    print(capture.stats)
```

Overlay upisuje broj kadra i fps u kopiju. Capture petlja ne crta i ne šalje HTTP.

## Živi izvor

Samo na PC2, gde ROS vidi kameru. Dozvoljena su dva sirova chest fisheye topica:

- `CHEST_LEFT_FISHEYE` (`/aima/hal/fish_eye_camera/chest_left/color`)
- `CHEST_RIGHT_FISHEYE` (`/aima/hal/fish_eye_camera/chest_right/color`)

Podrazumevani je levi, dok pravi kadar ne pokaže da je desni bolji. Interactive H.264 i svaki topic koji se završava na `/h264` se odbijaju: red od oko 120 kadrova kasni i do pet sekundi.

```python
from table_tennis.vision.a2 import A2FisheyeCapture

with A2FisheyeCapture("CHEST_LEFT_FISHEYE") as capture:
    for frame in capture:
        if capture.table_in_frame(corners_and_net):
            break
```

Isti piksel-bafer ne dobija novi `frame_seq`. Kad `read()` prestane da vraća sliku, `camera_missing` postaje istinit i predlozi staju.

## Kalibracija

Operater klikne četiri ugla, pa mrežu. Redosled uglova: `end_a_0`, `end_a_1`, `end_b_0` (uz `end_a_1`), `end_b_1`. To nisu leva i desna strana robota. Sto je 1525 × 2740 mm. Homografija ne ispravlja fisheye, pa sto treba da stoji u sredini kadra.

Sva četiri ugla i mreža moraju biti bar 8 px od ivice. Četvorougao koji se seče, mala površina (ispod 2% kadra) ili mreža van 12% od sredine dužine ostaju nevažeći i ne upisuju se. Posle odbijenog klika čeka se nov kadar i `camera_moved()`.

```python
from table_tennis.vision.calibration import CalibrationGate, mark_ends, write_calibration, write_marked_ppm

gate = CalibrationGate()
calibration = gate.submit(frame, corners, net)
if calibration.ready:
    write_calibration("table.json", calibration)
    write_marked_ppm("table.ppm", mark_ends(frame.image, calibration))
```

`calibration.set` šalje operator, ne vision. Promena kamere, rezolucije ili položaja robota poništava staru kalibraciju. Tačka u vazduhu projektovana na sto nije dokaz odskoka.

## Predlog

HSV u `config.example.yaml` ostaje prazan dok se ne izmeri na pravom fisheye kadru. Bez boje, bez `model_path` i bez `ballnet_path`, `BallTracker` ne kreće. Pretraga živog producera ide samo unutar kalibrisanog stola, plus pojas iznad njega. Igrač izvan tog pojasa nije kandidat.

Boja i pokret idu preko OpenCV-a (`inRange`, razlika tri kadra, `connectedComponentsWithStats`). Izdužen trag zamućenja se zadržava: položaj je sredina traga, a debljina traga je prečnik. Kad ima više kandidata, uzima se onaj najbliži predikciji.

Predlog nastaje samo za jasan promašen povratak: lopta je viđena na obe polovine, van pojasa mreže, pa track pređe u `missing`. Pobednik je igrač koji nije na prijemnoj strani (`court_end_by_player` iz snapshot-a). Isti `command_id` ostaje pri ponovnom slanju. Posle 409 predlog se baca i ne šalje se ponovo sa novim `expected_revision`.

`MatchVisionProducer` prvo šalje `camera.ready.set`, pa najviše jedan `point.propose` po rally-ju, i samo ako je `scoring_mode` jednak `assisted`.

Komanda ide na `POST /api/table-tennis/matches/{id}/commands` sa actorom `vision`. Rally, `calibration_id` i `assignment_version` uzimaju se iz trenutnog snapshot-a. Fixture koji prolazi ista polja: `fixtures/missed_return.json`.

## Benchmark

`benchmark.evaluate` meri finalni skup odvojeno od tuning snimaka. Demo bar je bar 50 razmena sa stvarnog A2 stola, oznake mreže, zaklona, brze loptice i prekida, precision bar 95% i coverage jednostavnih razmena bar 80%. Sintetički snimak taj bar ne otvara. Ispunjen bar ne uključuje automatiku.

## BlurBall

Naučeni detektor je BlurBall (cogsys-tuebingen/blurball, MIT), treniran na pravim kadrovima stonog tenisa sa označenim zamućenjem. Težine nisu u git-u:

https://cloud.cs.uni-tuebingen.de/index.php/s/6Z8TpM3sXRKHzGC

U configu `model_path` pokazuje na taj checkpoint. Njihov paket traži checkout u `TT_BLURBALL_ROOT` i CUDA; na CPU `locate` ćuti i kadar ostaje na OpenCV putu. Prag skora je 0.7. Jedan korak, ulaz tri kadra, izlaz je sredina traga. Torch se ne uvozi dok se taj put ne pozove, i nije u korenskom `requirements.txt`.

## BallNet put

Kad je `ballnet_path` postavljen, `BallTracker` ne zove HSV ni BlurBall (`model_path` i `ballnet_path` se isključuju). Kadar ide kroz `pipeline.BallNetPipeline`:

1. `candidates.py`: kadar širi od `ballnet.work_width_px` (960) se smanjuje. Prethodna dva kadra se poravnaju na trenutni (LK tok na 320 px, affine RANSAC; `compensate_motion: false` to gasi). Kandidat je ono što je svetlije od oba poravnata kadra (prag 18). Površine i patch se skaliraju sa radnom širinom. ROI (sto plus pojas, ili `roi`) seče pretragu.
2. `ballnet.py`: mala CNN nad patch-om 32 × 32 (B, G, R, diff), ceo kadar u jednom batch-u. `ballnet.onnx` ide kroz OpenCV DNN (oko 2 ms za 40 kandidata na laptopu), `.npz` (`c0w … l1b`) kroz čist numpy (oko 11 ms). Isti izlaz, razlika ispod 1e-6. Torch se ne uvozi.
3. `mht.py`: više hipoteza (mht3). Skor traga: logit, neto brzina, kazna za jedan pogodak, ivicu i sitan okvir; histereza `thr_new`/`thr_conf`. Samo potvrđen trag ide u `predicted`. Pikseli važe na 960 × 540, vreme u kadrovima od 30 fps (iz `capture_monotonic_ns`). Parametri su u `TrackerParams`; `thr_new` se bira ponovo za svaku novu težinu.

Izlaz je isti `TrackSample`, u pikselima originalnog kadra. `confidence` je skor mreže za taj kandidat. `coast` nije duži od `missing_frames - 1`.

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

To je dokaz za predlog, ne poen. `TrackSample.proves_bounce` ostaje False, a `RallyJudge` ga još ne koristi.

## Granice

- Težine i snimci ne idu u git. Dok checkout i CUDA nisu tu, kadar obrađuje OpenCV.
- Nema 50 označenih A2 razmena, pa automatika ostaje isključena.
- Vision ne pomera robota i ne bira servera.
- Snimci i težine ne idu u git.
