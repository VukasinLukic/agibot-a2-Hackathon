import type { Persona } from './generated/contract';

/**
 * Company roles for the corporate persona. Same list (label + rank) as
 * table_tennis/persona/roles.py; the backend maps the label to its jokes.
 * Voluntary and only for fun: never inferred from looks, voice or name.
 */
export const ROLES: ReadonlyArray<{ label: string; rank: number }> = [
  { label: 'Pripravnik', rank: 1 },
  { label: 'Junior', rank: 2 },
  { label: 'Senior', rank: 3 },
  { label: 'Team lead', rank: 4 },
  { label: 'Menadžer', rank: 5 },
  { label: 'Direktor', rank: 6 },
  { label: 'CEO', rank: 7 },
];

export function roleRank(label: string): number | null {
  return ROLES.find((r) => r.label === label)?.rank ?? null;
}

export const PERSONAS: ReadonlyArray<{ id: Persona; title: string; description: string; sample: string }> = [
  {
    id: 'regular',
    title: 'Regularni sudija',
    description: 'Vedar i fin. Jasno najavljuje rezultat i isti je prema oba igrača.',
    sample: '„Poen Ana. Četiri prema tri.”',
  },
  {
    id: 'corporate',
    title: 'Korporativni sudija',
    description: 'Dobronamerno zeza igrače po poziciji u firmi i krišom ima miljenika. Rezultat je uvek tačan.',
    sample: '„Poen Marko. Četiri prema pet. Hijerarhija se malo ljulja.”',
  },
];
