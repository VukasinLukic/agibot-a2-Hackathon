/**
 * Table tennis referee feature config.
 *
 * Mock vs live backend is chosen by configuration only:
 * - VITE_TT_API_BASE: origin prefix for the API (default '' = same origin).
 *   In dev the Vite proxy forwards /api to VITE_API_PROXY_TARGET
 *   (e.g. http://127.0.0.1:8099 for the standalone mock backend).
 * - VITE_TT_OPERATOR_TOKEN: optional bearer token for token auth mode.
 *   Sent only as an Authorization header, never in a URL.
 */

const rawBase: string = import.meta.env.VITE_TT_API_BASE ?? '';

export const TT_API_BASE: string = rawBase.replace(/\/+$/, '');
export const TT_API_ROOT = `${TT_API_BASE}/api/table-tennis`;

const rawToken: string | undefined = import.meta.env.VITE_TT_OPERATOR_TOKEN;
export const TT_OPERATOR_TOKEN: string = rawToken && rawToken.trim() ? rawToken.trim() : 'operator_secret';

export const DEFAULT_TABLE_ID = 'table-1';
export const DEFAULT_WAYPOINT_ID = 'referee-spot';

/** Stream is considered stale when no bytes (heartbeat included) arrive for this long. */
export const STREAM_STALE_MS = 40_000;
export const ROBOT_POLL_MS = 700;

export const STORAGE_KEYS = {
  lastMatchId: 'tt.lastMatchId',
  robotTarget: 'tt.robotTarget',
  robotCallId: 'tt.robotCallId',
} as const;

/** Auth headers for every request (also the SSE stream). */
export function authHeaders(): Record<string, string> {
  return { Authorization: `Bearer ${TT_OPERATOR_TOKEN}` };
}

export function storageGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function storageSet(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // storage unavailable (private mode etc.): ignore
  }
}
