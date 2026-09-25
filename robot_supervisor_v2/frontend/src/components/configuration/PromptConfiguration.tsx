import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, LockKeyhole, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type { PromptItem } from '@/api/types';
import { ActionDialog } from '@/components/ui/ActionDialog';
import styles from './PromptConfiguration.module.css';

type EditorMode = 'main' | 'create' | 'edit' | null;

type PromptForm = {
  title: string;
  prompt_text: string;
  initial_greeting: string;
  goodbye_text: string;
  is_archived: boolean;
};

const EMPTY_FORM: PromptForm = {
  title: '',
  prompt_text: '',
  initial_greeting: '',
  goodbye_text: '',
  is_archived: false,
};

const PROMPT_SKELETON = `Persona:
  Ti si humanoidni robot u jasno definisanoj ulozi.

Ton:
  - Govori kratko i jasno.
  - Odgovaraj na jeziku korisnika.

Granice:
  - Ne izmišljaj činjenice.
  - Ne menjaj pravila iz main prompta.`;

function slugify(value: string) {
  return value
    .normalize('NFKD')
    .split('')
    .filter((character) => character.charCodeAt(0) < 128)
    .join('')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

function itemToForm(item: PromptItem): PromptForm {
  return {
    title: item.title,
    prompt_text: item.prompt_text,
    initial_greeting: item.initial_greeting ?? '',
    goodbye_text: item.goodbye_text ?? '',
    is_archived: item.is_archived,
  };
}

export function PromptConfiguration() {
  const [mainPrompt, setMainPrompt] = useState<PromptItem | null>(null);
  const [personas, setPersonas] = useState<PromptItem[]>([]);
  const [activePersona, setActivePersona] = useState('');
  const [editorMode, setEditorMode] = useState<EditorMode>(null);
  const [selectedPersona, setSelectedPersona] = useState<PromptItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PromptItem | null>(null);
  const [form, setForm] = useState<PromptForm>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);

  const generatedSlug = useMemo(() => slugify(form.title), [form.title]);

  const load = async () => {
    setLoading(true);
    try {
      const [mainResult, personaResult] = await Promise.all([
        api.getMainPrompt(),
        api.listPersonas(true),
      ]);
      setMainPrompt(mainResult.item);
      setPersonas(personaResult.items);
      setActivePersona(personaResult.active);
    } catch (error) {
      toast.error(`Failed to load prompts: ${error}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const closeEditor = () => {
    setEditorMode(null);
    setSelectedPersona(null);
    setForm(EMPTY_FORM);
  };

  const openMain = () => {
    if (!mainPrompt) return;
    setSelectedPersona(null);
    setForm(itemToForm(mainPrompt));
    setEditorMode('main');
  };

  const openCreate = () => {
    setSelectedPersona(null);
    setForm(EMPTY_FORM);
    setEditorMode('create');
  };

  const openEdit = (persona: PromptItem) => {
    setSelectedPersona(persona);
    setForm(itemToForm(persona));
    setEditorMode('edit');
  };

  const updateForm = (key: keyof PromptForm, value: string | boolean) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const save = async () => {
    if (!form.title.trim() || !form.prompt_text.trim()) return;
    setWorking(true);
    try {
      if (editorMode === 'main') {
        await api.updateMainPrompt({
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
        });
        toast.success('Main prompt updated');
      } else if (editorMode === 'create') {
        await api.createPersona({
          slug: generatedSlug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
        });
        toast.success('Persona created');
      } else if (editorMode === 'edit' && selectedPersona) {
        await api.updatePersona(selectedPersona.id, {
          slug: selectedPersona.slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
          is_archived: form.is_archived,
        });
        toast.success('Persona updated');
      }
      closeEditor();
      await load();
      window.dispatchEvent(new CustomEvent('prompt-config-updated'));
    } catch (error) {
      toast.error(`Failed to save prompt: ${error}`);
    } finally {
      setWorking(false);
    }
  };

  const activate = async (persona: PromptItem) => {
    setWorking(true);
    try {
      const result = await api.activatePersona(persona.id);
      setActivePersona(result.active.persona);
      setPersonas((items) => items.map((item) => ({ ...item, active: item.slug === result.active.persona })));
      toast.success(result.runtime_applied ? 'Persona selected and applied to the live agent' : 'Persona selected');
      window.dispatchEvent(new CustomEvent('prompt-config-updated'));
    } catch (error) {
      toast.error(`Failed to select persona: ${error}`);
    } finally {
      setWorking(false);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    setWorking(true);
    try {
      const result = await api.deletePersona(deleteTarget.id);
      setDeleteTarget(null);
      setActivePersona(result.active);
      await load();
      toast.success('Persona deleted');
      window.dispatchEvent(new CustomEvent('prompt-config-updated'));
    } catch (error) {
      toast.error(`Failed to delete persona: ${error}`);
    } finally {
      setWorking(false);
    }
  };

  if (loading && !mainPrompt) {
    return <div className={styles.container}><div className={styles.loadingSpinner}><div className={styles.spinner} /><p>Loading prompts...</p></div></div>;
  }

  if (editorMode) {
    return (
      <div className={styles.container}>
        <div className={styles.panel}>
          <button type="button" className={styles.managerBackButton} onClick={closeEditor} disabled={working}>← Back to personas</button>
          <div className={styles.header}>
            <h1>{editorMode === 'main' ? 'Edit Main Prompt' : editorMode === 'create' ? 'Create Persona' : 'Edit Persona'}</h1>
            <p className={styles.subtitle}>
              {editorMode === 'main'
                ? 'Permanent base layer for language, safety, hallucination limits, and default behaviour.'
                : 'This persona is appended after main and may specialize role, tone, or demo behaviour.'}
            </p>
          </div>

          <div className={styles.controlGroup}>
            <label htmlFor="prompt-title">Title</label>
            <input id="prompt-title" className={styles.input} value={form.title} onChange={(event) => updateForm('title', event.target.value)} disabled={working} />
            <p className={styles.helperText}>{editorMode === 'create' ? `Slug preview: ${generatedSlug || 'enter_a_title'}` : 'The internal slug stays stable when editing.'}</p>
          </div>

          <div className={styles.controlGroup}>
            <div className={styles.managerFieldHeader}>
              <label htmlFor="prompt-text">Prompt text</label>
              {editorMode !== 'main' ? <button type="button" className={styles.skeletonButton} onClick={() => updateForm('prompt_text', PROMPT_SKELETON)} disabled={working}>Use template</button> : null}
            </div>
            <textarea id="prompt-text" className={styles.textarea} value={form.prompt_text} onChange={(event) => updateForm('prompt_text', event.target.value)} placeholder={editorMode === 'main' ? 'Base language, safety and behaviour rules...' : PROMPT_SKELETON} disabled={working} />
          </div>

          <div className={styles.formGroup}>
            <div className={styles.controlGroup}>
              <label htmlFor="initial-greeting">Initial greeting</label>
              <textarea id="initial-greeting" className={styles.input} value={form.initial_greeting} onChange={(event) => updateForm('initial_greeting', event.target.value)} placeholder="Optional greeting" disabled={working} />
            </div>
            <div className={styles.controlGroup}>
              <label htmlFor="goodbye-text">Goodbye text</label>
              <textarea id="goodbye-text" className={styles.input} value={form.goodbye_text} onChange={(event) => updateForm('goodbye_text', event.target.value)} placeholder="Optional goodbye" disabled={working} />
            </div>
          </div>

          {editorMode === 'edit' ? <label className={styles.managerCheckbox}><input type="checkbox" checked={form.is_archived} onChange={(event) => updateForm('is_archived', event.target.checked)} disabled={working} /><span>Archived</span></label> : null}

          <div className={styles.actions}>
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={closeEditor} disabled={working}>Cancel</button>
            <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={() => void save()} disabled={working || !form.title.trim() || !form.prompt_text.trim() || (editorMode === 'create' && !generatedSlug)}>{working ? 'Saving...' : 'Save'}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      <div className={styles.panel}>
        <div className={styles.header}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><h1>Prompt Profiles</h1><p className={styles.subtitle}>Main is always applied first. One selected persona continues and specializes it.</p></div>
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={() => void load()} disabled={loading || working}><RefreshCw size={14} /> Reload</button>
          </div>
        </div>

        {mainPrompt ? (
          <div className={styles.managerListItemActive} style={{ padding: 16, borderRadius: 8, borderStyle: 'solid', borderWidth: 1 }}>
            <div className={styles.managerListItemHeader}>
              <div><div className={styles.managerListItemTitle}>{mainPrompt.title}</div><div className={styles.managerListItemMeta}>Always active · language · guidance · hallucination limits · base behaviour</div></div>
              <span className={styles.managerReservedBadge}><LockKeyhole size={12} /> Main · protected</span>
            </div>
            <div className={styles.actions} style={{ marginTop: 12, marginBottom: 0 }}><button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={openMain}>Edit Main</button></div>
          </div>
        ) : null}

        <div className={styles.managerPanelHeader} style={{ marginTop: 24 }}>
          <div><h3>Personas</h3><p className={styles.helperText}>Select one persona to append after main.</p></div>
          <button type="button" className={`${styles.btn} ${styles.btnTertiary}`} onClick={openCreate}><Plus size={14} /> Add Persona</button>
        </div>

        <div className={styles.managerList}>
          {personas.map((persona) => {
            const active = persona.slug === activePersona;
            return (
              <div key={persona.id} className={`${styles.managerListItem} ${active ? styles.managerListItemActive : ''}`}>
                <div className={styles.managerListItemHeader}>
                  <div><div className={styles.managerListItemTitle}>{persona.title}</div><div className={styles.managerListItemMeta}>{persona.slug}{persona.is_archived ? ' · Archived' : ''}</div></div>
                  {active ? <span className={styles.managerReservedBadge}><CheckCircle2 size={12} /> Active</span> : null}
                </div>
                <div className={styles.actions} style={{ marginBottom: 0 }}>
                  <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={() => void activate(persona)} disabled={working || active || persona.is_archived}>{active ? 'Selected' : 'Select'}</button>
                  <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={() => openEdit(persona)} disabled={working}>Edit</button>
                  <button type="button" className={`${styles.btn} ${styles.managerDeleteButton}`} onClick={() => setDeleteTarget(persona)} disabled={working}>Delete</button>
                </div>
              </div>
            );
          })}
          {personas.length === 0 ? <div className={styles.managerEmptyState}>No additional personas yet. Main remains active by itself.</div> : null}
        </div>
      </div>

      <ActionDialog open={Boolean(deleteTarget)} title="Delete persona?" description={`“${deleteTarget?.title ?? ''}” will be removed from personas.yaml. Main will not be changed.`} confirmLabel="Delete persona" destructive loading={working} onConfirm={() => void remove()} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}

export default PromptConfiguration;
