/**
 * Table tennis referee feature config.
 *
 * Mock vs live backend is chosen by configuration only:
 * - VITE_TT_API_BASE: origin prefix for the API (default '' = same origin).
 *   In dev the Vite proxy forwards /api to VITE_API_PROXY_TARGET
 *   (e.g. http://127.0.0.1:8099 for the standalone mock backend).
 * - Never bake bearer tokens into VITE_* variables: those are public JS.
 * - On the robot (token auth) the player enters the operator token
 *   once in the app; it is kept in this browser's localStorage only.
 */

const rawBase: string = import.meta.env.VITE_TT_API_BASE ?? '';

export const TT_API_BASE: string = rawBase.replace(/\/+$/, '');
export const TT_API_ROOT = `${TT_API_BASE}/api/table-tennis`;

export const TT_OPERATOR_TOKEN: string = '';

export const DEFAULT_TABLE_ID = 'table-1';
export const DEFAULT_WAYPOINT_ID = 'referee-spot';

/** Stream is considered stale when no bytes (heartbeat included) arrive for this long. */
export const STREAM_STALE_MS = 40_000;
export const ROBOT_POLL_MS = 700;

export const STORAGE_KEYS = {
  lastMatchId: 'tt.lastMatchId',
  robotTarget: 'tt.robotTarget',
  robotCallId: 'tt.robotCallId',
  operatorToken: 'tt.operatorToken',
  calibrationId: 'tt.calibrationId',
} as const;

/** Only a token entered locally in this browser may authenticate the operator. */
export function operatorToken(): string {
  return storageGet(STORAGE_KEYS.operatorToken)?.trim() || TT_OPERATOR_TOKEN;
}

/** Auth headers for every request (also the SSE stream). */
export function authHeaders(): Record<string, string> {
  const token = operatorToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
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
