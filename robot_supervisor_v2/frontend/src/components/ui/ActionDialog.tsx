import { AlertTriangle, X } from 'lucide-react';

type ActionDialogProps = {
  open: boolean;
  title: string;
  description: string;
  detail?: string | null;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ActionDialog({
  open,
  title,
  description,
  detail,
  confirmLabel,
  cancelLabel = 'Cancel',
  destructive = false,
  loading = false,
  onConfirm,
  onCancel,
}: ActionDialogProps) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && !loading) onCancel();
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="action-dialog-title"
        aria-describedby="action-dialog-description"
        className="w-full max-w-md rounded-xl border border-border bg-card p-5 text-left shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <span className={`mt-0.5 rounded-lg p-2 ${destructive ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'}`}>
            <AlertTriangle size={18} aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="action-dialog-title" className="text-base font-semibold text-foreground">{title}</h2>
            <p id="action-dialog-description" className="mt-1 text-sm leading-6 text-muted-foreground">{description}</p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-50"
            aria-label="Close dialog"
          >
            <X size={18} />
          </button>
        </div>

        {detail ? (
          <details className="mt-4 rounded-lg border border-border/70 bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer font-medium">Technical details</summary>
            <p className="mt-2 break-words font-mono leading-5">{detail}</p>
          </details>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="rounded-md border border-border bg-muted px-3 py-2 text-sm font-medium text-foreground transition hover:bg-muted/70 disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            autoFocus
            className={`rounded-md px-3 py-2 text-sm font-semibold text-white transition disabled:cursor-wait disabled:opacity-60 ${
              destructive ? 'bg-red-600 hover:bg-red-500' : 'bg-[#c91932] hover:bg-[#a9162a] dark:hover:bg-[#df2842]'
            }`}
          >
            {loading ? 'Working...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
