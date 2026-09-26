import { useEffect, useRef, useState } from 'react';
import { Room, RoomEvent, Track } from 'livekit-client';
import { api } from '@/api/client';
import { useSystem } from '@/contexts/SystemContext';
import type { AudioBridgeStatus } from '@/api/types';

type MonitorMode = 'mic' | 'speaker';

const DEFAULT_INPUT_MIC_GAIN_DB = -26;
const DEFAULT_INPUT_MIC_GAIN_DB_MIN = -40;
const DEFAULT_INPUT_MIC_GAIN_DB_MAX = 12;
const INPUT_MIC_GAIN_STEP = 0.5;
const DEFAULT_OUTPUT_SPEAKER_GAIN_DB_MIN = -20;
const DEFAULT_OUTPUT_SPEAKER_GAIN_DB_MAX = 12;

interface AudioMonitorPanelProps {
  showControls?: boolean;
}

export function AudioMonitorPanel({ showControls = true }: AudioMonitorPanelProps) {
  const { services, conversation } = useSystem();
  const [monitoringMic, setMonitoringMic] = useState(false);
  const [monitoringSpeaker, setMonitoringSpeaker] = useState(false);
  const [muteLoading, setMuteLoading] = useState(false);
  const [bridgeStatus, setBridgeStatus] = useState<AudioBridgeStatus>({
    running: false,
    muted: false,
  });
  const [inputMicGainDb, setInputMicGainDb] = useState(DEFAULT_INPUT_MIC_GAIN_DB);
  const [outputSpeakerGainDb, setOutputSpeakerGainDb] = useState(0);
  const [speakerLoading, setSpeakerLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const roomRef = useRef<Room | null>(null);
  const micAudioRef = useRef<HTMLAudioElement | null>(null);
  const speakerAudioRef = useRef<HTMLAudioElement | null>(null);
  const bridgeIdentityRef = useRef('audio-streamer');
  const monitoringMicRef = useRef(false);
  const monitoringSpeakerRef = useRef(false);
  const speakerIdentityRef = useRef<string | null>(null);
  const inputGainTimerRef = useRef<number | null>(null);
  const outputGainTimerRef = useRef<number | null>(null);

  const audioBridgeRunning = services.find((service) => service.name === 'audio-bridge')?.state === 'running';
  const roomName = conversation.room;
  const inputMicGainMin = bridgeStatus.input_mic_gain_db_min ?? DEFAULT_INPUT_MIC_GAIN_DB_MIN;
  const inputMicGainMax = bridgeStatus.input_mic_gain_db_max ?? DEFAULT_INPUT_MIC_GAIN_DB_MAX;
  const outputGainMin = bridgeStatus.output_speaker_gain_db_min ?? DEFAULT_OUTPUT_SPEAKER_GAIN_DB_MIN;
  const outputGainMax = bridgeStatus.output_speaker_gain_db_max ?? DEFAULT_OUTPUT_SPEAKER_GAIN_DB_MAX;

  useEffect(() => {
    monitoringMicRef.current = monitoringMic;
  }, [monitoringMic]);

  useEffect(() => {
    monitoringSpeakerRef.current = monitoringSpeaker;
  }, [monitoringSpeaker]);

  useEffect(() => {
    speakerIdentityRef.current = bridgeStatus.active_remote_participant_identity ?? null;
  }, [bridgeStatus.active_remote_participant_identity]);

  useEffect(() => {
    if (typeof bridgeStatus.input_mic_gain_db === 'number') {
      setInputMicGainDb(bridgeStatus.input_mic_gain_db);
    }
  }, [bridgeStatus.input_mic_gain_db]);

  useEffect(() => {
    if (typeof bridgeStatus.output_speaker_gain_db === 'number') {
      setOutputSpeakerGainDb(bridgeStatus.output_speaker_gain_db);
    }
  }, [bridgeStatus.output_speaker_gain_db]);

  useEffect(() => {
    let cancelled = false;
    let intervalId: number | null = null;

    const loadBridgeStatus = async () => {
      if (!audioBridgeRunning) {
        if (!cancelled) {
          setBridgeStatus({
            running: false,
            muted: false,
          });
        }
        return;
      }

      try {
        const status = await api.getAudioBridgeStatus();
        if (!cancelled) {
          setBridgeStatus(status);
        }
      } catch (err) {
        if (!cancelled) {
          setError(String(err));
        }
      }
    };

    if (!audioBridgeRunning) {
      return () => {
        cancelled = true;
      };
    }

    const pollBridgeStatus = () => {
      if (document.visibilityState !== 'visible') {
        return;
      }
      void loadBridgeStatus();
    };

    pollBridgeStatus();
    intervalId = window.setInterval(pollBridgeStatus, 2000);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void loadBridgeStatus();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      cancelled = true;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [audioBridgeRunning]);

  useEffect(() => {
    return () => {
      detachAudio(micAudioRef.current);
      detachAudio(speakerAudioRef.current);
      void disconnectRoom(roomRef.current);
      roomRef.current = null;
      if (inputGainTimerRef.current !== null) {
        window.clearTimeout(inputGainTimerRef.current);
        inputGainTimerRef.current = null;
      }
      if (outputGainTimerRef.current !== null) {
        window.clearTimeout(outputGainTimerRef.current);
        outputGainTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!monitoringMic && !monitoringSpeaker) {
      detachAudio(micAudioRef.current);
      detachAudio(speakerAudioRef.current);
      void disconnectRoom(roomRef.current);
      roomRef.current = null;
    }
  }, [monitoringMic, monitoringSpeaker]);

  useEffect(() => {
    if (!monitoringMic || !roomRef.current || !micAudioRef.current) {
      detachAudio(micAudioRef.current);
      return;
    }

    attachParticipantAudio(roomRef.current, bridgeIdentityRef.current, micAudioRef.current);
  }, [monitoringMic]);

  useEffect(() => {
    if (!monitoringSpeaker || !roomRef.current || !speakerAudioRef.current) {
      detachAudio(speakerAudioRef.current);
      return;
    }

    const identity = bridgeStatus.active_remote_participant_identity;
    if (!identity) {
      detachAudio(speakerAudioRef.current);
      return;
    }

    attachParticipantAudio(roomRef.current, identity, speakerAudioRef.current);
  }, [monitoringSpeaker, bridgeStatus.active_remote_participant_identity]);

  const ensureRoom = async (): Promise<Room> => {
    if (roomRef.current) {
      return roomRef.current;
    }

    const monitorToken = await api.getLiveKitMonitorToken(roomName);
    bridgeIdentityRef.current = monitorToken.bridge_identity;

    const room = new Room();
    room.on(RoomEvent.Disconnected, () => {
      detachAudio(micAudioRef.current);
      detachAudio(speakerAudioRef.current);
      roomRef.current = null;
    });
    room.on(RoomEvent.TrackSubscribed, () => {
      if (monitoringMicRef.current && micAudioRef.current) {
        attachParticipantAudio(room, bridgeIdentityRef.current, micAudioRef.current);
      }
      if (monitoringSpeakerRef.current && speakerAudioRef.current && speakerIdentityRef.current) {
        attachParticipantAudio(room, speakerIdentityRef.current, speakerAudioRef.current);
      }
    });
    room.on(RoomEvent.TrackUnsubscribed, () => {
      if (monitoringMicRef.current && micAudioRef.current) {
        attachParticipantAudio(room, bridgeIdentityRef.current, micAudioRef.current);
      }
      if (monitoringSpeakerRef.current && speakerAudioRef.current) {
        const identity = speakerIdentityRef.current;
        if (identity) {
          attachParticipantAudio(room, identity, speakerAudioRef.current);
        } else {
          detachAudio(speakerAudioRef.current);
        }
      }
    });

    try {
      await room.connect(monitorToken.url, monitorToken.token, {
        autoSubscribe: true,
      });
    } catch (err) {
      throw new Error(`LiveKit signal connection failed at ${monitorToken.url}: ${String(err)}`);
    }

    roomRef.current = room;
    return room;
  };

  const handleToggle = async (mode: MonitorMode) => {
    const nextValue = mode === 'mic' ? !monitoringMic : !monitoringSpeaker;

    if (!nextValue) {
      if (mode === 'mic') {
        setMonitoringMic(false);
      } else {
        setMonitoringSpeaker(false);
      }
      return;
    }

    try {
      setError(null);
      const room = await ensureRoom();
      await room.startAudio();

      if (mode === 'mic') {
        setMonitoringMic(true);
        if (micAudioRef.current) {
          attachParticipantAudio(room, bridgeIdentityRef.current, micAudioRef.current);
        }
      } else {
        setMonitoringSpeaker(true);
        if (speakerAudioRef.current && bridgeStatus.active_remote_participant_identity) {
          attachParticipantAudio(room, bridgeStatus.active_remote_participant_identity, speakerAudioRef.current);
        }
      }
    } catch (err) {
      setError(String(err));
    }
  };

  const handleMuteToggle = async () => {
    if (!audioBridgeRunning) {
      return;
    }

    setMuteLoading(true);
    try {
      setError(null);
      const status = await api.setAudioBridgeMuted(!bridgeStatus.muted);
      setBridgeStatus(status);
    } catch (err) {
      setError(String(err));
    } finally {
      setMuteLoading(false);
    }
  };

  const commitInputMicGain = (nextGainDb: number) => {
    if (inputGainTimerRef.current !== null) {
      window.clearTimeout(inputGainTimerRef.current);
    }

    inputGainTimerRef.current = window.setTimeout(async () => {
      inputGainTimerRef.current = null;
      try {
        setError(null);
        const status = await api.setAudioBridgeInputGain(nextGainDb);
        setBridgeStatus(status);
      } catch (err) {
        setError(String(err));
      }
    }, 100);
  };

  const handleInputMicGainChange = (rawValue: string) => {
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed)) {
      return;
    }

    const nextGainDb = Math.min(inputMicGainMax, Math.max(inputMicGainMin, parsed));
    setInputMicGainDb(nextGainDb);

    if (!audioBridgeRunning) {
      return;
    }

    commitInputMicGain(nextGainDb);
  };

  const handleOutputGainChange = (rawValue: string) => {
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed)) return;
    const nextValue = Math.min(outputGainMax, Math.max(outputGainMin, parsed));
    setOutputSpeakerGainDb(nextValue);
    if (!audioBridgeRunning) return;
    if (outputGainTimerRef.current !== null) window.clearTimeout(outputGainTimerRef.current);
    outputGainTimerRef.current = window.setTimeout(async () => {
      outputGainTimerRef.current = null;
      try {
        setBridgeStatus(await api.setAudioBridgeOutputGain(nextValue));
      } catch (err) {
        setError(String(err));
      }
    }, 100);
  };

  const handleClearSpeaker = async () => {
    setSpeakerLoading(true);
    try {
      setError(null);
      setBridgeStatus(await api.releaseAudioBridgeRemotePlayback());
    } catch (err) {
      setError(String(err));
    } finally {
      setSpeakerLoading(false);
    }
  };

  return (
    <div className="rounded-xl border border-border/60 bg-muted/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Audio</h3>
          <p className="text-xs text-muted-foreground">
            Control mic, speaker, and mute.
          </p>
        </div>
        {showControls && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void handleToggle('mic')}
              disabled={!audioBridgeRunning}
              className={`px-3 py-1.5 text-xs rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                monitoringMic ? 'bg-emerald-600 hover:bg-emerald-700 text-white' : 'bg-slate-600 hover:bg-slate-700 text-white'
              }`}
            >
              {monitoringMic ? 'Stop Mic Monitor' : 'Monitor Mic'}
            </button>
            <button
              type="button"
              onClick={() => void handleToggle('speaker')}
              disabled={!audioBridgeRunning}
              className={`px-3 py-1.5 text-xs rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                monitoringSpeaker ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-slate-600 hover:bg-slate-700 text-white'
              }`}
            >
              {monitoringSpeaker ? 'Stop Speaker Monitor' : 'Monitor Speaker'}
            </button>
            <button
              type="button"
              onClick={() => void handleMuteToggle()}
              disabled={!audioBridgeRunning || muteLoading}
              className={`px-3 py-1.5 text-xs rounded-full font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                bridgeStatus.muted ? 'bg-amber-600 hover:bg-amber-700 text-white' : 'bg-slate-600 hover:bg-slate-700 text-white'
              }`}
              title={!audioBridgeRunning ? 'Start Audio Bridge first' : undefined}
            >
              {muteLoading ? '...' : bridgeStatus.muted ? 'Unmute Mic' : 'Mute Mic'}
            </button>
            <button type="button" onClick={() => void handleClearSpeaker()} disabled={!audioBridgeRunning || speakerLoading} className="rounded-full bg-slate-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50">
              {speakerLoading ? 'Clearing...' : 'Clear Robot Speaker'}
            </button>
          </div>
        )}
      </div>

      <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <div>
          <span className="font-medium text-foreground">Bridge:</span>{' '}
          {bridgeIdentityRef.current}
        </div>
        <div>
          <span className="font-medium text-foreground">Speaker Source:</span>{' '}
          {bridgeStatus.active_remote_participant_identity ?? 'No active remote audio'}
        </div>
      </div>

      {showControls && (
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-border/60 bg-background/70 p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <label htmlFor="input-mic-gain" className="text-xs font-medium text-foreground">
              Input mic gain
            </label>
            <div className="flex items-center gap-2">
              <input
                id="input-mic-gain-number"
                type="number"
                min={inputMicGainMin}
                max={inputMicGainMax}
                step={INPUT_MIC_GAIN_STEP}
                value={inputMicGainDb}
                onChange={(event) => handleInputMicGainChange(event.target.value)}
                disabled={!audioBridgeRunning}
                className="h-8 w-20 rounded-md border border-border bg-background px-2 text-right text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              />
              <span className="text-xs text-muted-foreground">dB</span>
            </div>
          </div>
          <input
            id="input-mic-gain"
            type="range"
            min={inputMicGainMin}
            max={inputMicGainMax}
            step={INPUT_MIC_GAIN_STEP}
            value={inputMicGainDb}
            onChange={(event) => handleInputMicGainChange(event.target.value)}
            disabled={!audioBridgeRunning}
            className="w-full accent-emerald-600 disabled:cursor-not-allowed disabled:opacity-50"
          />
          <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
            <span>{inputMicGainMin} dB</span>
            <span>{inputMicGainMax} dB</span>
          </div>
        </div>
        <div className="rounded-lg border border-border/60 bg-background/70 p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <label htmlFor="output-speaker-gain" className="text-xs font-medium text-foreground">Speaker boost</label>
            <div className="flex items-center gap-2"><input type="number" min={outputGainMin} max={outputGainMax} step={0.5} value={outputSpeakerGainDb} onChange={(event) => void handleOutputGainChange(event.target.value)} disabled={!audioBridgeRunning} className="h-8 w-20 rounded-md border border-border bg-background px-2 text-right text-xs disabled:opacity-50" /><span className="text-xs text-muted-foreground">dB</span></div>
          </div>
          <input id="output-speaker-gain" type="range" min={outputGainMin} max={outputGainMax} step={0.5} value={outputSpeakerGainDb} onChange={(event) => void handleOutputGainChange(event.target.value)} disabled={!audioBridgeRunning} className="w-full accent-rose-600 disabled:opacity-50" />
          <div className="mt-1 flex justify-between text-[11px] text-muted-foreground"><span>{outputGainMin} dB</span><span>{outputGainMax} dB</span></div>
        </div>
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-lg border border-red-300/60 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {!audioBridgeRunning && (
        <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          Start Audio Bridge first.
        </div>
      )}

      {audioBridgeRunning && !bridgeStatus.active_remote_participant_identity && (
        <div className="mt-3 rounded-lg border border-blue-300/60 bg-blue-500/10 px-3 py-2 text-xs text-blue-800 dark:text-blue-200">
          Waiting for speaker audio.
        </div>
      )}

      <audio ref={micAudioRef} autoPlay playsInline hidden />
      <audio ref={speakerAudioRef} autoPlay playsInline hidden />
    </div>
  );
}

function findAudioTrack(room: Room, participantIdentity: string) {
  for (const participant of room.remoteParticipants.values()) {
    if (participant.identity !== participantIdentity) {
      continue;
    }

    for (const publication of participant.trackPublications.values()) {
      const track = publication.track;
      if (track?.kind === Track.Kind.Audio) {
        return track;
      }
    }
  }

  return null;
}

function attachParticipantAudio(room: Room, participantIdentity: string, element: HTMLAudioElement) {
  detachAudio(element);
  const track = findAudioTrack(room, participantIdentity);
  if (track) {
    track.attach(element);
  }
}

function detachAudio(element: HTMLAudioElement | null) {
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
