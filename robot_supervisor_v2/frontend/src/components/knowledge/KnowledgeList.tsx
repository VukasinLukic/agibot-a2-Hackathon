import { useState, useEffect } from 'react';
import { api } from '@/api/client';
import type { KnowledgeState } from '@/api/types';
import { toast } from 'sonner';
import { Eye, Trash2, RotateCw, X } from 'lucide-react';
import styles from './Knowledge.module.css';
import { ActionDialog } from '@/components/ui/ActionDialog';

interface KnowledgeListProps {
  refreshTrigger: number;
  readOnly?: boolean;
  title?: string;
}

export function KnowledgeList({ refreshTrigger, readOnly = false, title = 'Documents' }: KnowledgeListProps) {
  const [knowledge, setKnowledge] = useState<KnowledgeState | null>(null);
  const [loading, setLoading] = useState(true);
  const [indexing, setIndexing] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; filename: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [viewTarget, setViewTarget] = useState<{ id: string; filename: string } | null>(null);

  const loadKnowledge = async () => {
    try {
      const data = await api.getKnowledge();
      setKnowledge(data);
    } catch {
      toast.error('Failed to load knowledge base');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadKnowledge();
  }, [refreshTrigger]);

  const handleDelete = async (docId: string) => {
    setDeleting(true);
    try {
      await api.deleteDocument(docId);
      toast.success('Document deleted');
      setDeleteTarget(null);
      await loadKnowledge();
    } catch {
      toast.error('Failed to delete document');
    } finally {
      setDeleting(false);
    }
  };

  const handleIndexAll = async () => {
    setIndexing(true);
    try {
      await api.indexAllDocuments();
      toast.success('Indexing complete');
      loadKnowledge();
    } catch {
      toast.error('Failed to index documents');
    } finally {
      setIndexing(false);
    }
  };

  if (loading) {
    return (
      <div className={styles.panel}>
        Loading...
      </div>
    );
  }

  const hasDocuments = (knowledge?.documents.length || 0) > 0;
  const showScrollHint = (knowledge?.documents.length || 0) > 2;

  return (
    <>
    <div className={styles.panel}>
      <div className={styles.summary}>
        <div>
          <h3 className={styles.summaryTitle}>
            {title}: {knowledge?.documents.length || 0}
          </h3>
          <p className={styles.summaryText}>
            Total chunks: {knowledge?.total_chunks || 0}
          </p>
          {knowledge?.last_indexed ? (
            <p className={styles.summaryText}>
              Last indexed: {new Date(knowledge.last_indexed).toLocaleString()}
            </p>
          ) : (
            <p className={styles.warningText}>Never indexed</p>
          )}
        </div>
        {!readOnly ? (
          <button
            onClick={handleIndexAll}
            disabled={indexing || !hasDocuments}
            className={styles.btnPrimary}
          >
            <RotateCw size={16} />
            {indexing ? 'Indexing...' : 'Re-index All'}
          </button>
        ) : null}
      </div>
      {readOnly ? (
        <div className={styles.protectedNotice}>This index is read-only here. Documents can be viewed but not changed.</div>
      ) : null}
      {showScrollHint && (
        <div className={styles.scrollHint}>↓ Scroll for more documents</div>
      )}

      {hasDocuments ? (
        <div className={`${styles.docs} ${showScrollHint ? styles.docsAfterTwo : ''}`}>
          {knowledge?.documents.map((doc) => (
            <div
              key={doc.id}
              className={styles.docRow}
            >
              <div>
                <p className={styles.docName}>{doc.filename}</p>
                <p className={styles.docMeta}>
                  {doc.chunks} chunks | {(doc.file_size / 1024).toFixed(1)} KB
                </p>
              </div>
              <div className={styles.row}>
                <button
                  type="button"
                  onClick={() => setViewTarget({ id: doc.id, filename: doc.filename })}
                  className={styles.btnSecondary}
                  title="View PDF"
                >
                  <Eye size={15} /> View
                </button>
              {!readOnly ? (
                <button
                  onClick={() => setDeleteTarget({ id: doc.id, filename: doc.filename })}
                  className={styles.iconBtn}
                  title="Delete document"
                >
                  <Trash2 size={16} />
                </button>
              ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className={styles.empty}>
          No documents indexed yet. Upload PDFs above.
        </div>
      )}
    </div>
    <ActionDialog
      open={Boolean(deleteTarget)}
      title="Delete document?"
      description={`“${deleteTarget?.filename ?? ''}” will be removed from the current document target. This cannot be undone.`}
      confirmLabel="Delete document"
      destructive
      loading={deleting}
      onConfirm={() => deleteTarget && void handleDelete(deleteTarget.id)}
      onCancel={() => setDeleteTarget(null)}
    />
    {viewTarget ? (
      <div className="fixed inset-0 z-[75] flex flex-col bg-black/85 p-3 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={`PDF viewer: ${viewTarget.filename}`}>
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between rounded-t-lg border border-border bg-card px-4 py-2">
          <span className="truncate text-sm font-semibold">{viewTarget.filename}</span>
          <button type="button" onClick={() => setViewTarget(null)} className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close PDF viewer"><X size={18} /></button>
        </div>
        <iframe title={viewTarget.filename} src={api.getKnowledgeDocumentFileUrl(viewTarget.id)} className="mx-auto h-full w-full max-w-6xl rounded-b-lg border-x border-b border-border bg-white" />
      </div>
    ) : null}
    </>
  );
}
