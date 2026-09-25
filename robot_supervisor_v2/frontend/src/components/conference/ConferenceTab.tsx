import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Settings2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type {
  ConferenceEventSummary,
  ConferenceProgram,
  ConferenceScenario,
  ConferenceStep,
} from '@/api/types';
import { useSystem } from '@/contexts/SystemContext';
import { ConferenceEventManagerModal } from '@/components/conference/ConferenceEventManagerModal';

function formatWaitLabel(pauseAfterMs: number): string {
  const seconds = pauseAfterMs / 1000;
  return Number.isInteger(seconds) ? `${seconds}s` : `${seconds.toFixed(1)}s`;
}

function getStepLabel(step: ConferenceStep): string {
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
    return `Wait ${formatWaitLabel(step.pause_after_ms)}`;
  }
  return 'Step';
}

function getScenarioActionLabel(scenario: ConferenceScenario): string {
  return scenario.action_label?.trim() || 'Send scenario';
}

function buildOpenSections(program: ConferenceProgram) {
  return Object.fromEntries(
    program.sections.map((section, index) => [section.id, index === 0]),
  );
}

function buildOpenScenarios(program: ConferenceProgram) {
  return Object.fromEntries(
    program.sections.flatMap((section, sectionIndex) =>
      section.scenarios.map((scenario, scenarioIndex) => [
        `${section.id}:${scenario.id}`,
        scenario.single_action_only || (sectionIndex === 0 && scenarioIndex === 0),
      ]),
    ),
  );
}

function buildGestureState(program: ConferenceProgram) {
  return Object.fromEntries(
    program.sections
      .flatMap((section) => section.scenarios)
      .flatMap((scenario) => scenario.steps)
      .flatMap((step) => (step.gesture ? [step.gesture] : []))
      .filter((gesture, index, all) => all.indexOf(gesture) === index)
      .map((gesture) => [gesture, true]),
  );
}

export function ConferenceTab() {
  const { conversation, robot } = useSystem();
  const [events, setEvents] = useState<ConferenceEventSummary[]>([]);
  const [activeEventKey, setActiveEventKey] = useState('');
  const [program, setProgram] = useState<ConferenceProgram | null>(null);
  const [loading, setLoading] = useState(true);
  const [switchingEvent, setSwitchingEvent] = useState(false);
  const [gestureEnabled, setGestureEnabled] = useState<Record<string, boolean>>({});
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({});
  const [openScenarios, setOpenScenarios] = useState<Record<string, boolean>>({});
  const [sendingKey, setSendingKey] = useState<string | null>(null);
  const [managerOpen, setManagerOpen] = useState(false);
  const conversationReady = conversation.state === 'engaged';
  const activeEventSummary = events.find((event) => event.key === activeEventKey) ?? null;
  const robotName = robot?.name?.trim() || undefined;

  const applyProgramState = (nextProgram: ConferenceProgram) => {
    setProgram(nextProgram);
    setActiveEventKey(nextProgram.event_key);
    setOpenSections(buildOpenSections(nextProgram));
    setOpenScenarios(buildOpenScenarios(nextProgram));
    setGestureEnabled(buildGestureState(nextProgram));
  };

  const loadConferenceData = async () => {
    setLoading(true);
    try {
      const eventsData = await api.listConferenceEvents();
      setEvents(eventsData.events);
      setActiveEventKey(eventsData.active_event_key ?? '');

      if (!eventsData.active_event_key || eventsData.events.length === 0) {
        setProgram(null);
        setGestureEnabled({});
        setOpenSections({});
        setOpenScenarios({});
        return;
      }

      try {
        const programData = await api.getConferenceProgram();
        applyProgramState(programData);
      } catch (err) {
        setProgram(null);
        setGestureEnabled({});
        setOpenSections({});
        setOpenScenarios({});
        toast.error(err instanceof Error ? err.message : 'Failed to load event program');
      }
    } catch (err) {
      setProgram(null);
      setEvents([]);
      setActiveEventKey('');
      toast.error(err instanceof Error ? err.message : 'Failed to load conference events');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadConferenceData();
  }, []);

  const handleActiveEventChange = async (nextEventKey: string) => {
    if (!nextEventKey || nextEventKey === activeEventKey) {
      return;
    }

    setSwitchingEvent(true);
    try {
      const activeEvent = await api.setActiveConferenceEvent(nextEventKey);
      const programData = await api.getConferenceProgram();
      setActiveEventKey(activeEvent.active_event_key);
      applyProgramState(programData);
      toast.success(`Active event set to ${activeEvent.active_event.name}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to switch event');
    } finally {
      setSwitchingEvent(false);
    }
  };

  const sendStep = async (step: ConferenceStep, key: string) => {
    setSendingKey(key);
    try {
      const gesture =
        step.gesture && gestureEnabled[step.gesture] !== false ? step.gesture : undefined;
      await api.sendAgentCommand({
        room: conversation.room,
        steps: [
          {
            text: step.text,
            gesture,
            pause_after_ms: step.pause_after_ms,
          },
        ],
      });
      toast.success(gesture ? 'Step and gesture sent' : 'Step sent');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to send step');
    } finally {
      setSendingKey(null);
    }
  };

  const sendScenario = async (scenario: ConferenceScenario, key: string) => {
    setSendingKey(key);
    try {
      await api.sendAgentCommand({
        room: conversation.room,
        steps: scenario.steps.map((step) => ({
          text: step.text,
          gesture: step.gesture && gestureEnabled[step.gesture] !== false ? step.gesture : undefined,
          pause_after_ms: step.pause_after_ms,
        })),
      });
      toast.success('Scenario sent');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to send scenario');
    } finally {
      setSendingKey(null);
    }
  };

  const sendStopCommand = async () => {
    try {
      await api.sendAgentCommand({ text: '__STOP__', plain_text: true, room: conversation.room });
      toast.success('Stop command sent');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to send stop command');
    }
  };

  const toggleSection = (sectionId: string) => {
    setOpenSections((current) => ({
      ...current,
      [sectionId]: !current[sectionId],
    }));
  };

  const toggleScenario = (sectionId: string, scenarioId: string) => {
    const key = `${sectionId}:${scenarioId}`;
    setOpenScenarios((current) => ({
      ...current,
      [key]: !current[key],
    }));
  };

  const toggleGesture = (gesture: string) => {
    setGestureEnabled((current) => ({
      ...current,
      [gesture]: current[gesture] === false,
    }));
  };

  const statusClasses: Record<string, string> = {
    ready: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200',
    empty: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-200',
    missing: 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-200',
  };

  return (
    <>
      <section className="space-y-6">
        <div className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="text-2xl font-semibold">
                {program?.event.name ?? activeEventSummary?.name ?? 'Conference event'}
              </h2>
              <div className="mt-2 space-y-1">
                <p className="text-sm text-muted-foreground">
                  {program?.event.location ?? activeEventSummary?.location ?? 'No event selected'}
                </p>
                <p className="text-sm font-semibold text-foreground">
                  Robot {program?.robot.name ?? activeEventSummary?.robot_name ?? robotName ?? 'Not configured'}
                </p>
              </div>
            </div>
            <div className="flex min-w-[280px] items-end gap-2">
              <label className="flex-1 text-sm font-medium text-foreground">
                <span className="mb-2 block text-xs uppercase tracking-wide text-muted-foreground">
                  Active event
                </span>
                <select
                  value={activeEventKey}
                  onChange={(event) => void handleActiveEventChange(event.target.value)}
                  disabled={loading || switchingEvent || events.length === 0}
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {events.length === 0 ? (
                    <option value="">No events</option>
                  ) : (
                    events.map((event) => (
                      <option key={event.key} value={event.key}>
                        {event.name}
                      </option>
                    ))
                  )}
                </select>
              </label>
              <button
                type="button"
                onClick={() => setManagerOpen(true)}
                className="inline-flex h-[42px] w-[42px] items-center justify-center rounded-xl border border-border bg-background text-foreground transition hover:border-emerald-400 hover:text-emerald-600"
                aria-label="Open conference event settings"
                title="Manage conference events"
              >
                <Settings2 size={18} />
              </button>
            </div>
          </div>
        </div>

        <div
          className={`rounded-2xl border p-4 shadow-sm ${
            conversationReady
              ? 'border-emerald-300/60 bg-emerald-500/10'
              : 'border-rose-300/60 bg-rose-500/10'
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span
                className={`h-3 w-3 rounded-full ${
                  conversationReady ? 'bg-emerald-500' : 'bg-rose-500'
                }`}
              />
              <div>
                <p className="text-sm font-semibold text-foreground">
                  {conversationReady ? 'Conversation active' : 'Conversation not started'}
                </p>
                <p className="text-xs text-muted-foreground">
                  {conversationReady
                    ? 'Commands can be sent from the event panels.'
                    : 'Start a conversation to enable sending commands.'}
                </p>
              </div>
            </div>
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                conversationReady
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200'
                  : 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-200'
              }`}
            >
              {conversationReady ? 'Ready' : 'Blocked'}
            </span>
          </div>
        </div>

        {loading ? (
          <section className="rounded-2xl border border-border/60 bg-card p-6">Loading event program...</section>
        ) : !program ? (
          <section className="rounded-2xl border border-border/60 bg-card p-6 text-sm text-muted-foreground">
            No event program loaded. Use the settings button to create or manage conference events.
          </section>
        ) : (
          program.sections.map((section) => (
            <div key={section.id} className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
              <button
                type="button"
                onClick={() => toggleSection(section.id)}
                className="flex w-full items-start justify-between gap-4 text-left"
                aria-expanded={openSections[section.id] ?? false}
              >
                <div>
                  <div className="flex flex-wrap items-center gap-3">
                    <h3 className="text-xl font-semibold">{section.section}</h3>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${statusClasses[section.status] ?? 'bg-muted text-muted-foreground'}`}>
                      {section.status}
                    </span>
                  </div>
                </div>
                <span className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
                  {openSections[section.id] ? 'Collapse' : 'Expand'}
                  {openSections[section.id] ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </span>
              </button>

              {openSections[section.id] && (
                <div className="mt-4 border-t border-border/60 pt-4">
                  {section.status !== 'ready' ? (
                    <p className="rounded-lg border border-dashed border-border/60 bg-muted/30 p-3 text-sm text-muted-foreground">
                      Section status: {section.status} ({section.source_file})
                    </p>
                  ) : section.scenarios.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-border/60 bg-muted/30 p-3 text-sm text-muted-foreground">
                      No scenarios defined for this section yet.
                    </p>
                  ) : (
                    section.scenarios.map((scenario) => (
                      <div key={scenario.id} className="mb-4 rounded-xl border border-border/60 bg-muted/40 p-4 last:mb-0">
                        <button
                          type="button"
                          onClick={() => toggleScenario(section.id, scenario.id)}
                          className="flex w-full items-start justify-between gap-4 text-left"
                          aria-expanded={openScenarios[`${section.id}:${scenario.id}`] ?? false}
                        >
                          <div>
                            <p className="font-medium">{scenario.label}</p>
                          </div>
                          <span className="flex items-center text-xs text-muted-foreground">
                            {openScenarios[`${section.id}:${scenario.id}`] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          </span>
                        </button>

                        {openScenarios[`${section.id}:${scenario.id}`] && (
                          <div className="mt-3 space-y-2 border-t border-border/50 pt-3">
                            {scenario.single_action_only ? (
                              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-300/60 bg-emerald-500/10 p-4 shadow-sm">
                                <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                                  {scenario.summary_text ?? 'Play the scripted sequence.'}
                                </p>
                                <button
                                  type="button"
                                  onClick={() => void sendStopCommand()}
                                  disabled={!conversationReady}
                                  className="rounded-full bg-rose-600 px-4 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-rose-500 disabled:opacity-50"
                                >
                                  Stop
                                </button>
                                <button
                                  type="button"
                                  onClick={() => void sendScenario(scenario, `${section.id}:${scenario.id}:scenario`)}
                                  disabled={sendingKey === `${section.id}:${scenario.id}:scenario` || !conversationReady}
                                  className="rounded-full bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-emerald-500 disabled:opacity-50"
                                >
                                  {sendingKey === `${section.id}:${scenario.id}:scenario` ? 'Sending...' : getScenarioActionLabel(scenario)}
                                </button>
                              </div>
                            ) : (
                              <>
                                {scenario.steps.map((step, index) => {
                                  const key = `${section.id}:${scenario.id}-${index}`;
                                  const stepLabel = getStepLabel(step);
                                  return (
                                    <div key={key} className="flex flex-wrap items-start justify-between gap-3 rounded-lg bg-background p-3">
                                      <div className="min-w-0 flex-1 space-y-2">
                                        <p className="text-sm font-medium leading-5">{stepLabel}</p>
                                        {(step.note || step.pause_after_ms) && (
                                          <div className="flex flex-wrap items-center gap-2 text-[11px]">
                                            {step.pause_after_ms && (
                                              <span className="rounded-full border border-sky-300 bg-sky-100 px-2.5 py-1 font-semibold text-sky-800 dark:border-sky-500/40 dark:bg-sky-500/15 dark:text-sky-200">
                                                Wait {formatWaitLabel(step.pause_after_ms)}
                                              </span>
                                            )}
                                            {step.note && (
                                              <span className="rounded-full border border-amber-300 bg-amber-100 px-2.5 py-1 font-medium text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-200">
                                                {step.note}
                                              </span>
                                            )}
                                          </div>
                                        )}
                                      </div>
                                      <div className="flex items-center gap-2">
                                        {step.gesture && (
                                          <button
                                            type="button"
                                            onClick={() => toggleGesture(step.gesture!)}
                                            className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                                              gestureEnabled[step.gesture] !== false
                                                ? 'border-emerald-300 bg-emerald-100 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-200'
                                                : 'border-border/70 bg-muted/60 text-muted-foreground'
                                            }`}
                                          >
                                            {step.gesture} {gestureEnabled[step.gesture] !== false ? 'on' : 'off'}
                                          </button>
                                        )}
                                        <button
                                          type="button"
                                          onClick={() => void sendStep(step, key)}
                                          disabled={sendingKey === key || !conversationReady}
                                          className="rounded-full bg-emerald-600 px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
                                        >
                                          {sendingKey === key ? 'Sending...' : 'Send'}
                                        </button>
                                      </div>
                                    </div>
                                  );
                                })}
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </section>

      <ConferenceEventManagerModal
        open={managerOpen}
        activeEventKey={activeEventKey}
        events={events}
        robotName={robotName}
        onClose={() => setManagerOpen(false)}
        onDataChanged={loadConferenceData}
      />
    </>
  );
}
