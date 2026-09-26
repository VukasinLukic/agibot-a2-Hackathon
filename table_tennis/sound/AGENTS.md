# sound/ (osoba 1, grana comp-vision)

- Plan je `README.md` u ovom folderu. Vizija čeka zaključak zvuka i šalje jedan predlog samo kad se slože. Kontakt pre tekuće razmene se ignoriše. `AUTOMATIC_ENABLED` ostaje false. `audio_bridge.py` nije menjana.
- Zvuk daje vreme udara i, kasnije, klasu podloge. Stranu daje vizija.
- Ne šalje `point.propose`. To i dalje radi jedan vision producer, actor `vision`.
- Ne piše rezultat i ne bira servera.
- Ne menja `robot_services/audio/audio_bridge.py`, ugovor, engine, personu ni frontend.
- Sirovi mikrofon treba pre AEC i noise suppression. Obrađen govorni signal nije ulaz.
- Snimci ne idu u git.
