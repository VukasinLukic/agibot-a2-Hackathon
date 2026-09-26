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

HSV u `config.example.yaml` ostaje prazan dok se ne izmeri na pravom fisheye kadru. `BallTracker` bez te boje ne kreće. Pretraga živog producera ide samo unutar kalibrisanog stola.

Predlog nastaje samo za jasan promašen povratak: lopta je viđena na obe polovine, van pojasa mreže, pa track pređe u `missing`. Pobednik je igrač koji nije na prijemnoj strani (`court_end_by_player` iz snapshot-a). Isti `command_id` ostaje pri ponovnom slanju. Posle 409 predlog se baca i ne šalje se ponovo sa novim `expected_revision`.

`MatchVisionProducer` prvo šalje `camera.ready.set`, pa najviše jedan `point.propose` po rally-ju, i samo ako je `scoring_mode` jednak `assisted`.

Komanda ide na `POST /api/table-tennis/matches/{id}/commands` sa actorom `vision`. Rally, `calibration_id` i `assignment_version` uzimaju se iz trenutnog snapshot-a. Fixture koji prolazi ista polja: `fixtures/missed_return.json`.

## Benchmark

`benchmark.evaluate` meri finalni skup odvojeno od tuning snimaka. Demo bar je bar 50 razmena sa stvarnog A2 stola, oznake mreže, zaklona, brze loptice i prekida, precision bar 95% i coverage jednostavnih razmena bar 80%. Sintetički snimak taj bar ne otvara. Ispunjen bar ne uključuje automatiku.

## Granice

- Nema obučenog modela. Ako boja i pokret ne drže loptu, sužavaju se pragovi i ROI.
- Nema 50 označenih A2 razmena, pa automatika ostaje isključena.
- Vision ne pomera robota i ne bira servera.
- Snimci i težine ne idu u git.
