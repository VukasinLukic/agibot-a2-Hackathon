# robot/ (osoba 3, grana navigation)

- Plan: `docs/table_tennis_plan/03_NAVIGATION.md`; audit: `06_REUSE_AUDIT.md`.
- `README.md`: faza 1 iz koda (model, poza, gestovi, vlasnik misije) i lista
  stavki koje čekaju termin sa mentorom. Waypoint i map_id se ne izmišljaju.
- `fake.py`: radni fake ekran, gest i navigator (dry-run, sve se beleži u FakeOutputLog).
- `call_service.py`: idempotentan poziv, single flight (`robot_busy`), persistencija,
  posle restarta nedovršen poziv postaje `failed` (ishod nepoznat).
- `a2_adapters.py`: scaffold sa oznakama `REAL:` gde ide postojeći A2 kod
  (screen_manip, gestures/motion_player, nav_missions/a2_nav). `dry_run=False`
  odbija rad bez eksplicitnog transporta.
- Mapiranje gesta: `winner_id -> robot_side_by_player -> "point left/right"`.
  Kamera-levo i robot-levo nisu ista stvar.
- `accepted` nije `completed`; prihvaćen HTTP poziv nije dolazak; programski cancel nije E-stop.
- Import `robot_services` samo lenjo, unutar real adaptera. Mock nikad ne konstruiše real adapter.
- Poziv prvo pita `readiness.assess`. Ako presuda nije `ready`, stanje poziva je
  `failed` ili `busy` i hod se ne šalje (`native_calls` / `nav.request` ostaju prazni).
  Podrazumevane činjenice su spreman robot, da mock demo i dalje prođe put do `ready`.
- `readiness.py` je čista presuda ready/busy/failed iz činjenica koje `a2_nav.preflight`
  već čita (E-stop, walking akcija, lokalizacija, mapa, sveža poza, zauzet poziv).
  Ne uvozi `robot_services` i ne otvara mrežu. Čitanje tih činjenica sa robota dolazi
  kasnije, iza eksplicitnog real režima, pozivom postojećeg `a2_nav` — taj klijent se ne menja.
- Tuđi paketi se ne menjaju: `contracts/`, `core/`, `api/`, `storage/`, `sim/`,
  `vision/`, `persona/`, frontend, niti `a2_nav.py`, `nav_missions.py`, gestovi i ekran.
  Hod, gest i ekran kasnije idu kroz te postojeće module.
- Registraciju rute u Supervisor main.py radi integrator.
