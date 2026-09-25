import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { CheckCircle2, Circle, Database, LockKeyhole, Plus, Settings2, Trash2 } from 'lucide-react';
import { api } from '@/api/client';
import type { KnowledgeIndexItem } from '@/api/types';
import { KnowledgeUpload } from './KnowledgeUpload';
import { KnowledgeList } from './KnowledgeList';
import { KnowledgeSearch } from './KnowledgeSearch';
import { ActionDialog } from '@/components/ui/ActionDialog';
import styles from './Knowledge.module.css';

const MAIN_INDEX_SLUG = 'main';

function slugifyIndexTitle(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

function isProtectedIndex(source: KnowledgeIndexItem) {
  return source.slug === MAIN_INDEX_SLUG || Boolean(source.immutable);
}

function canManageDocuments(source: KnowledgeIndexItem | null) {
  return Boolean(source && source.kind === 'managed' && !isProtectedIndex(source));
}

function canViewDocuments(source: KnowledgeIndexItem | null) {
  return Boolean(source && source.kind === 'managed');
}

function describeIndex(source: KnowledgeIndexItem) {
  return `${source.slug} | ${source.kind} | ${source.collection_name}`;
}

export function KnowledgeTab() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [sources, setSources] = useState<KnowledgeIndexItem[]>([]);
  const [activeSource, setActiveSource] = useState<KnowledgeIndexItem | null>(null);
  const [selectedSourceSlug, setSelectedSourceSlug] = useState('');
  const [querySlugs, setQuerySlugs] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [createTitle, setCreateTitle] = useState('');
  const [createSlug, setCreateSlug] = useState('');
  const [createDescription, setCreateDescription] = useState('');
  const [slugEdited, setSlugEdited] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deletingSlug, setDeletingSlug] = useState<string | null>(null);
  const [queryUpdating, setQueryUpdating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeIndexItem | null>(null);
  const [ragError, setRagError] = useState<string | null>(null);
  const [startingRag, setStartingRag] = useState(false);

  const handleUploadSuccess = () => {
    setRefreshKey((k) => k + 1);
  };

  const loadSources = async (preferredSlug?: string) => {
    try {
      const data = await api.getKnowledgeIndexOptions();
      const nextSources = data.options.indexes ?? [];
      const nextActive = data.active ?? null;
      const nextQuerySlugs = data.options.query_slugs?.filter((slug) =>
        nextSources.some((item) => item.slug === slug)
      ) ?? [];

      setSources(nextSources);
      setActiveSource(nextActive);
      setQuerySlugs(nextQuerySlugs.length > 0 ? nextQuerySlugs : nextActive ? [nextActive.slug] : []);
      setRagError(null);

      setSelectedSourceSlug((current) => {
        if (preferredSlug && nextSources.some((item) => item.slug === preferredSlug)) {
          return preferredSlug;
        }

        if (current && nextSources.some((item) => item.slug === current)) {
          return current;
        }

        return nextActive?.slug ?? nextSources[0]?.slug ?? '';
      });
      return true;
    } catch (error) {
      setSources([]);
      setActiveSource(null);
      setSelectedSourceSlug('');
      setQuerySlugs([]);
      setRagError(error instanceof Error ? error.message : String(error));
      return false;
    }
  };

  useEffect(() => {
    void loadSources();
  }, []);

  const querySources = useMemo(
    () => querySlugs
      .map((slug) => sources.find((source) => source.slug === slug))
      .filter((source): source is KnowledgeIndexItem => Boolean(source)),
    [querySlugs, sources]
  );

  const selectedSource = sources.find((source) => source.slug === selectedSourceSlug) ?? null;
  const viewableSources = sources.filter((source) => canViewDocuments(source));
  const managementDisabledReason = selectedSource && !canViewDocuments(selectedSource)
    ? selectedSource.kind === 'external'
      ? 'External indexes are search-only and do not expose local document files.'
      : 'This index cannot expose local document files.'
    : null;
  const querySummary = querySources.length > 0
    ? querySources.map((source) => source.title).join(' + ')
    : 'No indexes selected';

  const handleSetActiveSource = async () => {
    if (!selectedSourceSlug) {
      return;
    }

    const selected = sources.find((source) => source.slug === selectedSourceSlug);
    if (!canViewDocuments(selected ?? null)) {
      toast.error('Choose a managed index to view documents');
      return;
    }

    setLoading(true);
    try {
      const result = await api.updateKnowledgeIndex(selectedSourceSlug);
      setActiveSource(result.active);
      setSelectedSourceSlug(result.active.slug);
      setRefreshKey((k) => k + 1);
      toast.success(`Document view target set to ${result.active.title}`);
    } catch (error) {
      toast.error(`Failed to switch document view target: ${error}`);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateTitleChange = (value: string) => {
    setCreateTitle(value);
    if (!slugEdited) {
      setCreateSlug(slugifyIndexTitle(value));
    }
  };

  const handleCreateSlugChange = (value: string) => {
    setSlugEdited(true);
    setCreateSlug(slugifyIndexTitle(value));
  };

  const handleCreateIndex = async () => {
    const title = createTitle.trim();
    const slug = createSlug.trim() || slugifyIndexTitle(title);
    const description = createDescription.trim();

    if (!title || !slug) {
      toast.error('Name and slug are required');
      return;
    }

    if (slug === MAIN_INDEX_SLUG) {
      toast.error('Main is a protected index and cannot be recreated');
      return;
    }

    setCreating(true);
    try {
      const created = await api.createKnowledgeIndex({
        slug,
        title,
        description,
        kind: 'managed',
      });
      setCreateTitle('');
      setCreateSlug('');
      setCreateDescription('');
      setSlugEdited(false);
      await loadSources(created.slug);
      toast.success(`Knowledge index created: ${created.title}`);
    } catch (error) {
      toast.error(`Failed to create knowledge index: ${error}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteIndex = async (source: KnowledgeIndexItem) => {
    if (isProtectedIndex(source)) {
      toast.error('Main and protected indexes cannot be deleted');
      return;
    }

    setDeletingSlug(source.slug);
    try {
      const result = await api.deleteKnowledgeIndex(source.slug);
      await loadSources(result.active.slug);
      setRefreshKey((k) => k + 1);
      toast.success(`Knowledge index deleted: ${source.title}`);
      setDeleteTarget(null);
    } catch (error) {
      toast.error(`Failed to delete knowledge index: ${error}`);
    } finally {
      setDeletingSlug(null);
    }
  };

  const handleStartRag = async () => {
    setStartingRag(true);
    try {
      await api.startService('rag-service');
      const loaded = await loadSources();
      if (loaded) toast.success('RAG service is running');
    } catch (error) {
      setRagError(error instanceof Error ? error.message : String(error));
    } finally {
      setStartingRag(false);
    }
  };

  const updateQuerySelection = async (nextSlugs: string[]) => {
    const uniqueSlugs = nextSlugs.filter((slug, index, all) => all.indexOf(slug) === index);
    if (uniqueSlugs.length === 0) {
      toast.error('Select at least one index for RAG queries');
      return;
    }

    setQueryUpdating(true);
    try {
      const result = await api.updateKnowledgeQueryIndexes(uniqueSlugs);
      setQuerySlugs(result.query_slugs);
      setRefreshKey((k) => k + 1);
    } catch (error) {
      toast.error(`Failed to update RAG query indexes: ${error}`);
    } finally {
      setQueryUpdating(false);
    }
  };

  const handleToggleQueryIndex = async (source: KnowledgeIndexItem) => {
    if (querySlugs.includes(source.slug)) {
      await updateQuerySelection(querySlugs.filter((slug) => slug !== source.slug));
      return;
    }

    await updateQuerySelection([...querySlugs, source.slug]);
  };

  return (
    <>
      <div className={styles.sectionHeader}>
        <div>
          <h2 className={styles.sectionTitle}>RAG Knowledge</h2>
          <div className={styles.activeInline}>
            <span className={styles.queryBadge}>{querySources.length} queried</span>
            <span className={styles.activeInlineText}>{querySummary}</span>
          </div>
        </div>
      </div>

      <div className={styles.stack}>
        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <div>
              <h3 className={styles.panelTitle}>Agent Query Indexes</h3>
              <p className={styles.panelSubtitle}>
                These indexes are searched together for frontend search and for the voice agent RAG context.
              </p>
            </div>
          </div>

          <div className={styles.querySummaryBar}>
            <Database size={16} />
            <span className={styles.querySummaryLabel}>Current combination</span>
            <span className={styles.querySummaryText}>{querySummary}</span>
          </div>

          <div className={styles.queryMatrix}>
            {sources.map((source) => {
              const queried = querySlugs.includes(source.slug);
              const protectedIndex = isProtectedIndex(source);
              const lastSelected = queried && querySlugs.length <= 1;

              return (
                <button
                  key={source.slug}
                  type="button"
                  className={`${styles.queryMatrixRow} ${queried ? styles.queryMatrixRowSelected : ''}`}
                  onClick={() => void handleToggleQueryIndex(source)}
                  disabled={queryUpdating || lastSelected}
                  title={lastSelected ? 'At least one index must remain queried' : queried ? 'Stop querying this index' : 'Query this index'}
                >
                  <span className={styles.queryStateIcon}>
                    {queried ? <CheckCircle2 size={18} /> : <Circle size={18} />}
                  </span>
                  <span className={styles.indexInfo}>
                    <span className={styles.indexTitleLine}>
                      <span className={styles.indexTitle}>{source.title}</span>
                      {source.slug === MAIN_INDEX_SLUG ? <span className={styles.lockBadge}><LockKeyhole size={12} /> Main</span> : null}
                      {protectedIndex && source.slug !== MAIN_INDEX_SLUG ? <span className={styles.readOnlyBadge}>Protected</span> : null}
                      {source.kind === 'external' ? <span className={styles.readOnlyBadge}>External</span> : null}
                    </span>
                    <span className={styles.indexMeta}>{describeIndex(source)}</span>
                  </span>
                  <span className={queried ? styles.queriedPill : styles.notQueriedPill}>
                    {queried ? 'Queried' : 'Not queried'}
                  </span>
                </button>
              );
            })}
            {sources.length === 0 ? (
              <div className={styles.emptyCompact}>No knowledge indexes are available.</div>
            ) : null}
          </div>
        </div>

        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <div>
              <h3 className={styles.panelTitle}>Document View Target</h3>
              <p className={styles.panelSubtitle}>
                Main can be viewed here, but upload, delete, and re-index actions only appear for editable managed indexes.
              </p>
            </div>
          </div>

          <div className={styles.managementGrid}>
            <div className={styles.managementCurrent}>
              <Settings2 size={16} />
              <div>
                <div className={styles.managementLabel}>Current document target</div>
                <div className={styles.managementTitle}>{activeSource?.title ?? 'None selected'}</div>
                {activeSource ? <div className={styles.indexMeta}>{describeIndex(activeSource)}</div> : null}
              </div>
            </div>
            <div className={styles.headerControls}>
              <select
                value={selectedSourceSlug}
                onChange={(e) => setSelectedSourceSlug(e.target.value)}
                className={`${styles.input} ${styles.sourceSelect}`}
                disabled={sources.length === 0 || viewableSources.length === 0}
              >
                <option value="">Choose managed index...</option>
                {sources.map((item) => (
                  <option key={item.slug} value={item.slug} disabled={!canViewDocuments(item)}>
                    {item.title}{canViewDocuments(item) ? '' : ' (external)'}
                  </option>
                ))}
              </select>

              <button
                type="button"
                onClick={() => void handleSetActiveSource()}
                disabled={
                  loading
                  || !selectedSourceSlug
                  || activeSource?.slug === selectedSourceSlug
                  || !canViewDocuments(selectedSource)
                }
                className={styles.btnPrimary}
              >
                {loading ? 'Switching...' : 'View Documents'}
              </button>
            </div>
          </div>
          {managementDisabledReason ? (
            <div className={styles.protectedNotice}>{managementDisabledReason}</div>
          ) : null}
        </div>

        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <div>
              <h3 className={styles.panelTitle}>Indexes</h3>
              <p className={styles.panelSubtitle}>
                Create additional managed indexes. Main is protected and cannot be edited or deleted.
              </p>
            </div>
          </div>

          <div className={styles.indexCreateGrid}>
            <input
              type="text"
              className={styles.input}
              placeholder="Name"
              value={createTitle}
              onChange={(e) => handleCreateTitleChange(e.target.value)}
            />
            <input
              type="text"
              className={styles.input}
              placeholder="Slug"
              value={createSlug}
              onChange={(e) => handleCreateSlugChange(e.target.value)}
            />
            <input
              type="text"
              className={styles.input}
              placeholder="Description"
              value={createDescription}
              onChange={(e) => setCreateDescription(e.target.value)}
            />
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => void handleCreateIndex()}
              disabled={creating || !createTitle.trim() || !createSlug.trim() || createSlug.trim() === MAIN_INDEX_SLUG}
            >
              <Plus size={16} />
              {creating ? 'Creating...' : 'Create'}
            </button>
          </div>

          <div className={styles.indexList}>
            {sources.map((source) => {
              const isManagementTarget = activeSource?.slug === source.slug;
              const isQueried = querySlugs.includes(source.slug);
              const protectedIndex = isProtectedIndex(source);
              const canDelete =
                !protectedIndex
                && sources.length > 1;

              return (
                <div key={source.slug} className={styles.indexRow}>
                  <div className={styles.indexInfo}>
                    <div className={styles.indexTitleLine}>
                      <span className={styles.indexTitle}>{source.title}</span>
                      {isQueried ? <span className={styles.queryBadge}>Queried</span> : <span className={styles.notQueriedPill}>Not queried</span>}
                      {isManagementTarget ? <span className={styles.activeBadge}>Document target</span> : null}
                      {source.slug === MAIN_INDEX_SLUG ? <span className={styles.lockBadge}><LockKeyhole size={12} /> Main</span> : null}
                      {protectedIndex && source.slug !== MAIN_INDEX_SLUG ? <span className={styles.readOnlyBadge}>Protected</span> : null}
                      {source.kind === 'external' ? <span className={styles.readOnlyBadge}>External</span> : null}
                    </div>
                    <div className={styles.indexMeta}>{describeIndex(source)}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(source)}
                    className={styles.iconBtn}
                    title={canDelete ? 'Delete knowledge index' : 'This knowledge index cannot be deleted'}
                    disabled={!canDelete || deletingSlug === source.slug}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        <KnowledgeSearch refreshTrigger={refreshKey} />

        {canViewDocuments(activeSource) ? (
          <>
            {canManageDocuments(activeSource) ? <KnowledgeUpload onUploadSuccess={handleUploadSuccess} /> : null}
            <KnowledgeList
              refreshTrigger={refreshKey}
              readOnly={!canManageDocuments(activeSource)}
              title={`${activeSource?.title ?? 'Index'} documents`}
            />
          </>
        ) : (
          <div className={styles.panel}>
            <div className={styles.muted}>
              Select a managed index as the document view target to inspect local documents.
            </div>
          </div>
        )}
      </div>

      <ActionDialog
        open={Boolean(ragError)}
        title="You need to RUN RAG"
        description="RAG must be running before indexes and documents can be loaded. Start it now?"
        detail={ragError}
        confirmLabel="YES"
        cancelLabel="NO"
        loading={startingRag}
        onConfirm={() => void handleStartRag()}
        onCancel={() => setRagError(null)}
      />

      <ActionDialog
        open={Boolean(deleteTarget)}
        title="Delete knowledge index?"
        description={`The index “${deleteTarget?.title ?? ''}” and its managed data will be deleted permanently. This cannot be undone.`}
        confirmLabel="Delete index"
        destructive
        loading={Boolean(deleteTarget && deletingSlug === deleteTarget.slug)}
        onConfirm={() => deleteTarget && void handleDeleteIndex(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
}
