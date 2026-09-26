import type { SystemStatus } from './types';
import { API_BASE } from './base';

type SSECallback = (data: SystemStatus) => void;
type ErrorCallback = (error: Event) => void;

export class SSEConnection {
  private eventSource: EventSource | null = null;
  private callbacks: SSECallback[] = [];
  private errorCallbacks: ErrorCallback[] = [];
  private reconnectTimeout: number | null = null;
  private readonly url: string;
  private readonly reconnectDelay = 3000; // 3 seconds

  constructor(url: string = '/api/events') {
    this.url = `${API_BASE}${url}`;
  }

  connect() {
    if (this.eventSource) {
      return; // Already connected
    }

    console.log('[SSE] Connecting to', this.url);
    this.eventSource = new EventSource(this.url);

    this.eventSource.onmessage = (event) => {
      try {
        const data: SystemStatus = JSON.parse(event.data);
        this.callbacks.forEach(cb => cb(data));
      } catch (err) {
        console.error('[SSE] Failed to parse message:', err);
      }
    };

    this.eventSource.onerror = (error) => {
      console.error('[SSE] Connection error:', error);
      this.errorCallbacks.forEach(cb => cb(error));

      // Attempt reconnection
      this.disconnect();
      this.scheduleReconnect();
    };

    this.eventSource.onopen = () => {
      console.log('[SSE] Connected');
      if (this.reconnectTimeout) {
        clearTimeout(this.reconnectTimeout);
        this.reconnectTimeout = null;
      }
    };
  }

  disconnect() {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimeout) {
      return; // Already scheduled
    }
    console.log(`[SSE] Reconnecting in ${this.reconnectDelay}ms...`);
    this.reconnectTimeout = window.setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, this.reconnectDelay);
  }

  subscribe(callback: SSECallback): () => void {
    this.callbacks.push(callback);
    return () => {
      this.callbacks = this.callbacks.filter(cb => cb !== callback);
    };
  }

  onError(callback: ErrorCallback): () => void {
    this.errorCallbacks.push(callback);
    return () => {
      this.errorCallbacks = this.errorCallbacks.filter(cb => cb !== callback);
    };
  }
}
