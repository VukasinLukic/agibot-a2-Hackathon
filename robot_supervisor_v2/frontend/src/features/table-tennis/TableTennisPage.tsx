/**
 * TitanSudija: the players' app for the A2 table tennis referee (phone-first).
 *
 * One player creates the match on their phone; the other may join from
 * theirs ("Pridruži se") or not at all. Every score change goes through the
 * backend, which Titan's voice, screen and gestures follow.
 */
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import './table-tennis.css';
import { describeError, ttApi } from './api/client';
import { STORAGE_KEYS, storageGet, storageSet } from './config';
import type { CreateMatchRequest, HealthResponse } from './generated/contract';
import { useMatch } from './hooks/useMatch';
import { useRobotCall } from './hooks/useRobotCall';
import { STATUS_LABEL, playerName } from './labels';
import { MatchView } from './components/MatchView';
import { RobotPanel } from './components/RobotPanel';
import { Scoreboard } from './components/Scoreboard';
import { SetupForm } from './components/SetupForm';
import { SpeechLog } from './components/SpeechLog';

const CONNECTION_TEXT = {
  connecting: 'Povezujem se sa sudijom…',
  reconnecting: 'Veza je prekinuta. Povezujem ponovo…',
  stale: 'Veza je prekinuta. Proveri Wi-Fi.',
  error: 'Sudija ne odgovara. Proveri da li je aplikacija na istoj mreži kao robot.',
} as const;

export function TableTennisPage() {
  const [matchId, setMatchId] = useState<string | null>(() => storageGet(STORAGE_KEYS.lastMatchId));
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [creating, setCreating] = useState(false);
  const [formKey, setFormKey] = useState(0);
  const match = useMatch(matchId);
  const robot = useRobotCall(matchId);
  const { snapshot, connection } = match;

  useEffect(() => {
    ttApi.health(matchId).then(setHealth).catch(() => setHealth(null));
  }, [matchId]);

  const openMatch = (id: string | null) => {
    storageSet(STORAGE_KEYS.lastMatchId, id);
    if (!id) storageSet(STORAGE_KEYS.robotCallId, null);
    setMatchId(id);
  };

  const create = async (request: CreateMatchRequest) => {
    setCreating(true);
    try {
      const snap = await ttApi.createMatch(request);
      openMatch(snap.match_id);
    } catch (err) {
      toast.error('Meč nije napravljen.', { description: describeError(err) });
    } finally {
      setCreating(false);
    }
  };

  const joinLatest = async () => {
    try {
      const latest = (await ttApi.listMatches()).at(-1);
      if (!latest) {
        toast.info('Još nema meča. Napravi ga ispod.');
        return;
      }
      openMatch(latest);
    } catch (err) {
      toast.error('Ne mogu da nađem meč.', { description: describeError(err) });
    }
  };

  const newMatch = () => {
    openMatch(null);
    setFormKey((k) => k + 1);
  };

  // A stale stored id (e.g. the mock database was reset) must not trap the user.
  const notFound = Boolean(matchId && match.loadError && !snapshot);
  const locked = connection !== 'live' || !snapshot;
  const inMatch = Boolean(matchId && snapshot && !notFound);

  return (
    <div className="tt">
      <div className="mx-auto w-full max-w-md space-y-5 px-4 pb-10 pt-4">
        <header className="flex items-center justify-between gap-2">
          <p className="tt-display text-2xl font-extrabold leading-none">
            <span className="text-[var(--tt-red)]">TITAN</span>SUDIJA
          </p>
          <div className="flex items-center gap-2">
            {health?.simulated && (
              <span className="rounded-full bg-[var(--tt-ink)] px-2.5 py-1 text-[0.7rem] font-bold tracking-wider text-[var(--tt-lime)]">
                SIMULACIJA
              </span>
            )}
            {inMatch && (
              <button type="button" onClick={newMatch} className="text-sm font-semibold underline underline-offset-4">
                Nov meč
              </button>
            )}
          </div>
        </header>

        {!matchId || notFound ? (
          <>
            {notFound && <p className="text-sm text-[var(--tt-red)]">Prethodni meč više ne postoji. Napravi nov.</p>}
            <button type="button" onClick={() => void joinLatest()} className="tt-btn tt-btn-secondary w-full justify-between">
              <span>Drugi igrač je već napravio meč?</span>
              <span className="underline">Pridruži se</span>
            </button>
            <SetupForm key={formKey} busy={creating} onCreate={(r) => void create(r)} />
          </>
        ) : !snapshot ? (
          <p className="py-16 text-center text-[var(--tt-grey)]">Učitavam meč…</p>
        ) : (
          <>
            {connection !== 'live' && (
              <div role="status" className="rounded-xl bg-[var(--tt-red)] px-4 py-3 text-sm font-medium text-white">
                {CONNECTION_TEXT[connection]} Dugmad rade čim se veza vrati.
              </div>
            )}

            <div className="flex items-baseline justify-between gap-2">
              <p className="tt-display text-3xl font-extrabold">{STATUS_LABEL[snapshot.status]}</p>
              <p className={`text-sm font-semibold ${snapshot.persona === 'corporate' ? 'text-[var(--tt-red)]' : ''}`}>
                {snapshot.persona === 'corporate' ? 'Korporativni sudija' : 'Regularni sudija'}
              </p>
            </div>

            {snapshot.status === 'setup' ? (
              <>
                <Scoreboard snapshot={snapshot} />
                <RobotPanel snapshot={snapshot} robot={robot} send={match.send} locked={locked} />
              </>
            ) : snapshot.status === 'finished' ? (
              <>
                <section className="rounded-2xl bg-[var(--tt-lime)] p-5 text-center">
                  <p className="tt-label !text-[var(--tt-ink)]">{snapshot.winner_id ? 'Pobednik' : 'Meč je prekinut'}</p>
                  {snapshot.winner_id && (
                    <p className="tt-display text-5xl font-extrabold">{playerName(snapshot, snapshot.winner_id)}</p>
                  )}
                </section>
                <MatchView
                  snapshot={snapshot}
                  events={match.events}
                  send={match.send}
                  pendingIntent={match.pendingIntent}
                  locked={locked}
                />
                <button type="button" onClick={newMatch} className="tt-btn tt-btn-primary w-full min-h-16 text-xl">
                  Nov meč
                </button>
              </>
            ) : (
              <MatchView
                snapshot={snapshot}
                events={match.events}
                send={match.send}
                pendingIntent={match.pendingIntent}
                locked={locked}
              />
            )}

            {health?.simulated && <SpeechLog matchId={snapshot.match_id} revision={snapshot.revision} />}
          </>
        )}
      </div>
    </div>
  );
}
