# vision/ (osoba 1, grana comp-vision)

- Plan: `docs/table_tennis_plan/01_COMP_VISION.md`.
- Fixture put: `stub.py` (`FixtureVisionProducer`, `build_proposal`, `propose_command`).
- Živi put: `MatchVisionProducer` u `events.py`. Jedan proces, jedan producer. Ne pokreći oba.
- Šalješ samo `point.propose` i `camera.ready.set` (actor `vision`, u simulaciji `sim`).
  Nikad ne računaš rezultat ni servera.
- Rally, `calibration_id` i `assignment_version` uzimaš iz aktuelnog snapshot-a.
  Posle 409 predlog se baca. Ako je zvuk čut, predlog ide samo kad se pobednik i `missed_return` slože sa slikom.
- `confidence` je model score, ne kalibrisana verovatnoća. Nestanak loptice nije dokaz poena.
- OpenCV/numpy samo iz `requirements-vision.txt`, u zasebnom venv-u; ne uvoziti iz `core/`.
- BlurBall je naučeni detektor (`blurball.py`). Težine su van gita. Torch se ne uvozi na importu paketa.
- Snimci, težine i veliki fajlovi ne idu u Git.
