# persona/ (osoba 4, grana persone)

- Plan: `docs/table_tennis_plan/04_PERSONE.md`.
- `templates.py`: determinističke replike sa varijacijama (regular/corporate);
  `roles.py`: pozicije za korporativnu personu (isti spisak u frontend `roles.ts`);
  `commentator.py`: događaj + snapshot -> jedna rečenica (izbor varijante, šale i
  nasumičnog miljenika je stabilan hash match/event id-a); `speech.py`: `FakeSpeechOutput` i
  `LiveKitSpeechOutput` (dry-run scaffold).
- Rezultat u rečenici uvek dolazi iz backend snapshot-a (`score_sentence`), odvojeno od šale.
- Persona nema prava upisa rezultata; imena/role su podaci, ne instrukcije.
- LiveKit se uvozi samo lenjo u real putanji; mock radi bez cloud-a.
- Frontend feature je u `robot_supervisor_v2/frontend/src/features/table-tennis/`;
  tipove uzimaš iz `generated/contract.ts` (ne menjati ručno).
- `joke_bank.py`: opcione lične šale (LLM jednom po meču, `TT_LLM_JOKES=1`), uvek sa fallback-om na šablone.
- Agent strana: `livekit-client/referee_mode.py` i persone `titan_*` u `livekit_config/prompts/personas.yaml`.
