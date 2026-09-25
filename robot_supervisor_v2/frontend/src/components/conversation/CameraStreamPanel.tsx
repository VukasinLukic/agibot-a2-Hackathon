import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { Room, RoomEvent, Track } from 'livekit-client';
import { api } from '@/api/client';
import type { CameraBridgeStreamInfo } from '@/api/types';
import { useSystem } from '@/contexts/SystemContext';

const DEFAULT_CAMERA_STREAM_INFO: CameraBridgeStreamInfo = {
  service_name: 'camera-bridge',
  display_name: 'Camera',
  running: false,
  room: '',
  identity: 'camera-bridge',
  track_name: 'camera',
  topic: 'images',
};
const MONITOR_HEARTBEAT_MS = 5000;
const MONITOR_HEARTBEAT_FAILURES_BEFORE_ERROR = 2;
const MONITOR_HEARTBEAT_ERROR_PREFIX = 'Camera monitor heartbeat failed';

interface CameraStreamPanelProps {
  serviceName: string;
  displayName: string;
}

export function CameraStreamPanel({ serviceName, displayName }: CameraStreamPanelProps) {
  const { services, conversation } = useSystem();
  const [starting, setStarting] = useState(false);
  const [monitoring, setMonitoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [videoAttached, setVideoAttached] = useState(false);
  const [cameraStreamInfo, setCameraStreamInfo] = useState<CameraBridgeStreamInfo>(DEFAULT_CAMERA_STREAM_INFO);
  const roomRef = useRef<Room | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const connectedRoomNameRef = useRef<string | null>(null);
  const cameraIdentityRef = useRef(DEFAULT_CAMERA_STREAM_INFO.identity);
  const cameraTrackNameRef = useRef(DEFAULT_CAMERA_STREAM_INFO.track_name);
  const monitoringRef = useRef(false);
  const monitorSessionIdRef = useRef<string | null>(null);
  const monitorHeartbeatRef = useRef<number | null>(null);
  const monitorHeartbeatFailuresRef = useRef(0);

  const cameraService = services.find((service) => service.name === serviceName);
  const hasCameraService = Boolean(cameraService);
  const cameraBridgeRunning = cameraService?.state === 'running';
  const effectiveRoomName = cameraStreamInfo.room || conversation.room || '';

  useEffect(() => {
    monitoringRef.current = monitoring;
  }, [monitoring]);

  useEffect(() => {
    let cancelled = false;

    const loadCameraStreamInfo = async () => {
      if (!hasCameraService) {
        const fallbackInfo = {
          ...DEFAULT_CAMERA_STREAM_INFO,
          service_name: serviceName,
          display_name: displayName,
          room: conversation.room || '',
        };
        if (!cancelled) {
          setCameraStreamInfo(fallbackInfo);
          cameraIdentityRef.current = fallbackInfo.identity;
          cameraTrackNameRef.current = fallbackInfo.track_name;
        }
        return;
      }

      try {
        const streamInfo = await api.getCameraBridgeStreamInfo(serviceName);
        if (cancelled) {
          return;
        }

        const nextStreamInfo = {
          ...DEFAULT_CAMERA_STREAM_INFO,
          ...streamInfo,
          room: streamInfo.room || conversation.room || '',
        };
        setCameraStreamInfo(nextStreamInfo);
        cameraIdentityRef.current = nextStreamInfo.identity;
        cameraTrackNameRef.current = nextStreamInfo.track_name;
      } catch (err) {
        if (!cancelled) {
          setError(`Failed to load camera stream info: ${String(err)}`);
        }
      }
    };

    void loadCameraStreamInfo();
    return () => {
      cancelled = true;
    };
  }, [displayName, hasCameraService, cameraService?.state, conversation.room, serviceName]);

  useEffect(() => {
    return () => {
      detachVideo(videoRef.current);
      void disconnectRoom(roomRef.current);
      void releaseMonitorSession(serviceName, monitorSessionIdRef, monitorHeartbeatRef);
      roomRef.current = null;
      connectedRoomNameRef.current = null;
    };
  }, [serviceName]);

  useEffect(() => {
    if (!monitoring) {
      detachVideo(videoRef.current);
      setVideoAttached(false);
      monitorHeartbeatFailuresRef.current = 0;
      void disconnectRoom(roomRef.current);
      void releaseMonitorSession(serviceName, monitorSessionIdRef, monitorHeartbeatRef);
      roomRef.current = null;
      connectedRoomNameRef.current = null;
      return;
    }

    if (!effectiveRoomName) {
      return;
    }

    let cancelled = false;
    let localSessionId: string | null = null;

    const connectRoom = async () => {
      try {
        const monitorSession = await api.createCameraBridgeMonitorSession(serviceName);
        if (!monitorSession.session_id) {
          throw new Error('Camera monitor session did not include a session id.');
        }
        localSessionId = monitorSession.session_id;
        if (cancelled) {
          void api.deleteCameraBridgeMonitorSession(serviceName, localSessionId).catch(() => undefined);
          return;
        }

        monitorSessionIdRef.current = localSessionId;
        monitorHeartbeatFailuresRef.current = 0;
        startMonitorHeartbeat(
          serviceName,
          localSessionId,
          monitorHeartbeatRef,
          () => {
            if (!monitoringRef.current || monitorSessionIdRef.current !== localSessionId) {
              return;
            }
            monitorHeartbeatFailuresRef.current = 0;
            setError((currentError) => (
              currentError?.startsWith(MONITOR_HEARTBEAT_ERROR_PREFIX) ? null : currentError
            ));
          },
          (err) => {
            if (!monitoringRef.current || monitorSessionIdRef.current !== localSessionId) {
              return;
            }

            monitorHeartbeatFailuresRef.current += 1;
            if (monitorHeartbeatFailuresRef.current < MONITOR_HEARTBEAT_FAILURES_BEFORE_ERROR) {
              return;
            }

            setError(
              `${MONITOR_HEARTBEAT_ERROR_PREFIX}: ${formatMonitorHeartbeatError(err)}`,
            );
          },
        );

        const room = await ensureRoom({
          targetRoom: effectiveRoomName,
          roomRef,
          connectedRoomNameRef,
          cameraIdentityRef,
          cameraTrackNameRef,
          monitoringRef,
          videoRef,
          setVideoAttached,
          onDisconnected: () => {
            void releaseMonitorSession(serviceName, monitorSessionIdRef, monitorHeartbeatRef);
            setMonitoring(false);
          },
        });
        if (cancelled) {
          await disconnectRoom(room);
          if (roomRef.current === room) {
            roomRef.current = null;
            connectedRoomNameRef.current = null;
          }
          if (localSessionId && monitorSessionIdRef.current === localSessionId) {
            void releaseMonitorSession(serviceName, monitorSessionIdRef, monitorHeartbeatRef);
          }
          return;
        }

        syncVideoBinding({
          room,
          cameraIdentityRef,
          cameraTrackNameRef,
          monitoringRef,
          videoRef,
          setVideoAttached,
        });
      } catch (err) {
        if (!cancelled) {
          setError(String(err));
          setMonitoring(false);
          if (localSessionId && monitorSessionIdRef.current === localSessionId) {
            void releaseMonitorSession(serviceName, monitorSessionIdRef, monitorHeartbeatRef);
          }
        }
      } finally {
        if (!cancelled) {
          setStarting(false);
        }
      }
    };

    void connectRoom();
    return () => {
      cancelled = true;
      if (localSessionId && monitorSessionIdRef.current === localSessionId) {
        void releaseMonitorSession(serviceName, monitorSessionIdRef, monitorHeartbeatRef);
      }
    };
  }, [monitoring, effectiveRoomName, serviceName]);

  useEffect(() => {
    if (!monitoring || !roomRef.current || !videoRef.current) {
      detachVideo(videoRef.current);
      setVideoAttached(false);
      return;
    }

    setVideoAttached(
      attachParticipantVideo(
        roomRef.current,
        cameraIdentityRef.current,
        cameraTrackNameRef.current,
        videoRef.current,
      ),
    );
  }, [monitoring, cameraStreamInfo.identity, cameraStreamInfo.track_name, cameraBridgeRunning]);

  const handleToggleMonitor = () => {
    if (monitoring) {
      setStarting(false);
      setError(null);
      setMonitoring(false);
      return;
    }

    if (!effectiveRoomName) {
      setError('No LiveKit room is configured for browser monitoring.');
      return;
    }

    setError(null);
    setStarting(true);
    setMonitoring(true);
  };

  const waitingMessage = !monitoring
    ? 'Start Stream to view the camera.'
    : !cameraBridgeRunning
      ? 'Waiting for camera stream.'
      : 'Connecting stream...';

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/60 bg-muted/50 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-xs">
            <span className="font-semibold">{displayName}</span>
            <span className="ml-2 uppercase text-muted-foreground">
              {monitoring ? 'on' : 'off'}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            View the camera stream.
          </p>
        </div>
        <button
          type="button"
          onClick={handleToggleMonitor}
          disabled={starting}
          className={`px-3 py-1.5 text-xs rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
            monitoring ? 'bg-red-600 hover:bg-red-700 text-white' : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          {starting ? 'Starting...' : monitoring ? 'Stop Stream' : 'Start Stream'}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-300/60 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {monitoring && !videoAttached && (
        <div className="rounded-lg border border-blue-300/60 bg-blue-500/10 px-3 py-2 text-xs text-blue-800 dark:text-blue-200">
          {waitingMessage}
        </div>
      )}

      {!hasCameraService && (
        <div className="rounded-lg border border-amber-300/60 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          {displayName} is not configured.
        </div>
      )}

      <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-border/70 bg-muted/50">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className={`h-full w-full object-cover ${monitoring ? 'block' : 'hidden'}`}
          onLoadedData={() => {
            setVideoAttached(true);
            setError(null);
          }}
        />
        {(!monitoring || !videoAttached) && (
          <div className="absolute inset-0 flex items-center justify-center px-4">
            <div className="text-center text-muted-foreground">
              <p className="text-sm font-medium">{displayName}</p>
              <p className="text-xs">{waitingMessage}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function syncVideoBinding({
  room,
  cameraIdentityRef,
  cameraTrackNameRef,
  monitoringRef,
  videoRef,
  setVideoAttached,
}: {
  room: Room;
  cameraIdentityRef: MutableRefObject<string>;
  cameraTrackNameRef: MutableRefObject<string>;
  monitoringRef: MutableRefObject<boolean>;
  videoRef: MutableRefObject<HTMLVideoElement | null>;
  setVideoAttached: (value: boolean) => void;
}) {
  if (monitoringRef.current && videoRef.current) {
    setVideoAttached(
      attachParticipantVideo(room, cameraIdentityRef.current, cameraTrackNameRef.current, videoRef.current),
    );
    return;
  }

  detachVideo(videoRef.current);
  setVideoAttached(false);
}

async function ensureRoom({
  targetRoom,
  roomRef,
  connectedRoomNameRef,
  cameraIdentityRef,
  cameraTrackNameRef,
  monitoringRef,
  videoRef,
  setVideoAttached,
  onDisconnected,
}: {
  targetRoom: string;
  roomRef: MutableRefObject<Room | null>;
  connectedRoomNameRef: MutableRefObject<string | null>;
  cameraIdentityRef: MutableRefObject<string>;
  cameraTrackNameRef: MutableRefObject<string>;
  monitoringRef: MutableRefObject<boolean>;
  videoRef: MutableRefObject<HTMLVideoElement | null>;
  setVideoAttached: (value: boolean) => void;
  onDisconnected: () => void;
}) {
  if (roomRef.current && connectedRoomNameRef.current === targetRoom) {
    return roomRef.current;
  }

  if (roomRef.current) {
    await disconnectRoom(roomRef.current);
    roomRef.current = null;
    connectedRoomNameRef.current = null;
  }

  let lastError: unknown = null;
  let lastUrl = '';

  // LiveKit may cancel an in-flight signal connection when React changes the
  // monitor lifecycle at the same moment. Retry that cancellation once with a
  // fresh Room and token; real network/auth failures are still reported.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const monitorToken = await api.getLiveKitMonitorToken(targetRoom);
    const room = new Room();
    lastUrl = monitorToken.url;

    try {
      await room.connect(monitorToken.url, monitorToken.token, {
        autoSubscribe: true,
      });
    } catch (err) {
      lastError = err;
      await disconnectRoom(room);
      if (attempt === 0 && isCancelledSignalConnection(err) && monitoringRef.current) {
        await new Promise((resolve) => window.setTimeout(resolve, 250));
        continue;
      }
      break;
    }

    const sync = () => {
      syncVideoBinding({
        room,
        cameraIdentityRef,
        cameraTrackNameRef,
        monitoringRef,
        videoRef,
        setVideoAttached,
      });
    };

    // Register lifecycle callbacks only after connect succeeds. A failed
    // attempt otherwise emits Disconnected and turns monitoring off while the
    // original connect promise is still unwinding.
    room.on(RoomEvent.Disconnected, () => {
      detachVideo(videoRef.current);
      if (roomRef.current === room) {
        roomRef.current = null;
        connectedRoomNameRef.current = null;
      }
      setVideoAttached(false);
      onDisconnected();
    });
    room.on(RoomEvent.TrackSubscribed, sync);
    room.on(RoomEvent.TrackUnsubscribed, sync);
    room.on(RoomEvent.ParticipantConnected, sync);
    room.on(RoomEvent.ParticipantDisconnected, sync);

    roomRef.current = room;
    connectedRoomNameRef.current = monitorToken.room;
    return room;
  }

  throw new Error(`LiveKit signal connection failed at ${lastUrl}: ${String(lastError)}`);
}

function isCancelledSignalConnection(error: unknown) {
  const text = String(error).toLowerCase();
  return text.includes('abort handler called')
    || text.includes('connection attempt aborted')
    || text.includes('client initiated disconnect')
    || text.includes('cancelled');
}

function findVideoTrack(room: Room, participantIdentity: string, trackName: string) {
  const participant = Array.from(room.remoteParticipants.values()).find(
    (remoteParticipant) => remoteParticipant.identity === participantIdentity,
  );
  if (!participant) {
    return null;
  }

  for (const publication of participant.trackPublications.values()) {
    const track = publication.track;
    if (track?.kind === Track.Kind.Video && publication.trackName === trackName) {
      return track;
    }
  }

  for (const publication of participant.trackPublications.values()) {
    const track = publication.track;
    if (track?.kind === Track.Kind.Video) {
      return track;
    }
  }

  return null;
}

function isElementBoundToVideo(element: HTMLVideoElement) {
  return (
    element.srcObject instanceof MediaStream
    && element.srcObject.getVideoTracks().length > 0
  );
}

function attachParticipantVideo(
  room: Room,
  participantIdentity: string,
  trackName: string,
  element: HTMLVideoElement,
) {
  // Look the track up BEFORE touching the element. This used to detach first,
  // which meant a transient lookup miss - sync() runs on TrackSubscribed,
  // TrackUnsubscribed, ParticipantConnected and ParticipantDisconnected, so it
  // can fire at a moment the publication is not in trackPublications yet - left
  // the element with srcObject = null and no way to recover until the next
  // event. The result was a blank video box.
  const track = findVideoTrack(room, participantIdentity, trackName);
  if (!track) {
    // Report whatever the element is actually doing rather than forcing it
    // false, so a healthy stream is not torn down by a spurious sync.
    return isElementBoundToVideo(element);
  }

  const stream = element.srcObject;
  const alreadyAttached = (
    stream instanceof MediaStream
    && stream.getVideoTracks().includes(track.mediaStreamTrack)
  );
  if (alreadyAttached) {
    // Re-attaching the same track restarts playback for no reason and makes the
    // picture flicker on every room event.
    return true;
  }

  detachVideo(element);
  track.attach(element);
  return true;
}

function detachVideo(element: HTMLVideoElement | null) {
  if (!element) {
    return;
  }

  element.pause();
  element.srcObject = null;
}

async function disconnectRoom(room: Room | null) {
  if (!room) {
    return;
  }
  await room.disconnect();
}

function startMonitorHeartbeat(
  serviceName: string,
  sessionId: string,
  heartbeatRef: MutableRefObject<number | null>,
  onSuccess: () => void,
  onError: (err: unknown) => void,
) {
  stopMonitorHeartbeat(heartbeatRef);
  let inFlight = false;
  heartbeatRef.current = window.setInterval(() => {
    if (inFlight) {
      return;
    }

    inFlight = true;
    void api.heartbeatCameraBridgeMonitorSession(serviceName, sessionId)
      .then(onSuccess)
      .catch(onError)
      .finally(() => {
        inFlight = false;
      });
  }, MONITOR_HEARTBEAT_MS);
}

function formatMonitorHeartbeatError(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function stopMonitorHeartbeat(heartbeatRef: MutableRefObject<number | null>) {
  if (heartbeatRef.current == null) {
    return;
  }
  window.clearInterval(heartbeatRef.current);
  heartbeatRef.current = null;
}

async function releaseMonitorSession(
  serviceName: string,
  sessionIdRef: MutableRefObject<string | null>,
  heartbeatRef: MutableRefObject<number | null>,
) {
  stopMonitorHeartbeat(heartbeatRef);
  const sessionId = sessionIdRef.current;
  sessionIdRef.current = null;
  if (!sessionId) {
    return;
  }
  try {
    await api.deleteCameraBridgeMonitorSession(serviceName, sessionId);
  } catch {
    // The supervisor also expires stale monitor sessions by TTL.
  }
}
