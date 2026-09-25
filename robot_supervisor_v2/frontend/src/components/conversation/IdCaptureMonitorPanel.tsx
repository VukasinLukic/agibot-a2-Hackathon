import { useEffect, useMemo, useState } from 'react';
import { api } from '@/api/client';
import type {
  VisionCardCaptureState,
  VisionStatus,
} from '@/api/types';

const TERMINAL_STATES = new Set(['captured', 'failed', 'cancelled', 'expired']);

interface IdCaptureMonitorPanelProps {
  vision?: VisionStatus | null;
}

export function IdCaptureMonitorPanel({ vision }: IdCaptureMonitorPanelProps) {
  const summary = vision?.features?.card_capture ?? null;
  const [detail, setDetail] = useState<VisionCardCaptureState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestId = summary?.request_id ?? detail?.request_id ?? null;
  const state = detail?.state ?? summary?.state ?? 'idle';
  const displayState = mergeCaptureState(summary, detail);
  const metadata = displayState?.result?.metadata ?? displayState?.metadata ?? null;
  const quality = metadata?.quality;
  const imageBase64 = metadata?.image_jpeg_base64;
  const imageMimeType = metadata?.image_mime_type || 'image/jpeg';
  const timestamp = displayState?.result?.timestamp ?? displayState?.completed_at ?? null;
  const pollMs = isTerminalState(state) ? 5000 : 1000;

  useEffect(() => {
    if (!summary?.request_id) {
      setDetail(null);
      setError(null);
    }
  }, [summary?.request_id]);

  useEffect(() => {
    if (!requestId) {
      return;
    }

    let cancelled = false;
    let intervalId: number | null = null;

    const loadCapture = async () => {
      if (document.visibilityState !== 'visible') {
        return;
      }

      try {
        const capture = await api.getVisionCardCapture(requestId);
        if (!cancelled) {
          setDetail(capture);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(String(err));
        }
      }
    };

    void loadCapture();
    intervalId = window.setInterval(loadCapture, pollMs);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void loadCapture();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      cancelled = true;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [pollMs, requestId]);

  const statusClassName = useMemo(() => {
    if (state === 'captured') {
      return 'border-emerald-300 bg-emerald-100 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-200';
    }
    if (state === 'pending' || state === 'running') {
      return 'border-sky-300 bg-sky-100 text-sky-800 dark:border-sky-500/40 dark:bg-sky-500/15 dark:text-sky-200';
    }
    if (state === 'failed' || state === 'expired') {
      return 'border-red-300 bg-red-100 text-red-800 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-200';
    }
    if (state === 'cancelled') {
      return 'border-amber-300 bg-amber-100 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-200';
    }
    return 'border-border bg-muted text-muted-foreground';
  }, [state]);

  return (
    <div className="space-y-3 rounded-xl border border-border/60 bg-muted/50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-muted-foreground">ID Capture</h3>
        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase ${statusClassName}`}>
          {state}
        </span>
      </div>

      <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-border/70 bg-background/70">
        {imageBase64 ? (
          <img
            src={`data:${imageMimeType};base64,${imageBase64}`}
            alt="Latest captured ID card"
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center px-4 text-center">
            <div className="text-xs text-muted-foreground">
              <p className="font-medium text-foreground">{placeholderTitle(state)}</p>
              <p>{placeholderText(state)}</p>
            </div>
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-amber-300/60 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <Metric label="Score" value={formatNumber(quality?.score, 2)} />
        <Metric label="Blur" value={formatNumber(quality?.blur, 0)} />
        <Metric label="Glare" value={formatPercent(quality?.glare_ratio)} />
        <Metric label="Stable" value={formatInteger(quality?.stable_frames)} />
        <Metric label="Time" value={formatTimestamp(timestamp)} />
      </div>
    </div>
  );
}

function mergeCaptureState(
  summary: VisionCardCaptureState | null,
  detail: VisionCardCaptureState | null,
): VisionCardCaptureState | null {
  if (!summary) {
    return detail;
  }
  if (!detail || detail.request_id !== summary.request_id) {
    return summary;
  }
  return {
    ...summary,
    ...detail,
    result: detail.result ?? summary.result,
    metadata: detail.metadata ?? summary.metadata,
  };
}

function isTerminalState(state: string) {
  return TERMINAL_STATES.has(state);
}

function placeholderTitle(state: string) {
  if (state === 'pending' || state === 'running') {
    return 'Waiting for capture';
  }
  if (state === 'failed') {
    return 'Capture failed';
  }
  if (state === 'expired') {
    return 'Capture expired';
  }
  if (state === 'cancelled') {
    return 'Capture cancelled';
  }
  return 'No captured ID image';
}

function placeholderText(state: string) {
  if (state === 'pending' || state === 'running') {
    return 'The image will appear here after the card is accepted.';
  }
  if (isTerminalState(state)) {
    return 'No image is available for this request.';
  }
  return 'Start a capture from the agent flow to populate this panel.';
}

function formatNumber(value: unknown, digits: number) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : 'n/a';
}

function formatInteger(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value)) : 'n/a';
}

function formatPercent(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : 'n/a';
}

function formatTimestamp(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Date(value * 1000).toLocaleTimeString()
    : 'n/a';
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="min-w-0">
      <span className="uppercase">{label}</span>{' '}
      <span className="font-mono text-foreground" title={value}>{value}</span>
    </span>
  );
}
