import { useEffect, useState } from 'react';
import { ttApi } from '../api/client';
import type { FakeOutputRecord } from '../generated/contract';

const POLL_MS = 1500;

/**
 * Simulation only: the exact lines the referee code produced for this match
 * (same text the robot will speak), newest first. Hidden with a real backend.
 */
export function SpeechLog({ matchId, revision }: { matchId: string; revision: number }) {
  const [speech, setSpeech] = useState<FakeOutputRecord[]>([]);

  useEffect(() => {
    const ctrl = new AbortController();
    const load = () =>
      ttApi
        .debugOutputs(matchId, ctrl.signal)
        .then((out) => setSpeech((out.speech ?? []).slice(-5).reverse()))
        .catch(() => undefined);
    load();
    // Speech is produced in the background after each change: poll briefly.
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      ctrl.abort();
      window.clearInterval(timer);
    };
  }, [matchId, revision]);

  if (!speech.length) return null;
  const [latest, ...older] = speech;
  return (
    <section aria-label="Titan govori" className="border-l-4 border-[var(--tt-lime)] pl-4">
      <p className="tt-label">Titan kaže · simulacija</p>
      <p className="mt-1 text-lg font-medium leading-snug">{latest.text}</p>
      {older.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm text-[var(--tt-grey)]">
          {older.map((s, i) => (
            <li key={`${s.event_id}-${i}`}>{s.text}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
