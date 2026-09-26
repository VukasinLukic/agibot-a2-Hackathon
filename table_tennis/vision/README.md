# vision (osoba 1)

Praćenje loptice i jedan predlog poena po razmeni. Paket ne piše rezultat i ne bira servera. Backend (`point.confirm` ili `point.award`) jedini menja skor.

Šalje se samo:

- `camera.ready.set` (actor `vision`)
- `point.propose` sa razlogom `missed_return`

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

Podrazumevani je levi. Interactive H.264 i svaki topic koji se završava na `/h264` se odbijaju: red od oko 120 kadrova kasni i do pet sekundi. Kad `read()` prestane da vraća sliku, `camera_missing` postaje istinit, `camera.ready.set` ide na false i predlozi staju.

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

`MatchVisionProducer` prvo šalje `camera.ready.set`, pa najviše jedan `point.propose` po rally-ju, i samo dok je `scoring_mode` jednak `assisted`. Komanda ide na `POST /api/table-tennis/matches/{id}/commands` sa actorom `vision`. Rally, `calibration_id` i `assignment_version` dolaze iz trenutnog snapshot-a. Isti `command_id` ostaje pri ponovnom slanju. Posle 409 predlog se baca i ne šalje se ponovo sa novim `expected_revision`.

Zvuk, ako je uključen, javlja zaključak preko `RallyJudge.hear`. Predlog tada ide samo kad zvuk i slika imaju istog pobednika i razlog `missed_return`. Detalj signala je u `sound/README.md`. Fixture sa istim poljima: `fixtures/missed_return.json`.

## Benchmark

`benchmark.evaluate` meri finalni skup odvojeno od tuning snimaka. Bar je 50 razmena sa stvarnog A2 stola, oznake `net`, `occlusion`, `fast_ball` i `stop`, precision bar 95% i coverage jednostavnih razmena bar 80%. Sintetički snimak taj bar ne otvara. Ispunjen bar ne uključuje `AUTOMATIC_ENABLED`.

## Granice

- Vision ne pomera robota, ne bira servera i ne piše skor.
- Snimci i težine ne idu u git.
- Head, waist i svaki `/h264` topic nisu ulaz.
