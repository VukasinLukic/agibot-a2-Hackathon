# table_tennis: pravila za agente i članove tima

- Ugovor: `docs/table_tennis_plan/05_SHARED_CONTRACT.md`; kod: `contracts/`.
  `contracts/` i Supervisor registraciju (`robot_supervisor_v2/app/api/main.py`)
  menja samo integrator (osoba 2). Predlog izmene ide kroz PR.
- Posle svake izmene modela: `python -m table_tennis.contracts.generate` i
  `python -m table_tennis.sim.fixtures`. Generisane fajlove ne menjati ručno.
- `core/`, `contracts/`, `storage/`, `api/` ne smeju uvoziti ROS, OpenCV,
  LiveKit, GPU, vendor SDK ni `robot_services`/Supervisor kod. Hardver ide
  isključivo kroz adaptere, i to lenjo (import unutar funkcije).
- `mode: mock` je default. Real se bira eksplicitno i nikad nije automatski fallback.
- Automatsko suđenje ostaje isključeno dok osoba 1 ne isporuči benchmark.
- Ne commitovati `table_tennis/var/`, `.env`, tokene, snimke, modele, baze.
- Nemoj tvrditi da nešto radi na A2 bez fizičkog testa sa mentorom.

Vlasništvo: osoba 1 `vision/`; osoba 2 `contracts/ core/ storage/ api/ sim/`;
osoba 3 `robot/`; osoba 4 `persona/` i `robot_supervisor_v2/frontend/src/features/table-tennis/`.
