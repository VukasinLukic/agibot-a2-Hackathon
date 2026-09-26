import type {
  Service,
  ConversationStatus,
  AgentCommandRequest,
  AgentCommandResponse,
  Agent,
  AudioDevice,
  AudioBridgeStatus,
  LiveKitMonitorToken,
  CameraBridgeStreamInfo,
  CameraBridgeDemand,
  CameraDevice,
  GestureCatalogResponse,
  GestureBridgeConfigResponse,
  SystemStatus,
  VisionStatus,
  VisionCardCaptureState,
  TeleoperationRuntimeStatus,
  RobotTemperatureState,
  PromptOptionsResponse, 
  PromptConfig,
  PromptUpdateResponse,
  PromptPreviewResponse,
  APIError, 
  KnowledgeState,
  KnowledgeSearchResult, 
  ConferenceProgram,
  ConferenceEventListResponse,
  ConferenceActiveEventResponse,
  ConferenceEventDocument,
  ConferenceEventDeleteResponse,
  PromptItemCreateRequest,
  PromptItemUpdateRequest,
  EventMomentCreateRequest,
  EventMomentUpdateRequest,
  PromptItemListResponse,
  EventMomentListResponse,
  PromptItemResponse,
  PromptItemRuntimeResponse,
  PersonaListResponse,
  PersonaActivationResponse,
  EventMomentResponse,
  ActiveQuizResponse,
  QuizOptionsResponse,
  QuizUpdateResponse,
  QuizCreateRequest,
  QuizItemResponse,
  QuizListResponse,
  QuizQuestionCreateRequest,
  QuizQuestionListResponse,
  QuizQuestionResponse,
  QuizQuestionUpdateRequest,
  QuizUpdateRequest,
  RuntimeQuizResponse,
  SurveyOptionsResponse,
  ActiveSurveyResponse, 
  SurveyUpdateResponse, 
  SurveyListResponse,
  SurveyCreateRequest,
  SurveyItemResponse,
  SurveyUpdateRequest,
  SurveyQuestionListResponse,
  SurveyQuestionCreateRequest,
  SurveyQuestionResponse,
  SurveyQuestionUpdateRequest,
  RuntimeSurveyResponse,
  SurveyExportFormat,
  SurveyExportStatus,
  SurveyRunListResponse,
  SurveyRunResponse,
  KnowledgeIndexState,
  KnowledgeIndexItem,
  KnowledgeIndexCreateRequest,
  KnowledgeIndexDeleteResponse,
  KnowledgeIndexOptionsResponse,
  KnowledgeIndexUpdateResponse,
  CommandPreset,
  CommandPresetRunResponse,
  VideoRecordingConfig,
  VideoRecordingCvDebugMode,
  VideoRecordingFile,
  VideoRecordingIdDebugCaptureImageMode,
  VideoRecordingIdDebugMode,
  VideoRecordingStatus,
  RuntimeEnvironmentName,
  RuntimeEnvironmentStatus,
  NetworkConnectionListResponse,
  NetworkConnectionAddRequest,
  NetworkConnectionActionResponse,
  WifiScanResponse,
  SpeechConfig,
  SpeechBackgroundAudioOptionsResponse,
  SpeechBackgroundAudioUploadResponse,
  SpeechConfigResponse,
  SpeechVoiceOptionsResponse,
  PeopleFaceIdentity,
  PeopleFacesResponse,
} from './types';
import { API_BASE } from './base';

function browserReachableLiveKitUrl(rawUrl: string): string {
  if (typeof window === 'undefined') return rawUrl;
  try {
    const url = new URL(rawUrl);
    const returnedLoopback = url.hostname === 'localhost'
      || url.hostname === '::1'
      || url.hostname.startsWith('127.');
    const browserHostname = window.location.hostname;
    const browserIsLoopback = browserHostname === 'localhost'
      || browserHostname === '::1'
      || browserHostname.startsWith('127.');
    if (returnedLoopback && !browserIsLoopback) {
      url.hostname = browserHostname;
    }
    return url.toString();
  } catch {
    return rawUrl;
  }
}

class APIClient {
  private cachedVideoDevices: { devices: CameraDevice[] } | null = null;
  private cachedVideoDevicesPromise: Promise<{ devices: CameraDevice[] }> | null = null;

  private async request<T>(
    endpoint: string,
    options?: RequestInit
  ): Promise<T> {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
      ...options,
    });

    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    const contentType = response.headers.get('content-type')?.toLowerCase() ?? '';
    if (!contentType.includes('application/json')) {
      const responsePreview = (await response.text()).slice(0, 80).replace(/\s+/g, ' ');
      throw new Error(
        `API endpoint ${endpoint} returned ${contentType || 'an unknown content type'} instead of JSON. `
        + `The Supervisor backend may need to be restarted. Response: ${responsePreview}`
      );
    }

    return response.json() as Promise<T>;
  }


  // Services
  async listServices() {
    return this.request<{ services: Service[] }>('/api/services');
  }

  async startService(name: string) {
    return this.request<{ status: string; service: Service }>(
      `/api/services/${name}/start`,
      { method: 'POST' }
    );
  }

  async stopService(name: string) {
    return this.request<{ status: string; service: Service }>(
      `/api/services/${name}/stop`,
      { method: 'POST' }
    );
  }

  async restartService(name: string) {
    return this.request<{ status: string; service: Service }>(
      `/api/services/${name}/restart`,
      { method: 'POST' }
    );
  }

  async startAllServices() {
    return this.request<{ status: string; services: Service[] }>(
      '/api/services/start-all',
      { method: 'POST' }
    );
  }

  async startSpeechServices() {
    return this.request<{ status: string; services: Service[]; skipped_services: string[] }>(
      '/api/services/start-speech',
      { method: 'POST' }
    );
  }

  async stopAllServices() {
    return this.request<{ status: string; services: Service[] }>(
      '/api/services/stop-all',
      { method: 'POST' }
    );
  }

  async getRuntimeEnvironment() {
    return this.request<RuntimeEnvironmentStatus>('/api/environment');
  }

  async updateRuntimeEnvironment(environment: RuntimeEnvironmentName) {
    return this.request<RuntimeEnvironmentStatus>('/api/environment', {
      method: 'PATCH',
      body: JSON.stringify({ environment }),
    });
  }

  async getSpeechConfig() {
    return this.request<SpeechConfigResponse>('/api/speech/config');
  }

  async updateSpeechConfig(config: SpeechConfig) {
    return this.request<SpeechConfigResponse>('/api/speech/config', {
      method: 'PATCH',
      body: JSON.stringify(config),
    });
  }

  async getSpeechVoices() {
    return this.request<SpeechVoiceOptionsResponse>('/api/speech/voices');
  }

  async getSpeechBackgroundAudioOptions() {
    return this.request<SpeechBackgroundAudioOptionsResponse>('/api/speech/background-audio/options');
  }

  async uploadSpeechBackgroundAudio(file: File) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE}/api/speech/background-audio/upload`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    return response.json() as Promise<SpeechBackgroundAudioUploadResponse>;
  }

  // Conversation
  async dispatchConversation(room?: string, agentImplementation?: string) {
    const params = new URLSearchParams();
    if (room) params.set('room', room);
    if (agentImplementation) params.set('agent_implementation', agentImplementation);

    const query = params.toString() ? `?${params.toString()}` : '';
    return this.request<ConversationStatus>(
      `/api/conversation/dispatch${query}`,
      { method: 'POST' }
    );
  }

  async wrapConversation(graceful: boolean = false) {
    return this.request<{
      status: string;
      duration_seconds?: number;
      previous_job_id?: string;
      previous_room?: string;
    }>(
      `/api/conversation/wrap?graceful=${graceful}`,
      { method: 'POST' }
    );
  }

  async getConversationStatus() {
    return this.request<ConversationStatus>('/api/conversation/status');
  }

  async getTranscript() {
    return this.request<{
      connected: boolean;
      enabled: boolean;
      entries: Array<{
        id: string;
        segment_id: string;
        participant_identity?: string;
        role: 'user' | 'agent' | 'system' | 'unknown';
        text: string;
        final: boolean;
        updated_at: number;
        session_id: number;
      }>;
    }>('/api/conversation/transcript');
  }

  async sendAgentCommand(command: AgentCommandRequest) {
    return this.request<AgentCommandResponse>('/api/conversation/command', {
      method: 'POST',
      body: JSON.stringify(command),
    });
  }

  async listGestures() {
    return this.request<GestureCatalogResponse>('/api/gestures');
  }

  async getGestureBridgeConfig() {
    return this.request<GestureBridgeConfigResponse>('/api/gesture-bridge/config');
  }

  async updateGestureBridgeConfig(safetyPool: string) {
    const params = new URLSearchParams();
    params.set('safety_pool', safetyPool);

    return this.request<GestureBridgeConfigResponse>(
      `/api/gesture-bridge/config?${params.toString()}`,
      { method: 'POST' }
    );
  }

  // Vision
  async setVisionMode(enabled: boolean) {
    return this.request<VisionStatus>(
      '/api/vision',
      {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      }
    );
  }

  async getVisionStatus() {
    return this.request<VisionStatus>('/api/vision');
  }

  async getPeopleFaces() {
    return this.request<PeopleFacesResponse>('/api/people/faces');
  }

  async updatePeopleFace(faceId: string, payload: {
    display_name: string;
    canonical_name: string;
    aliases: string[];
    notes: string;
    match_images: 'both' | 'close' | 'far';
  }) {
    return this.request<{ item: PeopleFaceIdentity }>(`/api/people/faces/${encodeURIComponent(faceId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async deletePeopleFace(faceId: string) {
    return this.request<{ success: boolean }>(`/api/people/faces/${encodeURIComponent(faceId)}`, {
      method: 'DELETE',
    });
  }

  getPeopleFaceImageUrl(faceId: string, distance: 'close' | 'far') {
    return `${API_BASE}/api/people/faces/${encodeURIComponent(faceId)}/image?distance=${distance}`;
  }

  async getVisionCardCapture(requestId: string) {
    return this.request<VisionCardCaptureState>(
      `/api/vision/card-capture/${encodeURIComponent(requestId)}`
    );
  }

  async getTeleoperationStatus() {
    return this.request<TeleoperationRuntimeStatus>('/api/teleoperation/status');
  }

  async startTeleoperation() {
    return this.request<{
      status: string;
      reply: Record<string, unknown>;
      heartbeat: Record<string, boolean>;
    }>('/api/teleoperation/start', {
      method: 'POST',
    });
  }

  async stopTeleoperation() {
    return this.request<{
      status: string;
      reply: Record<string, unknown>;
      heartbeat: Record<string, boolean>;
      service_state: string;
    }>('/api/teleoperation/stop', {
      method: 'POST',
    });
  }

  async getRobotTemperatureStatus() {
    return this.request<RobotTemperatureState>('/api/robot-temperature/status');
  }

  async listCommandPresets() {
    return this.request<{ presets: CommandPreset[] }>('/api/command-presets');
  }

  async runCommandPreset(presetId: string, confirmed = false) {
    return this.request<CommandPresetRunResponse>(
      `/api/command-presets/${encodeURIComponent(presetId)}/run`,
      {
        method: 'POST',
        body: JSON.stringify({ confirmed }),
      }
    );
  }

  async getVideoRecordingConfig() {
    return this.request<VideoRecordingConfig>('/api/video-recording-service/config');
  }

  async updateVideoRecordingConfig(options: {
    device?: string | null;
    resolution?: string | null;
    framerate?: number | null;
    outputDir?: string | null;
    recordOverlayEnabled?: boolean | null;
    cvDebugEnabled?: boolean | null;
    cvDebugMode?: VideoRecordingCvDebugMode | null;
    idDebugEnabled?: boolean | null;
    idDebugRecordCapturesEnabled?: boolean | null;
    idDebugMode?: VideoRecordingIdDebugMode | null;
    idDebugCaptureImageMode?: VideoRecordingIdDebugCaptureImageMode | null;
  }) {
    const params = new URLSearchParams();
    if (options.device != null) params.set('device', options.device);
    if (options.resolution != null) params.set('resolution', options.resolution);
    if (options.framerate != null) params.set('framerate', options.framerate.toString());
    if (options.outputDir != null) params.set('output_dir', options.outputDir);
    if (options.recordOverlayEnabled != null) params.set('record_overlay_enabled', String(options.recordOverlayEnabled));
    if (options.cvDebugEnabled != null) params.set('cv_debug_enabled', String(options.cvDebugEnabled));
    if (options.cvDebugMode != null) params.set('cv_debug_mode', options.cvDebugMode);
    if (options.idDebugEnabled != null) params.set('id_debug_enabled', String(options.idDebugEnabled));
    if (options.idDebugRecordCapturesEnabled != null) {
      params.set('id_debug_record_captures_enabled', String(options.idDebugRecordCapturesEnabled));
    }
    if (options.idDebugMode != null) params.set('id_debug_mode', options.idDebugMode);
    if (options.idDebugCaptureImageMode != null) {
      params.set('id_debug_capture_image_mode', options.idDebugCaptureImageMode);
    }

    return this.request<VideoRecordingStatus>(
      `/api/video-recording-service/config?${params.toString()}`,
      { method: 'POST' },
    );
  }

  async getVideoRecordingStatus() {
    return this.request<VideoRecordingStatus>('/api/video-recording-service/status');
  }

  async resetVideoRecordingIdDebug() {
    return this.request<{ id_debug: VideoRecordingStatus['id_debug'] }>(
      '/api/video-recording-service/id-debug/reset',
      { method: 'POST' },
    );
  }

  async resetVideoRecordingCvDebug() {
    return this.request<{
      cv_debug: VideoRecordingStatus['cv_debug'];
      id_debug: VideoRecordingStatus['id_debug'];
    }>(
      '/api/video-recording-service/cv-debug/reset',
      { method: 'POST' },
    );
  }

  async captureVideoRecordingImage() {
    return this.request<{ file: VideoRecordingFile }>('/api/video-recording-service/capture', {
      method: 'POST',
    });
  }

  async startVideoRecording() {
    return this.request<{ recording: VideoRecordingStatus['recording'] }>('/api/video-recording-service/record/start', {
      method: 'POST',
    });
  }

  async stopVideoRecording() {
    return this.request<{ file: VideoRecordingFile }>('/api/video-recording-service/record/stop', {
      method: 'POST',
    });
  }

  async listVideoRecordingFiles() {
    return this.request<{ files: VideoRecordingFile[] }>('/api/video-recording-service/files');
  }

  async renameVideoRecordingFile(fileId: string, filename: string) {
    return this.request<{ file: VideoRecordingFile }>(
      `/api/video-recording-service/files/${encodeURIComponent(fileId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ filename }),
      },
    );
  }

  async deleteVideoRecordingFile(fileId: string) {
    return this.request<{ success: boolean; deleted: string }>(
      `/api/video-recording-service/files/${encodeURIComponent(fileId)}`,
      { method: 'DELETE' },
    );
  }

  async deleteAllVideoRecordingFiles() {
    return this.request<{ success: boolean; deleted: string[]; deleted_count: number }>(
      '/api/video-recording-service/files',
      { method: 'DELETE' },
    );
  }

  async downloadVideoRecordingFile(fileId: string) {
    const response = await fetch(`${API_BASE}/api/video-recording-service/files/${encodeURIComponent(fileId)}/download`);
    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filenameMatch = disposition.match(/filename="([^"]+)"/);
    return {
      blob: await response.blob(),
      filename: filenameMatch?.[1] ?? fileId,
    };
  }

  getVideoRecordingFilePreviewUrl(fileId: string) {
    return `${API_BASE}/api/video-recording-service/files/${encodeURIComponent(fileId)}/preview`;
  }

  async downloadAllVideoRecordingFiles() {
    const response = await fetch(`${API_BASE}/api/video-recording-service/files/download-all`);
    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filenameMatch = disposition.match(/filename="([^"]+)"/);
    return {
      blob: await response.blob(),
      filename: filenameMatch?.[1] ?? 'video-recording-service.zip',
    };
  }

  // Agents
  async listAgents() {
    return this.request<{ agents: Agent[] }>('/api/agents');
  }

  async getCurrentAgent() {
    return this.request<Agent>('/api/agents/current');
  }

  async selectAgent(agentImplementation: string) {
    return this.request<Agent>(`/api/agents/select/${agentImplementation}`, {
      method: 'POST',
    });
  }

  // Audio device configuration
  async getAudioBridgeConfig() {
    return this.request<{
      default_microphone?: string | number | null;
      default_speakers?: string | number | null;
      enable_rnnoise?: boolean;
    }>('/api/audio-bridge/config');
  }

  async updateAudioBridgeConfig(
    microphone?: string | number | null,
    speakers?: string | number | null,
    enableRnnoise?: boolean | null
  ) {
    const params = new URLSearchParams();
    if (microphone != null) params.set('default_microphone', microphone.toString());
    if (speakers != null) params.set('default_speakers', speakers.toString());
    if (enableRnnoise != null) params.set('enable_rnnoise', String(enableRnnoise));

    return this.request<Service>(
      `/api/audio-bridge/config?${params.toString()}`,
      { method: 'POST' }
    );
  }

  // Camera device configuration
  async getCameraBridgeConfig(serviceName = 'camera-bridge') {
    return this.request<{
      device?: string;
      source?: string | null;
      interval?: number | null;
      send_to_agent?: boolean;
      react_to_visuals?: boolean;
    }>(`/api/camera-bridge/${encodeURIComponent(serviceName)}/config`);
  }

  async updateCameraBridgeConfig(
    serviceName = 'camera-bridge',
    device?: string | null,
    interval?: number | null,
    sendToAgent?: boolean | null,
    source?: string | null,
    reactToVisuals?: boolean | null
  ) {
    const params = new URLSearchParams();
    if (device) params.set('device', device);
    if (source) params.set('source', source);
    if (interval != null) params.set('interval', interval.toString());
    if (sendToAgent != null) params.set('send_to_agent', String(sendToAgent));
    if (reactToVisuals != null) params.set('react_to_visuals', String(reactToVisuals));

    return this.request<Service>(
      `/api/camera-bridge/${encodeURIComponent(serviceName)}/config?${params.toString()}`,
      { method: 'POST' }
    );
  }

  async getVisionControllerConfig() {
    return this.request<{
      camera_id?: string | number;
      camera_resolution?: string | null;
      camera_fps?: number | null;
      camera_fourcc?: string | null;
      camera_buffer_size?: number | null;
      enable_id_scanning?: boolean;
      enable_face_recognition?: boolean;
    }>('/api/vision-controller/config');
  }

  async updateVisionControllerConfig(options: {
    cameraId?: string | number | null;
    cameraResolution?: string | null;
    cameraFps?: number | null;
    cameraFourcc?: string | null;
    cameraBufferSize?: number | null;
    enableIdScanning?: boolean | null;
    enableFaceRecognition?: boolean | null;
  }) {
    const params = new URLSearchParams();
    if (options.cameraId != null) params.set('camera_id', options.cameraId.toString());
    if (options.cameraResolution != null) params.set('camera_resolution', options.cameraResolution);
    if (options.cameraFps != null) params.set('camera_fps', options.cameraFps.toString());
    if (options.cameraFourcc != null) params.set('camera_fourcc', options.cameraFourcc);
    if (options.cameraBufferSize != null) params.set('camera_buffer_size', options.cameraBufferSize.toString());
    if (options.enableIdScanning != null) params.set('enable_id_scanning', String(options.enableIdScanning));
    if (options.enableFaceRecognition != null) params.set('enable_face_recognition', String(options.enableFaceRecognition));

    return this.request<Service>(
      `/api/vision-controller/config?${params.toString()}`,
      { method: 'POST' }
    );
  }

  async getAudioBridgeStatus() {
    return this.request<AudioBridgeStatus>('/api/audio-bridge/status');
  }

  async setAudioBridgeMuted(muted: boolean) {
    return this.request<AudioBridgeStatus>(`/api/audio-bridge/mute?muted=${muted}`, {
      method: 'POST',
    });
  }

  async setAudioBridgeInputGain(inputMicGainDb: number) {
    return this.request<AudioBridgeStatus>(
      `/api/audio-bridge/input-gain?input_mic_gain_db=${encodeURIComponent(inputMicGainDb.toString())}`,
      { method: 'POST' }
    );
  }

  async setAudioBridgeOutputGain(outputSpeakerGainDb: number) {
    return this.request<AudioBridgeStatus>(
      `/api/audio-bridge/output-gain?output_speaker_gain_db=${encodeURIComponent(outputSpeakerGainDb.toString())}`,
      { method: 'POST' }
    );
  }

  async releaseAudioBridgeRemotePlayback() {
    return this.request<AudioBridgeStatus>('/api/audio-bridge/remote-playback/release', {
      method: 'POST',
    });
  }

  async getLiveKitMonitorToken(room?: string) {
    const query = room ? `?room=${encodeURIComponent(room)}` : '';
    const result = await this.request<LiveKitMonitorToken>(`/api/livekit/monitor-token${query}`);
    return { ...result, url: browserReachableLiveKitUrl(result.url) };
  }

  async getCameraBridgeStreamInfo(serviceName = 'camera-bridge') {
    return this.request<CameraBridgeStreamInfo>(
      `/api/camera-bridge/${encodeURIComponent(serviceName)}/stream-info`
    );
  }

  async listCameraBridgeStreamInfo() {
    return this.request<{ streams: CameraBridgeStreamInfo[] }>('/api/camera-bridges/stream-info');
  }

  async createCameraBridgeMonitorSession(serviceName = 'camera-bridge') {
    return this.request<CameraBridgeDemand>(
      `/api/camera-bridge/${encodeURIComponent(serviceName)}/monitor-session`,
      { method: 'POST' }
    );
  }

  async heartbeatCameraBridgeMonitorSession(serviceName: string, sessionId: string) {
    return this.request<CameraBridgeDemand>(
      `/api/camera-bridge/${encodeURIComponent(serviceName)}/monitor-session/${encodeURIComponent(sessionId)}/heartbeat`,
      { method: 'POST' }
    );
  }

  async deleteCameraBridgeMonitorSession(serviceName: string, sessionId: string) {
    return this.request<CameraBridgeDemand>(
      `/api/camera-bridge/${encodeURIComponent(serviceName)}/monitor-session/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE' }
    );
  }

  // Devices
  async listAudioDevices() {
    return this.request<{
      input_devices: AudioDevice[];
      output_devices: AudioDevice[];
    }>('/api/devices/audio');
  }

  async listVideoDevices(options?: { force?: boolean }) {
    if (!options?.force) {
      if (this.cachedVideoDevices) {
        return this.cachedVideoDevices;
      }
      if (this.cachedVideoDevicesPromise) {
        return this.cachedVideoDevicesPromise;
      }
    }

    const requestPromise = this.request<{ devices: CameraDevice[] }>('/api/devices/video')
      .then((response) => {
        this.cachedVideoDevices = {
          devices: response.devices ?? [],
        };
        return this.cachedVideoDevices;
      })
      .finally(() => {
        this.cachedVideoDevicesPromise = null;
      });

    this.cachedVideoDevicesPromise = requestPromise;
    return requestPromise;
  }

  // Prompt configuration
  async getMainPrompt() {
    return this.request<PromptItemRuntimeResponse>('/api/prompts/main');
  }

  async updateMainPrompt(payload: {
    title: string;
    prompt_text: string;
    initial_greeting?: string;
    goodbye_text?: string;
  }) {
    return this.request<PromptItemRuntimeResponse>('/api/prompts/main', {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async listPersonas(includeArchived = false, locale?: string) {
    const params = new URLSearchParams({ include_archived: String(includeArchived) });
    if (locale) params.set('locale', locale);
    return this.request<PersonaListResponse>(`/api/prompts/personas?${params.toString()}`);
  }

  async createPersona(payload: PromptItemCreateRequest) {
    return this.request<PromptItemResponse>('/api/prompts/personas', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updatePersona(personaId: number | string, payload: PromptItemUpdateRequest, locale?: string) {
    const params = new URLSearchParams();
    if (locale) params.set('locale', locale);
    const query = params.toString() ? `?${params.toString()}` : '';
    return this.request<PromptItemRuntimeResponse>(`/api/prompts/personas/${personaId}${query}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deletePersona(personaId: number | string) {
    return this.request<{ success: boolean; active: string; runtime_applied: boolean }>(
      `/api/prompts/personas/${personaId}`,
      { method: 'DELETE' },
    );
  }

  async activatePersona(personaId: number | string) {
    return this.request<PersonaActivationResponse>(`/api/prompts/personas/${personaId}/activate`, {
      method: 'POST',
    });
  }

  async getPromptOptions() {
    return this.request<PromptOptionsResponse>('/api/prompts/options');
  }

  async getActivePrompt() {
    return this.request<PromptConfig>('/api/prompts/active');
  }
  
  async updatePromptConfig(
    coreMode?: string,
    persona?: string,
    context?: string,
    phase?: string
  ) {
    const params = new URLSearchParams();
    if (coreMode) params.set('core_mode', coreMode);
    if (persona) params.set('persona', persona);
    if (context) params.set('context', context);
    if (phase) params.set('phase', phase);

    const query = params.toString() ? `?${params.toString()}` : '';
    return this.request<PromptUpdateResponse>(
      `/api/prompts/update${query}`,
      { method: 'POST' }
    );
  }

  async previewPrompt(
    coreMode?: string,
    persona?: string,
    context?: string,
    phase?: string
  ) {
    const params = new URLSearchParams();
    if (coreMode) params.set('core_mode', coreMode);
    if (persona) params.set('persona', persona);
    if (context) params.set('context', context);
    if (phase) params.set('phase', phase);

    const query = params.toString() ? `?${params.toString()}` : '';
    return this.request<PromptPreviewResponse>(
      `/api/prompts/preview${query}`,
      { method: 'POST' }
    );
  }

  async listPromptModes(includeArchived = false) {
    return this.request<PromptItemListResponse>(`/api/prompts/modes?include_archived=${includeArchived}`);
  }

  async createPromptMode(payload: PromptItemCreateRequest) {
    return this.request<PromptItemResponse>('/api/prompts/modes', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updatePromptMode(modeId: number | string, payload: PromptItemUpdateRequest) {
    return this.request<PromptItemResponse>(`/api/prompts/modes/${modeId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deletePromptMode(modeId: number | string) {
    return this.request<{ success: boolean }>(`/api/prompts/modes/${modeId}`, {
      method: 'DELETE',
    });
  }

  async listSpeakingStyles(includeArchived = false) {
    return this.request<PromptItemListResponse>(`/api/prompts/speaking-styles?include_archived=${includeArchived}`);
  }

  async createSpeakingStyle(payload: PromptItemCreateRequest) {
    return this.request<PromptItemResponse>('/api/prompts/speaking-styles', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateSpeakingStyle(id: number | string, payload: PromptItemUpdateRequest) {
    return this.request<PromptItemResponse>(`/api/prompts/speaking-styles/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deleteSpeakingStyle(id: number | string) {
    return this.request<{ success: boolean }>(`/api/prompts/speaking-styles/${id}`, {
      method: 'DELETE',
    });
  }

  async listEventSettings(includeArchived = false) {
    return this.request<PromptItemListResponse>(`/api/prompts/event-settings?include_archived=${includeArchived}`);
  }

  async createEventSetting(payload: PromptItemCreateRequest) {
    return this.request<PromptItemResponse>('/api/prompts/event-settings', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateEventSetting(id: number | string, payload: PromptItemUpdateRequest) {
    return this.request<PromptItemResponse>(`/api/prompts/event-settings/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deleteEventSetting(id: number | string) {
    return this.request<{ success: boolean }>(`/api/prompts/event-settings/${id}`, {
      method: 'DELETE',
    });
  }

  async listEventMoments(eventSettingId: number | string, includeArchived = false) {
    return this.request<EventMomentListResponse>(
      `/api/prompts/event-settings/${eventSettingId}/event-moments?include_archived=${includeArchived}`
    );
  }

  async createEventMoment(eventSettingId: number | string, payload: EventMomentCreateRequest) {
    return this.request<EventMomentResponse>(`/api/prompts/event-settings/${eventSettingId}/event-moments`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateEventMoment(id: number | string, payload: EventMomentUpdateRequest) {
    return this.request<EventMomentResponse>(`/api/prompts/event-moments/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  } 

  async deleteEventMoment(id: number | string) {
    return this.request<{ success: boolean }>(`/api/prompts/event-moments/${id}`, {
      method: 'DELETE',
    });
  }


  // Service logs
  async getServiceLogs(serviceName: string, lines: number = 100) {
    return this.request<{
      service: string;
      log_path: string;
      lines: number;
      content: string;
    }>(`/api/services/${serviceName}/logs?lines=${lines}`);
  }

  // System
  async getSystemStatus() {
    return this.request<SystemStatus>('/api/status');
  }

  async healthCheck() {
    return this.request<{ status: string }>('/api/health');
  }

  // Knowledge base 
  async getKnowledge() {
      return this.request<KnowledgeState>('/api/knowledge');
  }

  async deleteDocument(docId: string) {
    return this.request<{ success: boolean; message: string }>(
      `/api/knowledge/${docId}`,
      { method: 'DELETE' }
    );
  }

  getKnowledgeDocumentFileUrl(docId: string) {
    return `${API_BASE}/api/knowledge/${encodeURIComponent(docId)}/file`;
  }

  async indexAllDocuments() {
    return this.request<{ indexed_documents: number; total_chunks: number; timestamp: string }>(
      '/api/knowledge/index',
      { method: 'POST' }
    );
  }

  async searchKnowledge(query: string, limit: number = 5): Promise<KnowledgeSearchResult[]> {
    const data = await this.request<{ results: KnowledgeSearchResult[] }>(
      `/api/knowledge/search?query=${encodeURIComponent(query)}&limit=${limit}`
    );
    return data.results;
  }

  async getKnowledgeIndexes() {
    return this.request<KnowledgeIndexState>('/api/knowledge/indexes');
  }

  async getKnowledgeIndexOptions() {
    return this.request<KnowledgeIndexOptionsResponse>('/api/knowledge/options');
  }

  async getActiveKnowledgeIndex() {
    return this.request<KnowledgeIndexItem>('/api/knowledge/active');
  }

  async createKnowledgeIndex(payload: KnowledgeIndexCreateRequest) {
    return this.request<KnowledgeIndexItem>('/api/knowledge/indexes/create', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async deleteKnowledgeIndex(indexSlug: string) {
    return this.request<KnowledgeIndexDeleteResponse>(
      `/api/knowledge/indexes/${encodeURIComponent(indexSlug)}`,
      { method: 'DELETE' }
    );
  }

  async updateKnowledgeQueryIndexes(slugs: string[]) {
    return this.request<KnowledgeIndexState>('/api/knowledge/query-indexes', {
      method: 'POST',
      body: JSON.stringify({ slugs }),
    });
  }

  async updateKnowledgeIndex(indexSlug: string) {
    const query = `?index_slug=${encodeURIComponent(indexSlug)}`;
    return this.request<KnowledgeIndexUpdateResponse>(`/api/knowledge/update${query}`, {
      method: 'POST',
    });
  }

  async uploadDocument(file: File) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE}/api/knowledge/upload`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    return response.json();
  }
 


  // Get conference program
  async getConferenceProgram() {
    return this.request<ConferenceProgram>('/api/conference/program');
  }

  async listConferenceEvents() {
    return this.request<ConferenceEventListResponse>('/api/conference/events');
  }

  async getActiveConferenceEvent() {
    return this.request<ConferenceActiveEventResponse>('/api/conference/active-event');
  }

  async setActiveConferenceEvent(eventKey: string) {
    return this.request<ConferenceActiveEventResponse>(
      `/api/conference/active-event?event_key=${encodeURIComponent(eventKey)}`,
      { method: 'POST' }
    );
  }

  async getConferenceEventEditor(eventKey: string) {
    return this.request<ConferenceEventDocument>(
      `/api/conference/events/${encodeURIComponent(eventKey)}/editor`
    );
  }

  async createConferenceEvent(payload: ConferenceEventDocument) {
    return this.request<ConferenceEventDocument>('/api/conference/events', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateConferenceEvent(eventKey: string, payload: ConferenceEventDocument) {
    return this.request<ConferenceEventDocument>(
      `/api/conference/events/${encodeURIComponent(eventKey)}`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      }
    );
  }

  async deleteConferenceEvent(eventKey: string) {
    return this.request<ConferenceEventDeleteResponse>(
      `/api/conference/events/${encodeURIComponent(eventKey)}`,
      { method: 'DELETE' }
    );
  }


 // Quiz options
  async getQuizOptions() {
    return this.request<QuizOptionsResponse>('/api/quizzes/options');
  }

  async getActiveQuiz() {
    return this.request<ActiveQuizResponse>('/api/quizzes/active');
  }

  async updateQuizConfig(quizSlug: string) {
    const query = `?quiz_slug=${encodeURIComponent(quizSlug)}`;
    return this.request<QuizUpdateResponse>(`/api/quizzes/update${query}`, {
      method: 'POST',
    });
  }

  async listQuizzes() {
    return this.request<QuizListResponse>('/api/quizzes');
  }

  async downloadQuizCatalogExport() {
    const response = await fetch(`${API_BASE}/api/quizzes/export`);
    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filenameMatch = disposition.match(/filename="([^"]+)"/);
    return {
      blob: await response.blob(),
      filename: filenameMatch?.[1] ?? 'quiz_catalog.xlsx',
    };
  }

  async createQuiz(payload: QuizCreateRequest) {
    return this.request<QuizItemResponse>('/api/quizzes', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateQuiz(quizId: number | string, payload: QuizUpdateRequest) {
    return this.request<QuizItemResponse>(`/api/quizzes/${quizId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deleteQuiz(quizId: number | string) {
    return this.request<{ success: boolean }>(`/api/quizzes/${quizId}`, {
      method: 'DELETE',
    });
  }

  async listQuizQuestions(quizId: number | string) {
    return this.request<QuizQuestionListResponse>(`/api/quizzes/${quizId}/questions`);
  }

  async createQuizQuestion(quizId: number | string, payload: QuizQuestionCreateRequest) {
    return this.request<QuizQuestionResponse>(`/api/quizzes/${quizId}/questions`, {
      method: 'POST',
      body: JSON.stringify(payload),
  });

  } 

  async updateQuizQuestion(questionId: number | string, payload: QuizQuestionUpdateRequest) {
    return this.request<QuizQuestionResponse>(`/api/quizzes/questions/${questionId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deleteQuizQuestion(questionId: number | string) {
    return this.request<{ success: boolean }>(`/api/quizzes/questions/${questionId}`, {
      method: 'DELETE',
    });
  }

  async getRuntimeQuiz(slug: string) {
    return this.request<RuntimeQuizResponse>(`/api/quizzes/runtime/${encodeURIComponent(slug)}`);
  }

  // Survey management 
  async getSurveyOptions() {
    return this.request<SurveyOptionsResponse>('/api/surveys/options');
  }

  async getActiveSurvey() {
    return this.request<ActiveSurveyResponse>('/api/surveys/active');
  }

  async updateSurveyConfig(surveySlug: string) {
    const query = `?survey_slug=${encodeURIComponent(surveySlug)}`;
    return this.request<SurveyUpdateResponse>(`/api/surveys/update${query}`, {
      method: 'POST',
    });
  }

  async listSurveys() {
    return this.request<SurveyListResponse>('/api/surveys');
  }

  async downloadSurveyCatalogExport() {
    const response = await fetch(`${API_BASE}/api/surveys/export/catalog`);
    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filenameMatch = disposition.match(/filename="([^"]+)"/);
    return {
      blob: await response.blob(),
      filename: filenameMatch?.[1] ?? 'survey_catalog.xlsx',
    };
  }

  async createSurvey(payload: SurveyCreateRequest) {
    return this.request<SurveyItemResponse>('/api/surveys', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateSurvey(surveyId: number | string, payload: SurveyUpdateRequest) {
    return this.request<SurveyItemResponse>(`/api/surveys/${surveyId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deleteSurvey(surveyId: number | string) {
    return this.request<{ success: boolean }>(`/api/surveys/${surveyId}`, {
      method: 'DELETE',
    });
  }

  async listSurveyQuestions(surveyId: number | string) {
    return this.request<SurveyQuestionListResponse>(`/api/surveys/${surveyId}/questions`);
  }

  async createSurveyQuestion(surveyId: number | string, payload: SurveyQuestionCreateRequest) {
    return this.request<SurveyQuestionResponse>(`/api/surveys/${surveyId}/questions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateSurveyQuestion(questionId: number | string, payload: SurveyQuestionUpdateRequest) {
    return this.request<SurveyQuestionResponse>(`/api/surveys/questions/${questionId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deleteSurveyQuestion(questionId: number | string) {
    return this.request<{ success: boolean }>(`/api/surveys/questions/${questionId}`, {
      method: 'DELETE',
    });
  }

  async getRuntimeSurvey(slug: string) {
    return this.request<RuntimeSurveyResponse>(`/api/surveys/runtime/${encodeURIComponent(slug)}`);
  }

  async getSurveyExportStatus() {
    return this.request<SurveyExportStatus>('/api/surveys/export/status');
  }

  async downloadSurveyExport(format: SurveyExportFormat, surveySlug?: string) {
    const params = new URLSearchParams({ format });
    if (surveySlug) {
      params.set('survey_slug', surveySlug);
    }

    const response = await fetch(`${API_BASE}/api/surveys/export/latest?${params.toString()}`);
    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filenameMatch = disposition.match(/filename="([^"]+)"/);
    return {
      blob: await response.blob(),
      filename: filenameMatch?.[1] ?? `survey_results.${format === 'word' ? 'doc' : format === 'xlsx' ? 'xlsx' : 'pdf'}`,
    };
  }

  async archiveLatestSurvey() {
    return this.request<SurveyRunResponse>('/api/surveys/archive/latest', {
      method: 'POST',
    });
  }

  async deleteLatestSurveyCache() {
    return this.request<{ success: boolean; deleted: boolean }>('/api/surveys/cache/latest', {
      method: 'DELETE',
    });
  }

  async listSurveyRuns(surveySlug?: string) {
    const query = surveySlug ? `?survey_slug=${encodeURIComponent(surveySlug)}` : '';
    return this.request<SurveyRunListResponse>(`/api/surveys/runs${query}`);
  }

  async downloadSurveyRunExport(runId: number, format: SurveyExportFormat) {
    const response = await fetch(`${API_BASE}/api/surveys/runs/${runId}/export?format=${encodeURIComponent(format)}`);
    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filenameMatch = disposition.match(/filename="([^"]+)"/);
    return {
      blob: await response.blob(),
      filename: filenameMatch?.[1] ?? `survey_results.${format === 'word' ? 'doc' : format === 'xlsx' ? 'xlsx' : 'pdf'}`,
    };
  }

  async downloadSurveyRunsExport(format: SurveyExportFormat) {
    const response = await fetch(`${API_BASE}/api/surveys/runs/export?format=${encodeURIComponent(format)}`);
    if (!response.ok) {
      const error: APIError = await response.json().catch(() => ({
        detail: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw new Error(error.detail);
    }

    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filenameMatch = disposition.match(/filename="([^"]+)"/);
    return {
      blob: await response.blob(),
      filename: filenameMatch?.[1] ?? `survey_results_archive.${format === 'word' ? 'doc' : format === 'xlsx' ? 'xlsx' : 'pdf'}`,
    };
  }

  async deleteSurveyRun(runId: number) {
    return this.request<{ success: boolean }>(`/api/surveys/runs/${runId}`, {
      method: 'DELETE',
    });
  }

  async deleteSurveyRuns() {
    return this.request<{ success: boolean; deleted: number }>('/api/surveys/runs', {
      method: 'DELETE',
    });
  }

  // Network Manager
  async listNetworkConnections() {
    return this.request<NetworkConnectionListResponse>('/api/network/connections');
  }

  async addNetworkConnection(payload: NetworkConnectionAddRequest) {
    return this.request<NetworkConnectionActionResponse>('/api/network/connections', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async networkConnectionUp(name: string) {
    return this.request<NetworkConnectionActionResponse>(
      `/api/network/connections/${encodeURIComponent(name)}/up`,
      { method: 'POST' },
    );
  }

  async networkConnectionDown(name: string) {
    return this.request<NetworkConnectionActionResponse>(
      `/api/network/connections/${encodeURIComponent(name)}/down`,
      { method: 'POST' },
    );
  }

  async deleteNetworkConnection(name: string) {
    return this.request<NetworkConnectionActionResponse>(
      `/api/network/connections/${encodeURIComponent(name)}`,
      { method: 'DELETE' },
    );
  }

  async scanWifiNetworks(rescan = false) {
    return this.request<WifiScanResponse>(
      `/api/network/scan?rescan=${rescan}`,
    );
  }

}

export const api = new APIClient();
