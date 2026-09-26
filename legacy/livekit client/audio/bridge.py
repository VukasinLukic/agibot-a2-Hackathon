from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Protocol

from livekit import rtc

from .config import LiveKitBridgeConfig
from .livekit_utils import build_token, participant_matches_target

LOG = logging.getLogger("audio_bridge")


class AudioInputSource(Protocol):
    sample_rate: int
    num_channels: int
    samples_per_frame: int

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def frames(self) -> AsyncIterator[bytes]: ...


class AudioOutputSink(Protocol):
    sample_rate: int
    num_channels: int

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def start_stream(self, label: str) -> None: ...

    async def stop_stream(self) -> None: ...

    async def feed(self, payload: bytes) -> None: ...


class LiveKitAudioBridge:
    def __init__(
        self,
        cfg: LiveKitBridgeConfig,
        *,
        input_source: AudioInputSource,
        output_sink: AudioOutputSink,
    ) -> None:
        self._cfg = cfg
        self._input = input_source
        self._output = output_sink
        self._room = rtc.Room()
        self._stop_event = asyncio.Event()
        self._input_task: asyncio.Task | None = None
        self._active_task: asyncio.Task | None = None
        self._active_label: str | None = None
        self._audio_source: rtc.AudioSource | None = None

    async def start(self) -> None:
        token = build_token(
            room=self._cfg.room_name,
            identity=self._cfg.identity,
            api_key=self._cfg.api_key,
            api_secret=self._cfg.api_secret,
        )

        @self._room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            if self._cfg.target_participant:
                if not participant_matches_target(participant, self._cfg.target_participant):
                    LOG.info(
                        "Ignoring track from %s (target mismatch)",
                        participant.identity,
                    )
                    return
            else:
                if self._cfg.ignore_tracks and publication.name in self._cfg.ignore_tracks:
                    LOG.info(
                        "Ignoring track '%s' from %s (ignore list)",
                        publication.name,
                        participant.identity,
                    )
                    return
            if self._active_task:
                LOG.info(
                    "Already relaying %s; skipping %s",
                    self._active_label,
                    participant.identity,
                )
                return
            LOG.info(
                "Subscribing to audio from %s (%s)",
                participant.identity,
                publication.name,
            )
            self._active_label = participant.identity or participant.sid
            self._active_task = asyncio.create_task(self._relay_audio(track, participant))

        @self._room.on("participant_disconnected")
        def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            if self._active_label and participant.identity == self._active_label:
                LOG.info("Active participant %s disconnected", participant.identity)
                if self._active_task:
                    self._active_task.cancel()

        @self._room.on("disconnected")
        def _on_disconnected(reason: rtc.DisconnectReason | None = None) -> None:
            LOG.info("LiveKit disconnected (%s)", reason)
            self._stop_event.set()

        await self._room.connect(self._cfg.url, token)
        LOG.info(
            "Audio bridge connected to room %s as %s",
            self._cfg.room_name,
            self._cfg.identity,
        )

        self._audio_source = rtc.AudioSource(
            self._input.sample_rate,
            self._input.num_channels,
        )
        track = rtc.LocalAudioTrack.create_audio_track(
            self._cfg.mic_track_name,
            self._audio_source,
        )
        publish_opts = rtc.TrackPublishOptions()
        publish_opts.source = rtc.TrackSource.SOURCE_MICROPHONE
        await self._room.local_participant.publish_track(track, publish_opts)
        LOG.info("Published mic track '%s'", self._cfg.mic_track_name)

        await self._output.start()
        await self._input.start()
        self._input_task = asyncio.create_task(self._pump_input())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._active_task:
            self._active_task.cancel()
            await asyncio.gather(self._active_task, return_exceptions=True)
            self._active_task = None

        if self._input_task:
            self._input_task.cancel()
            await asyncio.gather(self._input_task, return_exceptions=True)
            self._input_task = None

        await self._input.stop()
        await self._output.stop()

        if self._room.isconnected():
            await self._room.disconnect()

    async def _pump_input(self) -> None:
        if not self._audio_source:
            return
        try:
            async for frame_bytes in self._input.frames():
                if self._stop_event.is_set():
                    break
                audio_frame = rtc.AudioFrame(
                    data=frame_bytes,
                    sample_rate=self._input.sample_rate,
                    num_channels=self._input.num_channels,
                    samples_per_channel=self._input.samples_per_frame,
                )
                await self._audio_source.capture_frame(audio_frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("Audio input error: %s", exc)
            self._stop_event.set()

    async def _relay_audio(
        self,
        track: rtc.Track,
        participant: rtc.RemoteParticipant,
    ) -> None:
        label = participant.identity or participant.sid
        await self._output.start_stream(label)
        audio_stream = rtc.AudioStream(
            track,
            sample_rate=self._output.sample_rate,
            num_channels=self._output.num_channels,
        )
        try:
            async for frame_event in audio_stream:
                await self._output.feed(frame_event.frame.data.tobytes())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("Relay error (%s): %s", label, exc)
        finally:
            await self._output.stop_stream()
            self._active_task = None
            self._active_label = None
