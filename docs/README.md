# Analiza stanja projekta (ažurirano 26. septembra 2026.)

Pregledan je timski `main` na commitu `1f2f48f` (posle PR #14 i #15 „navigation" i commita „fixed
bugs regarding vision and sound pipelines"). Kod nije menjan i ništa nije commit-ovano.

| Dokument | Sadržaj |
|---|---|
| [01_PREGLED_KODA_I_GRESKE.md](01_PREGLED_KODA_I_GRESKE.md) | **samo greške koje i dalje postoje**, šta je popravljeno i šta se meri na robotu |
| [02_VISION_REALTIME_I_LOGIKA_POENA.md](02_VISION_REALTIME_I_LOGIKA_POENA.md) | koliko brzo vision može da radi na levom fisheye-u i logika poena |
| [03_BACKEND_KOMUNIKACIJA_EKRAN_TELEFON.md](03_BACKEND_KOMUNIKACIJA_EKRAN_TELEFON.md) | backend za poene, veza robot–backend, ekran na glavi, telefon |
| [04_STA_FALI_I_SKRIPTE.md](04_STA_FALI_I_SKRIPTE.md) | šta fali po prioritetu i šta sa skriptama i fajlovima u korenu |
| [05_PLAN_TESTIRANJA_NA_ROBOTU.md](05_PLAN_TESTIRANJA_NA_ROBOTU.md) | korak po korak kada dođete do robota (uz Safety Guide) |

## Ukratko

- **Testovi:** 326 passed, 1 skipped, **1 failed** (`test_run_demo_port_and_host`, zbog
  podrazumevanih tokena). `contracts.generate --check` pada (`openapi.json`).
- **Popravljeno od prošle analize:** postoji živi vision proces (`vision.live`); sudija više ne
  predlaže na prvi prazan kadar i traži odskok na strani primaoca; navigacija ima E-stop,
  otkazivanje i proveru dolaska.
- **Pet grešaka koje moramo popraviti:**
  1. javni podrazumevani tokeni (bezbednost, pada test, ne rade komande simulatora);
  2. capture sa fisheye-a vrti CPU i ne primeti ugašenu kameru;
  3. vision proces pada kad backend nije dostupan i ostavlja „kamera spremna";
  4. zastareo OpenAPI, a potvrda dolaska robota nema HTTP rutu (poziv ostaje zaglavljen);
  5. nova LiveKit konekcija za svaku izgovorenu rečenicu (kašnjenje govora).
- **Meriti na robotu (nisu dokazane greške):** izobličenje fisheye-a, veličina loptice, zaklon reketom
  posle odskoka.
