# BallNet težine (detektor loptice)

Ovo su **trenutne produkcione težine**, iste one sa kojima je rađen sav današnji test.
Novi doučeni model (`ballnet_0385_split`) namerno **nije** ovde, jer nije dokazano bolji.

| Fajl | Za šta | md5 |
|---|---|---|
| `ballnet.onnx` | `live --ballnet`, OpenCV DNN (brže, oko 2 ms za 40 kandidata na laptopu) | `86584fd5…` |
| `ballnet.npz` | isto, čist numpy (sporije, rezerva ako OpenCV na robotu ne čita `.onnx`) | `3d86e0e6…` |
| `net_final.pt` | PyTorch checkpoint, polazna tačka za doučavanje (`training/ballnet/`) | `0e1edbdb…` |

Učeno na jednom snimku (`IMG_5844.MOV`, telefon iz ruke, izbliza). Detalji učenja su u
`training/ballnet/README.md`.

## Upotreba

```bash
python -m table_tennis.vision.live --match-id latest --calibration table.json \
  --device CHEST_LEFT_FISHEYE --ballnet models/ballnet/ballnet.onnx
```

Na robotu: posle `git pull` težine su već u repou, pa nije potreban `scp`. Paket za robota
(`docs/ROBOT_VISION_KIT.md`, `scripts/`) i dalje sme da koristi svoju kopiju. Važno je
samo da `--ballnet` pokazuje na ovaj fajl.

Za Python 3.10 na robotu (ROS Humble), `pydantic` 2 i zavisnosti su u
`vendor_wheels/a2-jp60/` (aarch64, cp310):

```bash
python3 -m pip install --no-index --find-links vendor_wheels/a2-jp60 pydantic
```
