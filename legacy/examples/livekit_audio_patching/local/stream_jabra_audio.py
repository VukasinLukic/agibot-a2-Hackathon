#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "livekit",
#   "sounddevice",
#   "python-dotenv",
#   "asyncio",
#   "numpy",
# ]
# ///
"""
LiveKit bidirectional audio streaming using Jabra Speak2 55 device.

This script detects and uses the Jabra Speak2 55 for both microphone input and
speaker output. It will exit if the Jabra is not found.
"""
from __future__ import annotations

import os
import logging
import asyncio
import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from signal import SIGINT, SIGTERM
from livekit import rtc
import sounddevice as sd
import numpy as np

# Ensure repository root is on sys.path when running as a script
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from livekit_shared.auth import generate_token
from list_devices import list_audio_devices

import stream_audio as base_audio

load_dotenv()
# ensure LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET are set in your .env file
LIVEKIT_URL = os.environ.get("LIVEKIT_URL")
ROOM_NAME = os.environ.get("LIVEKIT_ROOM")


def detect_jabra_device(logger: logging.Logger):
    """
    Detect Jabra Speak2 55 device(s) and return device configurations.

    On Windows, the Jabra appears as two devices (input and output).
    On Linux, it typically appears as one device with both capabilities.
    """
    logger.info("=" * 60)
    logger.info("DETECTING JABRA SPEAK2 55 DEVICE")
    logger.info("=" * 60)

    logger.info("Available audio devices:")
    list_audio_devices()

    devices = sd.query_devices()
    jabra_hints = ["jabra", "speak2", "speak 2"]
    input_device = None
    output_device = None

    for i, device in enumerate(devices):
        name_lower = device["name"].lower()
        is_jabra = any(hint in name_lower for hint in jabra_hints)
        if not is_jabra:
            continue

        if device["max_input_channels"] > 0 and input_device is None:
            input_device = {
                "index": i,
                "name": device["name"],
                "sample_rate": int(device["default_samplerate"]),
                "channels": device["max_input_channels"],
            }
            logger.info(
                "Found Jabra INPUT: %s (index=%s, %s Hz)",
                device["name"],
                i,
                input_device["sample_rate"],
            )

        if device["max_output_channels"] > 0 and output_device is None:
            output_device = {
                "index": i,
                "name": device["name"],
                "sample_rate": int(device["default_samplerate"]),
                "channels": device["max_output_channels"],
            }
            logger.info(
                "Found Jabra OUTPUT: %s (index=%s, %s Hz)",
                device["name"],
                i,
                output_device["sample_rate"],
            )

    if input_device and output_device:
        logger.info("=" * 60)
        logger.info("JABRA SPEAK2 55 DETECTED")
        logger.info("INPUT Device: %s", input_device["name"])
        logger.info(
            "  Index: %s, Rate: %s Hz, Channels: %s",
            input_device["index"],
            input_device["sample_rate"],
            input_device["channels"],
        )
        logger.info("OUTPUT Device: %s", output_device["name"])
        logger.info(
            "  Index: %s, Rate: %s Hz, Channels: %s",
            output_device["index"],
            output_device["sample_rate"],
            output_device["channels"],
        )
        logger.info("=" * 60)
        return input_device, output_device

    logger.error("=" * 60)
    logger.error("JABRA SPEAK2 55 NOT FOUND")
    logger.error("Please ensure:")
    logger.error("  1. Jabra Speak2 55 is connected via USB")
    logger.error("  2. Device drivers are installed")
    logger.error("  3. Device is not in use by another application")
    logger.error(
        "  Found: Input=%s, Output=%s",
        "Yes" if input_device else "No",
        "Yes" if output_device else "No",
    )
    logger.error("=" * 60)
    return None, None


class JabraAudioStreamer(base_audio.AudioStreamer):
    def __init__(
        self,
        *,
        input_device: dict,
        output_device: dict,
        enable_aec: bool = True,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.input_device = input_device
        self.output_device = output_device
        self.sample_rate = input_device["sample_rate"]
        self.frame_samples = int(self.sample_rate * 0.01)
        self.blocksize = int(self.sample_rate * 0.1)

        # Update base module constants so inherited callbacks use the Jabra rate.
        base_audio.SAMPLE_RATE = self.sample_rate
        base_audio.FRAME_SAMPLES = self.frame_samples
        base_audio.BLOCKSIZE = self.blocksize

        super().__init__(enable_aec=enable_aec, loop=loop)
        self.input_device_name = input_device["name"]

    def start_audio_devices(self):
        """Initialize and start audio input/output devices using Jabra."""
        try:
            self.logger.info("=" * 60)
            self.logger.info("STARTING JABRA AUDIO DEVICES")
            self.logger.info("=" * 60)
            self.logger.info(
                "INPUT: %s (index=%s)",
                self.input_device["name"],
                self.input_device["index"],
            )
            self.logger.info(
                "  Sample Rate: %s Hz, Channels: %s",
                self.input_device["sample_rate"],
                base_audio.NUM_CHANNELS,
            )
            self.logger.info(
                "OUTPUT: %s (index=%s)",
                self.output_device["name"],
                self.output_device["index"],
            )
            self.logger.info(
                "  Sample Rate: %s Hz, Channels: %s",
                self.output_device["sample_rate"],
                base_audio.NUM_CHANNELS,
            )
            self.logger.info("Blocksize: %s", self.blocksize)

            self.input_stream = sd.InputStream(
                callback=self._input_callback,
                dtype="int16",
                channels=base_audio.NUM_CHANNELS,
                device=self.input_device["index"],
                samplerate=self.input_device["sample_rate"],
                blocksize=self.blocksize,
            )
            self.input_stream.start()
            self.logger.info(
                "Started Jabra microphone input on device %s",
                self.input_device["index"],
            )

            self.output_stream = sd.OutputStream(
                callback=self._output_callback,
                dtype="int16",
                channels=base_audio.NUM_CHANNELS,
                device=self.output_device["index"],
                samplerate=self.output_device["sample_rate"],
                blocksize=self.blocksize,
            )
            self.output_stream.start()
            self.logger.info(
                "Started Jabra speaker output on device %s",
                self.output_device["index"],
            )

            time.sleep(0.1)
            self.logger.info("Input stream active: %s", self.input_stream.active)
            self.logger.info("Output stream active: %s", self.output_stream.active)
            self.logger.info("=" * 60)
        except Exception as exc:
            self.logger.error("Failed to start Jabra audio devices: %s", exc)
            import traceback
            self.logger.error("Traceback: %s", traceback.format_exc())
            raise

    def _output_callback(self, outdata: np.ndarray, frame_count: int, time_info, status) -> None:
        """Output callback using the Jabra output sample rate."""
        # Reuse the base logic but override the render frame sample rate.
        self.output_callback_count += 1

        if status:
            self.logger.warning("Output callback status: %s", status)

        if self.output_callback_count <= 3:
            self.logger.info(
                "Output callback #%s: frame_count=%s, buffer_size=%s",
                self.output_callback_count,
                frame_count,
                len(self.output_buffer),
            )

        if not self.running:
            outdata.fill(0)
            return

        self.output_delay = time_info.outputBufferDacTime - time_info.currentTime

        with self.output_lock:
            bytes_needed = frame_count * 2
            if len(self.output_buffer) < bytes_needed:
                available_bytes = len(self.output_buffer)
                if available_bytes > 0:
                    outdata[: available_bytes // 2, 0] = np.frombuffer(
                        self.output_buffer[:available_bytes],
                        dtype=np.int16,
                        count=available_bytes // 2,
                    )
                    outdata[available_bytes // 2 :, 0] = 0
                    del self.output_buffer[:available_bytes]
                else:
                    outdata.fill(0)
            else:
                chunk = self.output_buffer[:bytes_needed]
                outdata[:, 0] = np.frombuffer(chunk, dtype=np.int16, count=frame_count)
                del self.output_buffer[:bytes_needed]

        if self.audio_processor:
            num_chunks = frame_count // base_audio.FRAME_SAMPLES
            for i in range(num_chunks):
                start = i * base_audio.FRAME_SAMPLES
                end = start + base_audio.FRAME_SAMPLES
                if end > frame_count:
                    break
                render_chunk = outdata[start:end, 0]
                render_frame = rtc.AudioFrame(
                    data=render_chunk.tobytes(),
                    samples_per_channel=base_audio.FRAME_SAMPLES,
                    sample_rate=self.output_device["sample_rate"],
                    num_channels=base_audio.NUM_CHANNELS,
                )
                try:
                    self.audio_processor.process_reverse_stream(render_frame)
                except Exception as exc:
                    if self.output_callback_count <= 10:
                        self.logger.warning("Error processing reverse stream with AEC: %s", exc)


async def main(participant_name: str, enable_aec: bool = True):
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("STARTING JABRA AUDIO STREAMER")
    logger.info("=" * 60)

    loop = asyncio.get_running_loop()

    logger.info("LIVEKIT_URL: %s", LIVEKIT_URL)
    logger.info("ROOM_NAME: %s", ROOM_NAME)

    if not LIVEKIT_URL or not ROOM_NAME:
        logger.error("Missing LIVEKIT_URL or ROOM_NAME environment variables")
        return

    input_device, output_device = detect_jabra_device(logger)
    if input_device is None or output_device is None:
        logger.error("Cannot proceed without Jabra Speak2 55 device")
        logger.error("Exiting...")
        return

    streamer = JabraAudioStreamer(
        input_device=input_device,
        output_device=output_device,
        enable_aec=enable_aec,
        loop=loop,
    )

    room = rtc.Room(loop=loop)
    streamer.room = room

    async def audio_processing_task():
        frames_sent = 0
        logger.info("Audio processing task started")
        while streamer.running:
            try:
                frame = await asyncio.wait_for(streamer.audio_input_queue.get(), timeout=1.0)
                await streamer.source.capture_frame(frame)
                frames_sent += 1
                if frames_sent <= 5:
                    logger.info("Sent frame %s to LiveKit source", frames_sent)
                elif frames_sent % 100 == 0:
                    logger.info("Sent %s frames total to LiveKit", frames_sent)
            except asyncio.TimeoutError:
                logger.debug("No audio frames in queue (timeout)")
                continue
            except Exception as exc:
                logger.error("Error in audio processing: %s", exc)
                break
        logger.info("Audio processing task ended. Total frames sent: %s", frames_sent)

    async def meter_task():
        logger.info("Meter task started")
        while streamer.running and streamer.meter_running:
            streamer.print_audio_meter()
            await asyncio.sleep(1 / base_audio.FPS)
        logger.info("Meter task ended")

    async def receive_audio_frames(stream: rtc.AudioStream, participant: rtc.RemoteParticipant):
        frames_received = 0
        logger.info("Audio receive task started")
        participant_id = participant.sid
        participant_name = participant.identity or f"User_{participant.sid[:8]}"
        logger.info("Receiving audio from participant: %s (%s)", participant_name, participant_id)

        async for frame_event in stream:
            if not streamer.running:
                break
            frames_received += 1
            if frames_received <= 5:
                logger.info("Received audio frame %s from %s", frames_received, participant_name)
            elif frames_received % 100 == 0:
                logger.info("Received %s frames total from %s", frames_received, participant_name)

            if streamer.active_remote_participant_id == participant_id and streamer.remote_playback_enabled:
                frame_data = frame_event.frame.data
                if len(frame_data) > 0:
                    audio_samples = np.frombuffer(frame_data, dtype=np.int16)
                    if len(audio_samples) > 0:
                        rms = np.sqrt(np.mean(audio_samples.astype(np.float32) ** 2))
                        max_int16 = np.iinfo(np.int16).max
                        participant_db = 20.0 * np.log10(rms / max_int16 + 1e-6)
                        with streamer.participants_lock:
                            streamer.participants[participant_id] = {
                                "name": participant_name,
                                "db_level": participant_db,
                                "last_update": time.time(),
                            }

                audio_data = frame_event.frame.data.tobytes()
                with streamer.output_lock:
                    streamer.output_buffer.extend(audio_data)

        logger.info(
            "Audio receive task ended for %s. Total frames received: %s",
            participant_name,
            frames_received,
        )
        with streamer.participants_lock:
            if participant_id in streamer.participants:
                del streamer.participants[participant_id]

    @room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        logger.info(
            "track subscribed: %s from participant %s (%s)",
            publication.sid,
            participant.sid,
            participant.identity,
        )
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            if streamer.active_remote_participant_id is None:
                streamer.active_remote_participant_id = participant.sid
                logger.info(
                    "Activating remote playback for first subscribed participant: %s",
                    participant.identity,
                )
                audio_stream = rtc.AudioStream(
                    track,
                    sample_rate=streamer.sample_rate,
                    num_channels=base_audio.NUM_CHANNELS,
                )
                asyncio.ensure_future(receive_audio_frames(audio_stream, participant))
            else:
                logger.info(
                    "Ignoring additional remote audio track from %s; active playback is participant %s",
                    participant.identity,
                    streamer.active_remote_participant_id,
                )

    @room.on("track_published")
    def on_track_published(
        publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
    ):
        logger.info(
            "track published: %s from participant %s (%s)",
            publication.sid,
            participant.sid,
            participant.identity,
        )

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info("participant connected: %s %s", participant.sid, participant.identity)
        with streamer.participants_lock:
            streamer.participants[participant.sid] = {
                "name": participant.identity or f"User_{participant.sid[:8]}",
                "db_level": base_audio.INPUT_DB_MIN,
                "last_update": time.time(),
            }
        logger.info("Added participant to tracking: %s", participant.identity)

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info("participant disconnected: %s %s", participant.sid, participant.identity)
        with streamer.participants_lock:
            if participant.sid in streamer.participants:
                del streamer.participants[participant.sid]
                logger.info("Removed participant from tracking: %s", participant.identity)
        if streamer.active_remote_participant_id == participant.sid:
            logger.info("Active remote participant disconnected; releasing playback and clearing buffer")
            streamer.active_remote_participant_id = None
            with streamer.output_lock:
                streamer.output_buffer.clear()

    @room.on("connected")
    def on_connected():
        logger.info("Successfully connected to LiveKit room")

    @room.on("disconnected")
    def on_disconnected(reason):
        logger.info("Disconnected from LiveKit room: %s", reason)

    try:
        logger.info("Starting Jabra audio devices...")
        streamer.start_audio_devices()

        logger.info("Starting keyboard handler...")
        streamer.start_keyboard_handler()

        streamer.init_terminal()

        logger.info("Connecting to LiveKit room...")
        token = generate_token(ROOM_NAME, participant_name, participant_name)
        logger.info("Generated token for participant: %s", participant_name)

        await room.connect(LIVEKIT_URL, token)
        logger.info("connected to room %s", room.name)

        logger.info("Publishing Jabra microphone track...")
        track = rtc.LocalAudioTrack.create_audio_track("jabra-mic", streamer.source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        publication = await room.local_participant.publish_track(track, options)
        logger.info("published track %s", publication.sid)

        if enable_aec:
            logger.info("Echo cancellation is enabled")
        else:
            logger.info("Echo cancellation is disabled")

        logger.info("Starting background tasks...")
        audio_task = asyncio.create_task(audio_processing_task())
        meter_display_task = asyncio.create_task(meter_task())

        logger.info("=" * 60)
        logger.info("JABRA AUDIO STREAMING ACTIVE")
        logger.info("Press 'm' to toggle mute, 'q' or Ctrl+C to stop")
        logger.info("=" * 60)

        try:
            while streamer.running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping audio streaming...")
    except Exception as exc:
        logger.error("Error in main: %s", exc)
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
    finally:
        logger.info("Starting cleanup...")
        streamer.running = False

        if "audio_task" in locals():
            audio_task.cancel()
            try:
                await audio_task
            except asyncio.CancelledError:
                pass

        if "meter_display_task" in locals():
            meter_display_task.cancel()
            try:
                await meter_display_task
            except asyncio.CancelledError:
                pass

        streamer.stop_audio_devices()
        streamer.stop_keyboard_handler()
        await room.disconnect()

        streamer.restore_terminal()
        logger.info("=" * 60)
        logger.info("CLEANUP COMPLETE")
        logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LiveKit bidirectional audio streaming with Jabra Speak2 55 and AEC"
    )
    parser.add_argument(
        "--name",
        "-n",
        type=str,
        default="audio-streamer",
        help="Participant name to use when connecting to the room (default: audio-streamer)",
    )
    parser.add_argument(
        "--disable-aec",
        action="store_true",
        help="Disable acoustic echo cancellation (AEC)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("stream_jabra_audio.log"),
            *([logging.StreamHandler()] if args.debug else []),
        ],
    )

    if args.debug:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        console_handler.setFormatter(formatter)

    async def cleanup():
        task = asyncio.current_task()
        tasks = [t for t in asyncio.all_tasks() if t is not task]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        main_task = asyncio.ensure_future(main(args.name, enable_aec=not args.disable_aec))

        def _schedule_cleanup() -> None:
            loop.call_soon_threadsafe(lambda: asyncio.create_task(cleanup()))

        def _install_signal_handlers() -> None:
            import signal as signal_module

            for sig in (SIGINT, SIGTERM):
                try:
                    loop.add_signal_handler(sig, _schedule_cleanup)
                except NotImplementedError:
                    signal_module.signal(sig, lambda *_: _schedule_cleanup())

        _install_signal_handlers()

        try:
            loop.run_until_complete(main_task)
        except KeyboardInterrupt:
            pass
        finally:
            loop.close()
    except KeyboardInterrupt:
        pass
