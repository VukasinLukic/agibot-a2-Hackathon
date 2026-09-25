import { useEffect, useState } from 'react';
import { ChevronDown, RefreshCw, Save, Trash2, UserRound } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type { PeopleFaceIdentity } from '@/api/types';
import { ActionDialog } from '@/components/ui/ActionDialog';

type Draft = {
  display_name: string;
  canonical_name: string;
  aliases: string;
  notes: string;
  match_images: 'both' | 'close' | 'far';
};

function draftFrom(item: PeopleFaceIdentity): Draft {
  return {
    display_name: item.display_name,
    canonical_name: item.canonical_name,
    aliases: item.aliases.join(', '),
    notes: item.notes,
    match_images: item.match_images,
  };
}

export function PeopleFacesTab() {
  const [items, setItems] = useState<PeopleFaceIdentity[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [storePath, setStorePath] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PeopleFaceIdentity | null>(null);

  const load = async () => {
    try {
      const result = await api.getPeopleFaces();
      setItems(result.items);
      setStorePath(result.store_path);
      setDrafts(Object.fromEntries(result.items.map((item) => [item.face_id, draftFrom(item)])));
    } catch (error) {
      toast.error(`Failed to load people: ${error}`);
    }
  };

  useEffect(() => { void load(); }, []);

  const patchDraft = (faceId: string, patch: Partial<Draft>) => {
    setDrafts((current) => ({ ...current, [faceId]: { ...current[faceId], ...patch } }));
  };

  const save = async (item: PeopleFaceIdentity) => {
    const draft = drafts[item.face_id];
    if (!draft) return;
    setWorkingId(item.face_id);
    try {
      await api.updatePeopleFace(item.face_id, {
        display_name: draft.display_name,
        canonical_name: draft.canonical_name,
        aliases: draft.aliases.split(',').map((value) => value.trim()).filter(Boolean),
        notes: draft.notes,
        match_images: draft.match_images,
      });
      await load();
      toast.success('Person mapping saved');
    } catch (error) {
      toast.error(`Failed to save person: ${error}`);
    } finally {
      setWorkingId(null);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    setWorkingId(deleteTarget.face_id);
    try {
      await api.deletePeopleFace(deleteTarget.face_id);
      setDeleteTarget(null);
      await load();
      toast.success('Face identity deleted');
    } catch (error) {
      toast.error(`Failed to delete face identity: ${error}`);
    } finally {
      setWorkingId(null);
    }
  };

  return (
    <section className="rounded-xl border border-border/60 bg-card p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="text-lg font-semibold">People</h2><p className="mt-1 text-xs text-muted-foreground">Face recognition names and canonical RAG identities.</p><p className="mt-2 break-all font-mono text-[11px] text-muted-foreground">Store: {storePath || 'Not loaded'}</p></div>
        <button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-md border border-border bg-muted px-3 py-2 text-xs font-semibold"><RefreshCw size={14} /> Refresh</button>
      </div>

      <div className="mt-4 space-y-3">
        {items.map((item) => {
          const draft = drafts[item.face_id] ?? draftFrom(item);
          const isExpanded = expanded === item.face_id;
          return (
            <article key={item.face_id} className="rounded-lg border border-border/60 bg-muted/35 p-4">
              <button type="button" onClick={() => setExpanded(isExpanded ? null : item.face_id)} className="flex w-full items-center justify-between gap-3 text-left">
                <span className="flex min-w-0 items-center gap-3"><UserRound size={18} className="text-rose-400" /><span><span className="block font-semibold">{item.display_name}</span><span className="block text-xs text-muted-foreground">RAG: {item.canonical_name}</span></span></span>
                <span className="flex items-center gap-2 text-[11px]"><span className={`rounded-full px-2 py-1 ${item.has_close_embedding ? 'bg-emerald-500/15 text-emerald-300' : 'bg-muted text-muted-foreground'}`}>Close</span><span className={`rounded-full px-2 py-1 ${item.has_far_embedding ? 'bg-emerald-500/15 text-emerald-300' : 'bg-muted text-muted-foreground'}`}>Far</span><ChevronDown size={16} className={isExpanded ? 'rotate-180' : ''} /></span>
              </button>
              {isExpanded ? (
                <div className="mt-4 grid gap-4 border-t border-border/60 pt-4 lg:grid-cols-[220px_1fr]">
                  <div className="grid grid-cols-2 gap-2">
                    {(['close', 'far'] as const).map((distance) => {
                      const available = distance === 'close' ? item.has_close_image : item.has_far_image;
                      return <div key={distance}><p className="mb-1 text-[11px] uppercase text-muted-foreground">{distance}</p>{available ? <img src={api.getPeopleFaceImageUrl(item.face_id, distance)} alt={`${item.display_name} ${distance}`} className="aspect-square w-full rounded-md border border-border object-cover" /> : <div className="flex aspect-square items-center justify-center rounded-md border border-dashed border-border text-xs text-muted-foreground">No image</div>}</div>;
                    })}
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-xs font-medium">Robot says<input value={draft.display_name} onChange={(event) => patchDraft(item.face_id, { display_name: event.target.value })} className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2" /></label>
                    <label className="text-xs font-medium">RAG identity<input value={draft.canonical_name} onChange={(event) => patchDraft(item.face_id, { canonical_name: event.target.value })} className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2" /></label>
                    <label className="text-xs font-medium">Match images<select value={draft.match_images} onChange={(event) => patchDraft(item.face_id, { match_images: event.target.value as Draft['match_images'] })} className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2"><option value="both" disabled={!item.has_close_embedding || !item.has_far_embedding}>Both</option><option value="close" disabled={!item.has_close_embedding}>Close</option><option value="far" disabled={!item.has_far_embedding}>Far</option></select></label>
                    <label className="text-xs font-medium">Extra aliases<input value={draft.aliases} onChange={(event) => patchDraft(item.face_id, { aliases: event.target.value })} placeholder="Vesa, Vesko" className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2" /></label>
                    <label className="text-xs font-medium sm:col-span-2">Notes<textarea value={draft.notes} onChange={(event) => patchDraft(item.face_id, { notes: event.target.value })} className="mt-1 min-h-20 w-full rounded-md border border-border bg-background px-3 py-2" /></label>
                    <div className="flex gap-2 sm:col-span-2"><button type="button" onClick={() => void save(item)} disabled={workingId === item.face_id} className="inline-flex items-center gap-2 rounded-md bg-rose-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"><Save size={14} /> Save</button><button type="button" onClick={() => setDeleteTarget(item)} className="inline-flex items-center gap-2 rounded-md border border-red-500/60 px-3 py-2 text-xs font-semibold text-red-300"><Trash2 size={14} /> Delete</button></div>
                  </div>
                </div>
              ) : null}
            </article>
          );
        })}
        {items.length === 0 ? <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No enrolled faces.</div> : null}
      </div>
      <ActionDialog open={Boolean(deleteTarget)} title="Delete face identity?" description={`All stored face embeddings and images for “${deleteTarget?.display_name ?? ''}” will be permanently deleted.`} confirmLabel="Delete person" destructive loading={workingId === deleteTarget?.face_id} onConfirm={() => void remove()} onCancel={() => setDeleteTarget(null)} />
    </section>
  );
}
