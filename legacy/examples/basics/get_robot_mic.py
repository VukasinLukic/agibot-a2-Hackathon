import socket
import struct
import wave
import time

MCAST_GRP = "239.168.123.161"
MCAST_PORT = 5555

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2  # 16-bit
RECORD_SECONDS = 5

# Bind UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", MCAST_PORT))

LOCAL_IFACE_IP = "192.168.123.50"  # <-- your PC IP on the G1 network
mreq = struct.pack("4s4s", socket.inet_aton(MCAST_GRP), socket.inet_aton(LOCAL_IFACE_IP))
sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

frames = bytearray()
target_bytes = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH_BYTES * RECORD_SECONDS

print("Recording multicast mic audio...")
start = time.time()
while len(frames) < target_bytes:
    data, _ = sock.recvfrom(65535)
    frames.extend(data)

print(f"Done in {time.time()-start:.2f}s, bytes={len(frames)}")

# Write WAV
with wave.open("g1_mic.wav", "wb") as wf:
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(SAMPLE_WIDTH_BYTES)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(frames)

print("Saved: g1_mic.wav")
