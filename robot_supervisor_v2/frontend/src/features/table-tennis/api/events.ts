/**
 * Match SSE stream over fetch streaming (not EventSource, so auth headers work
 * and no token ever goes into the URL).
 *
 * - parses `event:` / `id:` / `data:` fields and `:` comment (heartbeat) lines
 * - reconnects with exponential backoff, sending the last seen event id
 * - drops duplicate match events by event_id
 * - reports connecting / live / reconnecting / stale / error
 *
 * Snapshots are handed to the caller, which applies them as an idempotent
 * upsert (only when revision >= current).
 */
import { STREAM_STALE_MS, TT_API_ROOT, authHeaders } from '../config';
import type { EventEnvelope, MatchSnapshot, StreamEventMessage, StreamSnapshotMessage } from '../generated/contract';

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'stale' | 'error';

export interface MatchStreamHandlers {
  onSnapshot: (snapshot: MatchSnapshot, message: StreamSnapshotMessage) => void;
  onEvent: (event: EventEnvelope, message: StreamEventMessage) => void;
  onState: (state: ConnectionState, detail?: string) => void;
}

export interface MatchStreamHandle {
  close: () => void;
}

export interface SseMessage {
  event: string;
  id: string | null;
  data: string;
}

/** Incremental SSE parser. Feed decoded text chunks; get complete messages back. */
export class SseParser {
  private buffer = '';
  private eventName = '';
  private eventId: string | null = null;
  private dataLines: string[] = [];

  push(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const out: SseMessage[] = [];
    let idx: number;
    while ((idx = this.buffer.search(/\r\n|\r|\n/)) >= 0) {
      const line = this.buffer.slice(0, idx);
      const sepLen = this.buffer.startsWith('\r\n', idx) ? 2 : 1;
      // A lone trailing '\r' might be the first half of '\r\n': wait for more.
      if (this.buffer[idx] === '\r' && sepLen === 1 && idx === this.buffer.length - 1) break;
      this.buffer = this.buffer.slice(idx + sepLen);
      const msg = this.line(line);
      if (msg) out.push(msg);
    }
    return out;
  }

  private line(line: string): SseMessage | null {
    if (line === '') {
      if (this.dataLines.length === 0) {
        this.eventName = '';
        return null;
      }
      const msg: SseMessage = {
        event: this.eventName || 'message',
        id: this.eventId,
        data: this.dataLines.join('\n'),
      };
      this.eventName = '';
      this.eventId = null;
      this.dataLines = [];
      return msg;
    }
    if (line.startsWith(':')) return null; // comment / heartbeat
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.eventName = value;
    else if (field === 'data') this.dataLines.push(value);
    else if (field === 'id') this.eventId = value;
    return null;
  }
}

const MAX_SEEN = 500;
const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 10_000;

export function openMatchStream(matchId: string, handlers: MatchStreamHandlers): MatchStreamHandle {
  let closed = false;
  let controller: AbortController | null = null;
  let lastEventId: string | null = null;
  let attempt = 0;
  let lastByteAt = Date.now();
  let staleAborted = false;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  const seen = new Set<string>();
  const seenOrder: string[] = [];

  const remember = (id: string) => {
    seen.add(id);
    seenOrder.push(id);
    if (seenOrder.length > MAX_SEEN) {
      const drop = seenOrder.shift();
      if (drop) seen.delete(drop);
    }
  };

  const watchdog = setInterval(() => {
    if (closed || !controller) return;
    if (Date.now() - lastByteAt > STREAM_STALE_MS) {
      staleAborted = true;
      handlers.onState('stale', 'nema podataka (ni heartbeat)');
      controller.abort();
    }
  }, 2_000);

  const dispatch = (msg: SseMessage) => {
    if (!msg.data) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(msg.data);
    } catch {
      return;
    }
    if (msg.event === 'snapshot') {
      const m = parsed as StreamSnapshotMessage;
      if (m && m.snapshot) handlers.onSnapshot(m.snapshot, m);
    } else if (msg.event === 'match_event') {
      const m = parsed as StreamEventMessage;
      const id = msg.id ?? m?.event?.event_id ?? null;
      if (!m || !m.event) return;
      if (id) {
        lastEventId = id;
        if (seen.has(id)) return;
        remember(id);
      }
      handlers.onEvent(m.event, m);
    }
  };

  const scheduleReconnect = () => {
    if (closed) return;
    const delay = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt) * (0.8 + Math.random() * 0.4);
    attempt += 1;
    if (!staleAborted) handlers.onState('reconnecting', `pokušaj ${attempt}`);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void connect();
    }, delay);
  };

  const connect = async () => {
    if (closed) return;
    controller = new AbortController();
    lastByteAt = Date.now();
    const qs = lastEventId ? `?last_event_id=${encodeURIComponent(lastEventId)}` : '';
    const url = `${TT_API_ROOT}/matches/${encodeURIComponent(matchId)}/events${qs}`;
    try {
      const res = await fetch(url, {
        headers: { Accept: 'text/event-stream', 'Cache-Control': 'no-cache', ...authHeaders() },
        signal: controller.signal,
        cache: 'no-store',
      });
      if (!res.ok || !res.body) {
        if (res.status === 401 || res.status === 403 || res.status === 404) {
          handlers.onState('error', `HTTP ${res.status}`);
          return; // not retryable without user action
        }
        throw new Error(`HTTP ${res.status}`);
      }
      attempt = 0;
      staleAborted = false;
      handlers.onState('live');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const parser = new SseParser();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        lastByteAt = Date.now();
        if (staleAborted) {
          staleAborted = false;
          handlers.onState('live');
        }
        const text = decoder.decode(value, { stream: true });
        for (const msg of parser.push(text)) dispatch(msg);
      }
    } catch {
      // aborted or network error: fall through to reconnect
    } finally {
      controller = null;
    }
    scheduleReconnect();
  };

  handlers.onState('connecting');
  void connect();

  return {
    close: () => {
      closed = true;
      clearInterval(watchdog);
      if (retryTimer) clearTimeout(retryTimer);
      controller?.abort();
    },
  };
}
