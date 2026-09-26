import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Camera, CheckCircle2, Clock, Download, FileVideo, Image as ImageIcon, Lock, Pencil, Play, RefreshCw, Square, Terminal, Trash2, Video, Wifi, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { API_BASE } from '@/api/base';
import { api } from '@/api/client';
import { CameraDeviceSelect } from '@/components/services/CameraDeviceSelect';
import type { CommandPreset, CommandPresetRunResponse, NetworkConnectionItem, VideoRecordingCvDebugMode, VideoRecordingFile, VideoRecordingIdDebugCaptureImageMode, VideoRecordingStatus, WifiScanResult } from '@/api/types';

const statusClasses: Record<CommandPresetRunResponse['status'], string> = {
  success: 'text-emerald-600 dark:text-emerald-400',
  failed: 'text-red-600 dark:text-red-400',
  timeout: 'text-amber-600 dark:text-amber-400',
  accepted: 'text-blue-600 dark:text-blue-400',
};

function formatDuration(seconds: number) {
  if (seconds < 1) {
    return `${Math.round(seconds * 1000)}ms`;
  }
  return `${seconds.toFixed(1)}s`;
}

function StatusIcon({ status }: { status: CommandPresetRunResponse['status'] }) {
  if (status === 'success') return <CheckCircle2 className="h-4 w-4" aria-hidden="true" />;
  if (status === 'accepted') return <Clock className="h-4 w-4" aria-hidden="true" />;
  if (status === 'timeout') return <AlertTriangle className="h-4 w-4" aria-hidden="true" />;
  return <XCircle className="h-4 w-4" aria-hidden="true" />;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTimestamp(seconds: number) {
  return new Date(seconds * 1000).toLocaleString();
}

function formatElapsedSeconds(seconds: number) {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainingSeconds = safeSeconds % 60;
  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
  }
  return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
}

function formatEventTime(seconds: number) {
  return new Date(seconds * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function filenameStem(filename: string) {
  const dotIndex = filename.lastIndexOf('.');
  return dotIndex > 0 ? filename.slice(0, dotIndex) : filename;
}

function videoRecordingTypeLabel(type?: string | null) {
  if (type === 'id_scan') return 'ID Scan';
  if (type === 'vision_dispatch') return 'Vision Dispatch';
  return 'Standard';
}

function videoContentVariantLabel(variant?: string | null) {
  if (variant === 'overlay') return 'Overlay';
  return 'Normal';
}

const videoRecordingEventClasses: Record<string, string> = {
  info: 'border-border bg-background text-foreground',
  success: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200',
  warning: 'border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200',
  error: 'border-red-500/30 bg-red-500/10 text-red-800 dark:text-red-200',
};

type VideoRecordingCvDebugTool = 'id_scan' | 'vision_dispatch';
type VideoRecordingIdCvDebugMode = Extract<VideoRecordingCvDebugMode, 'id_capture' | 'portrait_edges'>;

const videoRecordingDebugTools: Record<VideoRecordingCvDebugTool, string> = {
  id_scan: 'ID Scan',
  vision_dispatch: 'Vision Dispatch',
};

const videoRecordingIdDebugModes: Record<VideoRecordingIdCvDebugMode, string> = {
  id_capture: 'Capture',
  portrait_edges: 'Portrait Edges Debug',
};

const videoRecordingIdCaptureImageModes: Record<VideoRecordingIdDebugCaptureImageMode, string> = {
  crop: 'Crop',
  whole: 'Whole Image',
};

// ── Video Recording Service Section ─────────────────────────────────────────

function VideoRecordingServiceSection() {
  const [status, setStatus] = useState<VideoRecordingStatus | null>(null);
  const [files, setFiles] = useState<VideoRecordingFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [streamVersion, setStreamVersion] = useState(0);
  const [nowSeconds, setNowSeconds] = useState(() => Date.now() / 1000);
  const [previewFile, setPreviewFile] = useState<VideoRecordingFile | null>(null);
  const lastSavedDebugCaptureIdRef = useRef<string | null>(null);

  const running = Boolean(status?.running);
  const recording = Boolean(status?.recording.active);
  const recordOverlayEnabled = Boolean(status?.record_overlay_enabled);
  const configuredDevice = status?.device ?? '/dev/video10';
  const recordingStartedAt = status?.recording.started_at ?? null;
  const recordingElapsed =
    recording && recordingStartedAt ? formatElapsedSeconds(nowSeconds - recordingStartedAt) : null;
  const cvDebug = status?.cv_debug;
  const idDebug = cvDebug?.id_capture ?? status?.id_debug;
  const dispatchDebug = cvDebug?.vision_dispatch;
  const cvDebugMode = cvDebug?.mode ?? 'id_capture';
  const idDebugModeActive = cvDebugMode === 'id_capture' || cvDebugMode === 'portrait_edges';
  const cvDebugTool: VideoRecordingCvDebugTool = cvDebugMode === 'vision_dispatch' ? 'vision_dispatch' : 'id_scan';
  const selectedIdDebugMode: VideoRecordingIdCvDebugMode = idDebugModeActive ? cvDebugMode : 'id_capture';
  const idDebugCaptureImageMode = idDebug?.capture_image_mode ?? 'crop';
  const cvDebugRecordCapturesEnabled = Boolean(cvDebug?.record_captures_enabled);
  const captureEvents = status?.events ?? [];
  const debugEvents = captureEvents.filter((event) => (
    cvDebugMode === 'vision_dispatch'
      ? event.type.startsWith('vision_dispatch')
      : event.type.startsWith('id_debug')
  ));
  const cvDebugEnabled = Boolean(cvDebug?.enabled);
  const activeDebugStatusText = cvDebugMode === 'vision_dispatch'
    ? dispatchDebug?.error
      || dispatchDebug?.last_status
      || (dispatchDebug?.available ? 'Waiting for lock events...' : 'Vision dispatch dependencies are unavailable.')
    : idDebug?.error
      || idDebug?.last_status
      || (idDebug?.available ? 'Waiting for frames...' : 'ID debug dependencies are unavailable.');

  const loadFiles = async () => {
    try {
      const response = await api.listVideoRecordingFiles();
      setFiles(response.files ?? []);
    } catch (err) {
      console.error('Failed to load video recording files:', err);
    }
  };

  const loadStatus = async () => {
    try {
      const nextStatus = await api.getVideoRecordingStatus();
      const nextSavedDebugCaptureId = nextStatus.cv_debug?.vision_dispatch?.last_capture?.id
        ?? nextStatus.cv_debug?.last_capture?.id
        ?? nextStatus.id_debug?.last_capture?.id
        ?? null;
      if (
        nextSavedDebugCaptureId &&
        nextSavedDebugCaptureId !== lastSavedDebugCaptureIdRef.current
      ) {
        lastSavedDebugCaptureIdRef.current = nextSavedDebugCaptureId;
        void loadFiles();
      }
      setStatus(nextStatus);
    } catch (err) {
      console.error('Failed to load video recording status:', err);
      setStatus(null);
    }
  };

  const reload = async () => {
    setLoading(true);
    try {
      await Promise.all([loadStatus(), loadFiles()]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    const intervalId = window.setInterval(() => {
      void loadStatus();
    }, 2000);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    if (!recording) {
      return;
    }

    setNowSeconds(Date.now() / 1000);
    const intervalId = window.setInterval(() => {
      setNowSeconds(Date.now() / 1000);
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [recording]);

  const toggleService = async () => {
    setBusy('service');
    try {
      if (running) {
        await api.stopService('video-recording-service');
        toast.success('Video Recording Service stopped');
      } else {
        await api.startService('video-recording-service');
        setStreamVersion((value) => value + 1);
        toast.success('Video Recording Service started');
      }
      await Promise.all([loadStatus(), loadFiles()]);
    } catch (err) {
      toast.error(`Video Recording Service: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const updateCamera = async (device: string) => {
    setBusy('camera');
    try {
      await api.updateVideoRecordingConfig({ device });
      setStreamVersion((value) => value + 1);
      toast.success('Recording camera updated');
      await loadStatus();
    } catch (err) {
      toast.error(`Failed to update recording camera: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const updateRecordOverlay = async (enabled: boolean) => {
    setBusy('record-overlay');
    try {
      const response = await api.updateVideoRecordingConfig({
        recordOverlayEnabled: enabled,
        idDebugCaptureImageMode: enabled ? 'whole' : undefined,
      });
      setStatus(response);
      toast.success(enabled ? 'Record overlay enabled' : 'Record overlay disabled');
    } catch (err) {
      toast.error(`Failed to update record overlay: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const updateIdDebug = async (updates: {
    enabled?: boolean;
    recordCapturesEnabled?: boolean;
    mode?: VideoRecordingCvDebugMode;
    captureImageMode?: VideoRecordingIdDebugCaptureImageMode;
  }) => {
    setBusy('id-debug');
    try {
      const response = await api.updateVideoRecordingConfig({
        cvDebugEnabled: updates.enabled,
        cvDebugMode: updates.mode,
        idDebugRecordCapturesEnabled: updates.recordCapturesEnabled,
        idDebugCaptureImageMode: updates.captureImageMode,
      });
      if (response.stopped_runtime?.length) {
        toast.info(`Stopped runtime services: ${response.stopped_runtime.join(', ')}`);
      }
      setStreamVersion((value) => value + 1);
      setStatus(response);
      toast.success(updates.enabled === false ? 'CV debug disabled' : 'CV debug updated');
    } catch (err) {
      toast.error(`Failed to update CV debug: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const resetIdDebug = async () => {
    setBusy('id-debug-reset');
    try {
      await api.resetVideoRecordingCvDebug();
      await loadStatus();
      toast.success('CV debug reset');
    } catch (err) {
      toast.error(`Failed to reset CV debug: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const captureImage = async () => {
    setBusy('capture');
    try {
      const response = await api.captureVideoRecordingImage();
      toast.success(`Captured ${response.file.filename}`);
      await loadFiles();
    } catch (err) {
      toast.error(`Image capture failed: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const toggleRecording = async () => {
    setBusy('recording');
    try {
      if (recording) {
        const response = await api.stopVideoRecording();
        toast.success(`Saved ${response.file.filename}`);
        await Promise.all([loadStatus(), loadFiles()]);
      } else {
        await api.startVideoRecording();
        toast.success('Recording started');
        await loadStatus();
      }
    } catch (err) {
      toast.error(`Recording failed: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const downloadFile = async (file: VideoRecordingFile) => {
    setBusy(`download:${file.id}`);
    try {
      const { blob, filename } = await api.downloadVideoRecordingFile(file.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(`Download failed: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const downloadAllFiles = async () => {
    setBusy('download-all');
    try {
      const { blob, filename } = await api.downloadAllVideoRecordingFiles();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(`Download all failed: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const renameFile = async (file: VideoRecordingFile) => {
    const nextName = window.prompt('Rename file', filenameStem(file.filename));
    if (!nextName || nextName.trim() === filenameStem(file.filename)) return;

    setBusy(`rename:${file.id}`);
    try {
      await api.renameVideoRecordingFile(file.id, nextName.trim());
      toast.success('File renamed');
      await loadFiles();
    } catch (err) {
      toast.error(`Rename failed: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const deleteFile = async (file: VideoRecordingFile) => {
    if (!window.confirm(`Delete "${file.filename}"?`)) return;

    setBusy(`delete:${file.id}`);
    try {
      await api.deleteVideoRecordingFile(file.id);
      toast.success('File deleted');
      await loadFiles();
    } catch (err) {
      toast.error(`Delete failed: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const deleteAllFiles = async () => {
    if (files.length === 0) return;
    if (!window.confirm(`Delete all ${files.length} captures and recordings?`)) return;

    setBusy('delete-all');
    try {
      const response = await api.deleteAllVideoRecordingFiles();
      toast.success(`Deleted ${response.deleted_count} file(s)`);
      await loadFiles();
    } catch (err) {
      toast.error(`Delete all failed: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const btn =
    'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50';

  const previewUrl = previewFile ? api.getVideoRecordingFilePreviewUrl(previewFile.id) : '';

  return (
    <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Camera className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
          <div>
            <h2 className="text-xl font-semibold">Video Recording Service</h2>
            <p className="text-xs text-muted-foreground">
              {status ? `${configuredDevice} · ${status.resolution} @ ${status.framerate} fps` : 'Not configured'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void reload()}
            disabled={loading || busy !== null}
            className={`${btn} border border-border bg-muted`}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => void toggleService()}
            disabled={busy !== null || !status}
            className={`${btn} ${running ? 'bg-red-600 text-white hover:bg-red-700' : 'bg-emerald-600 text-white hover:bg-emerald-700'}`}
          >
            {running ? <Square className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            {busy === 'service' ? 'Working...' : running ? 'Stop' : 'Start'}
          </button>
        </div>
      </div>

      {!status ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          Video Recording Service is not available from the supervisor backend.
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border/60 bg-muted/40 p-4">
            <span className="text-xs text-muted-foreground">Camera:</span>
            <CameraDeviceSelect
              value={configuredDevice}
              onChange={updateCamera}
              disabled={busy !== null}
            />
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              running
                ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                : 'border border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300'
            }`}>
              {running ? 'running' : 'stopped'}
            </span>
            {recording && (
              <span className="rounded-full border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-700 dark:text-red-300">
                recording
              </span>
            )}
          </div>

          <div className="space-y-3 rounded-xl border border-border/60 bg-muted/40 p-4">
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => void updateIdDebug({ enabled: !cvDebugEnabled })}
                disabled={busy !== null}
                className={`${btn} ${
                  cvDebugEnabled
                    ? 'border border-blue-500/30 bg-blue-500/10 text-blue-800 hover:bg-blue-500/20 dark:text-blue-200'
                    : 'border border-border bg-muted hover:bg-muted/80'
                }`}
              >
                {cvDebugEnabled ? 'Stop CV Debug' : 'Start CV Debug'}
              </button>
              <div className="inline-flex overflow-hidden rounded-full border border-border bg-background p-0.5">
                {(['id_scan', 'vision_dispatch'] as VideoRecordingCvDebugTool[]).map((tool) => (
                  <button
                    key={tool}
                    type="button"
                    onClick={() => void updateIdDebug({
                      enabled: true,
                      mode: tool === 'vision_dispatch' ? 'vision_dispatch' : selectedIdDebugMode,
                    })}
                    disabled={busy !== null}
                    className={`px-3 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                      cvDebugTool === tool
                        ? 'rounded-full bg-blue-600 text-white'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {videoRecordingDebugTools[tool]}
                  </button>
                ))}
              </div>
            </div>

            {cvDebugTool === 'id_scan' && (
              <div className="flex flex-wrap items-center gap-3">
                <div className="inline-flex overflow-hidden rounded-full border border-border bg-background p-0.5">
                  {(['id_capture', 'portrait_edges'] as VideoRecordingIdCvDebugMode[]).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => void updateIdDebug({ enabled: true, mode })}
                      disabled={busy !== null}
                      className={`px-3 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                        cvDebugMode === mode
                          ? 'rounded-full bg-blue-600 text-white'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {videoRecordingIdDebugModes[mode]}
                    </button>
                  ))}
                </div>
                <div className="inline-flex overflow-hidden rounded-full border border-border bg-background p-0.5">
                  {(['crop', 'whole'] as VideoRecordingIdDebugCaptureImageMode[]).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => void updateIdDebug({ captureImageMode: mode })}
                      disabled={busy !== null || (mode === 'crop' && recordOverlayEnabled)}
                      className={`px-3 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                        idDebugCaptureImageMode === mode
                          ? 'rounded-full bg-blue-600 text-white'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {videoRecordingIdCaptureImageModes[mode]}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              {(idDebugModeActive || cvDebugMode === 'vision_dispatch') && (
                <button
                  type="button"
                  onClick={() => void updateIdDebug({ recordCapturesEnabled: !cvDebugRecordCapturesEnabled })}
                  disabled={busy !== null || !cvDebugEnabled}
                  className={`${btn} ${
                    cvDebugRecordCapturesEnabled
                      ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-800 hover:bg-emerald-500/20 dark:text-emerald-200'
                      : 'border border-border bg-muted hover:bg-muted/80'
                  }`}
                >
                  {cvDebugMode === 'vision_dispatch'
                    ? (cvDebugRecordCapturesEnabled ? 'Record Lock Captures On' : 'Record Lock Captures Off')
                    : (cvDebugRecordCapturesEnabled ? 'Record ID Captures On' : 'Record ID Captures Off')}
                </button>
              )}
              <button
                type="button"
                onClick={() => void resetIdDebug()}
                disabled={busy !== null || !cvDebugEnabled}
                className={`${btn} border border-border bg-muted hover:bg-muted/80`}
              >
                <RefreshCw className={`h-3.5 w-3.5 ${busy === 'id-debug-reset' ? 'animate-spin' : ''}`} />
                Reset Debug
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-border/70 bg-muted/50">
              {running ? (
                <>
                  <img
                    src={`${API_BASE}/api/video-recording-service/stream?v=${streamVersion}`}
                    alt="Video recording service stream"
                    className="h-full w-full object-cover"
                  />
                  {recording && (
                    <div className="absolute left-3 top-3 inline-flex items-center gap-2 rounded-full border border-red-500/40 bg-black/65 px-3 py-1 text-xs font-semibold text-white shadow-sm">
                      <span className="h-2.5 w-2.5 rounded-full bg-red-500 shadow-[0_0_0_3px_rgba(239,68,68,0.25)]" />
                      <span>REC</span>
                      {recordingElapsed && <span className="font-mono">{recordingElapsed}</span>}
                    </div>
                  )}
                </>
              ) : (
                <div className="absolute inset-0 flex items-center justify-center px-4">
                  <div className="text-center text-muted-foreground">
                    <Video className="mx-auto mb-2 h-6 w-6" aria-hidden="true" />
                    <p className="text-sm font-medium">Stream off</p>
                    <p className="text-xs">Start the service to preview the camera.</p>
                  </div>
                </div>
              )}
            </div>

            <div className="rounded-xl border border-border/60 bg-muted/40 p-4 space-y-3">
              <button
                type="button"
                onClick={() => void updateRecordOverlay(!recordOverlayEnabled)}
                disabled={busy !== null || recording}
                className={`${btn} w-full justify-center ${
                  recordOverlayEnabled
                    ? 'border border-blue-500/30 bg-blue-500/10 text-blue-800 hover:bg-blue-500/20 dark:text-blue-200'
                    : 'border border-border bg-muted hover:bg-muted/80'
                }`}
              >
                {recordOverlayEnabled ? 'Record Overlay On' : 'Record Overlay Off'}
              </button>
              <button
                type="button"
                onClick={() => void captureImage()}
                disabled={!running || busy !== null}
                className={`${btn} w-full justify-center bg-blue-600 text-white hover:bg-blue-700`}
              >
                <ImageIcon className="h-4 w-4" />
                {busy === 'capture' ? 'Capturing...' : 'Capture Image'}
              </button>
              <button
                type="button"
                onClick={() => void toggleRecording()}
                disabled={!running || busy !== null}
                className={`${btn} w-full justify-center ${recording ? 'bg-red-600 text-white hover:bg-red-700' : 'bg-emerald-600 text-white hover:bg-emerald-700'}`}
              >
                {recording ? <Square className="h-4 w-4" /> : <FileVideo className="h-4 w-4" />}
                {busy === 'recording' ? 'Working...' : recording ? 'Stop Recording' : 'Start Recording'}
              </button>
              {recordingElapsed && (
                <div className="flex items-center justify-between rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                  <span className="inline-flex items-center gap-2 font-medium">
                    <span className="h-2 w-2 rounded-full bg-red-500" />
                    Recording
                  </span>
                  <span className="font-mono">{recordingElapsed}</span>
                </div>
              )}
              {status.recording.error && (
                <div className="rounded-lg border border-red-300/60 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                  {status.recording.error}
                </div>
              )}
              <div className="text-xs text-muted-foreground">
                Files are saved in <span className="font-mono">{status.output_dir}</span>.
              </div>
            </div>
          </div>

          {cvDebugEnabled && (
            <div className="rounded-xl border border-border/60 bg-muted/40 p-4">
              <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    {cvDebugMode === 'vision_dispatch' ? 'Vision dispatch events' : 'CV debug events'}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Local only · raw recordings unchanged
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 text-[11px] font-medium text-blue-800 dark:text-blue-200">
                    {videoRecordingDebugTools[cvDebugTool]}
                  </span>
                  {cvDebugMode === 'vision_dispatch' ? (
                    <>
                      <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                        {dispatchDebug?.locked ? 'Locked' : 'Unlocked'}
                      </span>
                      {dispatchDebug?.track_id && (
                        <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                          track {dispatchDebug.track_id}
                        </span>
                      )}
                      <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                        {dispatchDebug?.event_count ?? 0} dispatch event(s)
                      </span>
                      <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                        {cvDebugRecordCapturesEnabled ? 'Saving lock captures' : 'Not saving lock captures'}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                        {videoRecordingIdDebugModes[selectedIdDebugMode]}
                      </span>
                      <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                        {videoRecordingIdCaptureImageModes[idDebugCaptureImageMode]}
                      </span>
                      <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                        {idDebug?.captured_count ?? 0} detection(s)
                      </span>
                      <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                        {cvDebugRecordCapturesEnabled ? 'Saving detected captures' : 'Not saving detected captures'}
                      </span>
                    </>
                  )}
                  <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground">
                    {debugEvents.length} event(s)
                  </span>
                </div>
              </div>

              <div className="mb-3 rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-xs text-blue-800 dark:text-blue-200">
                <p className="break-words font-mono text-[11px] leading-relaxed">
                  {activeDebugStatusText}
                </p>
                {idDebugModeActive && idDebug?.last_capture && (
                  <button
                    type="button"
                    onClick={() => setPreviewFile(idDebug.last_capture ?? null)}
                    className="mt-1 block max-w-full truncate text-left text-[11px] font-medium underline-offset-2 hover:underline"
                    title={`Preview ${idDebug.last_capture.filename}`}
                  >
                    Last capture: {idDebug.last_capture.filename}
                  </button>
                )}
              </div>

              {debugEvents.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {cvDebugMode === 'vision_dispatch' ? 'No vision dispatch events yet.' : 'No CV debug events yet.'}
                </p>
              ) : (
                <div className="space-y-2">
                  {debugEvents.slice(0, 10).map((event) => (
                    <div
                      key={event.id}
                      className={`rounded-lg border px-3 py-2 text-xs ${videoRecordingEventClasses[event.level] ?? videoRecordingEventClasses.info}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <span className="min-w-0 font-medium">{event.title}</span>
                        <span className="shrink-0 font-mono text-[10px] opacity-70">
                          {formatEventTime(event.timestamp)}
                        </span>
                      </div>
                      {event.message && (
                        <p className="mt-1 break-words text-[11px] opacity-80">{event.message}</p>
                      )}
                      {event.file && (
                        <button
                          type="button"
                          onClick={() => setPreviewFile(event.file ?? null)}
                          className="mt-1 block max-w-full truncate text-left text-[11px] font-medium underline-offset-2 hover:underline"
                          title={`Preview ${event.file.filename}`}
                        >
                          {event.file.filename}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Recordings and captures ({files.length})
              </p>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void downloadAllFiles()}
                  disabled={files.length === 0 || busy !== null}
                  className={`${btn} border border-border bg-muted hover:bg-muted/80`}
                >
                  <Download className="h-3.5 w-3.5" />
                  {busy === 'download-all' ? 'Downloading...' : 'Download All'}
                </button>
                <button
                  type="button"
                  onClick={() => void deleteAllFiles()}
                  disabled={files.length === 0 || busy !== null}
                  className={`${btn} border border-red-500/30 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-300`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {busy === 'delete-all' ? 'Deleting...' : 'Delete All'}
                </button>
              </div>
            </div>
            {files.length === 0 ? (
              <p className="text-sm text-muted-foreground">No local captures or recordings yet.</p>
            ) : (
              <div className="space-y-2">
                {files.map((file) => (
                  <div
                    key={file.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/60 bg-muted/40 px-4 py-2.5"
                  >
                    <button
                      type="button"
                      onClick={() => setPreviewFile(file)}
                      className="relative h-14 w-20 shrink-0 overflow-hidden rounded-lg border border-border/70 bg-background text-muted-foreground transition hover:border-blue-500/60"
                      title={`Preview ${file.filename}`}
                    >
                      {file.type === 'image' ? (
                        <img
                          src={api.getVideoRecordingFilePreviewUrl(file.id)}
                          alt=""
                          loading="lazy"
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center bg-muted/70">
                          <FileVideo className="h-6 w-6" aria-hidden="true" />
                        </div>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => setPreviewFile(file)}
                      className="min-w-0 flex-1 text-left"
                      title={`Preview ${file.filename}`}
                    >
                      <div className="flex items-center gap-2">
                        {file.type === 'video' ? <FileVideo className="h-4 w-4 text-muted-foreground" /> : <ImageIcon className="h-4 w-4 text-muted-foreground" />}
                        <span className="truncate text-sm font-medium" title={file.filename}>{file.filename}</span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {file.type === 'video' && (
                          <span className="mr-1 rounded-full border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium">
                            {videoRecordingTypeLabel(file.recording_type)}
                          </span>
                        )}
                        {file.content_variant && (
                          <span className="mr-1 rounded-full border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium">
                            {videoContentVariantLabel(file.content_variant)}
                          </span>
                        )}
                        {file.image_mode === 'crop' || file.image_mode === 'whole' ? (
                          <span className="mr-1 rounded-full border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium">
                            {videoRecordingIdCaptureImageModes[file.image_mode]}
                          </span>
                        ) : null}
                        {formatBytes(file.size_bytes)} · {formatTimestamp(file.modified_at)}
                      </p>
                    </button>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => void downloadFile(file)}
                        disabled={busy !== null}
                        className={`${btn} border border-border bg-muted hover:bg-muted/80`}
                        title="Download"
                      >
                        <Download className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => void renameFile(file)}
                        disabled={busy !== null}
                        className={`${btn} border border-border bg-muted hover:bg-muted/80`}
                        title="Rename"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => void deleteFile(file)}
                        disabled={busy !== null}
                        className={`${btn} border border-red-500/30 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-300`}
                        title="Delete"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {previewFile && (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
              role="dialog"
              aria-modal="true"
              aria-label={`Preview ${previewFile.filename}`}
              onClick={() => setPreviewFile(null)}
            >
              <div
                className="max-h-[90vh] w-full max-w-5xl overflow-hidden rounded-xl border border-border bg-background shadow-xl"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{previewFile.filename}</p>
                    <p className="text-xs text-muted-foreground">
                      {formatBytes(previewFile.size_bytes)} · {formatTimestamp(previewFile.modified_at)}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setPreviewFile(null)}
                    className={`${btn} border border-border bg-muted hover:bg-muted/80`}
                  >
                    <XCircle className="h-3.5 w-3.5" />
                    Close
                  </button>
                </div>
                <div className="flex max-h-[calc(90vh-74px)] items-center justify-center bg-black">
                  {previewFile.type === 'image' ? (
                    <img
                      src={previewUrl}
                      alt={previewFile.filename}
                      className="max-h-[calc(90vh-74px)] max-w-full object-contain"
                    />
                  ) : (
                    <video
                      src={previewUrl}
                      controls
                      autoPlay
                      className="max-h-[calc(90vh-74px)] max-w-full"
                    />
                  )}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// ── Network Manager Section ──────────────────────────────────────────────────

/** 4-bar signal strength indicator */
function SignalBars({ signal }: { signal: number }) {
  const bars = Math.ceil((signal / 100) * 4);
  const color = signal >= 70 ? 'bg-emerald-500' : signal >= 40 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <span className="inline-flex items-end gap-px" title={`${signal}%`}>
      {[1, 2, 3, 4].map((b) => (
        <span
          key={b}
          className={`inline-block w-1 rounded-sm transition-all ${b <= bars ? color : 'bg-muted-foreground/25'}`}
          style={{ height: `${b * 3 + 3}px` }}
        />
      ))}
    </span>
  );
}

function NetworkManagerSection() {
  // ── connections state ──────────────────────────────────────
  const [connections, setConnections] = useState<NetworkConnectionItem[]>([]);
  const [connLoading, setConnLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  // ── add-form state ─────────────────────────────────────────
  const [showAdd, setShowAdd] = useState(false);
  const [addName, setAddName] = useState('');
  const [addSsid, setAddSsid] = useState('');
  const [addPass, setAddPass] = useState('');
  const [addBusy, setAddBusy] = useState(false);

  // ── scan state ─────────────────────────────────────────────
  const [networks, setNetworks] = useState<WifiScanResult[]>([]);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanned, setScanned] = useState(false);

  // ── loaders ────────────────────────────────────────────────
  const reloadConnections = async () => {
    setConnLoading(true);
    try {
      const data = await api.listNetworkConnections();
      setConnections(data.connections);
    } catch (err) {
      toast.error(`Connections: ${err}`);
    } finally {
      setConnLoading(false);
    }
  };

  const doScan = async (rescan: boolean) => {
    setScanLoading(true);
    try {
      const data = await api.scanWifiNetworks(rescan);
      setNetworks(data.networks);
      setScanned(true);
      if (rescan) toast.success(`Found ${data.networks.length} network(s)`);
    } catch (err) {
      toast.error(`Scan failed: ${err}`);
    } finally {
      setScanLoading(false);
    }
  };

  useEffect(() => {
    void reloadConnections();
    void doScan(false); // load cached scan on mount (no rescan, instant)
  }, []);

  // ── actions on existing connections ────────────────────────
  const act = async (name: string, action: 'up' | 'down' | 'delete') => {
    if (action === 'delete') {
      if (!window.confirm(`Remove managed connection "${name}"?`)) return;
    }
    setBusy(name);
    try {
      if (action === 'up')     await api.networkConnectionUp(name);
      if (action === 'down')   await api.networkConnectionDown(name);
      if (action === 'delete') await api.deleteNetworkConnection(name);
      toast.success(`${name}: ${action === 'up' ? 'connected' : action === 'down' ? 'disconnected' : 'removed'}`);
      void reloadConnections();
    } catch (err) {
      toast.error(`${name}: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  // ── add connection ─────────────────────────────────────────
  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddBusy(true);
    try {
      await api.addNetworkConnection({
        name: addName.trim(),
        ssid: addSsid.trim(),
        password: addPass,
      });
      toast.success(`"${addName.trim()}" added — click Up to connect`);
      setAddName(''); setAddSsid(''); setAddPass('');
      setShowAdd(false);
      void reloadConnections();
    } catch (err) {
      toast.error(`Add failed: ${err}`);
    } finally {
      setAddBusy(false);
    }
  };

  /** Click a scanned SSID → pre-fill the add form */
  const prefillFromScan = (net: WifiScanResult) => {
    setAddSsid(net.ssid);
    if (!addName) setAddName(net.ssid);
    setShowAdd(true);
  };

  const btn =
    'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50';

  // ── wifi-only connections for the connections panel ────────
  const wifiConns = connections.filter((c) => c.type === '802-11-wireless');
  const otherConns = connections.filter((c) => c.type !== '802-11-wireless');

  return (
    <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm space-y-6">

      {/* ── Header ─────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Wifi className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-xl font-semibold">Network Manager</h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void reloadConnections()}
            disabled={connLoading || busy !== null}
            className={`${btn} border border-border bg-muted`}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${connLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => { setShowAdd((v) => !v); }}
            className={`${btn} bg-blue-600 text-white hover:bg-blue-700`}
          >
            {showAdd ? 'Cancel' : '+ Add connection'}
          </button>
        </div>
      </div>

      {/* ── Add-connection form ─────────────────────────────── */}
      {showAdd && (
        <form
          onSubmit={(e) => void handleAdd(e)}
          className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-4 space-y-3"
        >
          <p className="text-xs font-semibold text-blue-700 dark:text-blue-300 uppercase tracking-wide">
            New WiFi connection
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">Connection name</label>
              <input
                required
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
                placeholder="e.g. Office WiFi"
                maxLength={63}
                className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">
                SSID <span className="text-muted-foreground/60">(click network below to fill)</span>
              </label>
              <input
                required
                value={addSsid}
                onChange={(e) => setAddSsid(e.target.value)}
                placeholder="e.g. Office-5G"
                maxLength={32}
                className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">
                Password <span className="text-muted-foreground/60">(blank = open network)</span>
              </label>
              <input
                type="password"
                value={addPass}
                onChange={(e) => setAddPass(e.target.value)}
                placeholder="WPA-PSK (8–63 chars)"
                maxLength={63}
                className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
          <button
            type="submit"
            disabled={addBusy || !addName.trim() || !addSsid.trim()}
            className={`${btn} bg-emerald-600 text-white hover:bg-emerald-700`}
          >
            {addBusy ? 'Adding…' : 'Add connection'}
          </button>
        </form>
      )}

      {/* ── WiFi connections ────────────────────────────────── */}
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          WiFi connections
        </p>
        {connLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : wifiConns.length === 0 ? (
          <p className="text-sm text-muted-foreground">No WiFi connections configured.</p>
        ) : (
          <div className="space-y-2">
            {wifiConns.map((conn) => {
              const isBusy = busy === conn.name;
              const isActive = !!conn.device;
              return (
                <div
                  key={conn.name}
                  className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-2.5 ${
                    isActive
                      ? 'border-emerald-500/40 bg-emerald-500/5'
                      : 'border-border/60 bg-muted/40'
                  }`}
                >
                  <div className="min-w-0 flex-1 flex flex-wrap items-center gap-2">
                    <Wifi className={`h-4 w-4 shrink-0 ${isActive ? 'text-emerald-500' : 'text-muted-foreground'}`} />
                    <span className="font-medium text-sm truncate">{conn.name}</span>
                    {isActive && (
                      <span className="rounded-full bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-300">
                        {conn.device}
                      </span>
                    )}
                    {conn.managed && (
                      <span className="rounded-full bg-blue-500/10 border border-blue-500/30 px-2 py-0.5 text-xs text-blue-700 dark:text-blue-300">
                        managed
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      disabled={isBusy || busy !== null}
                      onClick={() => void act(conn.name, 'up')}
                      className={`${btn} border border-border bg-muted hover:bg-muted/80`}
                    >
                      <Play className="h-3 w-3" /> Up
                    </button>
                    <button
                      type="button"
                      disabled={isBusy || busy !== null}
                      onClick={() => void act(conn.name, 'down')}
                      className={`${btn} border border-border bg-muted hover:bg-muted/80`}
                    >
                      <XCircle className="h-3 w-3" /> Down
                    </button>
                    {conn.managed && (
                      <button
                        type="button"
                        disabled={isBusy || busy !== null}
                        onClick={() => void act(conn.name, 'delete')}
                        className={`${btn} border border-red-500/30 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-300`}
                      >
                        <Trash2 className="h-3 w-3" /> Remove
                      </button>
                    )}
                    {isBusy && (
                      <span className="text-xs text-muted-foreground animate-pulse">working…</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Other connections (read-only, collapsed) ────────── */}
      {otherConns.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted-foreground select-none list-none flex items-center gap-1">
            <span className="group-open:rotate-90 inline-block transition-transform">▶</span>
            Other connections ({otherConns.length})
            <span className="font-normal normal-case tracking-normal ml-1">— read-only, WiFi-only ops</span>
          </summary>
          <div className="mt-2 space-y-1">
            {otherConns.map((conn) => (
              <div
                key={conn.name}
                className="flex items-center gap-2 rounded-lg border border-border/40 bg-muted/20 px-3 py-1.5 text-xs text-muted-foreground"
              >
                <span className="font-medium text-foreground/70">{conn.name}</span>
                <span>{conn.type}</span>
                {conn.device && <span className="text-emerald-600 dark:text-emerald-400">{conn.device}</span>}
              </div>
            ))}
          </div>
        </details>
      )}

      {/* ── WiFi scan ───────────────────────────────────────── */}
      <div>
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Available networks {scanned && <span className="font-normal normal-case">({networks.length} found)</span>}
          </p>
          <button
            type="button"
            disabled={scanLoading}
            onClick={() => void doScan(true)}
            className={`${btn} border border-border bg-muted`}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${scanLoading ? 'animate-spin' : ''}`} />
            {scanLoading ? 'Scanning…' : 'Rescan'}
          </button>
        </div>

        {!scanned && !scanLoading && (
          <p className="text-sm text-muted-foreground">Press Rescan to discover networks.</p>
        )}
        {scanLoading && (
          <p className="text-sm text-muted-foreground animate-pulse">Scanning for networks…</p>
        )}
        {scanned && !scanLoading && networks.length === 0 && (
          <p className="text-sm text-muted-foreground">No networks found. Try rescanning.</p>
        )}
        {scanned && !scanLoading && networks.length > 0 && (
          <div className="space-y-1">
            {networks.map((net) => {
              const alreadyAdded = connections.some(
                (c) => c.type === '802-11-wireless' && c.name === net.ssid
              );
              return (
                <button
                  key={net.ssid}
                  type="button"
                  onClick={() => prefillFromScan(net)}
                  title="Click to pre-fill Add form"
                  className={`w-full flex items-center gap-3 rounded-lg border px-3 py-2 text-left text-sm transition
                    ${net.in_use
                      ? 'border-emerald-500/40 bg-emerald-500/5'
                      : 'border-border/40 bg-muted/20 hover:bg-muted/50'
                    }`}
                >
                  <SignalBars signal={net.signal} />
                  <span className="flex-1 font-medium truncate">{net.ssid}</span>
                  {net.in_use && (
                    <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">connected</span>
                  )}
                  {alreadyAdded && !net.in_use && (
                    <span className="text-xs text-blue-600 dark:text-blue-400">saved</span>
                  )}
                  {net.security !== 'open' && (
                    <Lock className="h-3 w-3 text-muted-foreground shrink-0" />
                  )}
                  <span className="text-xs text-muted-foreground shrink-0">{net.security}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

    </section>
  );
}

// ── Command Presets Tab ──────────────────────────────────────────────────────

export function CommandPresetsTab() {
  const [presets, setPresets] = useState<CommandPreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, CommandPresetRunResponse>>({});

  const loadPresets = async () => {
    setLoading(true);
    try {
      const data = await api.listCommandPresets();
      setPresets(data.presets ?? []);
    } catch (err) {
      toast.error(`Failed to load command presets: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadPresets();
  }, []);

  const runPreset = async (preset: CommandPreset) => {
    if (preset.requires_confirmation) {
      const confirmed = window.confirm(`Run "${preset.label}"?`);
      if (!confirmed) {
        return;
      }
    }

    setRunningId(preset.id);
    try {
      const result = await api.runCommandPreset(preset.id, preset.requires_confirmation);
      setResults((prev) => ({
        ...prev,
        [preset.id]: result,
      }));

      if (result.status === 'success') {
        toast.success(`${preset.label} completed`);
      } else if (result.status === 'accepted') {
        toast.info(`${preset.label} started`);
      } else {
        toast.error(`${preset.label} ${result.status}`);
      }
    } catch (err) {
      toast.error(`Failed to run ${preset.label}: ${err}`);
    } finally {
      setRunningId(null);
    }
  };

  return (
    <>
    <section className="rounded-2xl border border-border/60 bg-card p-6 shadow-sm">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Terminal className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-xl font-semibold">Commands</h2>
        </div>
        <button
          type="button"
          onClick={() => void loadPresets()}
          disabled={loading || runningId !== null}
          className="inline-flex items-center gap-2 rounded-full border border-border bg-muted px-4 py-2 text-sm font-medium transition-colors hover:bg-muted/80 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          Refresh
        </button>
      </div>

      {loading ? (
        <div className="rounded-xl border border-border/60 bg-muted/40 p-4 text-sm text-muted-foreground">
          Loading command presets...
        </div>
      ) : presets.length === 0 ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          No command presets are configured.
        </div>
      ) : (
        <div className="space-y-3">
          {presets.map((preset) => {
            const result = results[preset.id];
            const isRunning = runningId === preset.id;
            const isBlocked = runningId !== null && !isRunning;

            return (
              <div key={preset.id} className="rounded-xl border border-border/60 bg-muted/40 p-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">{preset.label}</h3>
                      {preset.requires_confirmation && (
                        <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">
                          Confirm
                        </span>
                      )}
                      {preset.detached && (
                        <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 text-xs font-medium text-blue-700 dark:text-blue-300">
                          Detached
                        </span>
                      )}
                    </div>
                    {preset.description && (
                      <p className="mt-1 text-sm text-muted-foreground">{preset.description}</p>
                    )}
                    {preset.cwd && (
                      <p className="mt-2 truncate font-mono text-xs text-muted-foreground" title={preset.cwd}>
                        cwd: {preset.cwd}
                      </p>
                    )}
                    <div className="mt-2 space-y-1">
                      {preset.commands.map((command, index) => (
                        <p key={`${preset.id}-${index}`} className="truncate font-mono text-xs text-muted-foreground" title={command}>
                          {command}
                        </p>
                      ))}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void runPreset(preset)}
                    disabled={isRunning || isBlocked}
                    className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                      preset.requires_confirmation ? 'bg-amber-600 hover:bg-amber-700' : 'bg-blue-600 hover:bg-blue-700'
                    }`}
                  >
                    <Play className="h-4 w-4" aria-hidden="true" />
                    {isRunning ? 'Running...' : 'Run'}
                  </button>
                </div>

                {result && (
                  <div className="mt-4 rounded-lg border border-border/60 bg-background p-3">
                    <div className={`mb-3 flex flex-wrap items-center gap-2 text-sm font-medium ${statusClasses[result.status]}`}>
                      <StatusIcon status={result.status} />
                      <span>{result.status}</span>
                      <span className="text-muted-foreground">in {formatDuration(result.duration_seconds)}</span>
                      {result.exit_code != null && (
                        <span className="text-muted-foreground">exit {result.exit_code}</span>
                      )}
                    </div>
                    {result.stdout && (
                      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-mono text-xs text-foreground">
                        {result.stdout}
                      </pre>
                    )}
                    {result.stderr && (
                      <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-red-950/10 p-3 font-mono text-xs text-red-700 dark:text-red-300">
                        {result.stderr}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>

    <NetworkManagerSection />

    <VideoRecordingServiceSection />
    </>
  ); // end CommandPresetsTab
}
