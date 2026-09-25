import React, { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { EventMomentItem, PromptItem, PromptManagerSection } from '../../api/types';
import styles from './PromptConfiguration.module.css';
import { ActionDialog } from '../ui/ActionDialog';

type PromptManagerTabProps = {
  onChanged?: () => Promise<void> | void;
};

type PromptManagerForm = {
  title: string;
  prompt_text: string;
  initial_greeting: string;
  goodbye_text: string;
  order_index: number;
  is_archived: boolean;
};

const EMPTY_FORM: PromptManagerForm = {
  title: '',
  prompt_text: '',
  initial_greeting: '',
  goodbye_text: '',
  order_index: 0,
  is_archived: false,
};

const SECTIONS: Array<{ id: PromptManagerSection; label: string }> = [
  { id: 'modes', label: 'Modes' },
  { id: 'speaking-styles', label: 'Speaking styles' },
  { id: 'event-settings', label: 'Event settings' },
  { id: 'event-moments', label: 'Event moments' },
];

const PROMPT_SKELETON = `Persona:
  Ti si CORTEX, humanoidni robot koji razgovara sa posetiocima.

Ton:
  - Govori kratko i jasno.
  - Odgovaraj na jeziku korisnika.

Granice:
  - Ne izmišljaj činjenice.
  - Ne deli privatne podatke.`;

export const PromptManagerTab: React.FC<PromptManagerTabProps> = ({ onChanged }) => {
  const [section, setSection] = useState<PromptManagerSection>('modes');
  const [items, setItems] = useState<PromptItem[]>([]);
  const [eventSettings, setEventSettings] = useState<PromptItem[]>([]);
  const [eventMomentItems, setEventMomentItems] = useState<EventMomentItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | string | null>(null);
  const [selectedEventSettingId, setSelectedEventSettingId] = useState<number | string | null>(null);
  const [form, setForm] = useState<PromptManagerForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [workflow, setWorkflow] = useState<'list' | 'create' | 'edit'>('list');
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  const currentItems = section === 'event-moments' ? eventMomentItems : items;
  const selectedItem = currentItems.find((item) => String(item.id) === String(selectedId)) ?? null;
  const selectedEventSetting = eventSettings.find((item) => String(item.id) === String(selectedEventSettingId)) ?? null;
  const supportsInitialGreeting = section === 'modes' || section === 'event-settings' || section === 'event-moments';
  const isReservedSelection =
    selectedItem?.slug === 'no_event_setting' || selectedItem?.slug === 'no_event_moment';
  const canEditEventMoments = section !== 'event-moments' || selectedEventSettingId !== null;
  const isFormValid = Boolean(
    form.title.trim() &&
      (section === 'event-moments' || form.prompt_text.trim()) &&
      canEditEventMoments
  );

  const slugifyTitle = (value: string) =>
    value
      .normalize('NFKD')
      .replace(/[^\x00-\x7F]/g, '')
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');

  const getEffectiveSlug = () => {
    if (isReservedSelection && selectedItem) {
      return selectedItem.slug;
    }
    return slugifyTitle(form.title);
  };

  const resetForm = () => {
    setForm(EMPTY_FORM);
  };

  const refreshEventSettings = async () => {
    const data = await api.listEventSettings(true);
    setEventSettings(data.items);
  };

  const loadItems = async () => {
    setError(null);
    try {
      if (section === 'modes') {
        const data = await api.listPromptModes(true);
        setItems(data.items);
        setEventMomentItems([]);
        return;
      }

      if (section === 'speaking-styles') {
        const data = await api.listSpeakingStyles(true);
        setItems(data.items);
        setEventMomentItems([]);
        return;
      }

      if (section === 'event-settings') {
        const data = await api.listEventSettings(true);
        setItems(data.items);
        setEventMomentItems([]);
        return;
      }

      if (!selectedEventSettingId) {
        setEventMomentItems([]);
        return;
      }

      const data = await api.listEventMoments(selectedEventSettingId, true);
      setEventMomentItems(data.items);
      setItems([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load prompt items');
    }
  };

  useEffect(() => {
    void refreshEventSettings();
  }, []);

  useEffect(() => {
    setSelectedId(null);
    setWorkflow('list');
    resetForm();
    setError(null);
    setSuccess(null);
  }, [section, selectedEventSettingId]);

  useEffect(() => {
    void loadItems();
  }, [section, selectedEventSettingId]);

  useEffect(() => {
    if (!selectedId) {
      resetForm();
      return;
    }

    if (section === 'event-moments') {
      const item = eventMomentItems.find((entry) => String(entry.id) === String(selectedId));
      if (!item) {
        resetForm();
        return;
      }

      setForm({
        title: item.title,
        prompt_text: item.prompt_text,
        initial_greeting: item.initial_greeting ?? '',
        goodbye_text: item.goodbye_text ?? '',
        order_index: item.order_index,
        is_archived: item.is_archived,
      });
      return;
    }

    const item = items.find((entry) => String(entry.id) === String(selectedId));
    if (!item) {
      resetForm();
      return;
    }

    setForm({
      title: item.title,
      prompt_text: item.prompt_text,
      initial_greeting: item.initial_greeting ?? '',
      goodbye_text: item.goodbye_text ?? '',
      order_index: 0,
      is_archived: item.is_archived,
    });
  }, [selectedId, section, items, eventMomentItems]);

  const updateForm = (key: keyof PromptManagerForm, value: string | number | boolean) => {
    setForm((prev) => ({
      ...prev,
      [key]: value,
    }));
  };

  const clearSelection = () => {
    setSelectedId(null);
    resetForm();
  };

  const handleCreate = async () => {
    const slug = getEffectiveSlug();
    if (!slug) {
      setError('Title must contain letters or numbers so a valid internal key can be generated.');
      return;
    }

    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      if (section === 'modes') {
        await api.createPromptMode({
          slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
        });
      }

      if (section === 'speaking-styles') {
        await api.createSpeakingStyle({
          slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: '',
          goodbye_text: '',
        });
      }

      if (section === 'event-settings') {
        await api.createEventSetting({
          slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
        });
        await refreshEventSettings();
      }

      if (section === 'event-moments' && selectedEventSettingId) {
        await api.createEventMoment(selectedEventSettingId, {
          slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
          order_index: Number(form.order_index),
        });
      }

      clearSelection();
      await loadItems();
      await onChanged?.();
      setSuccess('Prompt created successfully.');
      setWorkflow('list');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create prompt');
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!selectedId) return;
    const slug = getEffectiveSlug();
    if (!slug) {
      setError('Title must contain letters or numbers so a valid internal key can be generated.');
      return;
    }

    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      if (section === 'modes') {
        await api.updatePromptMode(selectedId, {
          slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
          is_archived: form.is_archived,
        });
      }

      if (section === 'speaking-styles') {
        await api.updateSpeakingStyle(selectedId, {
          slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: '',
          goodbye_text: '',
          is_archived: form.is_archived,
        });
      }

      if (section === 'event-settings') {
        await api.updateEventSetting(selectedId, {
          slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
          is_archived: form.is_archived,
        });
        await refreshEventSettings();
      }

      if (section === 'event-moments') {
        await api.updateEventMoment(selectedId, {
          slug,
          title: form.title,
          prompt_text: form.prompt_text,
          initial_greeting: form.initial_greeting,
          goodbye_text: form.goodbye_text,
          order_index: Number(form.order_index),
          is_archived: form.is_archived,
        });
      }

      await loadItems();
      await onChanged?.();
      setSuccess('Prompt updated successfully.');
      setWorkflow('list');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update prompt');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!selectedId) return;

    setDeleting(true);
    setError(null);
    setSuccess(null);

    try {
      if (section === 'modes') {
        await api.deletePromptMode(selectedId);
      }

      if (section === 'speaking-styles') {
        await api.deleteSpeakingStyle(selectedId);
      }

      if (section === 'event-settings') {
        await api.deleteEventSetting(selectedId);
        await refreshEventSettings();
        if (String(selectedEventSettingId) === String(selectedId)) {
          setSelectedEventSettingId(null);
        }
      }

      if (section === 'event-moments') {
        await api.deleteEventMoment(selectedId);
      }

      clearSelection();
      await loadItems();
      await onChanged?.();
      setSuccess('Prompt deleted successfully.');
      setShowDeleteDialog(false);
      setWorkflow('list');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete prompt');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <h1>Manage Prompt Library</h1>
        <p className={styles.subtitle}>Create, edit, archive, and delete the prompt parts used by the setup tab.</p>
      </div>

      {(error || success) && (
        <div className={`${styles.managerBanner} ${error ? styles.managerBannerError : styles.managerBannerSuccess}`}>
          <span>{error ?? success}</span>
          <button type="button" onClick={() => { setError(null); setSuccess(null); }}>
            x
          </button>
        </div>
      )}

      <div className={`${styles.managerLayout} ${workflow !== 'list' ? styles.managerLayoutFocus : ''}`}>
        {workflow === 'list' ? <aside className={styles.managerSidebar}>
          <h3 className={styles.managerSectionTitle}>Sections</h3>
          <div className={styles.managerSectionButtons}>
            {SECTIONS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                className={`${styles.managerSectionButton} ${section === entry.id ? styles.managerSectionButtonActive : ''}`}
                onClick={() => setSection(entry.id)}
              >
                {entry.label}
              </button>
            ))}
          </div>

          {section === 'event-moments' && (
            <div className={styles.managerFilterBlock}>
              <label htmlFor="manager-event-setting">Event setting</label>
              <select
                id="manager-event-setting"
                className={styles.select}
                value={selectedEventSettingId ?? ''}
                onChange={(e) => setSelectedEventSettingId(e.target.value || null)}
              >
                <option value="">Select event setting...</option>
                {eventSettings.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
            </div>
          )}

          <button
            type="button"
            className={`${styles.btn} ${styles.btnSecondary}`}
            onClick={() => {
              clearSelection();
              setWorkflow('create');
            }}
          >
            Create Prompt
          </button>
        </aside> : null}

        {workflow === 'list' ? <section className={styles.managerListPanel}>
          <div className={styles.managerPanelHeader}>
            <h3>{section === 'event-moments' ? 'Event moments' : 'Entries'}</h3>
          </div>

          {section === 'event-moments' && !selectedEventSetting ? (
            <div className={styles.managerEmptyState}>
              Select an event setting to manage its event moments.
            </div>
          ) : currentItems.length === 0 ? (
            <div className={styles.managerEmptyState}>
              No prompts found for this section yet.
            </div>
          ) : (
            <div className={styles.managerList}>
              {currentItems.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`${styles.managerListItem} ${String(selectedId) === String(item.id) ? styles.managerListItemActive : ''}`}
                  onClick={() => {
                    setSelectedId(item.id);
                    setWorkflow('edit');
                  }}
                >
                  <div className={styles.managerListItemHeader}>
                    <span className={styles.managerListItemTitle}>{item.title}</span>
                    {item.is_archived && <span className={styles.managerBadge}>Archived</span>}
                  </div>
                  {(item.slug === 'no_event_setting' || item.slug === 'no_event_moment') && (
                    <div className={styles.managerListItemMeta}>
                      <span className={styles.managerReservedBadge}>Reserved</span>
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </section> : null}

        {workflow !== 'list' ? <section className={styles.managerEditor}>
          <div className={styles.managerPanelHeader}>
            <div>
              <button
                type="button"
                className={styles.managerBackButton}
                onClick={() => {
                  clearSelection();
                  setWorkflow('list');
                }}
                disabled={saving || deleting}
              >
                ← Back to prompts
              </button>
              <h3>{workflow === 'edit' ? 'Edit Prompt' : 'Create Prompt'}</h3>
            </div>
            {section === 'event-moments' && selectedEventSetting ? (
              <span className={styles.managerContextLabel}>{selectedEventSetting.title}</span>
            ) : null}
          </div>

          <div className={styles.controlGroup}>
            <label htmlFor="manager-title">Title</label>
            <input
              id="manager-title"
              className={styles.input}
              value={form.title}
              onChange={(e) => updateForm('title', e.target.value)}
              disabled={saving || deleting || isReservedSelection || !canEditEventMoments}
              placeholder="Display title"
            />
            <p className={styles.helperText}>
              Title is the label operators see. Slug is the generated internal key and cannot contain spaces.
            </p>
            <div className={styles.slugPreview}>Slug preview: <code>{getEffectiveSlug() || 'enter-a-title'}</code></div>
          </div>

          {section === 'event-moments' && (
            <div className={styles.controlGroup}>
              <label htmlFor="manager-order-index">Order index</label>
              <input
                id="manager-order-index"
                type="number"
                className={styles.input}
                value={form.order_index}
                onChange={(e) => updateForm('order_index', Number(e.target.value))}
                disabled={saving || deleting || isReservedSelection || !canEditEventMoments}
              />
            </div>
          )}

          {supportsInitialGreeting && (
            <div className={styles.controlGroup}>
              <label htmlFor="manager-initial-greeting">Initial greeting</label>
              <textarea
                id="manager-initial-greeting"
                className={styles.textarea}
                value={form.initial_greeting}
                onChange={(e) => updateForm('initial_greeting', e.target.value)}
                disabled={saving || deleting || isReservedSelection || !canEditEventMoments}
                placeholder="Optional startup greeting..."
              />
            </div>
          )}

          {supportsInitialGreeting && (
            <div className={styles.controlGroup}>
              <label htmlFor="manager-goodbye-text">Goodbye text</label>
              <textarea
                id="manager-goodbye-text"
                className={styles.textarea}
                value={form.goodbye_text}
                onChange={(e) => updateForm('goodbye_text', e.target.value)}
                disabled={saving || deleting || isReservedSelection || !canEditEventMoments}
                placeholder="Optional wrap-up goodbye..."
              />
            </div>
          )}

          <div className={styles.controlGroup}>
            <div className={styles.managerFieldHeader}>
              <label htmlFor="manager-prompt-text">Prompt text</label>
              <button
                type="button"
                className={styles.skeletonButton}
                onClick={() => updateForm('prompt_text', PROMPT_SKELETON)}
                disabled={saving || deleting || isReservedSelection || !canEditEventMoments}
              >
                Use template
              </button>
            </div>
            <textarea
              id="manager-prompt-text"
              className={styles.textarea}
              value={form.prompt_text}
              onChange={(e) => updateForm('prompt_text', e.target.value)}
              disabled={saving || deleting || isReservedSelection || !canEditEventMoments}
              placeholder={PROMPT_SKELETON}
            />
            <p className={styles.helperText}>Structure the prompt into short sections so intent, tone, and limits remain easy to review.</p>
          </div>

          <label className={styles.managerCheckbox}>
            <input
              type="checkbox"
              checked={form.is_archived}
              onChange={(e) => updateForm('is_archived', e.target.checked)}
              disabled={saving || deleting || isReservedSelection || !canEditEventMoments}
            />
            <span>Archived</span>
          </label>

          <div className={styles.actions}>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={() => {
                clearSelection();
                setWorkflow('list');
              }}
              disabled={saving || deleting}
            >
              Cancel
            </button>
            {workflow === 'create' ? <button
              type="button"
              className={`${styles.btn} ${styles.btnTertiary}`}
              onClick={() => void handleCreate()}
              disabled={saving || deleting || !isFormValid}
            >
              {saving && !selectedId ? 'Creating...' : 'Create'}
            </button> : null}
            {workflow === 'edit' ? <button
              type="button"
              className={`${styles.btn} ${styles.btnPrimary}`}
              onClick={() => void handleUpdate()}
              disabled={!selectedId || saving || deleting || isReservedSelection || !isFormValid}
            >
              {saving && selectedId ? 'Saving...' : 'Save'}
            </button> : null}
            {workflow === 'edit' ? <button
              type="button"
              className={`${styles.btn} ${styles.managerDeleteButton}`}
              onClick={() => setShowDeleteDialog(true)}
              disabled={!selectedId || saving || deleting || isReservedSelection}
            >
              {deleting ? 'Deleting...' : 'Delete'}
            </button> : null}
          </div>
        </section> : null}
      </div>

      <ActionDialog
        open={showDeleteDialog}
        title="Delete prompt?"
        description={`“${selectedItem?.title ?? form.title}” will be permanently removed from the prompt library. This cannot be undone.`}
        confirmLabel="Delete prompt"
        destructive
        loading={deleting}
        onConfirm={() => void handleDelete()}
        onCancel={() => setShowDeleteDialog(false)}
      />
    </div>
  );
};

export default PromptManagerTab;
