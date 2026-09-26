# core/, storage/, api/, sim/ (osoba 2, grana backend)

- Plan: `docs/table_tennis_plan/02_BACKEND.md`.
- `engine.py` je čist: `decide()` validira i vraća događaje, `apply_event()` je
  jedini prelaz stanja, replay istog log-a daje isto stanje.
- `service.py` je jedini ulaz za promenu meča. Redosled: dozvola actor-a ->
  idempotency -> expected_revision -> domen -> jedna SQLite transakcija -> SSE -> outbox.
- Side effects (`outputs.py`) nikad ne blokiraju transakciju; stari govor/gest se ne ponavlja.
- Promena ugovora: modeli -> `contracts.generate` -> `sim.fixtures` -> obavesti tim.
