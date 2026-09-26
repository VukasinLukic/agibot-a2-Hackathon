import type { MatchSnapshot, MatchStatus, PlayerId, PointReason, RobotCallState } from './generated/contract';

export const STATUS_LABEL: Record<MatchStatus, string> = {
  setup: 'Priprema',
  between_rallies: 'Između poena',
  rally: 'Poen u toku',
  pending_decision: 'Čeka odluku',
  paused: 'Pauza',
  finished: 'Kraj meča',
};

export const REASON_LABEL: Record<PointReason, string> = {
  missed_return: 'Promašen povratak',
  double_bounce: 'Dupli odskok',
  out_after_hit: 'Aut posle udarca',
  service_fault: 'Greška servisa',
  unknown: 'Nepoznato',
};

export const ROBOT_CALL_LABEL: Record<RobotCallState, string> = {
  requested: 'Primljeno',
  validating: 'Proveravam put',
  moving: 'Dolazim',
  arrived: 'Stigao sam',
  ready: 'Spreman',
  failed: 'Potrebna pomoć',
  busy: 'Zauzet',
  cancel_requested: 'Otkazujem',
  cancelled: 'Otkazano',
};

export const ROBOT_CALL_TERMINAL: ReadonlySet<RobotCallState> = new Set<RobotCallState>([
  'ready',
  'failed',
  'busy',
  'cancelled',
]);

export function playerName(snapshot: MatchSnapshot, id: PlayerId | null | undefined): string {
  if (!id) return '?';
  return snapshot.players.find((p) => p.id === id)?.display_name ?? id;
}
