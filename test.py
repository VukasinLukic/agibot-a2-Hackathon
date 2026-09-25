import cv2
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
 
# Global variable to store the live frame across threads
latest_frame = None
 
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle client browser connections in separate threads."""
    daemon_threads = True
 
class MJPEGStreamHandler(BaseHTTPRequestHandler):
    """Serve the live camera frames as an MJPEG network stream."""
    def do_GET(self):
        global latest_frame
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
           
            while True:
                if latest_frame is not None:
                    # Encode the current matrix frame into raw JPEG data bytes
                    ret, jpeg = cv2.imencode('.jpg', latest_frame)
                    if ret:
                        try:
                            self.wfile.write(b'--frame\r\n')
                            self.send_header('Content-Type', 'image/jpeg')
                            self.send_header('Content-Length', str(len(jpeg)))
                            self.end_headers()
                            self.wfile.write(jpeg.tobytes())
                            self.wfile.write(b'\r\n')
                        except (ConnectionResetError, BrokenPipeError):
                            # Browser tab closed or refreshed
                            break
                # Limit stream sync to ~25 frames per second
                time.sleep(0.04)
 
print("Initializing the Orbbec HD Color stream on /dev/video8...")
cap = cv2.VideoCapture(2, cv2.CAP_V4L2)
 
if not cap.isOpened():
    print("Error: Could not establish a connection to /dev/video7.")
    sys.exit(1)
 
# Force high-definition MJPEG compression profiles
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
time.sleep(0.5)
 
# Spin up the background web server on Port 8089
server = ThreadedHTTPServer(('0.0.0.0', 8089), MJPEGStreamHandler)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
 
print("\n🚀 Live MJPEG Server listening on http://localhost:8089")
print("Warming up the camera sensor...")
 
for i in range(10):
    try: cap.read()
    except cv2.error: continue
 
print("Streaming active! Open the link in your browser. Press Ctrl+C to stop.")
 
try:
    while True:
        ret, frame = cap.read()
        if ret and frame is not None:
            flip_frame = cv2.flip(frame, -1)
            # Update the global buffer for the web server thread
            latest_frame = flip_frame
        else:
            time.sleep(0.01)
except KeyboardInterrupt:
    print("\nShutting down stream...")
 
cap.release()
server.shutdown()
sys.exit(0)
 