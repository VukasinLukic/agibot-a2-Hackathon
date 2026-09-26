import type { MatchSnapshot } from '../generated/contract';
import type { UseMatchResult } from '../hooks/useMatch';
import type { UseRobotCallResult } from '../hooks/useRobotCall';
import { ROBOT_CALL_LABEL, ROBOT_CALL_TERMINAL, playerName } from '../labels';

interface RobotPanelProps {
  snapshot: MatchSnapshot;
  robot: UseRobotCallResult;
  send: UseMatchResult['send'];
  locked: boolean;
}

/** Before the start: get Titan to the table (or say it is already there), then start. */
export function RobotPanel({ snapshot, robot, send, locked }: RobotPanelProps) {
  const { call } = robot;
  const robotReady = Boolean(snapshot.ready.robot_ready);
  const moving = call !== null && !ROBOT_CALL_TERMINAL.has(call.state);
  const failed = call?.state === 'failed' || call?.state === 'busy';
  const status = robotReady ? 'Spreman za meč' : call ? ROBOT_CALL_LABEL[call.state] : 'Još nije pozvan';

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border-2 border-[var(--tt-ink)] p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="tt-label">Sudija Titan</p>
            <p className="tt-display text-3xl font-extrabold">{status}</p>
          </div>
          <span
            aria-hidden="true"
            className={`h-4 w-4 shrink-0 rounded-full ${
              robotReady ? 'bg-[var(--tt-lime)]' : failed ? 'bg-[var(--tt-red)]' : 'bg-[var(--tt-line)]'
            } ${moving ? 'animate-pulse' : ''}`}
          />
        </div>
        {failed && call?.reason && <p className="mt-2 text-sm text-[var(--tt-red)]">{call.reason}</p>}
        {call?.simulated && <p className="mt-2 text-xs text-[var(--tt-grey)]">Simulacija: pravi robot se ne pomera.</p>}

        {!robotReady && (
          <div className="mt-4 grid gap-2">
            {moving ? (
              <button type="button" disabled={robot.busy} onClick={() => void robot.cancel()} className="tt-btn tt-btn-secondary">
                Otkaži poziv
              </button>
            ) : (
              <button
                type="button"
                disabled={robot.busy || locked}
                onClick={() => void robot.request()}
                className="tt-btn tt-btn-primary"
              >
                {failed ? 'Pozovi Titana ponovo' : 'Pozovi Titana do stola'}
              </button>
            )}
            <button
              type="button"
              disabled={locked || moving}
              onClick={() =>
                void send('robot.manual', { type: 'robot.ready.set', payload: { ready: true, reason: 'manual_arrival' } })
              }
              className="tt-btn tt-btn-secondary"
            >
              Titan je već kod stola
            </button>
          </div>
        )}
      </section>

      <button
        type="button"
        disabled={!robotReady || locked}
        onClick={() => void send('start', { type: 'match.start', payload: {} })}
        className="tt-btn tt-btn-primary w-full min-h-16 text-xl"
      >
        Počni meč
      </button>
      <p className="text-center text-sm text-[var(--tt-grey)]">
        {robotReady
          ? `Prvi servira ${playerName(snapshot, snapshot.server_id ?? snapshot.first_server_id)}.`
          : 'Meč može da počne kad Titan bude spreman.'}
      </p>
    </div>
  );
}
