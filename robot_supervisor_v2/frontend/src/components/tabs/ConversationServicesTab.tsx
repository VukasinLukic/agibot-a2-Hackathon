import { useEffect, useState } from 'react';
import { CollapsibleSection } from '@/components/ui/CollapsibleSection';
import { CameraMonitorSection } from '@/components/conversation/CameraMonitorSection';
import { IdCaptureMonitorPanel } from '@/components/conversation/IdCaptureMonitorPanel';
import { AgentCommandPanel } from '@/components/conversation/AgentCommandPanel';
import { TranscriptView } from '@/components/conversation/TranscriptView';
import { ConferenceTab } from '@/components/conference/ConferenceTab';
import { ServiceConfigRow } from '@/components/services/ServiceConfigRow';
import { api } from '@/api/client';
import type {
  Service,
  ConversationStatus,
  VisionStatus,
  QuizOptionItem,
  SurveyOptionItem,
  PromptConfig,
} from '@/api/types';

const PROMPT_PARTS: Array<{
  key: keyof PromptConfig;
  label: string;
  className: string;
}> = [
  {
    key: 'core_mode',
    label: 'Mode',
    className: 'border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-500/40 dark:bg-slate-500/15 dark:text-slate-200',
  },
  {
    key: 'persona',
    label: 'Persona',
    className: 'border-sky-300 bg-sky-100 text-sky-800 dark:border-sky-500/40 dark:bg-sky-500/15 dark:text-sky-200',
  },
  {
    key: 'context',
    label: 'Context',
    className: 'border-violet-300 bg-violet-100 text-violet-800 dark:border-violet-500/40 dark:bg-violet-500/15 dark:text-violet-200',
  },
  {
    key: 'phase',
    label: 'Phase',
    className: 'border-emerald-300 bg-emerald-100 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-200',
  },
];

function formatPromptPart(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

interface ConversationServicesTabProps {
  voiceAgentServices: Service[];
  cameraBridgeServices: Service[];
  visionControllerService?: Service;
  conversation: ConversationStatus;
  vision?: VisionStatus | null;
  loading: string | null;
  visionEnabled: boolean;
  visionAvailable: boolean;
  visionLoading: boolean;
  ragRuntimeEnabled: boolean;
  ragRuntimeAvailable: boolean;
  ragToggleLoading: boolean;
  quizRuntimeEnabled: boolean;
  quizToggleLoading: boolean;
  surveyRuntimeEnabled: boolean;
  surveyToggleLoading: boolean;
  quizOptions: QuizOptionItem[];
  surveyOptions: SurveyOptionItem[];
  activeQuizSlug: string;
  activeSurveySlug: string;
  canToggleConversation: boolean;
  getServiceDisplayName: (service: Service) => string;
  getServiceStartConflict: (serviceName: string) => string | null;
  canToggleService: (service: Service) => boolean;
  getServiceActionLabel: (service: Service) => string;
  requiredServicesRunning: () => boolean;
  onToggleVoiceAgentServices: () => void;
  onToggleService: (serviceName: string, currentState: string) => void;
  onToggleConversation: () => void;
  onToggleVision: () => void;
  onToggleRag: () => void;
  onToggleQuiz: () => void;
  onToggleSurvey: () => void;
  onSelectQuiz: (slug: string) => void;
  onSelectSurvey: (slug: string) => void;
  onViewLogs: (serviceName: string) => void;
  onOpenPromptConfig: () => void;
}

export function ConversationServicesTab({
  voiceAgentServices,
  cameraBridgeServices,
  visionControllerService,
  conversation,
  vision,
  loading,
  visionEnabled,
  visionAvailable,
  visionLoading,
  ragRuntimeEnabled,
  ragRuntimeAvailable,
  ragToggleLoading,
  quizRuntimeEnabled,
  quizToggleLoading,
  surveyRuntimeEnabled,
  surveyToggleLoading,
  quizOptions,
  surveyOptions,
  activeQuizSlug,
  activeSurveySlug,
  canToggleConversation,
  getServiceDisplayName,
  getServiceStartConflict,
  canToggleService,
  getServiceActionLabel,
  requiredServicesRunning,
  onToggleVoiceAgentServices,
  onToggleService,
  onToggleConversation,
  onToggleVision,
  onToggleRag,
  onToggleQuiz,
  onToggleSurvey,
  onSelectQuiz,
  onSelectSurvey,
  onViewLogs,
  onOpenPromptConfig,
}: ConversationServicesTabProps) {
  const serviceRows = [
    ...voiceAgentServices,
    ...cameraBridgeServices,
    ...(visionControllerService ? [visionControllerService] : []),
  ];
  const voiceServicesActive = voiceAgentServices.some((s) => ['starting', 'running', 'stopping'].includes(s.state));
  const voiceServicesLoading = loading === 'voice-services-start' || loading === 'voice-services-stop';
  const hasVoiceAgent = serviceRows.some((service) => service.name === 'voice-agent');
  const [activePromptConfig, setActivePromptConfig] = useState<PromptConfig | null>(null);
  const showIdCaptureMonitor = Boolean(vision?.active && vision?.features?.card_capture?.enabled);

  useEffect(() => {
    if (!hasVoiceAgent) {
      setActivePromptConfig(null);
      return;
    }

    let cancelled = false;

    const loadActivePrompt = async () => {
      try {
        const config = await api.getActivePrompt();
        if (!cancelled) {
          setActivePromptConfig(config);
        }
      } catch (err) {
        if (!cancelled) {
          setActivePromptConfig(null);
        }
        console.error('Failed to load active persona parts:', err);
      }
    };

    void loadActivePrompt();
    window.addEventListener('prompt-config-updated', loadActivePrompt);

    return () => {
      cancelled = true;
      window.removeEventListener('prompt-config-updated', loadActivePrompt);
    };
  }, [hasVoiceAgent]);

  return (
    <div className="space-y-4">
      {/* Section 1: Voice Agent Services */}
      <CollapsibleSection title="Voice Agent Services">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <p className="text-xs text-muted-foreground">LiveKit server, voice services, camera bridge, and vision controller.</p>
          <button
            onClick={onToggleVoiceAgentServices}
            disabled={voiceServicesLoading || voiceAgentServices.length === 0}
            className={`px-5 py-2 text-sm text-white rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
              voiceServicesActive ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700'
            }`}
          >
            {loading === 'voice-services-start'
              ? 'Starting...'
              : loading === 'voice-services-stop'
              ? 'Stopping...'
              : voiceServicesActive
              ? 'Stop All'
              : 'Start All'}
          </button>
        </div>
        <div className="space-y-3">
          {serviceRows.map((service) => {
            const isLoading = loading === `service-${service.name}`;
            const startConflict = getServiceStartConflict(service.name);
            const canToggle = canToggleService(service);

            return (
              <div
                key={service.name}
                className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border/60 bg-muted/50 p-4"
              >
                <div className="min-w-[220px] flex-1">
                  <div className="flex items-center gap-3">
                    <span className="font-medium">{getServiceDisplayName(service)}</span>
                    <button
                      onClick={() => onViewLogs(service.name)}
                      className="rounded-full border border-border/30 bg-background/20 px-2 py-0.5 text-[11px] text-muted-foreground/55 transition-colors hover:border-border/55 hover:bg-background/45 hover:text-muted-foreground/80"
                      title="View logs"
                    >
                      View Logs
                    </button>
                  </div>
                  {service.name === 'voice-agent' && activePromptConfig && (
                    <div className="mt-2 flex max-w-full flex-wrap items-center gap-1.5">
                      {PROMPT_PARTS.map(({ key, label, className }) => {
                        const value = activePromptConfig[key];

                        if (!value) {
                          return null;
                        }

                        return (
                          <span
                            key={key}
                            className={`inline-flex max-w-[220px] items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium leading-none ${className}`}
                            title={`${label}: ${value}`}
                          >
                            <span className="shrink-0 opacity-70">{label}</span>
                            <span className="truncate">{formatPromptPart(value)}</span>
                          </span>
                        );
                      })}
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <ServiceConfigRow
                    serviceName={service.name}
                    serviceState={service.state}
                    onOpenPromptConfig={service.name === 'voice-agent' ? onOpenPromptConfig : undefined}
                  />
                  <span
                    className={`h-2.5 w-2.5 rounded-full ${
                      service.state === 'running'
                        ? 'bg-emerald-500'
                        : service.state === 'stopped'
                        ? 'bg-amber-500'
                        : service.state === 'failed'
                        ? 'bg-red-500'
                        : 'bg-blue-500'
                    }`}
                    title={service.state}
                  />
                  <span className="text-xs uppercase text-muted-foreground">{service.state}</span>
                  {service.uptime_seconds != null && service.uptime_seconds > 0 && (
                    <span className="font-mono text-xs text-muted-foreground">
                      {Math.floor(service.uptime_seconds / 60)}m {Math.floor(service.uptime_seconds % 60)}s
                    </span>
                  )}
                  <button
                    onClick={() => onToggleService(service.name, service.state)}
                    disabled={!canToggle}
                    title={service.state === 'running' ? undefined : startConflict ?? undefined}
                    className={`px-3 py-1 text-xs text-white rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                      service.state === 'running' ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700'
                    }`}
                  >
                    {isLoading ? '...' : getServiceActionLabel(service)}
                  </button>
                </div>
              </div>
            );
          })}
          {voiceAgentServices.length === 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              Voice agent services are not configured in the supervisor backend.
            </div>
          )}
        </div>
      </CollapsibleSection>

      {/* Section 2: Conversation + Camera Monitor (two columns) */}
      <CollapsibleSection title="Conversation & Camera Monitor">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Left: Conversation controls + transcript */}
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={onToggleVision}
                  disabled={visionLoading || !requiredServicesRunning() || !visionAvailable}
                  className={`flex items-center gap-2 px-4 py-1.5 text-xs rounded-full font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed ${
                    visionEnabled ? 'bg-purple-600 hover:bg-purple-700 text-white' : 'bg-muted hover:bg-muted/80 text-muted-foreground'
                  }`}
                  title={!visionAvailable ? 'Start Vision Controller to use vision mode' : undefined}
                >
                  {visionLoading ? 'Loading...' : visionEnabled ? 'Vision: ON' : 'Vision: OFF'}
                </button>
                <button
                  onClick={onToggleRag}
                  disabled={ragToggleLoading || !ragRuntimeAvailable}
                  className={`flex items-center gap-2 px-4 py-1.5 text-xs rounded-full font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed ${
                    ragRuntimeEnabled ? 'bg-sky-600 hover:bg-sky-700 text-white' : 'bg-muted hover:bg-muted/80 text-muted-foreground'
                  }`}
                >
                  {ragToggleLoading ? 'Updating...' : ragRuntimeEnabled ? 'RAG: ON' : 'RAG: OFF'}
                </button>
                <button
                  onClick={onToggleQuiz}
                  disabled={quizToggleLoading || conversation.state !== 'engaged'}
                  className={`flex items-center gap-2 px-4 py-1.5 text-xs rounded-full font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed ${
                    quizRuntimeEnabled ? 'bg-sky-600 hover:bg-sky-700 text-white' : 'bg-muted hover:bg-muted/80 text-muted-foreground'
                  }`}
                >
                  {quizToggleLoading ? 'Updating...' : quizRuntimeEnabled ? 'Quiz: ON' : 'Quiz: OFF'}
                </button>
                {quizRuntimeEnabled && (
                  <select
                    value={activeQuizSlug}
                    onChange={(e) => onSelectQuiz(e.target.value)}
                    disabled={!quizRuntimeEnabled || quizOptions.length === 0}
                    className="max-w-[180px] text-xs border border-border bg-background text-foreground rounded-md px-2 py-1"
                  >
                    <option value="">Select quiz...</option>
                    {quizOptions.map((quiz) => (
                      <option key={quiz.slug} value={quiz.slug}>{quiz.title}</option>
                    ))}
                  </select>
                )}
                <button
                  onClick={onToggleSurvey}
                  disabled={surveyToggleLoading || conversation.state !== 'engaged'}
                  className={`flex items-center gap-2 px-4 py-1.5 text-xs rounded-full font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed ${
                    surveyRuntimeEnabled ? 'bg-sky-600 hover:bg-sky-700 text-white' : 'bg-muted hover:bg-muted/80 text-muted-foreground'
                  }`}
                >
                  {surveyToggleLoading ? 'Updating...' : surveyRuntimeEnabled ? 'Survey: ON' : 'Survey: OFF'}
                </button>
                {surveyRuntimeEnabled && (
                  <select
                    value={activeSurveySlug}
                    onChange={(e) => onSelectSurvey(e.target.value)}
                    disabled={!surveyRuntimeEnabled || surveyOptions.length === 0}
                    className="max-w-[180px] text-xs border border-border bg-background text-foreground rounded-md px-2 py-1"
                  >
                    <option value="">Select survey...</option>
                    {surveyOptions.map((survey) => (
                      <option key={survey.slug} value={survey.slug}>{survey.title}</option>
                    ))}
                  </select>
                )}
              </div>
              <button
                onClick={onToggleConversation}
                disabled={!canToggleConversation || visionEnabled}
                className={`px-5 py-2 text-sm text-white rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  conversation.state === 'engaged' ? 'bg-orange-600 hover:bg-orange-700' : 'bg-blue-600 hover:bg-blue-700'
                }`}
                title={visionEnabled ? 'Disabled - vision mode is controlling conversations' : undefined}
              >
                {loading === 'conversation'
                  ? conversation.state === 'engaged' ? 'Ending...' : 'Starting...'
                  : conversation.state === 'engaged' ? 'End Conversation' : 'Start Conversation'}
              </button>
            </div>

            <div className="space-y-2 text-sm">
              <p className="flex flex-wrap items-center gap-2">
                <span className="text-muted-foreground">State</span>
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    conversation.state === 'engaged' ? 'bg-emerald-500' : conversation.state === 'idle' ? 'bg-amber-500' : conversation.state === 'error' ? 'bg-red-500' : 'bg-blue-500'
                  }`}
                />
                <span
                  className={`font-mono font-semibold ${
                    conversation.state === 'engaged' ? 'text-emerald-500' : conversation.state === 'idle' ? 'text-muted-foreground' : conversation.state === 'error' ? 'text-red-500' : 'text-blue-500'
                  }`}
                >
                  {conversation.state.toUpperCase()}
                </span>
                {visionEnabled && (
                  <span className="rounded-full bg-purple-100 px-2 py-0.5 text-xs font-semibold text-purple-700 dark:bg-purple-900/30 dark:text-purple-200">
                    AUTO-MODE
                  </span>
                )}
              </p>
              {conversation.room && (
                <p className="flex items-center gap-2">
                  <span className="text-muted-foreground">Room</span>
                  <span className="font-mono">{conversation.room}</span>
                </p>
              )}
              {conversation.job_id && (
                <p className="flex items-center gap-2">
                  <span className="text-muted-foreground">Job ID</span>
                  <span className="font-mono text-xs">{conversation.job_id}</span>
                </p>
              )}
              {conversation.uptime_seconds != null && conversation.uptime_seconds > 0 && (
                <p className="flex items-center gap-2">
                  <span className="text-muted-foreground">Duration</span>
                  <span className="font-mono font-semibold text-emerald-500">
                    {Math.floor(conversation.uptime_seconds / 60)}m {Math.floor(conversation.uptime_seconds % 60)}s
                  </span>
                </p>
              )}
            </div>

            {!requiredServicesRunning() && conversation.state === 'idle' && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                Warning: Start LiveKit, Voice Agent, and Audio Bridge before{visionEnabled ? ' enabling vision mode or ' : ' '}dispatching a conversation
              </div>
            )}
            {visionEnabled && conversation.state === 'idle' && requiredServicesRunning() && (
              <div className="rounded-lg border border-purple-200 bg-purple-50 p-3 text-xs text-purple-800 dark:border-purple-500/30 dark:bg-purple-500/10 dark:text-purple-200">
                Vision mode active - conversations will auto-start when a person is detected by the camera
              </div>
            )}
            {!visionEnabled && conversation.state === 'idle' && requiredServicesRunning() && (
              <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-800 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200">
                Manual mode: click Start Conversation to begin, or enable Vision mode for auto-start when a person is detected
              </div>
            )}

            {conversation.state === 'engaged' && (
              <div>
                <h3 className="mb-2 text-xs font-semibold text-muted-foreground">Transcript</h3>
                <TranscriptView />
              </div>
            )}

            {showIdCaptureMonitor && <IdCaptureMonitorPanel vision={vision} />}
          </div>

          {/* Right: Camera monitor */}
          <CameraMonitorSection
            cameraServices={cameraBridgeServices}
            showAudioMonitor
          />
        </div>
      </CollapsibleSection>

      {/* Section 3: Tell the Robot What to Do */}
      <CollapsibleSection title="Tell the Robot What to Do">
        <AgentCommandPanel conversation={conversation} />
      </CollapsibleSection>

      {/* Section 4: Event */}
      <CollapsibleSection title="Event" defaultOpen={false}>
        <ConferenceTab />
      </CollapsibleSection>
    </div>
  );
}
