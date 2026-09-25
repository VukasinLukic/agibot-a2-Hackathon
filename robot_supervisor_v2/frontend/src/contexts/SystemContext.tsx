import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { SSEConnection } from '@/api/sse';
import type {
  Service,
  ConversationStatus,
  TranscriptState,
  RobotTemperatureState,
  VisionStatus,
  NetworkStatus,
  RuntimeStatus,
  RobotIdentity,
} from '@/api/types';

interface SystemContextValue {
  services: Service[];
  conversation: ConversationStatus;
  vision: VisionStatus | null;
  transcript: TranscriptState;
  robotTemperature: RobotTemperatureState | null;
  network: NetworkStatus | null;
  runtime: RuntimeStatus | null;
  robot: RobotIdentity | null;
  connected: boolean;
  error: boolean;
}

const SystemContext = createContext<SystemContextValue | undefined>(undefined);

export function SystemProvider({ children }: { children: ReactNode }) {
  const [services, setServices] = useState<Service[]>([]);
  const [conversation, setConversation] = useState<ConversationStatus>({
    state: 'idle'
  });
  const [vision, setVision] = useState<VisionStatus | null>(null);
  const [transcript, setTranscript] = useState<TranscriptState>({
    connected: false,
    enabled: false,
    entries: []
  });
  const [robotTemperature, setRobotTemperature] = useState<RobotTemperatureState | null>(null);
  const [network, setNetwork] = useState<NetworkStatus | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [robot, setRobot] = useState<RobotIdentity | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    const sse = new SSEConnection();

    const unsubscribe = sse.subscribe((data) => {
      setServices(data.services);
      setConversation(data.conversation);
      setVision(data.vision || null);
      setTranscript(data.transcript || { connected: false, enabled: false, entries: [] });
      setRobotTemperature(data.robot_temperature || null);
      setNetwork(data.network || null);
      setRuntime(data.runtime || null);
      setRobot(data.robot || null);
      setConnected(true);
      setError(false);
    });

    const unsubscribeError = sse.onError(() => {
      setConnected(false);
      setError(true);
    });

    sse.connect();

    return () => {
      unsubscribe();
      unsubscribeError();
      sse.disconnect();
    };
  }, []);

  return (
    <SystemContext.Provider value={{ services, conversation, vision, transcript, robotTemperature, network, runtime, robot, connected, error }}>
      {children}
    </SystemContext.Provider>
  );
}

export function useSystem() {
  const context = useContext(SystemContext);
  if (!context) {
    throw new Error('useSystem must be used within SystemProvider');
  }
  return context;
}
