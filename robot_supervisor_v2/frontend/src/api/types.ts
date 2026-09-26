// Service types
export type ServiceState = 'stopped' | 'starting' | 'running' | 'stopping' | 'failed';

export interface Service {
  name: string;
  state: ServiceState;
  display_name: string;
  pid?: number;
  uptime_seconds?: number;
  current_mode?: string | null;
  available_modes?: string[];
}

// Conversation types
export type ConversationState = 'idle' | 'dispatching' | 'engaged' | 'wrapping' | 'error';

export interface ConversationStatus {
  state: ConversationState;
  room?: string;
  job_id?: string;
  uptime_seconds?: number;
  dispatch_time?: number;
  last_error?: string;
}

export interface VisionStatus {
  enabled: boolean;
  active: boolean;
  features?: {
    dispatch?: {
      enabled?: boolean;
      active: boolean;
      paused?: boolean;
    };
    card_capture?: VisionCardCaptureState | null;
  };
  service: {
    service_name: string;
    configured: boolean;
    running: boolean;
    state: ServiceState | 'unregistered';
    pid?: number | null;
  };
  enabled_override?: boolean | null;
  person_present: boolean;
  track_id?: string | null;
  last_detected_at?: number | null;
  last_left_at?: number | null;
  card_capture_dispatch_paused?: boolean;
  card_capture_person_left_hold?: boolean;
  conversation: ConversationStatus;
}

export interface PeopleFaceIdentity {
  face_id: string;
  name: string;
  display_name: string;
  canonical_name: string;
  aliases: string[];
  notes: string;
  match_images: 'both' | 'close' | 'far';
  created_at: number;
  has_close_embedding: boolean;
  has_far_embedding: boolean;
  has_close_image: boolean;
  has_far_image: boolean;
}

export interface PeopleFacesResponse {
  store_path: string;
  items: PeopleFaceIdentity[];
}

export type VisionCardCaptureStateName =
  | 'idle'
  | 'pending'
  | 'running'
  | 'captured'
  | 'failed'
  | 'cancelled'
  | 'expired';

export interface VisionCardCaptureQuality {
  score?: number;
  blur?: number;
  brightness?: number;
  glare_ratio?: number;
  area_ratio?: number;
  aspect_ratio?: number;
  rectangularity?: number;
  center_score?: number;
  stable_frames?: number;
}

export interface VisionCardCaptureMetadata {
  processor?: string;
  image_mime_type?: string;
  image_jpeg_base64?: string;
  crop_width?: number;
  crop_height?: number;
  request_id?: string;
  target_type?: string | null;
  source?: string | null;
  started_at?: number | null;
  completed_at?: number | null;
  frame_count?: number;
  quality?: VisionCardCaptureQuality;
  [key: string]: unknown;
}

export interface VisionCardCaptureResult {
  status: VisionCardCaptureStateName | string;
  timestamp?: number | null;
  metadata?: VisionCardCaptureMetadata;
}

export interface VisionCardCaptureState {
  state: VisionCardCaptureStateName | string;
  active: boolean;
  enabled?: boolean;
  request_id?: string | null;
  target_type?: string | null;
  source?: string | null;
  requested_at?: number | null;
  expires_at?: number | null;
  completed_at?: number | null;
  result?: VisionCardCaptureResult | null;
  metadata?: VisionCardCaptureMetadata;
}

// Transcript types
export type ConversationRole = 'user' | 'agent' | 'system' | 'unknown';

export interface TranscriptEntry {
  id: string;
  segment_id: string;
  participant_identity?: string;
  role: ConversationRole;
  text: string;
  final: boolean;
  updated_at: number;
  session_id: number;
}

export interface TranscriptState {
  connected: boolean;
  enabled: boolean;
  entries: TranscriptEntry[];
}

export interface AgentCommandStep {
  text?: string;
  gesture?: string;
  pause_after_ms?: number;
  force_gesture?: boolean | null;
}

export interface AgentCommandRequest {
  text?: string;
  gesture?: string;
  force_gesture?: boolean;
  steps?: AgentCommandStep[];
  room?: string;
  topic?: string;
  plain_text?: boolean;
}

export interface AgentCommandResponse {
  status: string;
  room: string;
  topic: string;
  mime_type: string;
  payload_size: number;
  plain_text: boolean;
  text: string;
  gesture?: string;
}

// Agent types
export interface Agent {
  name: string;
  module: string;
  path: string;
}

// Device types
export interface AudioDevice {
  index: string | number;
  name: string;
  max_input_channels?: number;
  max_output_channels?: number;
  channels?: number;
  sample_rate?: number;
  hostapi?: string;
  is_default?: boolean;
  connected?: boolean;
  always_present?: boolean;
  forced?: boolean;
  virtual?: boolean;
  alsa_device?: string;
  alsa_card_id?: string;
  alsa_device_index?: number;
  card_name?: string;
  device_name?: string;
  id_path?: string;
  id_path_tag?: string;
  detected_name?: string;
  original_index?: string | number;
}

export interface AudioBridgeStatus {
  running: boolean;
  muted: boolean;
  input_mic_gain_db?: number;
  input_mic_gain?: number;
  input_mic_gain_db_min?: number;
  input_mic_gain_db_max?: number;
  output_speaker_gain_db?: number;
  output_speaker_gain_db_min?: number;
  output_speaker_gain_db_max?: number;
  control_port?: number;
  control_available?: boolean;
  active_remote_participant_id?: string | null;
  active_remote_participant_identity?: string | null;
  error?: string;
}

export interface LiveKitMonitorToken {
  token: string;
  url: string;
  room: string;
  identity: string;
  bridge_identity: string;
}

export interface CameraBridgeStreamInfo {
  service_name: string;
  display_name: string;
  running: boolean;
  room: string;
  identity: string;
  track_name: string;
  topic: string;
}

export interface CameraBridgeDemand {
  service_name: string;
  session_id?: string | null;
  active: boolean;
  monitor_sessions: number;
}

export interface CameraDevice {
  index: number;
  path: string;
  name: string;
  is_default?: boolean;
  source?: 'opencv' | 'ros2' | string | null;
  backend?: string | null;
  device?: string | null;
  width?: number | null;
  height?: number | null;
  resolution?: string | null;
  has_valid_resolution?: boolean;
}

export type GestureSafetyPool = 'safe_only' | 'restricted' | 'unrestricted';

export interface GestureCatalogResponse {
  gestures: string[];
  all_gestures: string[];
  active_pool: GestureSafetyPool;
  available_pools: GestureSafetyPool[];
  service_state: string;
  service_running: boolean;
}

export interface GestureBridgeConfigResponse {
  safety_pool: GestureSafetyPool;
  available_pools: GestureSafetyPool[];
  service_state: string;
  service_running: boolean;
}

export interface NetworkStatus {
  connected: boolean;
  ssid?: string | null;
  interface?: string | null;
  ip?: string | null;
  error?: string | null;
}

// Network manager types
export interface NetworkConnectionItem {
  name: string;
  uuid: string;
  type: string;
  device: string | null;
  managed: boolean;   // true = added via the supervisor (can be deleted)
}

export interface NetworkConnectionListResponse {
  connections: NetworkConnectionItem[];
  managed_names: string[];
}

export interface NetworkConnectionAddRequest {
  name: string;
  ssid: string;
  password?: string;
}

export interface NetworkConnectionActionResponse {
  name: string;
  status: string;
  stdout?: string;
}

export interface WifiScanResult {
  ssid: string;
  signal: number;   // 0–100
  security: string; // e.g. "WPA2" or "open"
  in_use: boolean;
}

export interface WifiScanResponse {
  networks: WifiScanResult[];
  rescanned: boolean;
}

export interface CommandPreset {
  id: string;
  label: string;
  description?: string;
  cwd?: string | null;
  commands: string[];
  timeout_seconds: number;
  requires_confirmation: boolean;
  detached: boolean;
}

export interface CommandPresetStepResult {
  command: string;
  exit_code?: number | null;
  timed_out: boolean;
  duration_seconds: number;
  stdout: string;
  stderr: string;
}

export interface CommandPresetRunResponse {
  preset_id: string;
  status: 'success' | 'failed' | 'timeout' | 'accepted';
  exit_code?: number | null;
  duration_seconds: number;
  stdout: string;
  stderr: string;
  steps: CommandPresetStepResult[];
}

export interface VideoRecordingConfig {
  device: string;
  resolution: string;
  framerate: number;
  output_dir: string;
  record_overlay_enabled: boolean;
  cv_debug_enabled: boolean;
  cv_debug_mode: VideoRecordingCvDebugMode;
  id_debug_enabled: boolean;
  id_debug_overlay_enabled: boolean;
  id_debug_record_captures_enabled: boolean;
  id_debug_mode: VideoRecordingIdDebugMode;
  id_debug_capture_image_mode: VideoRecordingIdDebugCaptureImageMode;
}

export interface VideoRecordingFile {
  id: string;
  filename: string;
  type: 'image' | 'video';
  size_bytes: number;
  modified_at: number;
  metadata_id?: string | null;
  recording_type?: 'standard' | 'id_scan' | 'vision_dispatch' | string;
  cv_debug_mode?: VideoRecordingCvDebugMode | null;
  content_variant?: 'normal' | 'overlay' | string;
  image_mode?: VideoRecordingIdDebugCaptureImageMode | string;
}

export interface VideoRecordingState {
  active: boolean;
  started_at?: number | null;
  filename?: string | null;
  recording_type?: 'standard' | 'id_scan' | 'vision_dispatch' | string | null;
  content_variant?: 'normal' | 'overlay' | string | null;
  error?: string | null;
}

export interface VideoRecordingEvent {
  id: string;
  timestamp: number;
  type: string;
  level: 'info' | 'success' | 'warning' | 'error';
  title: string;
  message?: string | null;
  file?: VideoRecordingFile | null;
  details?: Record<string, unknown>;
}

export type VideoRecordingIdDebugMode = 'capture' | 'portrait';
export type VideoRecordingIdDebugCaptureImageMode = 'crop' | 'whole';
export type VideoRecordingCvDebugMode = 'id_capture' | 'portrait_edges' | 'vision_dispatch';

export interface VideoRecordingIdDebugStatus {
  enabled: boolean;
  overlay_enabled: boolean;
  record_captures_enabled: boolean;
  mode: VideoRecordingIdDebugMode;
  capture_image_mode: VideoRecordingIdDebugCaptureImageMode;
  available: boolean;
  request_id?: string | null;
  captured_count: number;
  capture_completed?: boolean;
  card_capture_fps?: number;
  last_status?: string | null;
  last_capture?: VideoRecordingFile | null;
  error?: string | null;
}

export interface VideoRecordingVisionDispatchDebugStatus {
  enabled: boolean;
  available: boolean;
  locked: boolean;
  track_id?: string | null;
  event_count: number;
  last_event_at?: number | null;
  detection_fps?: number;
  last_status?: string | null;
  last_capture?: VideoRecordingFile | null;
  record_captures_enabled?: boolean;
  error?: string | null;
}

export interface VideoRecordingCvDebugStatus {
  enabled: boolean;
  mode: VideoRecordingCvDebugMode;
  available: boolean;
  record_captures_enabled: boolean;
  last_status?: string | null;
  error?: string | null;
  id_capture: VideoRecordingIdDebugStatus;
  vision_dispatch: VideoRecordingVisionDispatchDebugStatus;
  request_id?: string | null;
  captured_count: number;
  capture_completed?: boolean;
  card_capture_fps?: number;
  last_capture?: VideoRecordingFile | null;
}

export interface VideoRecordingStatus {
  service: Service;
  running: boolean;
  recording: VideoRecordingState;
  device: string;
  resolution: string;
  framerate: number;
  record_overlay_enabled: boolean;
  output_dir: string;
  actual_width?: number | null;
  actual_height?: number | null;
  actual_fps?: number | null;
  last_frame_at?: number | null;
  cv_debug: VideoRecordingCvDebugStatus;
  id_debug: VideoRecordingIdDebugStatus;
  events: VideoRecordingEvent[];
  stopped_runtime?: string[];
}

export type RuntimeEnvironmentName = 'DEV' | 'UAT' | 'PROD' | 'CT';

export interface RuntimeEnvironmentStatus {
  environment: RuntimeEnvironmentName;
  available_environments: RuntimeEnvironmentName[];
  env_file: string;
  env_file_exists: boolean;
  valid: boolean;
  missing_required: string[];
  restart_required: boolean;
  restarted_services?: string[];
  azure: {
    openai_host?: string | null;
    openai_key?: string | null;
    api_version?: string | null;
    completion_model?: string | null;
    openai_deployment?: string | null;
    embedding_model?: string | null;
    search_host?: string | null;
    search_key?: string | null;
    search_index?: string | null;
    vector_field?: string | null;
  };
  truebar: {
    username?: string | null;
    password?: string | null;
    client_id?: string | null;
    auth_host?: string | null;
    api_host?: string | null;
    stt_ws_host?: string | null;
    tts_ws_host?: string | null;
    asr_tag?: string | null;
    tts_tag?: string | null;
  };
}

export interface SpeechVoiceConfig {
  provider: string;
  model: string;
  voice_id: string;
  label: string;
  language: string;
}

export type SpeechBackgroundAudioSourceType = 'builtin' | 'upload';

export interface SpeechBackgroundAudioConfig {
  enabled: boolean;
  source_type: SpeechBackgroundAudioSourceType;
  source: string;
  volume: number;
}

export interface SpeechConfig {
  active_voice: SpeechVoiceConfig;
  initial_greeting: string;
  goodbye_text: string;
  voices: SpeechVoiceConfig[];
  custom_transformations: Record<string, string>;
  english_transformations: Record<string, string>;
  background_audio: SpeechBackgroundAudioConfig;
}

export interface SpeechConfigResponse extends SpeechConfig {
  effective_transformations: Record<string, string>;
  english_locale_active: boolean;
  state_file: string;
  service_state: ServiceState | 'unregistered';
  service_running: boolean;
  restarted_services: string[];
}

export type SpeechVoiceOptionsSource = 'configured' | 'configured_fallback';

export interface SpeechVoiceOptionsResponse {
  voices: SpeechVoiceConfig[];
  source: SpeechVoiceOptionsSource;
  error: string | null;
}

export interface SpeechBackgroundAudioOption {
  label: string;
  source: string;
}

export interface UploadedSpeechAudioOption extends SpeechBackgroundAudioOption {
  filename: string;
  size_bytes: number;
}

export interface SpeechBackgroundAudioOptionsResponse {
  built_in: SpeechBackgroundAudioOption[];
  uploads: UploadedSpeechAudioOption[];
  accepted_extensions: string[];
}

export interface SpeechBackgroundAudioUploadResponse {
  file: UploadedSpeechAudioOption;
  options: SpeechBackgroundAudioOptionsResponse;
}

export type RuntimeStateSource = 'env' | 'operator';

export interface RagRuntimeStatus {
  enabled: boolean;
  default_enabled: boolean;
  available: boolean;
  source: RuntimeStateSource;
  updated_at: number;
}

export interface RuntimeStatus {
  rag: RagRuntimeStatus;
}

export interface RobotIdentity {
  id: string;
  name: string;
  platform: string;
  model: string;
}

// System types
export interface SystemStatus {
  services: Service[];
  conversation: ConversationStatus;
  vision?: VisionStatus | null;
  transcript?: TranscriptState;
  robot_temperature?: RobotTemperatureState | null;
  network?: NetworkStatus | null;
  runtime?: RuntimeStatus;
  robot?: RobotIdentity;
  system: {
    version: string;
    total_services: number;
  };
}

export interface TeleoperationRuntimeStatus {
  service_state: ServiceState;
  ipc_online: boolean;
  heartbeat: Record<string, boolean>;
  last_error?: string | null;
}

export interface RobotMotorTemperature {
  index: number;
  label: string;
  temperature_c: number | null;
  temperature_readings_c?: number[];
  mode?: number | string | null;
  q?: number | null;
  dq?: number | null;
}

export interface RobotTemperatureState {
  configured: boolean;
  service_state: ServiceState;
  topic: string;
  last_update?: number | null;
  message_age_seconds?: number | null;
  stale: boolean;
  last_error?: string | null;
  warn_threshold_c: number;
  critical_threshold_c: number;
  motor_count: number;
  motors: RobotMotorTemperature[];
  max_temperature_c?: number | null;
  hottest_motor?: RobotMotorTemperature | null;
  warning_count: number;
  critical_count: number;
}

// API error type
export interface APIError {
  detail: string;
}

// Configuration types
export interface PromptOptions {
  core_modes: string[];
  personas: string[];
  contexts: string[];
  event_moments_by_context: Record<string, string[]>;
}

export interface PromptConfig {
  core_mode: string;
  persona: string;
  context: string;
  phase: string;
}

export interface PromptOptionsResponse {
  options: PromptOptions;
  active: PromptConfig;
}

export interface PromptUpdateResponse {
  success: boolean;
  active: PromptConfig;
  prompt_preview: string;
}

export interface PromptPreviewResponse {
  prompt: string;
}

export interface PromptItem {
  id: number | string;
  slug: string;
  title: string;
  prompt_text: string;
  initial_greeting: string;
  goodbye_text: string;
  is_archived: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  active?: boolean;
  protected?: boolean;
  role?: 'main' | 'persona' | string;
}

export interface PromptItemRuntimeResponse {
  item: PromptItem;
  runtime_applied?: boolean;
}

export interface PersonaListResponse {
  items: PromptItem[];
  active: string;
}

export interface PersonaActivationResponse {
  success: boolean;
  active: PromptConfig;
  runtime_applied: boolean;
  prompt_preview: string;
}

export interface EventMomentItem extends PromptItem {
  event_setting_id: number | string;
  event_setting_slug?: string;
  order_index: number;
}

export interface PromptItemCreateRequest {
  slug: string;
  title: string;
  prompt_text: string;
  initial_greeting?: string;
  goodbye_text?: string;
}

export interface PromptItemUpdateRequest extends PromptItemCreateRequest {
  is_archived: boolean;
}

export interface EventMomentCreateRequest extends PromptItemCreateRequest {
  order_index: number;
}

export interface EventMomentUpdateRequest extends EventMomentCreateRequest {
  is_archived: boolean;
}

export interface PromptItemListResponse {
  items: PromptItem[];
}

export interface EventMomentListResponse {
  items: EventMomentItem[];
}

export interface PromptItemResponse {
  item: PromptItem;
}

export interface EventMomentResponse {
  item: EventMomentItem;
}

export type PromptManagerSection =
  | 'modes'
  | 'speaking-styles'
  | 'event-settings'
  | 'event-moments';

// Knowledge base types
export type KnowledgeIndexKind = 'managed' | 'external';


export interface KnowledgeIndexItem {
  slug: string;
  title: string;
  description: string;
  kind: KnowledgeIndexKind;
  collection_name: string;
  storage_path?: string | null;
  immutable?: boolean;
}

export interface KnowledgeIndexCreateRequest {
  slug: string;
  title: string;
  description?: string;
  kind?: KnowledgeIndexKind;
  collection_name?: string | null;
  storage_path?: string | null;
}

export interface KnowledgeIndexState {
  active_slug: string;
  query_slugs: string[];
  indexes: KnowledgeIndexItem[];
}

export interface KnowledgeIndexOptionsResponse {
  options: KnowledgeIndexState;
  active: KnowledgeIndexItem;
}

export interface KnowledgeIndexUpdateResponse {
  success: boolean;
  active: KnowledgeIndexItem;
}

export interface KnowledgeIndexDeleteResponse {
  success: boolean;
  deleted: KnowledgeIndexItem;
  active: KnowledgeIndexItem;
  deleted_collection: boolean;
  deleted_storage: boolean;
}

export interface Document {
  id: string;
  filename: string;
  chunks: number;
  uploaded_at: string;
  file_size: number;
}

export interface KnowledgeState {
  documents: Document[];
  total_chunks: number;
  last_indexed: string | null;
}

export interface KnowledgeSearchResult {
  index_slug?: string;
  index_title?: string;
  collection_name?: string;
  document_id: string;
  chunk_id: string;
  text: string;
  score: number;
  start_pos?: number;
  end_pos?: number;
}


// Conference script types
export interface ConferenceScenario {
  id: string;
  label: string;
  summary_text?: string;
  single_action_only?: boolean;
  action_label?: string;
  steps: ConferenceStep[];
}

export interface ConferenceStep {
  text?: string;
  display_text?: string;
  gesture?: string;
  pause_after_ms?: number;
  note?: string;
}

export interface ConferenceEditableSection {
  id: string;
  section: string;
  description?: string;
  scenarios: ConferenceScenario[];
}

export interface ConferenceSection {
  id: string;
  section: string;
  description?: string;
  status: 'ready' | 'missing' | 'empty';
  source_file: string;
  scenarios: ConferenceScenario[];
}

export interface ConferenceEventSummary {
  key: string;
  event_id: string;
  name: string;
  location?: string;
  language?: string;
  robot_name?: string;
  source_dir: string;
}

export interface ConferenceEventListResponse {
  active_event_key?: string;
  events: ConferenceEventSummary[];
}

export interface ConferenceActiveEventResponse {
  active_event_key: string;
  active_event: ConferenceEventSummary;
}

export interface ConferenceProgram {
  event_key: string;
  source_dir: string;
  event: Record<string, string>;
  robot: Record<string, string>;
  scripts: string[];
  sections: ConferenceSection[];
}

export interface ConferenceEventDocument {
  key: string;
  event_id: string;
  name: string;
  location?: string;
  language?: string;
  robot_name?: string;
  robot_role?: string;
  sections: ConferenceEditableSection[];
}

export interface ConferenceEventDeleteResponse {
  deleted_key: string;
  active_event_key?: string;
}


// Quiz management
export interface ActiveQuizResponse {
  quiz_slug?: string;
  quiz_title?: string;
}

export interface QuizOptionItem {
  id: number | string;
  slug: string;
  title: string;
  description: string;
  max_attempts: number;
  question_count: number;
}

export interface ActiveQuizConfig {
  quiz_slug: string;
  quiz_title: string;
}

export interface QuizOptionsResponse {
  options: {
    quizzes: QuizOptionItem[];
  };
  active: ActiveQuizResponse;
}

export interface QuizUpdateResponse {
  success: boolean;
  active: ActiveQuizConfig;
  quiz_preview: {
    quiz_id: string;
    max_attempts: number;
    questions: Array<{
      id: string;
      question: string;
      options: string[];
      correct_answer: string;
      hint: string;
      explanation: string;
    }>;
  };
}


export interface QuizItem {
  id: number | string;
  slug: string;
  title: string;
  description: string;
  max_attempts: number;
  created_at?: string | null;
  updated_at?: string | null;
}


export interface QuizQuestionItem {
  id: number | string;
  quiz_id: number | string;
  quiz_slug?: string;
  question_key: string;
  question: string;
  options: string[];
  correct_answer: string;
  hint: string;
  explanation: string;
  order_index: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface QuizCreateRequest {
  slug: string;
  title: string;
  description: string;
  max_attempts: number;
}

export type QuizUpdateRequest = QuizCreateRequest; 

export interface QuizQuestionCreateRequest {
  question_key: string;
  question: string;
  options: string[];
  correct_answer: string;
  hint: string;
  explanation: string;
  order_index: number;
}

export type QuizQuestionUpdateRequest = QuizQuestionCreateRequest; 

export interface QuizListResponse {
  items: QuizItem[];
}

export interface QuizItemResponse {
  item: QuizItem;
}

export interface QuizQuestionListResponse {
  items: QuizQuestionItem[];
}

export interface QuizQuestionResponse {
  item: QuizQuestionItem;
}

export interface RuntimeQuizQuestion {
  id: string;
  question: string;
  options: string[];
  correct_answer: string;
  hint: string;
  explanation: string;
}

export interface RuntimeQuiz {
  quiz_id: string;
  max_attempts: number;
  questions: RuntimeQuizQuestion[];
}

export interface RuntimeQuizResponse {
  item: RuntimeQuiz;
}

// Survey Management
export type SurveyResponseKind =
  | 'single_choice'
  | 'multiple_choice'
  | 'free_text'
  | 'ranking';

export interface ActiveSurveyResponse {
  survey_slug?: string;
  survey_title?: string;
}

export interface SurveyOptionItem {
  id: number | string;
  slug: string;
  title: string;
  description: string;
  question_count: number;
}

export interface ActiveSurveyConfig {
  survey_slug: string;
  survey_title: string;
}

export interface SurveyOptionsResponse {
  options: {
    surveys: SurveyOptionItem[];
  };
  active: ActiveSurveyResponse;
}

export interface SurveyUpdateResponse {
  success: boolean;
  active: ActiveSurveyConfig;
  survey_preview: {
    survey_id: string;
    title: string;
    description: string;
    questions: Array<{
      id: string;
      question: string;
      response_kind: SurveyResponseKind;
      options: string[];
      allow_skip: boolean;
    }>;
  };
}

export interface SurveyItem {
  id: number | string;
  slug: string;
  title: string;
  description: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SurveyQuestionItem {
  id: number | string;
  survey_id: number | string;
  survey_slug?: string;
  question_key: string;
  question: string;
  response_kind: SurveyResponseKind;
  options: string[];
  allow_skip: boolean;
  order_index: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SurveyCreateRequest {
  slug: string;
  title: string;
  description: string;
}

export type SurveyUpdateRequest = SurveyCreateRequest;

export interface SurveyQuestionCreateRequest {
  question_key: string;
  question: string;
  response_kind: SurveyResponseKind;
  options: string[];
  allow_skip: boolean;
  order_index: number;
}

export type SurveyQuestionUpdateRequest = SurveyQuestionCreateRequest;

export interface SurveyListResponse {
  items: SurveyItem[];
}

export interface SurveyItemResponse {
  item: SurveyItem;
}

export interface SurveyQuestionListResponse {
  items: SurveyQuestionItem[];
}

export interface SurveyQuestionResponse {
  item: SurveyQuestionItem;
}

export interface RuntimeSurveyQuestion {
  id: string;
  question: string;
  response_kind: SurveyResponseKind;
  options: string[];
  allow_skip: boolean;
}

export interface RuntimeSurvey {
  survey_id: string;
  title: string;
  description: string;
  questions: RuntimeSurveyQuestion[];
}

export interface RuntimeSurveyResponse {
  item: RuntimeSurvey;
}

export type SurveyExportFormat = 'word' | 'pdf' | 'xlsx';

export interface SurveyExportStatus {
  available: boolean;
  survey_id?: string;
  phase?: 'idle' | 'waiting_for_response' | 'finished' | string;
  total_questions?: number;
  responses_recorded?: number;
  updated_at?: number;
  reason?: string | null;
  archive?: SurveyRunItem;
}

export interface SurveyRunItem {
  id: number;
  survey_slug: string;
  survey_title: string;
  phase: string;
  responses_recorded: number;
  total_questions: number;
  snapshot_hash: string;
  source_updated_at?: string | null;
  started_at?: string | null;
  created_at?: string | null;
  archived?: boolean;
}

export interface SurveyRunListResponse {
  items: SurveyRunItem[];
}

export interface SurveyRunResponse {
  item: SurveyRunItem;
}
