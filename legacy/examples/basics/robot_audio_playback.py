import argparse
import subprocess
import sys
import time
from datetime import datetime

# Unitree SDK2 Python (install from unitree_sdk2_python)

import os

# MUST be set before importing unitree_sdk2py / cyclonedds through it
os.environ["CYCLONEDDS_URI"] = r"""
<CycloneDDS>
  <Domain>
    <Tracing>
      <Verbosity>warning</Verbosity>
      <OutputFile>stdout</OutputFile>
    </Tracing>
  </Domain>
</CycloneDDS>
"""


from unitree_sdk2py.core.channel import ChannelFactory
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient


# Robot expects: 16kHz, mono, 16-bit PCM (little endian)
SAMPLE_RATE = 16000
CHANNELS = 1
BYTES_PER_SAMPLE = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE


def ok(ret) -> bool:
    # Unitree SDK2py sometimes returns int, sometimes (int, str)
    if isinstance(ret, tuple):
        return int(ret[0]) == 0
    return int(ret) == 0


def which_or_die(cmd: str, explicit_path: str | None = None) -> str:
    import shutil
    if explicit_path:
        if os.path.isfile(explicit_path):
            return explicit_path
        raise RuntimeError(f"Executable not found at: {explicit_path}")

    p = shutil.which(cmd)
    if not p:
        raise RuntimeError(
            f"Missing dependency '{cmd}'. Install it or pass --{cmd} <path> (for ffmpeg use --ffmpeg)."
        )
    return p



def start_youtube_pcm_pipe(youtube_url: str, ffmpeg_path: str | None, ytdlp_path: str | None):
    """
    yt-dlp -> ffmpeg -> raw PCM s16le @ 16kHz mono on stdout
    """
    yt_dlp = which_or_die("yt-dlp", ytdlp_path)
    ffmpeg = which_or_die("ffmpeg", ffmpeg_path)


    # yt-dlp writes media to stdout
    ytdlp_cmd = [
        yt_dlp,
        "-f", "bestaudio/best",
        "-o", "-",                 # output to stdout
        youtube_url,
    ]

    # ffmpeg reads from stdin and writes raw PCM to stdout
    ffmpeg_cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-i", "pipe:0",
        "-vn",
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "pipe:1",
    ]

    # Use larger buffer for smoother streaming (64KB)
    ytdlp_proc = subprocess.Popen(
        ytdlp_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=65536,
    )

    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=ytdlp_proc.stdout,      # type: ignore[arg-type]
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=65536,
    )

    # Important: allow ytdlp_proc to receive SIGPIPE if ffmpeg exits
    if ytdlp_proc.stdout:
        ytdlp_proc.stdout.close()

    return ytdlp_proc, ffmpeg_proc


def kill_process_tree(*procs):
    for p in procs:
        try:
            if p and p.poll() is None:
                p.terminate()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="Stream YouTube audio to Unitree G1 speaker via AudioClient.PlayStream()")
    ap.add_argument("--iface", required=True, help="Network interface name for Unitree ChannelFactory (e.g. eth0/enp3s0). On Windows, use your adapter name if supported.")
    ap.add_argument("--url", required=True, help="YouTube URL")
    ap.add_argument("--app", default="yt_player", help="app_name for PlayStream/PlayStop")
    ap.add_argument("--chunk-ms", type=int, default=500, help="PCM chunk size in milliseconds (default 500ms, larger = smoother but more latency)")
    ap.add_argument("--prebuffer-ms", type=int, default=1000, help="Pre-buffer this many milliseconds before starting playback (default 1000ms)")
    ap.add_argument("--volume", type=int, default=None, help="Optional: set volume 0-100 before playing")
    ap.add_argument("--ffmpeg", default=None, help="Path to ffmpeg executable (optional). If not set, uses PATH.")
    ap.add_argument("--yt-dlp", dest="ytdlp", default=None, help="Path to yt-dlp executable (optional). If not set, uses PATH.")

    args = ap.parse_args()

    chunk_bytes = int(BYTES_PER_SECOND * (args.chunk_ms / 1000.0))
    # align to 2 bytes (int16)
    chunk_bytes = max(320, (chunk_bytes // 2) * 2)

    prebuffer_bytes = int(BYTES_PER_SECOND * (args.prebuffer_ms / 1000.0))
    prebuffer_bytes = max(320, (prebuffer_bytes // 2) * 2)

    # Init Unitree RPC channel + AudioClient
    cf = ChannelFactory()
    cf.Init(0, args.iface)

    client = AudioClient()
    client.Init()
    client.SetTimeout(10.0)

    if args.volume is not None:
        v = max(0, min(100, args.volume))
        code = client.SetVolume(v)
        print(f"SetVolume({v}) -> {code}")

    stream_id = datetime.now().strftime("%Y%m%d%H%M%S%f")  # keep constant to avoid interruptions
    print(f"Starting YouTube -> PCM pipeline. stream_id={stream_id}, chunk={chunk_bytes} bytes (~{args.chunk_ms}ms)")

    ytdlp_proc = ffmpeg_proc = None
    total_sent = 0
    t0 = time.time()

    try:
        ytdlp_proc, ffmpeg_proc = start_youtube_pcm_pipe(args.url, args.ffmpeg, args.ytdlp)
        if not ffmpeg_proc.stdout:
            raise RuntimeError("ffmpeg stdout pipe not available")

        # Pre-buffer data before starting playback to smooth out network delays
        print(f"Pre-buffering {args.prebuffer_ms}ms of audio...")
        prebuffer_data = bytearray()
        while len(prebuffer_data) < prebuffer_bytes:
            data = ffmpeg_proc.stdout.read(prebuffer_bytes - len(prebuffer_data))
            if not data:
                raise RuntimeError("Stream ended during pre-buffering")
            prebuffer_data.extend(data)
        print(f"Pre-buffered {len(prebuffer_data)} bytes. Starting playback...")

        # Send pre-buffered data immediately
        ret = client.PlayStream(args.app, stream_id, list(prebuffer_data))
        if not ok(ret):
            print(f"PlayStream error ret={ret}; stopping.")
        else:
            total_sent += len(prebuffer_data)
            # Start timing from after pre-buffer
            t0 = time.time()
            last_chunk_time = t0

            while True:
                chunk = ffmpeg_proc.stdout.read(chunk_bytes)
                if not chunk:
                    # End of stream or pipeline error
                    break

                # PlayStream expects bytes-like PCM data (16k, mono, 16-bit)
                ret = client.PlayStream(args.app, stream_id, list(chunk))
                if not ok(ret):
                    print(f"PlayStream error ret={ret}; stopping.")
                    break

                total_sent += len(chunk)

                # Improved pacing: track actual time and adjust sleep to maintain real-time
                chunk_duration = len(chunk) / BYTES_PER_SECOND
                now = time.time()
                elapsed = now - last_chunk_time
                sleep_time = chunk_duration - elapsed

                if sleep_time > 0:
                    time.sleep(sleep_time)
                elif sleep_time < -0.1:  # More than 100ms behind
                    print(f"Warning: falling behind by {abs(sleep_time):.3f}s")

                last_chunk_time = time.time()

                # Periodic progress
                if int(time.time() - t0) % 5 == 0:
                    secs = total_sent / BYTES_PER_SECOND
                    print(f"sent ~{secs:.1f}s audio")

        # Stop playback
        print("Stopping playback...")
        client.PlayStop(args.app)

    except KeyboardInterrupt:
        print("Interrupted. Stopping playback...")
        try:
            client.PlayStop(args.app)
        except Exception:
            pass
    finally:
        kill_process_tree(ffmpeg_proc, ytdlp_proc)

        # If there were errors, print stderr to help debug
        if ytdlp_proc and ytdlp_proc.stderr:
            err = ytdlp_proc.stderr.read().decode("utf-8", errors="replace").strip()
            if err:
                print("\n--- yt-dlp stderr ---\n", err, file=sys.stderr)

        if ffmpeg_proc and ffmpeg_proc.stderr:
            err = ffmpeg_proc.stderr.read().decode("utf-8", errors="replace").strip()
            if err:
                print("\n--- ffmpeg stderr ---\n", err, file=sys.stderr)


if __name__ == "__main__":
    main()