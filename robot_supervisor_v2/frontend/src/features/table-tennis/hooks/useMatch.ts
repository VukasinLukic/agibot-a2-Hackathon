/**
 * Live match state. The UI renders only backend snapshot values: no optimistic
 * score, no client-side server/score computation.
 *
 * Commands keep a stable command_id per intent (intent key + revision), so a
 * retry of the same intent after a network failure is idempotent on the backend.
 * On 409 the snapshot is refetched, the intent cleared and the operator told to
 * decide again.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, describeError, isNetworkError, newId, ttApi } from '../api/client';
import { openMatchStream } from '../api/events';
import type { ConnectionState } from '../api/events';
import type { CommandEnvelope, CommandResult, EventEnvelope, MatchSnapshot } from '../generated/contract';

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

/** A command without the transport fields (the hook fills those in). */
export type CommandDraft = DistributiveOmit<CommandEnvelope, 'command_id' | 'expected_revision' | 'schema_version'>;

const REVISIONLESS = new Set<CommandEnvelope['type']>(['robot.ready.set', 'camera.ready.set', 'operator.ready.set']);
const MAX_EVENTS = 40;

export interface UseMatchResult {
  snapshot: MatchSnapshot | null;
  connection: ConnectionState;
  connectionDetail: string | null;
  events: EventEnvelope[];
  loadError: string | null;
  /** Intent key currently being sent (buttons use it to show busy state). */
  pendingIntent: string | null;
  send: (intentKey: string, draft: CommandDraft) => Promise<CommandResult | null>;
  resync: () => Promise<void>;
}

/** Console trace for on-robot debugging: state plus the reason the camera cannot propose. */
function logSnapshot(s: MatchSnapshot): void {
  console.info(`[TT] meč ${s.match_id.slice(0, 8)} rev ${s.revision} status=${s.status} mode=${s.scoring_mode}`, {
    score: s.score_by_player,
    ready: s.ready,
    calibration_id: s.calibration_id,
    active_rally_id: s.active_rally_id,
    active_proposal: s.active_proposal,
  });
  if (s.scoring_mode !== 'assisted') console.warn('[TT] kamera: meč nije „Kamera, uz potvrdu” — predlozi su isključeni');
  else if (!s.calibration_id) console.warn('[TT] kamera: meč nema calibration_id — pauza → Postavi kalibraciju');
  else if (!s.ready.camera_ready) console.warn('[TT] kamera: camera_ready=false — vision proces ne radi ili gleda drugi meč');
  else if (s.status === 'between_rallies') console.warn('[TT] kamera: čeka „Servis — kamera gleda” (rally.arm)');
}

function isNewer(prev: MatchSnapshot | null, next: MatchSnapshot): boolean {
  return !prev || prev.match_id !== next.match_id || next.revision >= prev.revision;
}

export function useMatch(matchId: string | null): UseMatchResult {
  const [snapshot, setSnapshot] = useState<MatchSnapshot | null>(null);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [connectionDetail, setConnectionDetail] = useState<string | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pendingIntent, setPendingIntent] = useState<string | null>(null);

  const snapshotRef = useRef<MatchSnapshot | null>(null);
  const intentIds = useRef(new Map<string, string>());
  const inFlight = useRef(new Set<string>());

  const upsert = useCallback((next: MatchSnapshot) => {
    if (!isNewer(snapshotRef.current, next)) return;
    if (snapshotRef.current?.revision !== next.revision || snapshotRef.current?.match_id !== next.match_id) logSnapshot(next);
    snapshotRef.current = next;
    setSnapshot(next);
  }, []);

  const resync = useCallback(async () => {
    if (!matchId) return;
    try {
      const snap = await ttApi.getMatch(matchId);
      upsert(snap);
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err));
    }
  }, [matchId, upsert]);

  useEffect(() => {
    snapshotRef.current = null;
    setSnapshot(null);
    setEvents([]);
    setLoadError(null);
    intentIds.current.clear();
    if (!matchId) return undefined;

    const ctrl = new AbortController();
    ttApi
      .getMatch(matchId, ctrl.signal)
      .then(upsert)
      .catch((err: unknown) => {
        if (!ctrl.signal.aborted) setLoadError(describeError(err));
      });

    const stream = openMatchStream(matchId, {
      onSnapshot: (snap) => upsert(snap),
      onEvent: (event) => {
        console.info(`[TT] događaj ${event.type} rev ${event.revision}`, event.payload);
        setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS));
      },
      onState: (state, detail) => {
        setConnection(state);
        setConnectionDetail(detail ?? null);
      },
    });
    return () => {
      ctrl.abort();
      stream.close();
    };
  }, [matchId, upsert]);

  const send = useCallback(
    async (intentKey: string, draft: CommandDraft): Promise<CommandResult | null> => {
      const snap = snapshotRef.current;
      if (!matchId || !snap) return null;
      if (inFlight.current.has(intentKey)) return null;

      const revisionless = REVISIONLESS.has(draft.type);
      const idKey = `${intentKey}@${revisionless ? 'any' : snap.revision}`;
      let commandId = intentIds.current.get(idKey);
      if (!commandId) {
        commandId = newId();
        intentIds.current.set(idKey, commandId);
      }
      const envelope = {
        ...draft,
        command_id: commandId,
        expected_revision: revisionless ? null : snap.revision,
      } as CommandEnvelope;

      inFlight.current.add(intentKey);
      setPendingIntent(intentKey);
      try {
        const result = await ttApi.sendCommand(matchId, envelope);
        intentIds.current.delete(idKey);
        upsert(result.snapshot);
        return result;
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          intentIds.current.delete(idKey);
          await resync();
          toast.warning('Stanje meča se promenilo. Proveri i odluči ponovo.', {
            description: describeError(err),
          });
        } else if (isNetworkError(err)) {
          // keep the command_id: retrying this intent is idempotent
          toast.error('Komanda nije potvrđena. Pokušaj ponovo.', { description: describeError(err) });
        } else {
          intentIds.current.delete(idKey);
          toast.error('Komanda odbijena.', { description: describeError(err) });
        }
        return null;
      } finally {
        inFlight.current.delete(intentKey);
        setPendingIntent((cur) => (cur === intentKey ? null : cur));
      }
    },
    [matchId, resync, upsert],
  );

  return { snapshot, connection, connectionDetail, events, loadError, pendingIntent, send, resync };
}
