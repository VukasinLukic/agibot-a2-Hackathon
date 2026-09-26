import { useEffect, useState, type KeyboardEvent } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type { ConversationStatus, GestureCatalogResponse } from '@/api/types';

interface AgentCommandPanelProps {
  conversation: ConversationStatus;
}

export function AgentCommandPanel({ conversation }: AgentCommandPanelProps) {
  const [text, setText] = useState('');
  const [selectedGesture, setSelectedGesture] = useState<string | null>(null);
  const [gestureCatalog, setGestureCatalog] = useState<GestureCatalogResponse | null>(null);
  const [loadingGestures, setLoadingGestures] = useState(true);
  const [sending, setSending] = useState(false);
  const [forceGesture, setForceGesture] = useState(false);

  useEffect(() => {
    let active = true;

    const loadGestures = async () => {
      try {
        const data = await api.listGestures();
        if (!active) {
          return;
        }
        setGestureCatalog(data);
      } catch (err) {
        if (active) {
          const message = err instanceof Error ? err.message : 'Failed to load movements';
          toast.error(message);
        }
      } finally {
        if (active) {
          setLoadingGestures(false);
        }
      }
    };

    void loadGestures();
    const handleCatalogUpdate = () => {
      void loadGestures();
    };
    window.addEventListener('gesture-catalog-updated', handleCatalogUpdate);

    return () => {
      active = false;
      window.removeEventListener('gesture-catalog-updated', handleCatalogUpdate);
    };
  }, []);

  useEffect(() => {
    const availableGestures = forceGesture
      ? (gestureCatalog?.all_gestures ?? [])
      : (gestureCatalog?.gestures ?? []);

    if (selectedGesture && !availableGestures.includes(selectedGesture)) {
      setSelectedGesture(null);
    }
  }, [forceGesture, gestureCatalog, selectedGesture]);

  const gestures = forceGesture
    ? (gestureCatalog?.all_gestures ?? [])
    : (gestureCatalog?.gestures ?? []);

  const trimmedText = text.trim();
  const canSend = conversation.state === 'engaged' && !sending && (trimmedText.length > 0 || Boolean(selectedGesture));
  const sendLabel =
    trimmedText && selectedGesture ? 'Send message + movement' : selectedGesture ? 'Send movement' : 'Send message';

  const handleSend = async () => {
    const gesture = selectedGesture ?? undefined;

    if (!trimmedText && !gesture) {
      toast.error('Add something for the robot to say or choose a movement first');
      return;
    }

    setSending(true);
    try {
      await api.sendAgentCommand({
        text: trimmedText,
        gesture,
        force_gesture: forceGesture && Boolean(gesture),
        room: conversation.room,
      });
      toast.success(
        gesture && trimmedText
          ? 'Sent message and movement'
          : gesture
            ? 'Sent movement'
            : 'Sent message',
      );
      setText('');
      setSelectedGesture(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to send command';
      toast.error(message);
    } finally {
      setSending(false);
    }
  };

  const handleTextareaKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      if (canSend) {
        void handleSend();
      }
    }
  };

  return (
    <section className="rounded-2xl border border-border/60 bg-card p-5 shadow-sm">
      <h2 className="text-base font-semibold">Tell the Robot What to Do</h2>

      <div className="mt-4 space-y-4">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">What should the robot say?</span>
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={handleTextareaKeyDown}
            rows={3}
            placeholder="Type what you want the robot to say..."
            className="w-full rounded-xl border border-border/60 bg-background px-3 py-2 text-sm outline-none transition focus:border-blue-500"
          />
        </label>

        <div>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              Optional movement
              {gestureCatalog ? ` · pool: ${gestureCatalog.active_pool}` : ''}
            </span>
            {selectedGesture && (
              <button
                type="button"
                onClick={() => setSelectedGesture(null)}
                className="rounded-full border border-border/60 px-2 py-0.5 text-[11px] hover:bg-background"
              >
                Clear
              </button>
            )}
          </div>

          <label className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={forceGesture}
              onChange={(event) => setForceGesture(event.target.checked)}
              className="rounded border border-border/60"
            />
            Force safety override
          </label>

          {loadingGestures ? (
            <div className="rounded-xl border border-dashed border-border/60 px-3 py-4 text-xs text-muted-foreground">
              Loading movements...
            </div>
          ) : (
            <select
              value={selectedGesture ?? ''}
              onChange={(event) => setSelectedGesture(event.target.value || null)}
              className="w-full rounded-xl border border-border/60 bg-background px-3 py-2 text-sm outline-none transition focus:border-blue-500"
            >
              <option value="">No movement</option>
              {gestures.map((gesture) => (
                <option key={gesture} value={gesture}>
                  {gesture}
                </option>
              ))}
            </select>
          )}
        </div>

        {conversation.state !== 'engaged' && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            Start a conversation first to send commands.
          </div>
        )}

        <div className="flex flex-wrap items-center justify-end gap-3">
          <button
            type="button"
            onClick={() => void handleSend()}
            disabled={!canSend}
            className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {sending ? 'Sending...' : sendLabel}
          </button>
        </div>
      </div>
    </section>
  );
}
