import { useState } from 'react';
import type { CreateMatchRequest, Persona, PlayerId, ScoringMode } from '../generated/contract';
import { newId } from '../api/client';
import { DEFAULT_TABLE_ID } from '../config';
import { PERSONAS, ROLES, roleRank } from '../roles';

const NAME_MAX = 40;

interface PlayerDraft {
  name: string;
  role: string; // '' = no role
}

interface SetupFormProps {
  busy: boolean;
  onCreate: (request: CreateMatchRequest) => void;
}

/** New match: who plays, who referees, who serves; rarely-changed settings folded away. */
export function SetupForm({ busy, onCreate }: SetupFormProps) {
  const [players, setPlayers] = useState<[PlayerDraft, PlayerDraft]>([
    { name: '', role: '' },
    { name: '', role: '' },
  ]);
  const [persona, setPersona] = useState<Persona>('regular');
  const [firstServer, setFirstServer] = useState<PlayerId>('p1');
  const [leftOfRobot, setLeftOfRobot] = useState<PlayerId>('p1');
  const [mode, setMode] = useState<ScoringMode>('manual');
  // One id per form: a retried submit is idempotent on the backend.
  const [commandId] = useState(newId);

  const names = players.map((p) => p.name.trim());
  const filled = names.every((n) => n.length > 0);
  const sameNames = filled && names[0].toLowerCase() === names[1].toLowerCase();
  const valid = filled && !sameNames;
  const label = (id: PlayerId) => names[id === 'p1' ? 0 : 1] || (id === 'p1' ? 'Igrač 1' : 'Igrač 2');
  const playerOptions = [
    { value: 'p1' as const, label: label('p1') },
    { value: 'p2' as const, label: label('p2') },
  ];

  const update = (index: 0 | 1, patch: Partial<PlayerDraft>) =>
    setPlayers((prev) => {
      const next: [PlayerDraft, PlayerDraft] = [{ ...prev[0] }, { ...prev[1] }];
      next[index] = { ...next[index], ...patch };
      return next;
    });

  const submit = () => {
    if (!valid) return;
    const p1Left = leftOfRobot === 'p1';
    const withRoles = persona === 'corporate';
    onCreate({
      command_id: commandId,
      table_id: DEFAULT_TABLE_ID,
      players: players.map((p, i) => ({
        id: i === 0 ? 'p1' : 'p2',
        display_name: p.name.trim(),
        role_label: withRoles && p.role ? p.role : null,
        role_rank: withRoles && p.role ? roleRank(p.role) : null,
      })),
      config: { first_server_id: firstServer, persona, scoring_mode: mode },
      robot_side_by_player: p1Left ? { p1: 'left', p2: 'right' } : { p1: 'right', p2: 'left' },
      court_end_by_player: p1Left ? { p1: 'end_a', p2: 'end_b' } : { p1: 'end_b', p2: 'end_a' },
    });
  };

  return (
    <div className="space-y-7">
      <section className="space-y-3">
        <h2 className="tt-display text-2xl font-extrabold">Ko igra?</h2>
        {([0, 1] as const).map((i) => (
          <div key={i} className="space-y-2">
            <input
              id={`tt-name-${i}`}
              aria-label={`Ime igrača ${i + 1}`}
              className="tt-input"
              placeholder={`Ime igrača ${i + 1}`}
              maxLength={NAME_MAX}
              autoComplete="off"
              value={players[i].name}
              onChange={(e) => update(i, { name: e.target.value })}
            />
            {persona === 'corporate' && (
              <select
                aria-label={`Pozicija igrača ${i + 1} u firmi`}
                className="tt-input text-base"
                value={players[i].role}
                onChange={(e) => update(i, { role: e.target.value })}
              >
                <option value="">Pozicija u firmi (nije obavezno)</option>
                {ROLES.map((r) => (
                  <option key={r.label} value={r.label}>
                    {r.label}
                  </option>
                ))}
              </select>
            )}
          </div>
        ))}
        {sameNames && <p className="text-sm text-[var(--tt-red)]">Upiši različita imena, da sudija zna ko je ko.</p>}
      </section>

      <section className="space-y-3">
        <h2 className="tt-display text-2xl font-extrabold">Ko sudi?</h2>
        <div className="grid gap-3">
          {PERSONAS.map((p) => {
            const selected = persona === p.id;
            return (
              <button
                key={p.id}
                type="button"
                aria-pressed={selected}
                onClick={() => setPersona(p.id)}
                className={`relative rounded-2xl border-2 p-4 text-left transition ${
                  selected ? 'border-[var(--tt-ink)] bg-[var(--tt-lime)]' : 'border-[var(--tt-line)] bg-[var(--tt-paper)]'
                }`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-lg font-bold">{p.title}</span>
                  <span
                    aria-hidden="true"
                    className={`h-5 w-5 rounded-full border-2 border-[var(--tt-ink)] ${selected ? 'bg-[var(--tt-ink)]' : ''}`}
                  />
                </span>
                <span className="mt-1 block text-sm">{p.description}</span>
                <span className="mt-2 block text-sm italic opacity-80">{p.sample}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="tt-display text-2xl font-extrabold">Prvi servira</h2>
        <Segmented value={firstServer} onChange={setFirstServer} options={playerOptions} label="Prvi servira" />
      </section>

      <details className="rounded-2xl border-2 border-[var(--tt-line)] px-4 py-2">
        <summary className="cursor-pointer py-2 font-semibold">Podešavanja stola</summary>
        <div className="space-y-4 pb-3 pt-2">
          <div className="space-y-2">
            <p className="text-sm font-semibold">Levo od Titana stoji</p>
            <Segmented value={leftOfRobot} onChange={setLeftOfRobot} options={playerOptions} label="Levo od Titana stoji" />
            <p className="text-xs text-[var(--tt-grey)]">Gledano iz ugla robota, da pokaže rukom na pravu stranu.</p>
          </div>
          <div className="space-y-2">
            <p className="text-sm font-semibold">Poene dodeljuje</p>
            <Segmented
              value={mode}
              onChange={setMode}
              label="Poene dodeljuje"
              options={[
                { value: 'manual', label: 'Mi, dodirom' },
                { value: 'assisted', label: 'Kamera, uz potvrdu' },
              ]}
            />
            <p className="text-xs text-[var(--tt-grey)]">
              Sa kamerom Titan sam predlaže poen, a vi ga potvrđujete. Bez kamere poen dodeljujete dodirom.
            </p>
          </div>
        </div>
      </details>

      <button type="button" disabled={!valid || busy} onClick={submit} className="tt-btn tt-btn-primary w-full min-h-16 text-xl">
        {busy ? 'Pravim meč…' : 'Napravi meč'}
      </button>
    </div>
  );
}

interface SegmentedProps<T extends string> {
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string }>;
}

function Segmented<T extends string>({ label, value, onChange, options }: SegmentedProps<T>) {
  return (
    <div role="group" aria-label={label} className="tt-seg">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          <span className="block truncate">{o.label}</span>
        </button>
      ))}
    </div>
  );
}
