import { useEffect, useState } from 'react';
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Plus,
  Save,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '@/api/client';
import type {
  ConferenceEditableSection,
  ConferenceEventDocument,
  ConferenceEventSummary,
  ConferenceScenario,
  ConferenceStep,
} from '@/api/types';

const NEW_EVENT_KEY = '__new__';
const SAFE_PATH_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 ._-]*$/;
const SAFE_SCENARIO_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

type ConferenceEventManagerModalProps = {
  open: boolean;
  activeEventKey: string;
  events: ConferenceEventSummary[];
  robotName?: string;
  onClose: () => void;
  onDataChanged: (preferredEventKey?: string) => Promise<void> | void;
};

function slugify(value: string, fallback: string) {
  const normalized = value
    .normalize('NFKD')
    .replace(/[^\x00-\x7F]/g, '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return normalized || fallback;
}

function ensureUniqueValue(base: string, existing: string[]) {
  const seen = new Set(existing.map((value) => value.toLowerCase()));
  if (!seen.has(base.toLowerCase())) {
    return base;
  }

  let index = 2;
  let candidate = `${base}_${index}`;
  while (seen.has(candidate.toLowerCase())) {
    index += 1;
    candidate = `${base}_${index}`;
  }
  return candidate;
}

function moveItem<T>(items: T[], index: number, direction: -1 | 1) {
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= items.length) {
    return items;
  }

  const nextItems = [...items];
  const [item] = nextItems.splice(index, 1);
  nextItems.splice(nextIndex, 0, item);
  return nextItems;
}

function makeEmptyEventDocument(robotName?: string): ConferenceEventDocument {
  return {
    key: 'new_event',
    event_id: 'new_event',
    name: 'New event',
    location: '',
    language: 'sl',
    robot_name: robotName?.trim() || 'Robot',
    robot_role: 'event_host',
    sections: [],
  };
}

function makeEmptySection(existingIds: string[]): ConferenceEditableSection {
  const id = ensureUniqueValue('new_section', existingIds);
  return {
    id,
    section: 'New section',
    description: '',
    scenarios: [],
  };
}

function makeEmptyScenario(existingIds: string[]): ConferenceScenario {
  const id = ensureUniqueValue('new_scenario', existingIds);
  return {
    id,
    label: 'New scenario',
    summary_text: '',
    single_action_only: false,
    action_label: '',
    steps: [],
  };
}

function makeSpeechStep(): ConferenceStep {
  return {
    text: '',
    gesture: '',
    pause_after_ms: undefined,
    display_text: '',
    note: '',
  };
}

function makeGestureStep(): ConferenceStep {
  return {
    text: '',
    gesture: '',
    pause_after_ms: undefined,
    display_text: '',
    note: '',
  };
}

function makeWaitStep(): ConferenceStep {
  return {
    text: '',
    gesture: '',
    pause_after_ms: 1000,
    display_text: '',
    note: '',
  };
}

function isGestureAllowed(gesture: string | undefined, allowedGestures: string[]) {
  return !gesture || allowedGestures.includes(gesture);
}

function getStepPreview(step: ConferenceStep) {
  if (step.display_text?.trim()) {
    return step.display_text.trim();
  }
  if (step.text?.trim()) {
    return step.text.trim();
  }
  if (step.gesture?.trim()) {
    return `Gesture: ${step.gesture.trim()}`;
  }
  if (step.pause_after_ms) {
    return `Wait ${step.pause_after_ms} ms`;
  }
  return 'Empty step';
}

function validateConferenceDocument(document: ConferenceEventDocument, allowedGestures: string[]) {
  const issues: string[] = [];

  if (!document.key.trim()) {
    issues.push('Event key is required.');
  } else if (!SAFE_PATH_NAME_RE.test(document.key.trim())) {
    issues.push('Event key may only contain letters, numbers, spaces, dots, dashes, and underscores.');
  }

  if (!document.event_id.trim()) {
    issues.push('Event id is required.');
  }

  const sectionIds = new Set<string>();
  document.sections.forEach((section) => {
    const sectionId = section.id.trim();
    if (!sectionId) {
      issues.push('Each section must have an id.');
      return;
    }
    if (!SAFE_PATH_NAME_RE.test(sectionId)) {
      issues.push(`Section id "${sectionId}" contains unsupported characters.`);
    }
    const loweredSectionId = sectionId.toLowerCase();
    if (sectionIds.has(loweredSectionId)) {
      issues.push(`Duplicate section id "${sectionId}".`);
    }
    sectionIds.add(loweredSectionId);

    const scenarioIds = new Set<string>();
    section.scenarios.forEach((scenario) => {
      const scenarioId = scenario.id.trim();
      if (!scenarioId) {
        issues.push(`Section "${section.section}" has a scenario without an id.`);
      } else if (!SAFE_SCENARIO_ID_RE.test(scenarioId)) {
        issues.push(`Scenario id "${scenarioId}" contains unsupported characters.`);
      }

      if (scenarioIds.has(scenarioId)) {
        issues.push(`Duplicate scenario id "${scenarioId}" in section "${section.section}".`);
      }
      scenarioIds.add(scenarioId);

      scenario.steps.forEach((step, index) => {
        const hasText = Boolean(step.text?.trim());
        const hasGesture = Boolean(step.gesture?.trim());
        const hasWait = step.pause_after_ms != null && Number(step.pause_after_ms) > 0;
        if (!hasText && !hasGesture && !hasWait) {
          issues.push(
            `Scenario "${scenario.label}" in section "${section.section}" has an empty step at position ${index + 1}.`
          );
        }
        if (step.pause_after_ms != null && Number(step.pause_after_ms) <= 0) {
          issues.push(
            `Scenario "${scenario.label}" in section "${section.section}" has a wait value that must be greater than zero.`
          );
        }
        if (step.gesture?.trim() && !isGestureAllowed(step.gesture.trim(), allowedGestures)) {
          issues.push(
            `Gesture "${step.gesture.trim()}" in section "${section.section}" is not available in the current gesture pool.`
          );
        }
      });
    });
  });

  return issues;
}

export function ConferenceEventManagerModal({
  open,
  activeEventKey,
  events: initialEvents,
  robotName,
  onClose,
  onDataChanged,
}: ConferenceEventManagerModalProps) {
  const [events, setEvents] = useState<ConferenceEventSummary[]>([]);
  const [selectedEventKey, setSelectedEventKey] = useState('');
  const [document, setDocument] = useState<ConferenceEventDocument | null>(null);
  const [originalSnapshot, setOriginalSnapshot] = useState('');
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(null);
  const [allowedGestures, setAllowedGestures] = useState<string[]>([]);
  const [advancedMode, setAdvancedMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const robotLabel = robotName?.trim() || 'the robot';
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const isDirty = document !== null && JSON.stringify(document) !== originalSnapshot;
  const selectedSection = document?.sections.find((section) => section.id === selectedSectionId) ?? null;

  const refreshEvents = async () => {
    const nextEvents = await api.listConferenceEvents();
    setEvents(nextEvents.events);
    return nextEvents;
  };

  const loadAllowedGestures = async () => {
    try {
      const gestureData = await api.listGestures();
      setAllowedGestures(gestureData.gestures);
    } catch (err) {
      setAllowedGestures([]);
      setError(err instanceof Error ? err.message : 'Failed to load available gestures');
    }
  };

  const loadEventDocument = async (eventKey: string) => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const nextDocument = await api.getConferenceEventEditor(eventKey);
      setSelectedEventKey(eventKey);
      setDocument(nextDocument);
      setOriginalSnapshot(JSON.stringify(nextDocument));
      setSelectedSectionId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load conference event');
      setDocument(null);
    } finally {
      setLoading(false);
    }
  };

  const beginCreateEvent = () => {
    const draft = makeEmptyEventDocument(robotName);
    setSelectedEventKey(NEW_EVENT_KEY);
    setDocument(draft);
    setOriginalSnapshot(JSON.stringify(draft));
    setSelectedSectionId(null);
    setError(null);
    setSuccess(null);
  };

  useEffect(() => {
    if (!open) {
      return;
    }

    setEvents(initialEvents);
    setAdvancedMode(false);
    void loadAllowedGestures();

    if (initialEvents.length === 0) {
      beginCreateEvent();
      return;
    }

    const nextEventKey = activeEventKey || initialEvents[0]?.key || '';
    if (!nextEventKey) {
      beginCreateEvent();
      return;
    }

    void loadEventDocument(nextEventKey);
  }, [open]);

  if (!open) {
    return null;
  }

  const confirmDiscardChanges = (message: string) => {
    if (!isDirty) {
      return true;
    }
    return window.confirm(message);
  };

  const updateDocument = (updater: (current: ConferenceEventDocument) => ConferenceEventDocument) => {
    setDocument((current) => {
      if (!current) {
        return current;
      }
      return updater(current);
    });
    setSuccess(null);
    setError(null);
  };

  const updateEventField = (key: keyof ConferenceEventDocument, value: string) => {
    updateDocument((current) => {
      const nextDocument: ConferenceEventDocument = {
        ...current,
        [key]: value,
      };

      if (key === 'name' && selectedEventKey === NEW_EVENT_KEY) {
        const currentSuggestedKey = slugify(current.name, 'new_event');
        const nextSuggestedKey = ensureUniqueValue(
          slugify(value, 'new_event'),
          events.map((event) => event.key)
        );
        if (
          !current.key.trim() ||
          current.key === 'new_event' ||
          current.key === currentSuggestedKey
        ) {
          nextDocument.key = nextSuggestedKey;
        }
        if (
          !current.event_id.trim() ||
          current.event_id === 'new_event' ||
          current.event_id === currentSuggestedKey
        ) {
          nextDocument.event_id = slugify(value, 'new_event');
        }
      }

      return nextDocument;
    });
  };

  const updateSection = (sectionId: string, updater: (section: ConferenceEditableSection) => ConferenceEditableSection) => {
    updateDocument((current) => ({
      ...current,
      sections: current.sections.map((section) =>
        section.id === sectionId ? updater(section) : section
      ),
    }));
  };

  const updateScenario = (
    sectionId: string,
    scenarioId: string,
    updater: (scenario: ConferenceScenario) => ConferenceScenario
  ) => {
    updateSection(sectionId, (section) => ({
      ...section,
      scenarios: section.scenarios.map((scenario) =>
        scenario.id === scenarioId ? updater(scenario) : scenario
      ),
    }));
  };

  const updateStep = (
    sectionId: string,
    scenarioId: string,
    stepIndex: number,
    updater: (step: ConferenceStep) => ConferenceStep
  ) => {
    updateScenario(sectionId, scenarioId, (scenario) => ({
      ...scenario,
      steps: scenario.steps.map((step, index) =>
        index === stepIndex ? updater(step) : step
      ),
    }));
  };

  const handleSelectEvent = async (eventKey: string) => {
    if (eventKey === selectedEventKey) {
      return;
    }
    if (!confirmDiscardChanges('You have unsaved conference event changes. Switch events without saving?')) {
      return;
    }
    await loadEventDocument(eventKey);
  };

  const handleStartCreateEvent = () => {
    if (!confirmDiscardChanges('You have unsaved conference event changes. Start a new event without saving?')) {
      return;
    }
    beginCreateEvent();
  };

  const handleClose = () => {
    if (!confirmDiscardChanges('You have unsaved conference event changes. Close the modal without saving?')) {
      return;
    }
    onClose();
  };

  const handleLeaveSectionEditor = () => {
    if (!confirmDiscardChanges('You have unsaved conference event changes. Leave this section editor without saving?')) {
      return;
    }
    setSelectedSectionId(null);
  };

  const handleOpenSectionEditor = (sectionId: string) => {
    if (selectedSectionId === sectionId) {
      return;
    }
    if (selectedSectionId && !confirmDiscardChanges('You have unsaved conference event changes. Open another section without saving?')) {
      return;
    }
    setSelectedSectionId(sectionId);
  };

  const handleAddSection = () => {
    updateDocument((current) => {
      const nextSection = makeEmptySection(current.sections.map((section) => section.id));
      return {
        ...current,
        sections: [...current.sections, nextSection],
      };
    });
  };

  const handleDeleteSection = (sectionId: string) => {
    if (!window.confirm('Delete this section from the event?')) {
      return;
    }
    updateDocument((current) => ({
      ...current,
      sections: current.sections.filter((section) => section.id !== sectionId),
    }));
    if (selectedSectionId === sectionId) {
      setSelectedSectionId(null);
    }
  };

  const handleMoveSection = (sectionId: string, direction: -1 | 1) => {
    updateDocument((current) => {
      const index = current.sections.findIndex((section) => section.id === sectionId);
      if (index < 0) {
        return current;
      }
      return {
        ...current,
        sections: moveItem(current.sections, index, direction),
      };
    });
  };

  const handleAddScenario = (sectionId: string) => {
    updateSection(sectionId, (section) => ({
      ...section,
      scenarios: [
        ...section.scenarios,
        makeEmptyScenario(section.scenarios.map((scenario) => scenario.id)),
      ],
    }));
  };

  const handleDeleteScenario = (sectionId: string, scenarioId: string) => {
    if (!window.confirm('Delete this scenario?')) {
      return;
    }
    updateSection(sectionId, (section) => ({
      ...section,
      scenarios: section.scenarios.filter((scenario) => scenario.id !== scenarioId),
    }));
  };

  const handleMoveScenario = (sectionId: string, scenarioId: string, direction: -1 | 1) => {
    updateSection(sectionId, (section) => {
      const index = section.scenarios.findIndex((scenario) => scenario.id === scenarioId);
      if (index < 0) {
        return section;
      }
      return {
        ...section,
        scenarios: moveItem(section.scenarios, index, direction),
      };
    });
  };

  const handleAddStep = (sectionId: string, scenarioId: string, kind: 'speech' | 'gesture' | 'wait') => {
    const stepFactory =
      kind === 'gesture' ? makeGestureStep : kind === 'wait' ? makeWaitStep : makeSpeechStep;

    updateScenario(sectionId, scenarioId, (scenario) => ({
      ...scenario,
      steps: [...scenario.steps, stepFactory()],
    }));
  };

  const handleDeleteStep = (sectionId: string, scenarioId: string, stepIndex: number) => {
    updateScenario(sectionId, scenarioId, (scenario) => ({
      ...scenario,
      steps: scenario.steps.filter((_, index) => index !== stepIndex),
    }));
  };

  const handleMoveStep = (
    sectionId: string,
    scenarioId: string,
    stepIndex: number,
    direction: -1 | 1
  ) => {
    updateScenario(sectionId, scenarioId, (scenario) => ({
      ...scenario,
      steps: moveItem(scenario.steps, stepIndex, direction),
    }));
  };

  const handleSave = async () => {
    if (!document) {
      return;
    }

    const validationIssues = validateConferenceDocument(document, allowedGestures);
    if (validationIssues.length > 0) {
      setError(validationIssues[0]);
      setSuccess(null);
      return;
    }

    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      const savedDocument =
        selectedEventKey === NEW_EVENT_KEY
          ? await api.createConferenceEvent(document)
          : await api.updateConferenceEvent(selectedEventKey, document);

      await refreshEvents();
      await onDataChanged(savedDocument.key);

      setSelectedEventKey(savedDocument.key);
      setDocument(savedDocument);
      setOriginalSnapshot(JSON.stringify(savedDocument));
      setSuccess('Conference event saved.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save conference event');
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteEvent = async () => {
    if (!document || selectedEventKey === NEW_EVENT_KEY) {
      beginCreateEvent();
      return;
    }

    if (!window.confirm(`Delete the event "${document.name}"?`)) {
      return;
    }

    setDeleting(true);
    setError(null);
    setSuccess(null);

    try {
      const result = await api.deleteConferenceEvent(selectedEventKey);
      const nextEvents = await refreshEvents();
      await onDataChanged(result.active_event_key || nextEvents.active_event_key || undefined);

      if (nextEvents.events.length === 0) {
        beginCreateEvent();
      } else {
        const nextEventKey =
          result.active_event_key || nextEvents.active_event_key || nextEvents.events[0].key;
        await loadEventDocument(nextEventKey);
      }
      setSuccess('Conference event deleted.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete conference event');
    } finally {
      setDeleting(false);
    }
  };

  const renderEventOverview = () => {
    if (!document) {
      return null;
    }

    return (
      <div className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm font-medium text-foreground">
            <span>Event name</span>
            <input
              value={document.name}
              onChange={(event) => updateEventField('name', event.target.value)}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <label className="space-y-2 text-sm font-medium text-foreground">
            <span>Location</span>
            <input
              value={document.location ?? ''}
              onChange={(event) => updateEventField('location', event.target.value)}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <label className="space-y-2 text-sm font-medium text-foreground">
            <span>Language</span>
            <input
              value={document.language ?? ''}
              onChange={(event) => updateEventField('language', event.target.value)}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <label className="space-y-2 text-sm font-medium text-foreground">
            <span>Robot name</span>
            <input
              value={document.robot_name ?? ''}
              onChange={(event) => updateEventField('robot_name', event.target.value)}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <label className="space-y-2 text-sm font-medium text-foreground">
            <span>Robot role</span>
            <input
              value={document.robot_role ?? ''}
              onChange={(event) => updateEventField('robot_role', event.target.value)}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
        </div>

        {advancedMode && (
          <div className="grid gap-4 rounded-2xl border border-border/60 bg-muted/30 p-4 md:grid-cols-2">
            <label className="space-y-2 text-sm font-medium text-foreground">
              <span>Directory key</span>
              <input
                value={document.key}
                onChange={(event) => updateEventField('key', event.target.value)}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="space-y-2 text-sm font-medium text-foreground">
              <span>Manifest event id</span>
              <input
                value={document.event_id}
                onChange={(event) => updateEventField('event_id', event.target.value)}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
          </div>
        )}

        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold">Sections</h3>
              <p className="text-sm text-muted-foreground">
                Reorder sections here, then open one to edit its scenarios and steps.
              </p>
            </div>
            <button
              type="button"
              onClick={handleAddSection}
              className="inline-flex items-center gap-2 rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white"
            >
              <Plus size={16} />
              Add section
            </button>
          </div>

          {document.sections.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 p-6 text-sm text-muted-foreground">
              No sections yet. Add the first section to start building the event script.
            </div>
          ) : (
            document.sections.map((section, index) => (
              <div
                key={section.id}
                className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-border/60 bg-card p-4"
              >
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-foreground">{section.section || section.id}</p>
                  <p className="text-sm text-muted-foreground">
                    {section.scenarios.length} scenario{section.scenarios.length === 1 ? '' : 's'}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleMoveSection(section.id, -1)}
                    disabled={index === 0}
                    className="rounded-full border border-border bg-background px-3 py-1.5 text-xs disabled:opacity-40"
                  >
                    Up
                  </button>
                  <button
                    type="button"
                    onClick={() => handleMoveSection(section.id, 1)}
                    disabled={index === document.sections.length - 1}
                    className="rounded-full border border-border bg-background px-3 py-1.5 text-xs disabled:opacity-40"
                  >
                    Down
                  </button>
                  <button
                    type="button"
                    onClick={() => handleOpenSectionEditor(section.id)}
                    className="rounded-full bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteSection(section.id)}
                    className="rounded-full border border-rose-300 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    );
  };

  const renderSectionEditor = () => {
    if (!document || !selectedSection) {
      return null;
    }

    return (
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <button
              type="button"
              onClick={handleLeaveSectionEditor}
              className="inline-flex items-center gap-2 rounded-full border border-border bg-background px-3 py-1.5 text-xs font-semibold"
            >
              <ArrowLeft size={14} />
              Back to event
            </button>
            <div>
              <h3 className="text-lg font-semibold">{selectedSection.section || selectedSection.id}</h3>
              <p className="text-sm text-muted-foreground">
                Build scenarios in order. Each scenario can contain speech, gesture, and wait steps.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => handleAddScenario(selectedSection.id)}
            className="inline-flex items-center gap-2 rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white"
          >
            <Plus size={16} />
            Add scenario
          </button>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm font-medium text-foreground">
            <span>Section title</span>
            <input
              value={selectedSection.section}
              onChange={(event) =>
                updateSection(selectedSection.id, (section) => ({
                  ...section,
                  section: event.target.value,
                }))
              }
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <label className="space-y-2 text-sm font-medium text-foreground">
            <span>Description</span>
            <input
              value={selectedSection.description ?? ''}
              onChange={(event) =>
                updateSection(selectedSection.id, (section) => ({
                  ...section,
                  description: event.target.value,
                }))
              }
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
        </div>

        {advancedMode && (
          <div className="rounded-2xl border border-border/60 bg-muted/30 p-4">
            <label className="space-y-2 text-sm font-medium text-foreground">
              <span>Section file id</span>
              <input
                value={selectedSection.id}
                onChange={(event) => {
                  const nextId = event.target.value;
                  updateSection(selectedSection.id, (section) => ({
                    ...section,
                    id: nextId,
                  }));
                  setSelectedSectionId(nextId);
                }}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
          </div>
        )}

        <div className="space-y-4">
          {selectedSection.scenarios.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 p-6 text-sm text-muted-foreground">
              No scenarios in this section yet. Add one to start assembling steps.
            </div>
          ) : (
            selectedSection.scenarios.map((scenario, scenarioIndex) => (
              <div key={scenario.id} className="rounded-2xl border border-border/60 bg-card p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <p className="text-lg font-semibold">{scenario.label || scenario.id}</p>
                    <p className="text-sm text-muted-foreground">
                      {scenario.steps.length} step{scenario.steps.length === 1 ? '' : 's'}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handleMoveScenario(selectedSection.id, scenario.id, -1)}
                      disabled={scenarioIndex === 0}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs disabled:opacity-40"
                    >
                      Up
                    </button>
                    <button
                      type="button"
                      onClick={() => handleMoveScenario(selectedSection.id, scenario.id, 1)}
                      disabled={scenarioIndex === selectedSection.scenarios.length - 1}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs disabled:opacity-40"
                    >
                      Down
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDeleteScenario(selectedSection.id, scenario.id)}
                      className="rounded-full border border-rose-300 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <label className="space-y-2 text-sm font-medium text-foreground">
                    <span>Scenario label</span>
                    <input
                      value={scenario.label}
                      onChange={(event) =>
                        updateScenario(selectedSection.id, scenario.id, (current) => ({
                          ...current,
                          label: event.target.value,
                        }))
                      }
                      className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                    />
                  </label>
                  <label className="space-y-2 text-sm font-medium text-foreground">
                    <span>Summary</span>
                    <input
                      value={scenario.summary_text ?? ''}
                      onChange={(event) =>
                        updateScenario(selectedSection.id, scenario.id, (current) => ({
                          ...current,
                          summary_text: event.target.value,
                        }))
                      }
                      className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                    />
                  </label>
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-4 rounded-2xl border border-border/60 bg-muted/20 p-4">
                  <label className="inline-flex items-center gap-2 text-sm font-medium text-foreground">
                    <input
                      type="checkbox"
                      checked={Boolean(scenario.single_action_only)}
                      onChange={(event) =>
                        updateScenario(selectedSection.id, scenario.id, (current) => ({
                          ...current,
                          single_action_only: event.target.checked,
                        }))
                      }
                    />
                    Play as one button
                  </label>
                  {scenario.single_action_only && (
                    <label className="min-w-[240px] flex-1 space-y-2 text-sm font-medium text-foreground">
                      <span>Button label</span>
                      <input
                        value={scenario.action_label ?? ''}
                        onChange={(event) =>
                          updateScenario(selectedSection.id, scenario.id, (current) => ({
                            ...current,
                            action_label: event.target.value,
                          }))
                        }
                        className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                      />
                    </label>
                  )}
                </div>

                {advancedMode && (
                  <div className="mt-4 rounded-2xl border border-border/60 bg-muted/30 p-4">
                    <label className="space-y-2 text-sm font-medium text-foreground">
                      <span>Scenario id</span>
                      <input
                        value={scenario.id}
                        onChange={(event) =>
                          updateScenario(selectedSection.id, scenario.id, (current) => ({
                            ...current,
                            id: event.target.value,
                          }))
                        }
                        className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                      />
                    </label>
                  </div>
                )}

                <div className="mt-5 space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handleAddStep(selectedSection.id, scenario.id, 'speech')}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs font-semibold"
                    >
                      + Speech step
                    </button>
                    <button
                      type="button"
                      onClick={() => handleAddStep(selectedSection.id, scenario.id, 'gesture')}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs font-semibold"
                    >
                      + Gesture step
                    </button>
                    <button
                      type="button"
                      onClick={() => handleAddStep(selectedSection.id, scenario.id, 'wait')}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs font-semibold"
                    >
                      + Wait step
                    </button>
                  </div>

                  {scenario.steps.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 p-4 text-sm text-muted-foreground">
                      Add speech, gesture, or wait steps to define how {robotLabel} performs this scenario.
                    </div>
                  ) : (
                    scenario.steps.map((step, stepIndex) => {
                      const gestureAllowed = isGestureAllowed(step.gesture?.trim(), allowedGestures);
                      return (
                        <div
                          key={`${scenario.id}:${stepIndex}`}
                          className="rounded-2xl border border-border/60 bg-background p-4"
                        >
                          <div className="flex flex-wrap items-start justify-between gap-4">
                            <div>
                              <p className="font-semibold">Step {stepIndex + 1}</p>
                              <p className="text-sm text-muted-foreground">{getStepPreview(step)}</p>
                            </div>
                            <div className="flex flex-wrap items-center gap-2">
                              <button
                                type="button"
                                onClick={() => handleMoveStep(selectedSection.id, scenario.id, stepIndex, -1)}
                                disabled={stepIndex === 0}
                                className="rounded-full border border-border bg-background px-3 py-1.5 text-xs disabled:opacity-40"
                              >
                                <ChevronUp size={14} />
                              </button>
                              <button
                                type="button"
                                onClick={() => handleMoveStep(selectedSection.id, scenario.id, stepIndex, 1)}
                                disabled={stepIndex === scenario.steps.length - 1}
                                className="rounded-full border border-border bg-background px-3 py-1.5 text-xs disabled:opacity-40"
                              >
                                <ChevronDown size={14} />
                              </button>
                              <button
                                type="button"
                                onClick={() => handleDeleteStep(selectedSection.id, scenario.id, stepIndex)}
                                className="rounded-full border border-rose-300 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
                              >
                                Delete
                              </button>
                            </div>
                          </div>

                          <div className="mt-4 grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(220px,1fr)_minmax(180px,1fr)]">
                            <label className="space-y-2 text-sm font-medium text-foreground">
                              <span>What the robot says</span>
                              <textarea
                                value={step.text ?? ''}
                                onChange={(event) =>
                                  updateStep(selectedSection.id, scenario.id, stepIndex, (current) => ({
                                    ...current,
                                    text: event.target.value,
                                  }))
                                }
                                rows={3}
                                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                              />
                            </label>
                            <label className="space-y-2 text-sm font-medium text-foreground">
                              <span>Gesture</span>
                              <select
                                value={gestureAllowed ? step.gesture ?? '' : ''}
                                onChange={(event) =>
                                  updateStep(selectedSection.id, scenario.id, stepIndex, (current) => ({
                                    ...current,
                                    gesture: event.target.value,
                                  }))
                                }
                                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                              >
                                <option value="">No gesture</option>
                                {allowedGestures.map((gesture) => (
                                  <option key={gesture} value={gesture}>
                                    {gesture}
                                  </option>
                                ))}
                              </select>
                              {!gestureAllowed && step.gesture?.trim() && (
                                <p className="text-xs text-rose-600 dark:text-rose-300">
                                  Stored gesture "{step.gesture.trim()}" is not available right now. Pick a replacement or clear it.
                                </p>
                              )}
                            </label>
                            <label className="space-y-2 text-sm font-medium text-foreground">
                              <span>Wait after step (ms)</span>
                              <input
                                type="number"
                                min={1}
                                value={step.pause_after_ms ?? ''}
                                onChange={(event) =>
                                  updateStep(selectedSection.id, scenario.id, stepIndex, (current) => ({
                                    ...current,
                                    pause_after_ms: event.target.value ? Number(event.target.value) : undefined,
                                  }))
                                }
                                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                              />
                              <p className="text-xs text-muted-foreground">Milliseconds to wait after this step. 1000 = 1 second.</p>
                            </label>
                          </div>

                          {advancedMode && (
                            <div className="mt-4 grid gap-4 md:grid-cols-2">
                              <label className="space-y-2 text-sm font-medium text-foreground">
                                <span>Display label</span>
                                <input
                                  value={step.display_text ?? ''}
                                  onChange={(event) =>
                                    updateStep(selectedSection.id, scenario.id, stepIndex, (current) => ({
                                      ...current,
                                      display_text: event.target.value,
                                    }))
                                  }
                                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                                />
                              </label>
                              <label className="space-y-2 text-sm font-medium text-foreground">
                                <span>Note</span>
                                <input
                                  value={step.note ?? ''}
                                  onChange={(event) =>
                                    updateStep(selectedSection.id, scenario.id, stepIndex, (current) => ({
                                      ...current,
                                      note: event.target.value,
                                    }))
                                  }
                                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                                />
                              </label>
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm dark:bg-black/75">
      <div className="flex h-[92vh] w-full max-w-7xl overflow-hidden rounded-3xl border border-border/80 bg-card shadow-2xl ring-1 ring-black/10 dark:ring-white/5">
        <aside className="w-full max-w-[280px] border-r border-border/70 bg-slate-100 dark:bg-zinc-950">
          <div className="flex items-center justify-between border-b border-border/70 bg-slate-100 px-5 py-4 dark:bg-zinc-950">
            <div>
              <p className="text-sm font-semibold">Conference events</p>
              <p className="text-xs text-muted-foreground">Pick an event or create a new one.</p>
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="rounded-full border border-border bg-card p-2 shadow-sm"
              aria-label="Close modal"
            >
              <X size={16} />
            </button>
          </div>

          <div className="space-y-2 p-4">
            <button
              type="button"
              onClick={handleStartCreateEvent}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white"
            >
              <Plus size={16} />
              New event
            </button>
            <div className="max-h-[calc(92vh-180px)] space-y-2 overflow-y-auto pr-1">
              {events.map((event) => {
                const isSelected = selectedEventKey === event.key;
                const isActive = activeEventKey === event.key;
                return (
                  <button
                    key={event.key}
                    type="button"
                    onClick={() => void handleSelectEvent(event.key)}
                    className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
                      isSelected
                        ? 'border-emerald-400 bg-emerald-500/12'
                        : 'border-border/70 bg-card hover:border-emerald-300/50 hover:bg-slate-50 dark:hover:bg-zinc-900'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate font-semibold text-foreground">{event.name}</p>
                        <p className="truncate text-xs text-muted-foreground">{event.location || event.key}</p>
                      </div>
                      {isActive && (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200">
                          Active
                        </span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col bg-card">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border/70 bg-card px-6 py-4">
            <div>
              <h2 className="text-xl font-semibold">
                {selectedSection ? `Edit section: ${selectedSection.section || selectedSection.id}` : 'Manage conference event'}
              </h2>
              <p className="text-sm text-muted-foreground">
                {selectedSection
                  ? 'Use steps to describe speech, gestures, and waits in the order the robot should perform them.'
                  : 'Keep the live Event panel compact and manage the actual content here.'}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setAdvancedMode((current) => !current)}
                className="rounded-full border border-border bg-card px-4 py-2 text-sm font-medium shadow-sm"
              >
                {advancedMode ? 'Hide advanced' : 'Show advanced'}
              </button>
              <button
                type="button"
                onClick={handleDeleteEvent}
                disabled={deleting}
                className="inline-flex items-center gap-2 rounded-full border border-rose-300 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-700 disabled:opacity-50 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
              >
                <Trash2 size={16} />
                {selectedEventKey === NEW_EVENT_KEY ? 'Discard draft' : deleting ? 'Deleting...' : 'Delete event'}
              </button>
              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={saving || loading || !document}
                className="inline-flex items-center gap-2 rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                <Save size={16} />
                {saving ? 'Saving...' : 'Save changes'}
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50 px-6 py-5 dark:bg-zinc-900">
            {error && (
              <div className="mb-4 rounded-2xl border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200">
                {error}
              </div>
            )}
            {success && (
              <div className="mb-4 rounded-2xl border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-200">
                {success}
              </div>
            )}

            {loading ? (
              <div className="rounded-2xl border border-border/60 bg-card p-6 text-sm text-muted-foreground">
                Loading conference event...
              </div>
            ) : !document ? (
              <div className="rounded-2xl border border-border/60 bg-card p-6 text-sm text-muted-foreground">
                Select an event on the left or create a new one.
              </div>
            ) : selectedSection ? (
              renderSectionEditor()
            ) : (
              renderEventOverview()
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
