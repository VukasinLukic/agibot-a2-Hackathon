import { useState } from 'react';
import type { EventEnvelope, MatchSnapshot, PlayerId } from '../generated/contract';
import type { UseMatchResult } from '../hooks/useMatch';
import { REASON_LABEL, playerName } from '../labels';
import { Scoreboard } from './Scoreboard';

interface MatchViewProps {
  snapshot: MatchSnapshot;
  events: EventEnvelope[];
  send: UseMatchResult['send'];
  pendingIntent: string | null;
  /** No fresh revision (offline/stale): every change is disabled. */
  locked: boolean;
}

/**
 * A running match: the table (tap a half = point for that player) and the
 * few actions around it. Nothing here changes the score locally; every tap
 * is a backend command and the table redraws from the returned snapshot.
 */
export function MatchView({ snapshot, events, send, pendingIntent, locked }: MatchViewProps) {
  const { status, scoring_mode: mode } = snapshot;
  const off = locked || pendingIntent !== null;
  const rallyId = snapshot.active_rally_id;
  const playing = status === 'between_rallies' || status === 'rally' || status === 'pending_decision';
  // With the camera, a rally starts with "Servis" so the camera knows to watch.
  const waitingForServe = mode === 'assisted' && status === 'between_rallies';

  const awardPoint = async (winner: PlayerId) => {
    let rally = rallyId;
    if (!rally) {
      // Without the camera one tap does both: open the rally and give the point.
      const armed = await send('rally.arm', { type: 'rally.arm', payload: {} });
      rally = armed?.snapshot.active_rally_id ?? null;
      if (!rally) return;
    }
    await send(`award:${winner}`, {
      type: 'point.award',
      payload: { rally_id: rally, winner_id: winner, reason: 'unknown' },
    });
  };

  const lastPoint = events.find((e) => e.type === 'point.confirmed' && e.event_id === snapshot.last_point_event_id);
  const lastWinner = lastPoint?.type === 'point.confirmed' ? playerName(snapshot, lastPoint.payload.winner_id) : null;
  const canUndo = Boolean(snapshot.last_point_event_id) && status !== 'setup';

  const undo = () => {
    if (!snapshot.last_point_event_id) return;
    const what = lastWinner ? `poslednji poen (${lastWinner})` : 'poslednji poen';
    if (!window.confirm(`Poništiti ${what}?`)) return;
    void send('undo', {
      type: 'point.undo',
      payload: { target_event_id: snapshot.last_point_event_id, reason: 'ispravka iz aplikacije' },
    });
  };

  const proposal = status === 'pending_decision' ? snapshot.active_proposal : null;
  const [calibrationDraft, setCalibrationDraft] = useState(() => ({
    matchId: snapshot.match_id,
    value: snapshot.calibration_id ?? '',
  }));
  const calibrationValue = calibrationDraft.matchId === snapshot.match_id ? calibrationDraft.value : snapshot.calibration_id ?? '';

  const setCalibration = () => {
    const calibrationId = calibrationValue.trim();
    if (!calibrationId) return;
    void send('calibration', { type: 'calibration.set', payload: { calibration_id: calibrationId } });
  };

  return (
    <div className="space-y-4">
      {proposal && (
        <section className="rounded-2xl border-2 border-[var(--tt-ink)] bg-[var(--tt-lime)] p-4">
          <p className="tt-label !text-[var(--tt-ink)]">Kamera predlaže</p>
          <p className="tt-display mt-1 text-3xl font-extrabold">Poen {playerName(snapshot, proposal.winner_id)}</p>
          <p className="mt-1 text-sm">
            {REASON_LABEL[proposal.reason]} · sigurnost modela {Math.round(proposal.confidence * 100)}/100
          </p>
          <button
            type="button"
            disabled={off}
            className="tt-btn mt-3 w-full bg-[var(--tt-ink)] text-[var(--tt-paper)]"
            onClick={() => void send('confirm', { type: 'point.confirm', payload: { proposal_id: proposal.proposal_id } })}
          >
            Potvrdi poen
          </button>
          <p className="mt-2 text-center text-sm">Nije tačno? Dodirni polovinu stola pravog igrača.</p>
        </section>
      )}

      {waitingForServe && (
        <button
          type="button"
          disabled={off}
          className="tt-btn tt-btn-primary w-full text-lg"
          onClick={() => void send('rally.arm', { type: 'rally.arm', payload: {} })}
        >
          Servis — kamera gleda
        </button>
      )}

      <Scoreboard
        snapshot={snapshot}
        onPoint={playing && !waitingForServe ? (id) => void awardPoint(id) : undefined}
        disabled={off}
      />

      {status === 'paused' ? (
        <button
          type="button"
          disabled={off}
          className="tt-btn tt-btn-primary w-full text-lg"
          onClick={() => void send('resume', { type: 'match.resume', payload: { reason: null } })}
        >
          Nastavi meč
        </button>
      ) : (
        status !== 'finished' && (
          <div className="grid grid-cols-3 gap-2">
            <button type="button" disabled={off || !canUndo} onClick={undo} className="tt-btn tt-btn-secondary px-2" title="Poništi poslednji poen">
              Poništi
            </button>
            <button
              type="button"
              disabled={off || !rallyId}
              onClick={() => rallyId && void send('let', { type: 'rally.let', payload: { rally_id: rallyId, reason: 'let' } })}
              className="tt-btn tt-btn-secondary px-2"
              title="Let: poen se ponavlja, rezultat se ne menja"
            >
              Ponovi
            </button>
            <button
              type="button"
              disabled={off}
              onClick={() => void send('pause', { type: 'match.pause', payload: { reason: null } })}
              className="tt-btn tt-btn-secondary px-2"
            >
              Pauza
            </button>
          </div>
        )
      )}

      {status === 'finished' && canUndo && (
        <button type="button" disabled={off} onClick={undo} className="tt-btn tt-btn-secondary w-full">
          Poništi poslednji poen
        </button>
      )}

      {status !== 'finished' && (
        <details className="rounded-2xl border-2 border-[var(--tt-line)] px-4 py-2">
          <summary className="cursor-pointer py-2 font-semibold">Još opcija</summary>
          <div className="space-y-2 pb-2 pt-1">
            <button
              type="button"
              disabled={off || status !== 'between_rallies'}
              className="tt-btn tt-btn-secondary w-full"
              onClick={() =>
                void send('persona', {
                  type: 'persona.set',
                  payload: { persona: snapshot.persona === 'regular' ? 'corporate' : 'regular' },
                })
              }
            >
              {snapshot.persona === 'regular' ? 'Prebaci na korporativnog sudiju' : 'Prebaci na regularnog sudiju'}
            </button>
            {status !== 'between_rallies' && (
              <p className="text-xs text-[var(--tt-grey)]">Sudija se menja samo između poena.</p>
            )}
            {status === 'paused' && (
              <div className="space-y-2 rounded-xl border border-[var(--tt-line)] p-3">
                <label className="block space-y-2">
                  <span className="text-sm font-semibold">Kalibracija kamere</span>
                  <input
                    aria-label="ID kalibracije"
                    className="tt-input"
                    placeholder="table-1-camera-a-v1"
                    maxLength={120}
                  value={calibrationValue}
                  onChange={(e) => setCalibrationDraft({ matchId: snapshot.match_id, value: e.target.value })}
                  />
                </label>
                <button
                  type="button"
                  disabled={off || !calibrationValue.trim()}
                  onClick={setCalibration}
                  className="tt-btn tt-btn-secondary w-full"
                >
                  Postavi kalibraciju
                </button>
              </div>
            )}
            <button
              type="button"
              disabled={off}
              className="tt-btn tt-btn-danger w-full"
              onClick={() => {
                if (window.confirm('Završiti meč sada? Rezultat ostaje kakav jeste.')) {
                  void send('end', { type: 'match.end', payload: { reason: 'završeno iz aplikacije' } });
                }
              }}
            >
              Završi meč
            </button>
          </div>
        </details>
      )}
    </div>
  );
}
