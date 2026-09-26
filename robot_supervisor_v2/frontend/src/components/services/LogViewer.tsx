import { useEffect, useState, useRef } from 'react';
import { api } from '@/api/client';

interface LogViewerProps {
  serviceName: string;
  onClose: () => void;
}

export function LogViewer({ serviceName, onClose }: LogViewerProps) {
  const [logs, setLogs] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const [streaming, setStreaming] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Initial log load
  useEffect(() => {
    loadLogs();
  }, [serviceName]);

  // Auto-scroll to bottom when logs update
  useEffect(() => {
    if (autoScroll && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  // Cleanup SSE on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  const loadLogs = async () => {
    setLoading(true);
    try {
      const response = await api.getServiceLogs(serviceName, 200);
      setLogs(response.content);
    } catch (err) {
      setLogs(`Error loading logs: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const startStreaming = () => {
    if (eventSourceRef.current) return;

    const apiBase = import.meta.env.DEV ? 'http://127.0.0.1:8000' : '';
    const eventSource = new EventSource(
      `${apiBase}/api/services/${serviceName}/logs/stream`
    );

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'initial') {
        setLogs(data.content);
      } else if (data.type === 'line') {
        setLogs(prev => prev + data.content);
      } else if (data.type === 'stopped') {
        eventSource.close();
        setStreaming(false);
      } else if (data.type === 'error') {
        console.error('Log stream error:', data.message);
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
      setStreaming(false);
    };

    eventSourceRef.current = eventSource;
    setStreaming(true);
  };

  const stopStreaming = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setStreaming(false);
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-card text-card-foreground rounded-2xl border border-border/60 shadow-2xl w-full max-w-4xl h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border/60">
          <h2 className="text-xl font-semibold">
            Logs: {serviceName}
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={loadLogs}
              className="px-3 py-1 text-sm border border-border rounded-md bg-background hover:bg-muted transition-colors"
            >
              Refresh
            </button>
            <button
              onClick={streaming ? stopStreaming : startStreaming}
              className={`px-3 py-1 text-sm rounded text-white ${
                streaming
                  ? 'bg-red-600 hover:bg-red-700'
                  : 'bg-blue-600 hover:bg-blue-700'
              }`}
            >
              {streaming ? 'Stop Stream' : 'Start Stream'}
            </button>
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="h-4 w-4 rounded border-border bg-background"
              />
              Auto-scroll
            </label>
            <button
              onClick={onClose}
              className="px-3 py-1 text-sm border border-border rounded-md bg-background hover:bg-muted transition-colors"
            >
              ✕ Close
            </button>
          </div>
        </div>

        {/* Log Content */}
        <div
          ref={logContainerRef}
          className="flex-1 overflow-auto p-4 bg-slate-950 text-slate-100 font-mono text-sm"
        >
          {loading ? (
            <div className="text-slate-400">Loading logs...</div>
          ) : logs ? (
            <pre className="whitespace-pre-wrap">{logs}</pre>
          ) : (
            <div className="text-slate-400">No logs available</div>
          )}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
}
