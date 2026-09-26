import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import type {
  Agent,
  AudioDevice,
  GestureSafetyPool,
} from '@/api/types';
import { CameraDeviceSelect } from './CameraDeviceSelect';

interface ServiceConfigRowProps {
  serviceName: string;
  serviceState: string;
  onOpenPromptConfig?: () => void;
}

function isCameraBridgeService(serviceName: string) {
  return serviceName.startsWith('camera-bridge');
}

function isVisionControllerService(serviceName: string) {
  return serviceName === 'vision-controller';
}

function isVideoDeviceSelectableService(serviceName: string) {
  return isCameraBridgeService(serviceName) || isVisionControllerService(serviceName);
}

export function ServiceConfigRow({
  serviceName,
  serviceState,
  onOpenPromptConfig,
}: ServiceConfigRowProps) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [currentAgent, setCurrentAgent] = useState('');
  const [audioDevices, setAudioDevices] = useState<{
    input_devices: AudioDevice[];
    output_devices: AudioDevice[];
  }>({ input_devices: [], output_devices: [] });
  const [selectedMic, setSelectedMic] = useState<string | undefined>();
  const [selectedSpeaker, setSelectedSpeaker] = useState<string | number | undefined>();
  const [rnnoiseEnabled, setRnnoiseEnabled] = useState(false);
  const [selectedCamera, setSelectedCamera] = useState('');
  const [cameraInterval, setCameraInterval] = useState(3.0);
  const [cameraSendToAgent, setCameraSendToAgent] = useState(false);
  const [cameraReactToVisuals, setCameraReactToVisuals] = useState(false);
  const [visionIdScanningEnabled, setVisionIdScanningEnabled] = useState(false);
  const [visionFaceRecognitionEnabled, setVisionFaceRecognitionEnabled] = useState(false);
  const [loading, setLoading] = useState(false);
  const [gesturePools, setGesturePools] = useState<GestureSafetyPool[]>([]);
  const [selectedGesturePool, setSelectedGesturePool] = useState<GestureSafetyPool>('safe_only');

  const filteredInputDevices = audioDevices.input_devices;
  const filteredOutputDevices = audioDevices.output_devices;

  const getMicConfigValue = (device: AudioDevice) => device.alsa_device ?? String(device.index);
  const getSpeakerConfigValue = (device: AudioDevice) => device.alsa_device ?? String(device.index);

  const resolveConfiguredValue = (
    configured: string | number | null | undefined,
    devices: AudioDevice[],
    getConfigValue: (device: AudioDevice) => string
  ) => {
    if (configured == null) {
      return undefined;
    }

    const rawValue = String(configured);
    const matchedDevice = devices.find((device) => getConfigValue(device) === rawValue)
      ?? devices.find((device) => String(device.index) === rawValue)
      ?? devices.find((device) => device.name === rawValue)
      ?? devices.find((device) => device.id_path === rawValue)
      ?? devices.find((device) => device.alsa_device === rawValue);

    return matchedDevice ? getConfigValue(matchedDevice) : rawValue;
  };

  const isConfiguredValueListed = (
    configured: string | number | undefined,
    devices: AudioDevice[],
    getConfigValue: (device: AudioDevice) => string
  ) => {
    if (configured == null || String(configured) === '') {
      return true;
    }

    const configuredValue = String(configured);
    return devices.some((device) => getConfigValue(device) === configuredValue);
  };

  useEffect(() => {
    if (serviceName !== 'voice-agent') {
      return;
    }

    api.listAgents().then(({ agents }) => {
      setAgents(agents);
      api.getCurrentAgent().then((current) => {
        setCurrentAgent(current.name);
      }).catch(() => {
        if (agents.length > 0) {
          setCurrentAgent(agents[0].name);
        }
      });
    });
  }, [serviceName]);

  useEffect(() => {
    if (serviceName !== 'audio-bridge') {
      return;
    }

    void (async () => {
      const [devicesResult, configResult] = await Promise.allSettled([
        api.listAudioDevices(),
        api.getAudioBridgeConfig(),
      ]);

      const nextAudioDevices =
        devicesResult.status === 'fulfilled'
          ? {
              input_devices: devicesResult.value.input_devices ?? [],
              output_devices: devicesResult.value.output_devices ?? [],
            }
          : {
              input_devices: [],
              output_devices: [],
            };

      setAudioDevices(nextAudioDevices);

      if (devicesResult.status === 'rejected') {
        console.error('Failed to load audio devices:', devicesResult.reason);
      }

      if (configResult.status === 'fulfilled') {
        setSelectedMic(
          resolveConfiguredValue(
            configResult.value.default_microphone,
            nextAudioDevices.input_devices,
            getMicConfigValue
          )
        );
        setSelectedSpeaker(
          resolveConfiguredValue(
            configResult.value.default_speakers,
            nextAudioDevices.output_devices,
            getSpeakerConfigValue
          )
        );
        setRnnoiseEnabled(Boolean(configResult.value.enable_rnnoise));
      } else {
        console.error('Failed to load audio bridge config:', configResult.reason);
      }
    })();
  }, [serviceName, serviceState]);

  const getSpeakerOptionLabel = (device: AudioDevice) => {
    const label = `[${device.index}] ${device.name}`;
    if (device.connected !== undefined) {
      return `${label} [${device.connected ? 'connected' : 'disconnected'}]`;
    }
    return label;
  };

  const selectedMicIsMissing = !isConfiguredValueListed(
    selectedMic,
    filteredInputDevices,
    getMicConfigValue
  );
  const selectedSpeakerIsMissing = !isConfiguredValueListed(
    selectedSpeaker,
    filteredOutputDevices,
    getSpeakerConfigValue
  );

  useEffect(() => {
    if (!isVideoDeviceSelectableService(serviceName)) {
      return;
    }

    void (async () => {
      if (isVisionControllerService(serviceName)) {
        const [configResult] = await Promise.allSettled([api.getVisionControllerConfig()]);
        const configuredCamera =
          configResult.status === 'fulfilled'
            ? String(configResult.value.camera_id ?? '6')
            : '6';
        const nextVisionIdScanningEnabled =
          configResult.status === 'fulfilled'
            ? configResult.value.enable_id_scanning ?? false
            : false;
        const nextVisionFaceRecognitionEnabled =
          configResult.status === 'fulfilled'
            ? configResult.value.enable_face_recognition ?? false
            : false;

        setSelectedCamera(configuredCamera);
        setVisionIdScanningEnabled(nextVisionIdScanningEnabled);
        setVisionFaceRecognitionEnabled(nextVisionFaceRecognitionEnabled);
        if (configResult.status === 'rejected') {
          console.error('Failed to load vision controller config:', configResult.reason);
        }
        return;
      }

      if (isCameraBridgeService(serviceName)) {
        const [configResult] = await Promise.allSettled([api.getCameraBridgeConfig(serviceName)]);
        const configuredCamera =
          configResult.status === 'fulfilled'
            ? configResult.value.device ?? ''
            : '';
        const nextCameraInterval =
          configResult.status === 'fulfilled'
            ? configResult.value.interval ?? 3.0
            : 3.0;
        const nextSendToAgent =
          configResult.status === 'fulfilled'
            ? configResult.value.send_to_agent ?? false
            : false;
        const nextReactToVisuals =
          configResult.status === 'fulfilled'
            ? configResult.value.react_to_visuals ?? false
            : false;

        setSelectedCamera(configuredCamera);
        setCameraInterval(nextCameraInterval);
        setCameraSendToAgent(nextSendToAgent);
        setCameraReactToVisuals(nextReactToVisuals);
        if (configResult.status === 'rejected') {
          console.error('Failed to load camera bridge config:', configResult.reason);
        }
      }
    })();
  }, [serviceName]);

  useEffect(() => {
    if (serviceName !== 'gesture-bridge') {
      return;
    }

    api.getGestureBridgeConfig()
      .then((config) => {
        setGesturePools(config.available_pools ?? []);
        setSelectedGesturePool(config.safety_pool ?? 'safe_only');
      })
      .catch((err) => {
        console.error('Failed to load gesture bridge config:', err);
      });
  }, [serviceName, serviceState]);

  const handleAgentChange = async (agentImplementation: string) => {
    setLoading(true);
    try {
      await api.selectAgent(agentImplementation);
      setCurrentAgent(agentImplementation);
      toast.success(`Switched to ${agentImplementation}`);
      if (serviceState === 'running') {
        toast.info('Voice agent is restarting...');
      }
    } catch (err) {
      toast.error(`Failed to select agent: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleMicChange = async (deviceName: string) => {
    const nextMic = deviceName === '' ? undefined : deviceName;
    setLoading(true);
    try {
      await api.updateAudioBridgeConfig(nextMic, selectedSpeaker, rnnoiseEnabled);
      setSelectedMic(nextMic);
      toast.success('Microphone updated');
      if (serviceState === 'running') {
        toast.info('Audio bridge is restarting...');
      }
    } catch (err) {
      toast.error(`Failed to update microphone: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSpeakerChange = async (deviceIndex: string) => {
    const index = deviceIndex === '' ? undefined : deviceIndex;
    setLoading(true);
    try {
      await api.updateAudioBridgeConfig(selectedMic, index, rnnoiseEnabled);
      setSelectedSpeaker(index);
      toast.success('Speaker updated');
      if (serviceState === 'running') {
        toast.info('Audio bridge is restarting...');
      }
    } catch (err) {
      toast.error(`Failed to update speaker: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleRnnoiseChange = async (enabled: boolean) => {
    setLoading(true);
    try {
      await api.updateAudioBridgeConfig(selectedMic, selectedSpeaker, enabled);
      setRnnoiseEnabled(enabled);
      toast.success(enabled ? 'RNNoise enabled' : 'RNNoise disabled');
      if (serviceState === 'running') {
        toast.info('Audio bridge is restarting...');
      }
    } catch (err) {
      toast.error(`Failed to update RNNoise: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  async function handleCameraChange(devicePath: string) {
    setLoading(true);
    try {
      if (isVisionControllerService(serviceName)) {
        await api.updateVisionControllerConfig({
          cameraId: devicePath,
          enableIdScanning: visionIdScanningEnabled,
        });
      } else {
        await api.updateCameraBridgeConfig(serviceName, devicePath, cameraInterval, cameraSendToAgent, undefined, cameraReactToVisuals);
      }
      setSelectedCamera(devicePath);
      toast.success('Camera updated');
      if (serviceState === 'running') {
        toast.info(
          isVisionControllerService(serviceName)
            ? 'Vision controller is restarting...'
            : 'Camera bridge is restarting...'
        );
      }
    } catch (err) {
      toast.error(`Failed to update camera: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleIntervalChange(interval: number) {
    setLoading(true);
    try {
      await api.updateCameraBridgeConfig(serviceName, selectedCamera, interval, cameraSendToAgent, undefined, cameraReactToVisuals);
      setCameraInterval(interval);
      toast.success('Interval updated');
      if (serviceState === 'running') {
        toast.info('Camera bridge is restarting...');
      }
    } catch (err) {
      toast.error(`Failed to update interval: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleCameraSendToAgentChange(sendToAgent: boolean) {
    setLoading(true);
    try {
      await api.updateCameraBridgeConfig(serviceName, selectedCamera, cameraInterval, sendToAgent, undefined, cameraReactToVisuals);
      setCameraSendToAgent(sendToAgent);
      toast.success(sendToAgent ? 'Agent snapshots enabled' : 'Agent snapshots disabled');
      if (serviceState === 'running') {
        toast.info('Camera bridge is restarting...');
      }
    } catch (err) {
      toast.error(`Failed to update agent snapshot setting: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleCameraReactToVisualsChange(enabled: boolean) {
    setLoading(true);
    try {
      await api.updateCameraBridgeConfig(
        serviceName,
        selectedCamera,
        cameraInterval,
        cameraSendToAgent,
        undefined,
        enabled,
      );
      setCameraReactToVisuals(enabled);
      toast.success(enabled ? 'Visual reactions enabled' : 'Visual reactions disabled');
      if (serviceState === 'running') {
        toast.info('Camera bridge is restarting...');
      }
    } catch (err) {
      toast.error(`Failed to update visual reactions: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleVisionIdScanningChange(enabled: boolean) {
    setLoading(true);
    try {
      await api.updateVisionControllerConfig({
        enableIdScanning: enabled,
      });
      setVisionIdScanningEnabled(enabled);
      toast.success(enabled ? 'ID scanning enabled' : 'ID scanning disabled');
      if (serviceState === 'running') {
        toast.info('Vision controller is restarting...');
      }
    } catch (err) {
      toast.error(`Failed to update ID scanning setting: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleVisionFaceRecognitionChange(enabled: boolean) {
    setLoading(true);
    try {
      await api.updateVisionControllerConfig({
        enableFaceRecognition: enabled,
      });
      setVisionFaceRecognitionEnabled(enabled);
      // Unlike the other vision settings, this one is polled live by the
      // detector, so it takes effect without restarting the service.
      toast.success(
        enabled
          ? 'Face recognition enabled (applied live)'
          : 'Face recognition disabled (applied live)'
      );
    } catch (err) {
      toast.error(`Failed to update face recognition setting: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleGesturePoolChange(pool: GestureSafetyPool) {
    setLoading(true);
    try {
      const result = await api.updateGestureBridgeConfig(pool);
      setGesturePools(result.available_pools ?? []);
      setSelectedGesturePool(result.safety_pool ?? pool);
      toast.success(`Gesture pool set to ${result.safety_pool}`);
      toast.info('Gesture metadata refreshed. Running gesture and voice services were restarted if needed.');
      window.dispatchEvent(new CustomEvent('gesture-catalog-updated'));
    } catch (err) {
      toast.error(`Failed to update gesture pool: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  if (serviceName === 'voice-agent') {
    return (
      <>
        {agents.length > 0 && (
          <>
            <span className="text-xs text-muted-foreground">Agent:</span>
            <select
              value={currentAgent}
              onChange={(e) => void handleAgentChange(e.target.value)}
              disabled={loading}
              className="text-xs border border-border bg-background text-foreground rounded-md px-2 py-1 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring/40"
            >
              {agents.map((agent) => (
                <option key={agent.name} value={agent.name} className="bg-background text-foreground">
                  {agent.name}
                </option>
              ))}
            </select>
          </>
        )}

        <button
          type="button"
          onClick={onOpenPromptConfig}
          disabled={!onOpenPromptConfig}
          className="px-3 py-1 text-xs rounded-full font-medium transition-colors bg-slate-600 hover:bg-slate-700 text-white disabled:opacity-50 disabled:cursor-not-allowed"
          title="Open persona settings"
        >
          Persona
        </button>
      </>
    );
  }

  if (serviceName === 'audio-bridge') {
    return (
      <>
        <span className="text-xs text-muted-foreground">Mic:</span>
        <select
          value={selectedMic ?? ''}
          onChange={(e) => void handleMicChange(e.target.value)}
          disabled={loading}
          className="text-xs border border-border bg-background text-foreground rounded-md px-2 py-1 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring/40 max-w-[150px]"
        >
          <option value="" disabled hidden className="bg-background text-foreground" />
          {selectedMicIsMissing && (
            <option value={selectedMic} disabled className="bg-background text-foreground">
              Configured: {selectedMic} (not found)
            </option>
          )}
          {filteredInputDevices.map((device) => (
            <option key={device.index} value={getMicConfigValue(device)} className="bg-background text-foreground">
              [{device.index}] {device.name}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground">Speaker:</span>
        <select
          value={selectedSpeaker ?? ''}
          onChange={(e) => void handleSpeakerChange(e.target.value)}
          disabled={loading}
          className="text-xs border border-border bg-background text-foreground rounded-md px-2 py-1 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring/40 max-w-[150px]"
        >
          <option value="" disabled hidden className="bg-background text-foreground" />
          {selectedSpeakerIsMissing && (
            <option value={String(selectedSpeaker)} disabled className="bg-background text-foreground">
              Configured: {selectedSpeaker} (not found)
            </option>
          )}
          {filteredOutputDevices.map((device) => (
            <option key={device.index} value={getSpeakerConfigValue(device)} className="bg-background text-foreground">
              {getSpeakerOptionLabel(device)}
            </option>
          ))}
        </select>
        <label className="inline-flex items-center gap-2 text-xs text-foreground">
          <input
            type="checkbox"
            checked={rnnoiseEnabled}
            onChange={(e) => void handleRnnoiseChange(e.target.checked)}
            disabled={loading}
            className="h-4 w-4 rounded border border-border bg-background text-slate-700 focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
          />
          <span>RNNoise</span>
        </label>
      </>
    );
  }

  if (serviceName === 'gesture-bridge' && gesturePools.length > 0) {
    return (
      <>
        <span className="text-xs text-muted-foreground">Gesture Pool:</span>
        <select
          value={selectedGesturePool}
          onChange={(e) => void handleGesturePoolChange(e.target.value as GestureSafetyPool)}
          disabled={loading}
          className="text-xs border border-border bg-background text-foreground rounded-md px-2 py-1 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring/40"
        >
          {gesturePools.map((pool) => (
            <option key={pool} value={pool} className="bg-background text-foreground">
              {pool}
            </option>
          ))}
        </select>
      </>
    );
  }

  if (isVideoDeviceSelectableService(serviceName)) {
    const showCameraBridgeControls = isCameraBridgeService(serviceName);
    const showVisionControllerControls = isVisionControllerService(serviceName);

    return (
      <>
        <span className="text-xs text-muted-foreground">Camera:</span>
        <CameraDeviceSelect
          value={selectedCamera}
          onChange={handleCameraChange}
          disabled={loading}
        />
        {showVisionControllerControls && (
          <label className="inline-flex items-center gap-2 text-xs text-foreground">
            <input
              type="checkbox"
              checked={visionIdScanningEnabled}
              onChange={(e) => void handleVisionIdScanningChange(e.target.checked)}
              disabled={loading}
              className="h-4 w-4 rounded border border-border bg-background text-slate-700 focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
            />
            <span>Enable ID Scanning</span>
          </label>
        )}
        {showVisionControllerControls && (
          <label
            className="inline-flex items-center gap-2 text-xs text-foreground"
            title="Greet enrolled people by name, ask unknown visitors for consent to remember their face, and honour 'forget me'. Applied live, without restarting the vision controller."
          >
            <input
              type="checkbox"
              checked={visionFaceRecognitionEnabled}
              onChange={(e) => void handleVisionFaceRecognitionChange(e.target.checked)}
              disabled={loading}
              className="h-4 w-4 rounded border border-border bg-background text-slate-700 focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
            />
            <span>Enable Face Recognition</span>
          </label>
        )}
        {showCameraBridgeControls && (
          <>
            <label className="inline-flex items-center gap-2 text-xs text-foreground">
              <input
                type="checkbox"
                checked={cameraSendToAgent}
                onChange={(e) => void handleCameraSendToAgentChange(e.target.checked)}
                disabled={loading}
                className="h-4 w-4 rounded border border-border bg-background text-slate-700 focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
              />
              <span>Send to Agent</span>
            </label>
            <label
              className="inline-flex items-center gap-2 text-xs text-foreground"
              title="Experimental: allow the agent to consider one safe social gesture when a visual snapshot is attached to a user turn."
            >
              <input
                type="checkbox"
                checked={cameraReactToVisuals}
                onChange={(e) => void handleCameraReactToVisualsChange(e.target.checked)}
                disabled={loading}
                className="h-4 w-4 rounded border border-border bg-background text-slate-700 focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
              />
              <span>React</span>
            </label>
            <span className="text-xs text-muted-foreground">Interval:</span>
            <select
              value={cameraInterval}
              onChange={(e) => void handleIntervalChange(Number(e.target.value))}
              disabled={loading || (!cameraSendToAgent && !cameraReactToVisuals)}
              className="text-xs border border-border bg-background text-foreground rounded-md px-2 py-1 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring/40"
              title={!cameraSendToAgent && !cameraReactToVisuals ? 'Used when Send to Agent or React is enabled' : undefined}
            >
              <option value={1}>1s</option>
              <option value={2}>2s</option>
              <option value={3}>3s</option>
              <option value={5}>5s</option>
              <option value={10}>10s</option>
              <option value={30}>30s</option>
            </select>
          </>
        )}
      </>
    );
  }

  return null;
}
