# BallNet trening (van gita)

Izvor: `IMG_5844.MOV` (telefon iz ruke, 1920x1080, 30 fps, 922 kadra). Radna rezolucija 960x540.

- `cache.py` pravi `frames960.npy` (1.4 GB, ne čuva se). Putanja do snimka je u skripti.
- `cands_v1.json`: kandidati po kadru (redosled indeksa važi za `ds_v4.json`).
- `ds_v4.json`: oznake kandidata `[kadar, indeks, 1 loptica / 0 nije]` (554 / 19703).
- `gt_v4.json`: `gt` loptica po kadru `[x, y, w, h]` na 960 px, `ignore`, `neg` (proverene lažne detekcije).
- `extract.py` -> `ds_v4.npz` (patch-evi), `cv_train.py` dvostruka validacija po vremenu (kadrovi < 461 / >= 461),
  `stage1.py` + `stage2.py` metrika trackera na neviđenoj polovini, `export_npz.py` i ONNX izvoz za repo.
- `net_final.pt` -> `ballnet.onnx` / `ballnet.npz` (30 epoha, sve oznake).

Novi snimak sa robota: `cache.py` -> kandidati -> pregled isečaka -> nove oznake u `ds_*.json` -> ponovo trening.

## Alati sa IMG_0385 (demo sto, 27.9.)

Rade iz repoa (putanje su relativne na koren repozitorijuma), težine se čitaju iz `models/ballnet/`.

- `stabilize.py <ulaz.mov> <izlaz.avi> <sekunda_reference>`: poravna snimak iz ruke na jedan kadar, pa se ponaša kao nepomična kamera robota.
- `diag.py <video> <table.json> <start_s>`: pusti snimak kroz ceo lanac (kandidati → BallNet → MHT → odskoci → logika poena), sa razmenom stalno otvorenom. Ispiše odskoke, udarce, predloge i pitanja „Ko je dobio poen?“.
- `dump_cands.py <video> <table.json> <out.npz>`: kandidati, isečci i ocene za svaki kadar, istim kodom kao robot (`CandidateSource` iz repoa).
- `tracklets_auto.py`: putanje po fizici (konstantna brzina), kao automatske oznake.
- `sheet_tracklets.py`: slika sa putanjama za ručni pregled.
- `finetune_0385.py`: doučavanje od `net_final.pt` (pokreće se iz ovog foldera; iskopiraj `models/ballnet/net_final.pt` ovde). `--split` meri na poslednjih 40% snimka.
- `table_0385.json`: kalibracija za stabilizovan `IMG_0385` (referenca 10. s).
- `t0385.json`, `review_ids.txt`: putanje i one koje su pregledane (24 loptice su navedene u `finetune_0385.py`).

`c0385.npz` (480 MB) i stabilizovan snimak nisu u gitu; prave se ponovo sa `stabilize.py` i `dump_cands.py`.
`cache.py` ima fiksnu putanju do `IMG_5844.MOV`; promeni je pre pokretanja.

Rezultat 27.9: doučavanje na 24 putanje nije dalo merljivo poboljšanje (vidi `docs/VISION_PLAN.md`).
