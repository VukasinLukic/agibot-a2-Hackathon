import { TT_API_ROOT, authHeaders } from '../config';
import type {
  CommandEnvelope,
  CommandResult,
  CreateMatchRequest,
  DebugOutputs,
  ErrorResponse,
  HealthResponse,
  MatchSnapshot,
  RobotCall,
  RobotCallRequest,
  RobotStatus,
} from '../generated/contract';

/** HTTP error with the backend ErrorResponse (when the body had one). */
export class ApiError extends Error {
  readonly status: number;
  readonly body: ErrorResponse | null;

  constructor(status: number, body: ErrorResponse | null, fallback: string) {
    super(body?.message ?? fallback);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }

  get code(): string | null {
    return this.body?.code ?? null;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }
}

/** True when the request never got a response (network down, aborted, proxy error). */
export function isNetworkError(err: unknown): boolean {
  return !(err instanceof ApiError) || err.status >= 500;
}

/** UUID v4. crypto.randomUUID only exists in secure contexts, so fall back. */
export function newId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') {
    try {
      return c.randomUUID();
    } catch {
      // insecure context: fall through
    }
  }
  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === 'function') {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as ErrorResponse).code === 'string' &&
    typeof (value as ErrorResponse).message === 'string'
  );
}

async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...authHeaders() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${TT_API_ROOT}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (!res.ok) {
    console.warn(`[TT] ${method} ${path} -> ${res.status}`, parsed);
    throw new ApiError(res.status, isErrorResponse(parsed) ? parsed : null, `HTTP ${res.status}`);
  }
  if (method === 'POST') console.info(`[TT] ${method} ${path} -> ${res.status}`, body);
  return parsed as T;
}

const enc = encodeURIComponent;

export const ttApi = {
  health: (matchId?: string | null, signal?: AbortSignal) =>
    request<HealthResponse>('GET', `/health${matchId ? `?match_id=${enc(matchId)}` : ''}`, undefined, signal),
  listMatches: (signal?: AbortSignal) => request<string[]>('GET', '/matches', undefined, signal),
  createMatch: (body: CreateMatchRequest) => request<MatchSnapshot>('POST', '/matches', body),
  getMatch: (matchId: string, signal?: AbortSignal) =>
    request<MatchSnapshot>('GET', `/matches/${enc(matchId)}`, undefined, signal),
  sendCommand: (matchId: string, command: CommandEnvelope) =>
    request<CommandResult>('POST', `/matches/${enc(matchId)}/commands`, command),
  requestRobotCall: (body: RobotCallRequest) => request<RobotCall>('POST', '/robot/calls', body),
  getRobotCall: (callId: string, signal?: AbortSignal) =>
    request<RobotCall>('GET', `/robot/calls/${enc(callId)}`, undefined, signal),
  cancelRobotCall: (callId: string, commandId: string) =>
    request<RobotCall>('POST', `/robot/calls/${enc(callId)}/cancel`, { command_id: commandId }),
  robotStatus: (signal?: AbortSignal) => request<RobotStatus>('GET', '/robot/status', undefined, signal),
  debugOutputs: (matchId: string | null, signal?: AbortSignal) =>
    request<DebugOutputs>('GET', `/debug/outputs${matchId ? `?match_id=${enc(matchId)}` : ''}`, undefined, signal),
};

/** Short human message for a toast. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.body ? `${err.body.code}: ${err.body.message}` : err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}
