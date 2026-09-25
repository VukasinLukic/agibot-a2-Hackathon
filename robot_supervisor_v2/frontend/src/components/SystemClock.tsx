/**
 * SystemClock — the robot's wall clock, ticking to the second.
 *
 * WHY THIS DOES NOT POLL THE SERVER EVERY SECOND
 * -----------------------------------------------
 * It syncs once, works out the offset between the robot's clock and this
 * browser's, then ticks locally off `Date.now()` corrected by that offset. It
 * re-syncs every `resync_after_s` (60 s by default).
 *
 * The naive alternative — GET /api/system-clock once a second — would cost real
 * time on this machine: `timedatectl` measured 716 ms under load, and the robot
 * already sits at load ~29 on 12 cores. The endpoint is now 0.1 ms because it
 * avoids subprocesses, but calling anything 60x more often than needed for a
 * clock is still the wrong shape.
 *
 * THE SYNC WARNING IS NOT DECORATION
 * ----------------------------------
 * This robot has no NTP (`System clock synchronized: no`). Its clock free-runs,
 * so a scheduled mission fires on a clock nothing is disciplining. The badge and
 * the on-demand drift check exist so that is visible rather than assumed.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '@/api/base';

interface ClockPayload {
  epoch: number;
  timezone: string;
  abbreviation: string;
  utc_offset_seconds: number;
  synchronized: boolean;
  ntp_active: boolean;
  resync_after_s: number;
}

interface DriftPayload {
  ok: boolean;
  drift_seconds?: number;
  round_trip_ms?: number;
  error?: string;
  resolution_note?: string;
}

function two(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** Format an epoch (seconds) in a fixed UTC offset, without touching Intl. */
function formatAt(epochSeconds: number, offsetSeconds: number) {
  const d = new Date((epochSeconds + offsetSeconds) * 1000);
  return {
    time: `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}:${two(d.getUTCSeconds())}`,
    date: `${d.getUTCFullYear()}-${two(d.getUTCMonth() + 1)}-${two(d.getUTCDate())}`,
  };
}

export function SystemClock({ compact = false }: { compact?: boolean }) {
  const [sync, setSync] = useState<ClockPayload | null>(null);
  const [error, setError] = useState('');
  const [drift, setDrift] = useState<DriftPayload | null>(null);
  const [checkingDrift, setCheckingDrift] = useState(false);
  const [, forceTick] = useState(0);

  // Robot epoch minus browser epoch, in seconds. Everything below is rendered
  // from this rather than from a fresh request.
  const offsetRef = useRef<number>(0);

  const resync = useCallback(async () => {
    try {
      const before = Date.now();
      const res = await fetch(`${API_BASE}/api/system-clock`);
      if (!res.ok) throw new Error(`clock ${res.status}`);
      const json: ClockPayload = await res.json();
      const after = Date.now();
      // Assume the response was generated halfway through the round trip, the
      // same correction the drift check makes.
      offsetRef.current = json.epoch - (before + after) / 2000;
      setSync(json);
      setError('');
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    void resync();
  }, [resync]);

  useEffect(() => {
    if (!sync) return;
    const period = Math.max(15, sync.resync_after_s) * 1000;
    const id = window.setInterval(() => void resync(), period);
    return () => window.clearInterval(id);
  }, [sync, resync]);

  // Local tick. 250 ms rather than 1000 ms so the displayed second rolls over
  // promptly instead of lagging by up to a full second.
  useEffect(() => {
    const id = window.setInterval(() => forceTick((n) => n + 1), 250);
    return () => window.clearInterval(id);
  }, []);

  const checkDrift = async () => {
    setCheckingDrift(true);
    try {
      const res = await fetch(`${API_BASE}/api/system-clock/drift`);
      setDrift(await res.json());
    } catch (err) {
      setDrift({ ok: false, error: String(err) });
    } finally {
      setCheckingDrift(false);
    }
  };

  if (error && !sync) {
    return <span className="text-xs text-red-600">clock unavailable: {error}</span>;
  }
  if (!sync) return <span className="text-xs text-muted-foreground">clock…</span>;

  const nowEpoch = Date.now() / 1000 + offsetRef.current;
  const { time, date } = formatAt(nowEpoch, sync.utc_offset_seconds);
  const offsetLabel = `UTC${sync.utc_offset_seconds >= 0 ? '+' : '-'}${two(
    Math.floor(Math.abs(sync.utc_offset_seconds) / 3600),
  )}:${two(Math.floor((Math.abs(sync.utc_offset_seconds) % 3600) / 60))}`;

  if (compact) {
    return (
      <span className="text-xs text-muted-foreground" title={`${sync.timezone} (${offsetLabel})`}>
        <span className="font-mono">{time}</span> {sync.abbreviation}
        {!sync.synchronized ? (
          <span className="ml-1 text-amber-600" title="clock is not NTP-synced">
            ⚠
          </span>
        ) : null}
      </span>
    );
  }

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="font-mono text-3xl tabular-nums">{time}</span>
        <span className="text-sm text-muted-foreground">
          {date} · {sync.timezone} ({sync.abbreviation}, {offsetLabel})
        </span>
      </div>

      {!sync.synchronized ? (
        <p className="mt-2 rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          This clock is <strong>not NTP-synced</strong>
          {sync.ntp_active ? '' : ' and the NTP service is inactive'} — it free-runs
          and can drift. Anything scheduled by wall-clock time fires on <em>this</em>
          {' '}clock, not on real time.
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
        <button
          type="button"
          className="rounded border px-2 py-1"
          disabled={checkingDrift}
          onClick={() => void checkDrift()}
        >
          {checkingDrift ? 'checking…' : 'check drift vs internet'}
        </button>
        {drift ? (
          drift.ok ? (
            <span
              className={
                Math.abs(drift.drift_seconds ?? 0) > 30
                  ? 'text-red-600'
                  : 'text-muted-foreground'
              }
            >
              {(drift.drift_seconds ?? 0) >= 0 ? 'ahead by ' : 'behind by '}
              {Math.abs(drift.drift_seconds ?? 0).toFixed(1)}s
              <span className="ml-1 opacity-70">({drift.resolution_note})</span>
            </span>
          ) : (
            <span className="text-muted-foreground">drift check failed: {drift.error}</span>
          )
        ) : null}
      </div>

      <p className="mt-2 text-[11px] text-muted-foreground">
        To change the timezone:{' '}
        <code>sudo timedatectl set-timezone Europe/Berlin</code>. This panel picks it
        up on its own; the robot&apos;s other services keep the old zone in their log
        timestamps until they are restarted.
      </p>
    </div>
  );
}
