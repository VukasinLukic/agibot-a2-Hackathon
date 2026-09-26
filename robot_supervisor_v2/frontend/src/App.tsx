import { useEffect, useState } from 'react';
import { Wifi } from 'lucide-react';
import { toast } from 'sonner';
import { API_BASE } from '@/api/base';
import { api } from '@/api/client';
import { SystemProvider, useSystem } from '@/contexts/SystemContext';
import { PromptConfiguration } from '@/components/configuration/PromptConfiguration';
import { CommandPresetsTab } from '@/components/configuration/CommandPresetsTab';
import { KnowledgeTab } from '@/components/knowledge/KnowledgeTab';
import { LogViewer } from '@/components/services/LogViewer';
import { ServiceConfigRow } from '@/components/services/ServiceConfigRow';
import { EngagementManagementTab } from '@/components/engagement/EngagementManagementTab';
import { ConversationServicesTab } from '@/components/tabs/ConversationServicesTab';
import { SpeechTab } from '@/components/tabs/SpeechTab';
import { TeleoperationTab } from '@/components/tabs/TeleoperationTab';
import { PeopleFacesTab } from '@/components/tabs/PeopleFacesTab';
import { LeaderboardTab } from '@/components/tabs/LeaderboardTab';
import { LidarCostmapTab } from '@/components/tabs/LidarCostmapTab';
import { NavigationMissionsTab } from '@/components/tabs/NavigationMissionsTab';
import type {
  Service,
  TeleoperationRuntimeStatus,
  QuizOptionItem,
  SurveyOptionItem,
  RuntimeEnvironmentName,
  RuntimeEnvironmentStatus,
  PromptConfig,
} from '@/api/types';

const VOICE_AGENT_SERVICE_NAMES = ['livekit', 'voice-agent', 'audio-bridge', 'gesture-bridge', 'rag-service'] as const;
const LEGACY_CAMERA_SERVICE_NAME = 'conversation-camera-stream';
const CAMERA_BRIDGE_SERVICE_PREFIX = 'camera-bridge';
const VISION_CONTROLLER_SERVICE_NAME = 'vision-controller';
const RECORDING_SERVICE_NAMES = ['video-recording-service'] as const;

type AppTab = 'conversation-services' | 'speech' | 'rag' | 'quiz-survey' | 'teleoperation' | 'people' | 'leaderboard' | 'lidar' | 'navigation' | 'commands' | 'services';

function getServiceDisplayName(service: Service) {
  if (service.name === 'teleimager-server') return 'camera stream to VR';
  if (service.name === 'inspire-hands') return 'hand and finger tracking';
  if (service.name === 'xr-teleop') return 'teleoperation';
  if (service.name === 'igra') return 'IGRA (hand gestures / RPS)';
  return service.display_name;
}

function AppContent() {
  const { services, conversation, vision, robotTemperature, network, runtime, robot, connected, error } = useSystem();
  //const visibleServices = services.filter((service) => !HIDDEN_SERVICE_NAMES.has(service.name));
  const visibleServices = services;
  const [loading, setLoading] = useState<string | null>(null);
  const [logViewerService, setLogViewerService] = useState<string | null>(null);
  const [visionLoading, setVisionLoading] = useState(false);
  const [ragToggleLoading, setRagToggleLoading] = useState(false);
  const [quizRuntimeEnabled, setQuizRuntimeEnabled] = useState(false);
  const [quizToggleLoading, setQuizToggleLoading] = useState(false);
  const [surveyRuntimeEnabled, setSurveyRuntimeEnabled] = useState(false);
  const [surveyToggleLoading, setSurveyToggleLoading] = useState(false);
  const [quizOptions, setQuizOptions] = useState<QuizOptionItem[]>([]);
  const [surveyOptions, setSurveyOptions] = useState<SurveyOptionItem[]>([]);
  const [activeQuizSlug, setActiveQuizSlug] = useState('');
  const [activeSurveySlug, setActiveSurveySlug] = useState('');
  const [showPromptModal, setShowPromptModal] = useState(false);
  const [runtimeEnvironment, setRuntimeEnvironment] = useState<RuntimeEnvironmentStatus | null>(null);
  const [environmentLoading, setEnvironmentLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<AppTab>('conversation-services');
  const [teleopRuntime, setTeleopRuntime] = useState<TeleoperationRuntimeStatus | null>(null);
  const [activePromptConfig, setActivePromptConfig] = useState<PromptConfig | null>(null);
  const [isDarkMode, setIsDarkMode] = useState(() => {
    if (typeof window === 'undefined') {
      return true;
    }
    const stored = window.localStorage.getItem('theme');
    if (stored) {
      return stored === 'dark';
    }
    return true;
  });
  const ragRuntimeEnabled = runtime?.rag.enabled ?? false;
  const ragRuntimeAvailable = runtime?.rag.available ?? false;
  const robotName = robot?.name?.trim() || 'Robot';

  useEffect(() => {
    const root = document.documentElement;
    if (isDarkMode) {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    root.style.colorScheme = isDarkMode ? 'dark' : 'light';
    window.localStorage.setItem('theme', isDarkMode ? 'dark' : 'light');
  }, [isDarkMode]);

  useEffect(() => {
    document.title = `RS: ${robotName}`;
  }, [robotName]);

  useEffect(() => {
    const loadActivePrompt = async () => {
      try {
        setActivePromptConfig(await api.getActivePrompt());
      } catch (err) {
        console.error('Failed to load active persona:', err);
      }
    };
    const handlePromptUpdate = () => void loadActivePrompt();

    void loadActivePrompt();
    window.addEventListener('prompt-config-updated', handlePromptUpdate);
    return () => window.removeEventListener('prompt-config-updated', handlePromptUpdate);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        setRuntimeEnvironment(await api.getRuntimeEnvironment());
      } catch (err) {
        console.error('Failed to load runtime environment:', err);
      }
    })();
  }, []);

  const handleToggleServices = async () => {
    const anyActive = visibleServices.some((s) => ['starting', 'running', 'stopping'].includes(s.state));
    setLoading('services');
    try {
      if (anyActive) {
        await api.stopAllServices();
        toast.success('Stopped all services');
      } else {
        const failedServices = visibleServices.filter((service) => service.state === 'failed');
        for (const service of failedServices) {
          await api.restartService(service.name);
        }
        await api.startAllServices();
        toast.success('Started all services');
      }
    } catch (err) {
      toast.error(`Failed to toggle services: ${err}`);
    } finally {
      setLoading(null);
    }
  };

  const handleToggleConversation = async () => {
    setLoading('conversation');
    try {
      if (conversation.state === 'engaged') {
        await api.wrapConversation(false);
        toast.success('Conversation ended');
      } else {
        await api.dispatchConversation('g1-lab');
        toast.success('Conversation started');
      }
    } catch (err) {
      toast.error(`Failed to toggle conversation: ${err}`);
    } finally {
      setLoading(null);
    }
  };

  const stopRecordingServicesForTabLeave = async () => {
    try {
      const status = await api.getVideoRecordingStatus();
      const shouldStop =
        status.running ||
        status.recording.active ||
        status.cv_debug.enabled ||
        ['starting', 'running', 'stopping', 'failed'].includes(status.service.state);

      if (shouldStop) {
        await Promise.all(RECORDING_SERVICE_NAMES.map((serviceName) => api.stopService(serviceName)));
      }
    } catch (err) {
      console.error('Failed to stop recording services after leaving Commands tab:', err);
      toast.error(`Failed to stop recording services: ${err}`);
    }
  };

  const handleTabChange = (nextTab: AppTab) => {
    setActiveTab((currentTab) => {
      if (currentTab === 'commands' && nextTab !== 'commands') {
        void stopRecordingServicesForTabLeave();
      }
      return nextTab;
    });
  };

  useEffect(() => {
    if (activeTab !== 'commands') {
      return;
    }

    const stopRecordingServicesOnPageHide = () => {
      for (const serviceName of RECORDING_SERVICE_NAMES) {
        const url = `${API_BASE}/api/services/${encodeURIComponent(serviceName)}/stop`;
        if (!navigator.sendBeacon?.(url, new Blob([], { type: 'text/plain' }))) {
          void fetch(url, { method: 'POST', keepalive: true }).catch(() => undefined);
        }
      }
    };

    window.addEventListener('pagehide', stopRecordingServicesOnPageHide);
    return () => {
      window.removeEventListener('pagehide', stopRecordingServicesOnPageHide);
    };
  }, [activeTab]);

  const canToggleService = (service: Service) => {
    const startConflict = getServiceStartConflict(service.name);
    const isLoading = loading === `service-${service.name}`;

    return (
      !isLoading &&
      (service.state === 'running' ||
        ((service.state === 'stopped' || service.state === 'failed') && !startConflict))
    );
  };

  const getServiceActionLabel = (service: Service) => {
    if (service.state === 'running') {
      return 'Stop';
    }
    if (service.state === 'failed') {
      return 'Retry';
    }
    return 'Start';
  };

  const handleToggleService = async (serviceName: string, currentState: string) => {
    const action = currentState === 'running' ? 'stop' : currentState === 'failed' ? 'retry' : 'start';
    setLoading(`service-${serviceName}`);
    try {
      if (action === 'start') {
        const legacyCameraStreamRunning = services.find((service) => service.name === LEGACY_CAMERA_SERVICE_NAME)?.state;
        if (
          serviceName.startsWith(CAMERA_BRIDGE_SERVICE_PREFIX) &&
          (legacyCameraStreamRunning === 'running' || legacyCameraStreamRunning === 'starting')
        ) {
          await api.stopService(LEGACY_CAMERA_SERVICE_NAME);
          toast.info('Stopped a conflicting legacy camera service so Camera Bridge can use the device');
        }
        await api.startService(serviceName);
        toast.success(`Started ${serviceName}`);
      } else if (action === 'retry') {
        await api.restartService(serviceName);
        toast.success(`Retried ${serviceName}`);
      } else {
        await api.stopService(serviceName);
        toast.success(`Stopped ${serviceName}`);
      }
    } catch (err) {
      toast.error(`Failed to ${action} ${serviceName}: ${err}`);
    } finally {
      setLoading(null);
    }
  };

  const handleToggleVision = async () => {
    setVisionLoading(true);
    try {
      if (visionEnabled) {
        await api.setVisionMode(false);
        toast.success('Vision mode disabled');
      } else {
        const result = await api.setVisionMode(true);
        if (result.active) {
          toast.success('Vision mode enabled');
        } else {
          toast.info('Vision mode will activate when Vision Controller is running');
        }
      }
    } catch (err) {
      toast.error(`Failed to toggle vision mode: ${err}`);
    } finally {
      setVisionLoading(false);
    }
  };

  const handleToggleRag = async () => {
    if (conversation.state !== 'engaged') {
      return;
    }
    if (!conversation.room) {
      toast.error('No active conversation room found');
      return;
    }

    const nextEnabled = !ragRuntimeEnabled;
    setRagToggleLoading(true);
    try {
      await api.sendAgentCommand({
        text: nextEnabled ? '__RAG_ON__' : '__RAG_OFF__',
        plain_text: true,
        room: conversation.room,
      });
      toast.success(nextEnabled ? 'RAG enabled' : 'RAG disabled');
    } catch (err) {
      toast.error(`Failed to toggle RAG: ${err}`);
    } finally {
      setRagToggleLoading(false);
    }
  };

  useEffect(() => {
    void (async () => {
      try {
        const [quizData, surveyData] = await Promise.all([
          api.getQuizOptions(),
          api.getSurveyOptions(),
        ]);

        setQuizOptions(quizData.options?.quizzes ?? []);
        setActiveQuizSlug(quizData.active?.quiz_slug ?? '');

        setSurveyOptions(surveyData.options?.surveys ?? []);
        setActiveSurveySlug(surveyData.active?.survey_slug ?? '');
      } catch (err) {
        console.error('Failed to load quiz/survey options:', err);
      }
    })();
  }, []);

  const handleToggleQuiz = async () => {
    if (conversation.state !== 'engaged' || !conversation.room) {
      toast.error('Start a conversation first');
      return;
    }

    const nextEnabled = !quizRuntimeEnabled;
      setQuizToggleLoading(true);
      try {
        await api.sendAgentCommand({
          text: nextEnabled ? '__QUIZ_ON__' : '__QUIZ_OFF__',
          plain_text: true,
          room: conversation.room,
        });
        setQuizRuntimeEnabled(nextEnabled);
        toast.success(nextEnabled ? 'Quiz enabled' : 'Quiz disabled');
      } catch (err) {
        toast.error(`Failed to toggle quiz: ${err}`);
      } finally {
        setQuizToggleLoading(false);
      }
  };

  const handleToggleSurvey = async () => {
    if (conversation.state !== 'engaged' || !conversation.room) {
      toast.error('Start a conversation first');
      return;
    }

    const nextEnabled = !surveyRuntimeEnabled;
    setSurveyToggleLoading(true);
    try {
      await api.sendAgentCommand({
        text: nextEnabled ? '__SURVEY_ON__' : '__SURVEY_OFF__',
        plain_text: true,
        room: conversation.room,
      });
      setSurveyRuntimeEnabled(nextEnabled);
      toast.success(nextEnabled ? 'Survey enabled' : 'Survey disabled');
    } catch (err) {
      toast.error(`Failed to toggle survey: ${err}`);
    } finally {
      setSurveyToggleLoading(false);
    }
  };

  const handleSelectQuiz = async (quizSlug: string) => {
    if (!quizSlug) return;

    try {
      const result = await api.updateQuizConfig(quizSlug);
      setActiveQuizSlug(result.active.quiz_slug);
      toast.success(`Active quiz set to ${result.active.quiz_title}`);
    } catch (err) {
      toast.error(`Failed to update active quiz: ${err}`);
    }
  };

  const handleSelectSurvey = async (surveySlug: string) => {
      if (!surveySlug) return;

      try {
        const result = await api.updateSurveyConfig(surveySlug);
        setActiveSurveySlug(result.active.survey_slug);
        toast.success(`Active survey set to ${result.active.survey_title}`);
      } catch (err) {
        toast.error(`Failed to update active survey: ${err}`);
      }
  };

  const handleStartTeleoperation = async () => {
    setLoading('teleop-start');
    try {
      const result = await api.startTeleoperation();
      setTeleopRuntime((prev) => ({
        service_state: (xrTeleopService?.state ?? prev?.service_state ?? 'running'),
        ipc_online: true,
        heartbeat: result.heartbeat ?? {},
        last_error: null,
      }));
      toast.success('Teleoperation started');
    } catch (err) {
      toast.error(`Failed to start teleoperation: ${err}`);
    } finally {
      setLoading(null);
    }
  };

  const handleStopTeleoperation = async () => {
    setLoading('teleop-stop');
    try {
      const result = await api.stopTeleoperation();
      setTeleopRuntime((prev) => ({
        service_state: (result.service_state as TeleoperationRuntimeStatus['service_state']) ?? prev?.service_state ?? 'stopped',
        ipc_online: false,
        heartbeat: result.heartbeat ?? {},
        last_error: null,
      }));
      toast.success('Teleoperation stop command sent');
    } catch (err) {
      toast.error(`Failed to stop teleoperation: ${err}`);
    } finally {
      setLoading(null);
    }
  };

  const handleToggleVoiceAgentServices = async () => {
    const activeVoiceServices = voiceAgentServices.filter((service) =>
      ['starting', 'running', 'stopping'].includes(service.state)
    );

    if (activeVoiceServices.length > 0) {
      setLoading('voice-services-stop');
      try {
        await Promise.all([...activeVoiceServices].reverse().map((service) => api.stopService(service.name)));
        toast.success('Stopped voice agent services');
      } catch (err) {
        toast.error(`Failed to stop voice agent services: ${err}`);
      } finally {
        setLoading(null);
      }
      return;
    }

    setLoading('voice-services-start');
    try {
      const result = await api.startSpeechServices();
      if (result.skipped_services.length > 0) {
        toast.success(`Started voice agent services. Skipped: ${result.skipped_services.join(', ')}`);
      } else {
        toast.success('Started voice agent services');
      }
    } catch (err) {
      toast.error(`Failed to start voice agent services: ${err}`);
    } finally {
      setLoading(null);
    }
  };

  const handleSelectRuntimeEnvironment = async (environment: RuntimeEnvironmentName) => {
    if (environmentLoading || runtimeEnvironment?.environment === environment) {
      return;
    }
    if (conversation.state !== 'idle') {
      toast.error('End the active conversation before changing environment');
      return;
    }

    setEnvironmentLoading(true);
    try {
      const result = await api.updateRuntimeEnvironment(environment);
      setRuntimeEnvironment(result);
      if (result.restarted_services?.length) {
        toast.success(`Switched to ${environment} and restarted ${result.restarted_services.join(', ')}`);
      } else {
        toast.success(`Switched to ${environment}`);
      }
    } catch (err) {
      toast.error(`Failed to switch environment: ${err}`);
    } finally {
      setEnvironmentLoading(false);
    }
  };

  const anyServiceActive = visibleServices.some((s) => ['starting', 'running', 'stopping'].includes(s.state));
  const requiredServicesRunning = () => {
    const required = ['livekit', 'voice-agent', 'audio-bridge'];
    return required.every((name) => services.find((s) => s.name === name)?.state === 'running');
  };
  const getServiceStartConflict = (serviceName: string) => {
    const gestureBridgeRunning = services.find((service) => service.name === 'gesture-bridge')?.state === 'running';
    const xrTeleopRunning = services.find((service) => service.name === 'xr-teleop')?.state === 'running';

    if (serviceName === 'gesture-bridge' && xrTeleopRunning) {
      return 'Gesture Bridge is exclusive with XR Teleoperation. Stop teleoperation first.';
    }
    if (serviceName === 'xr-teleop' && gestureBridgeRunning) {
      return 'Teleoperation is exclusive with Gesture Bridge. Stop Gesture Bridge first.';
    }
    return null;
  };
  const teleimagerService = services.find((service) => service.name === 'teleimager-server');
  const inspireHandsService = services.find((service) => service.name === 'inspire-hands');
  const xrTeleopService = services.find((service) => service.name === 'xr-teleop');
  const cameraBridgeServices = visibleServices.filter((service) =>
    service.name.startsWith(CAMERA_BRIDGE_SERVICE_PREFIX)
  );
  const cameraBridgeService = cameraBridgeServices.find((service) => service.name === 'camera-bridge');
  const visionControllerService = services.find((service) => service.name === VISION_CONTROLLER_SERVICE_NAME);
  const temperatureMonitorService = services.find((service) => service.name === 'robot-temperature-monitor');
  const igraService = services.find((service) => service.name === 'igra');
  const voiceAgentServices = VOICE_AGENT_SERVICE_NAMES
    .map((name) => services.find((service) => service.name === name))
    .filter((service): service is Service => Boolean(service));

  useEffect(() => {
    if (activeTab !== 'teleoperation') {
      return;
    }

    let cancelled = false;

    const loadTeleopRuntime = async () => {
      try {
        const status = await api.getTeleoperationStatus();
        if (!cancelled) {
          setTeleopRuntime(status);
        }
      } catch {
        if (!cancelled) {
          setTeleopRuntime(null);
        }
      }
    };

    void loadTeleopRuntime();
    const intervalId = window.setInterval(() => {
      void loadTeleopRuntime();
    }, 2000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeTab, xrTeleopService?.state]);

  const servicesLoading = loading === 'services';
  const conversationLoading = loading === 'conversation';
  const visionEnabled = Boolean(vision?.active);
  const visionAvailable = Boolean(vision?.service.running);
  const canToggleConversation =
    (conversation.state === 'idle' && requiredServicesRunning() && !conversationLoading) ||
    (conversation.state === 'engaged' && !conversationLoading);
  const connectionLabel = connected ? 'Connected' : error ? 'Error' : 'Connecting';
  const networkLabel = network
    ? network.connected
      ? network.ssid?.trim() || 'WiFi connected'
      : network.error
        ? 'WiFi unavailable'
        : 'WiFi not connected'
    : null;
  const networkTitle = [
    networkLabel,
    network?.interface ? `iface: ${network.interface}` : null,
    network?.ip ? `IP: ${network.ip}` : null,
  ].filter(Boolean).join(' · ') || undefined;
  const environmentOptions = runtimeEnvironment?.available_environments ?? (['DEV', 'UAT', 'PROD', 'CT'] as RuntimeEnvironmentName[]);
  const environmentDisabled = environmentLoading || conversation.state !== 'idle';
  const activeEnvClass = runtimeEnvironment
    ? !runtimeEnvironment.env_file_exists
      ? 'bg-destructive text-destructive-foreground shadow-sm'
      : !runtimeEnvironment.valid
        ? 'bg-amber-500 text-white shadow-sm'
        : 'bg-emerald-600 text-white shadow-sm'
    : 'bg-primary text-primary-foreground shadow-sm';
  const environmentTitle = runtimeEnvironment
    ? runtimeEnvironment.valid
      ? [
          runtimeEnvironment.azure.openai_host ? `OpenAI: ${runtimeEnvironment.azure.openai_host}` : null,
          runtimeEnvironment.azure.search_host ? `Search: ${runtimeEnvironment.azure.search_host}` : null,
          runtimeEnvironment.azure.search_index ? `Index: ${runtimeEnvironment.azure.search_index}` : null,
          runtimeEnvironment.truebar.api_host ? `Truebar: ${runtimeEnvironment.truebar.api_host}` : null,
        ].filter(Boolean).join(' | ') || 'Azure environment'
      : `Missing: ${runtimeEnvironment.missing_required.join(', ')}`
    : 'Azure environment';

  const servicesPanel = (
    <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">Services</h2>
          <p className="text-xs text-muted-foreground">Manage the LiveKit stack services.</p>
        </div>
        <button
          onClick={handleToggleServices}
          disabled={servicesLoading}
          className={`px-5 py-2 text-sm text-white rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
            anyServiceActive ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700'
          }`}
        >
          {servicesLoading ? (anyServiceActive ? 'Stopping...' : 'Starting...') : anyServiceActive ? 'Stop All' : 'Start All'}
        </button>
      </div>
      <div className="space-y-3">
        {visibleServices.map((service) => {
          const isLoading = loading === `service-${service.name}`;
          const startConflict = getServiceStartConflict(service.name);
          const canToggle = canToggleService(service);

          return (
            <div
              key={service.name}
              className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border/60 bg-muted/50 p-4"
            >
              <div className="flex items-center gap-3">
                <span className="font-medium">{getServiceDisplayName(service)}</span>
                <button
                  onClick={() => setLogViewerService(service.name)}
                  className="rounded-full border border-border/30 bg-background/20 px-2 py-0.5 text-[11px] text-muted-foreground/55 transition-colors hover:border-border/55 hover:bg-background/45 hover:text-muted-foreground/80"
                  title="View logs"
                >
                  View Logs
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <ServiceConfigRow
                  serviceName={service.name}
                  serviceState={service.state}
                  onOpenPromptConfig={service.name === 'voice-agent' ? () => setShowPromptModal(true) : undefined}
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
                {service.uptime_seconds && service.uptime_seconds > 0 && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {Math.floor(service.uptime_seconds / 60)}m {Math.floor(service.uptime_seconds % 60)}s
                  </span>
                )}
                <button
                  onClick={() => void handleToggleService(service.name, service.state)}
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
      </div>
    </section>
  );

  const knowledgePanel = (
    <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
      <KnowledgeTab />
    </section>
  );

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <nav className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-3">
              <img
                src="/ct-logo-icon.png"
                alt=""
                aria-hidden="true"
                className="h-10 w-10 rounded-lg object-contain"
              />
              <div>
                <p className="text-lg font-semibold">Robot Supervisor</p>
                <p className="text-xs text-muted-foreground">{robotName}</p>
              </div>
            </div>
            <div className="hidden h-6 w-px bg-border/60 md:block" />
            <div className="flex max-w-[calc(100vw-3rem)] items-center gap-2 overflow-x-auto rounded-full border border-border/60 bg-muted/60 p-1">
              <button
                type="button"
                onClick={() => handleTabChange('conversation-services')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'conversation-services' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'conversation-services'}
              >
                Conversation Services
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('rag')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'rag' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'rag'}
              >
                RAG
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('speech')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'speech' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'speech'}
              >
                Speech
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('quiz-survey')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'quiz-survey' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'quiz-survey'}
              >
                Quiz &amp; Survey
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('teleoperation')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'teleoperation' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'teleoperation'}
              >
                Teleoperation
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('people')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'people' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'people'}
              >
                People
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('leaderboard')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'leaderboard' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'leaderboard'}
              >
                Leaderboard
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('lidar')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'lidar' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'lidar'}
              >
                LiDAR
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('navigation')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'navigation' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'navigation'}
              >
                Navigation
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('commands')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'commands' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'commands'}
              >
                Commands
              </button>
              <button
                type="button"
                onClick={() => handleTabChange('services')}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  activeTab === 'services' ? 'bg-rose-600 text-white shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
                aria-pressed={activeTab === 'services'}
              >
                All Services
              </button>
            </div>
          </div>
          <div className="flex min-w-0 items-center gap-3 text-sm font-medium">
            <div
              className="flex items-center gap-1 rounded-full border border-border/60 bg-muted/60 p-1"
              title={environmentTitle}
            >
              {environmentOptions.map((environment) => (
                <button
                  key={environment}
                  type="button"
                  onClick={() => void handleSelectRuntimeEnvironment(environment)}
                  disabled={environmentDisabled}
                  aria-pressed={runtimeEnvironment?.environment === environment}
                  className={`rounded-full px-3 py-1 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                    runtimeEnvironment?.environment === environment
                      ? activeEnvClass
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {environment}
                </button>
              ))}
            </div>
            {networkLabel && (
              <div
                className="flex min-w-0 max-w-[18rem] items-center gap-2 rounded-full border border-border/60 bg-muted/60 px-3 py-1.5 text-muted-foreground"
                title={networkTitle}
              >
                <Wifi
                  className={`h-4 w-4 shrink-0 ${network?.connected ? 'text-emerald-500' : 'text-muted-foreground'}`}
                  aria-hidden="true"
                />
                <span className="truncate">{networkLabel}</span>
                {network?.ip && (
                  <>
                    <span className="shrink-0 text-border/80">·</span>
                    <span className="shrink-0 font-mono text-xs">{network.ip}</span>
                  </>
                )}
              </div>
            )}
            <div className="flex items-center gap-2">
              <span
                className={`h-2.5 w-2.5 rounded-full ${
                  connected ? 'bg-emerald-500' : error ? 'bg-red-500' : 'bg-amber-500'
                }`}
              />
              <span>{connectionLabel}</span>
            </div>
          </div>
        </div>
      </nav>

      <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 px-6 py-8">
        {activeTab === 'conversation-services' ? (
          <ConversationServicesTab
            voiceAgentServices={voiceAgentServices}
            cameraBridgeServices={cameraBridgeServices}
            visionControllerService={visionControllerService}
            igraService={igraService}
            conversation={conversation}
            vision={vision}
            loading={loading}
            visionEnabled={visionEnabled}
            visionAvailable={visionAvailable}
            visionLoading={visionLoading}
            ragRuntimeEnabled={ragRuntimeEnabled}
            ragRuntimeAvailable={ragRuntimeAvailable}
            ragToggleLoading={ragToggleLoading}
            quizRuntimeEnabled={quizRuntimeEnabled}
            quizToggleLoading={quizToggleLoading}
            surveyRuntimeEnabled={surveyRuntimeEnabled}
            surveyToggleLoading={surveyToggleLoading}
            quizOptions={quizOptions}
            surveyOptions={surveyOptions}
            activeQuizSlug={activeQuizSlug}
            activeSurveySlug={activeSurveySlug}
            canToggleConversation={canToggleConversation}
            getServiceDisplayName={getServiceDisplayName}
            getServiceStartConflict={getServiceStartConflict}
            canToggleService={canToggleService}
            getServiceActionLabel={getServiceActionLabel}
            requiredServicesRunning={requiredServicesRunning}
            onToggleVoiceAgentServices={handleToggleVoiceAgentServices}
            onToggleService={(name, state) => void handleToggleService(name, state)}
            onToggleConversation={handleToggleConversation}
            onToggleVision={handleToggleVision}
            onToggleRag={handleToggleRag}
            onToggleQuiz={handleToggleQuiz}
            onToggleSurvey={handleToggleSurvey}
            onSelectQuiz={(slug) => void handleSelectQuiz(slug)}
            onSelectSurvey={(slug) => void handleSelectSurvey(slug)}
            onViewLogs={setLogViewerService}
            onOpenPromptConfig={() => setShowPromptModal(true)}
          />
        ) : activeTab === 'speech' ? (
          <SpeechTab />
        ) : activeTab === 'rag' ? (
          <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/60 bg-card px-4 py-3 shadow-sm">
              <div>
                <p className="text-sm font-semibold">RAG / Prompt Profile</p>
                <p className="text-xs text-muted-foreground">Persona controls how the agent speaks; queried indexes control what it knows.</p>
                <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                  <span className="rounded-full bg-rose-500/15 px-2 py-1 font-semibold text-rose-300">
                    Main: {activePromptConfig?.core_mode || 'Not loaded'}
                  </span>
                  <span className="rounded-full bg-muted px-2 py-1 text-muted-foreground">
                    Persona: {activePromptConfig?.persona || 'Main only'}
                  </span>
                  {activePromptConfig?.context ? <span className="rounded-full bg-muted px-2 py-1 text-muted-foreground">Context: {activePromptConfig.context}</span> : null}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowPromptModal(true)}
                className="rounded-md border border-rose-500/50 bg-rose-500/10 px-3 py-2 text-sm font-semibold text-rose-300 transition hover:bg-rose-500/20"
              >
                Configure Persona
              </button>
            </div>
            {knowledgePanel}
          </section>
        ) : activeTab === 'quiz-survey' ? (
          <EngagementManagementTab />
        ) : activeTab === 'teleoperation' ? (
          <TeleoperationTab
            teleimagerService={teleimagerService}
            inspireHandsService={inspireHandsService}
            xrTeleopService={xrTeleopService}
            cameraBridgeService={cameraBridgeService}
            temperatureMonitorService={temperatureMonitorService}
            robotTemperature={robotTemperature}
            teleopRuntime={teleopRuntime}
            loading={loading}
            getServiceDisplayName={getServiceDisplayName}
            getServiceStartConflict={getServiceStartConflict}
            canToggleService={canToggleService}
            getServiceActionLabel={getServiceActionLabel}
            onToggleService={(name, state) => void handleToggleService(name, state)}
            onStartTeleoperation={handleStartTeleoperation}
            onStopTeleoperation={handleStopTeleoperation}
            onViewLogs={setLogViewerService}
          />
        ) : activeTab === 'people' ? (
          <PeopleFacesTab />
        ) : activeTab === 'leaderboard' ? (
          <LeaderboardTab />
        ) : activeTab === 'lidar' ? (
          <LidarCostmapTab />
        ) : activeTab === 'navigation' ? (
          <NavigationMissionsTab />
        ) : activeTab === 'commands' ? (
          <CommandPresetsTab />
        ) : activeTab === 'services' ? (
          <>{servicesPanel}</>
        ) : null}
      </main>

      <div className="flex justify-center px-6 pb-8">
        <button
          type="button"
          onClick={() => setIsDarkMode((prev) => !prev)}
          aria-pressed={isDarkMode}
          className="rounded-full border border-border bg-muted px-5 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted/80"
        >
          {isDarkMode ? 'Dark mode: On' : 'Dark mode: Off'}
        </button>
      </div>

      {showPromptModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-6xl overflow-hidden rounded-2xl">
            <div className="flex justify-end bg-slate-50 px-6 pt-4 pb-2 dark:bg-zinc-900">
              <button
                onClick={() => setShowPromptModal(false)}
                className="rounded-full border border-slate-300 bg-white px-4 py-1.5 text-sm font-medium text-slate-900 shadow-sm transition-colors hover:bg-slate-100 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700"
                aria-label="Close modal"
              >
                Close
              </button>
            </div>
            <div className="max-h-[calc(92vh-64px)] overflow-y-auto">
              <PromptConfiguration />
            </div>
          </div>
        </div>
      )}

      {logViewerService && (
        <LogViewer serviceName={logViewerService} onClose={() => setLogViewerService(null)} />
      )}
    </div>
  );
}

export default function App() {
  return (
    <SystemProvider>
      <AppContent />
    </SystemProvider>
  );
}
