# Camera Share

Share one physical Linux camera with multiple programs by mirroring it into a
single `v4l2loopback` virtual camera.

Consumers should read from:

```text
/dev/video10
```

Do not point multiple services directly at the physical camera, such as
`/dev/video0`.

## How It Works

```text
/dev/video0 physical camera
  |
  v
camera-share.service
  |
  v
/dev/video10 virtual shared camera
  |
  +-- service A
  +-- service B
  +-- service C
```

Only `camera-share.service` opens the physical camera. Other applications open
the virtual camera.

## Files

```text
camera_share/
  README.md
  camera_share.sh       Runtime ffmpeg bridge from physical to virtual camera
  install_deps.sh       Debian/Ubuntu/Raspberry Pi OS dependency installer
  install.sh            Installs the service and v4l2loopback config
  systemd_entry.txt     Source for /etc/systemd/system/camera-share.service
```

## Install

Add permisions:

```bash
chmod +x camera_share/install.sh
chmod +x camera_share/install_deps.sh
```

Run this on the Linux machine that owns the camera:

```bash
camera_share/install_deps.sh
camera_share/install.sh
```

`install_deps.sh` supports Debian, Ubuntu, and Raspberry Pi OS. It installs:

```text
v4l2loopback-dkms
v4l-utils
ffmpeg
matching kernel headers for DKMS
```

`install.sh` installs the runtime script to:

```text
/opt/camera-share/camera-share.sh
```

It installs the systemd unit to:

```text
/etc/systemd/system/camera-share.service
```

It also creates persistent v4l2loopback config for `/dev/video10`.

## Runtime Config

The installer creates `/etc/default/camera-share` if it does not already exist:

```bash
CAMERA_SRC=/dev/video0
CAMERA_OUT=/dev/video10
WIDTH=960
HEIGHT=540
FPS=30
INPUT_FORMAT=mjpeg
```

For production, prefer a stable physical camera path instead of `/dev/video0`.
List stable paths with:

```bash
ls -l /dev/v4l/by-id/
ls -l /dev/v4l/by-path/
```

Then edit:

```bash
sudo nano /etc/default/camera-share
```

Example:

```bash
CAMERA_SRC=/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_12345678-video-index0
CAMERA_OUT=/dev/video10
WIDTH=960
HEIGHT=540
FPS=30
INPUT_FORMAT=mjpeg
```

Restart after config changes:

```bash
sudo systemctl restart camera-share.service
```

## Resolution And FPS Rule

`/dev/video10` is one shared virtual stream. Its resolution and source FPS come
from `camera-share.service`, specifically `WIDTH`, `HEIGHT`, and `FPS` in
`/etc/default/camera-share`.

Every consumer reading `/dev/video10` should request that same resolution. Do
not configure the shared camera to produce `960x540` while a consumer requests
`1280x720`; the consumer should be treated as reading the producer's actual
frame size.

FPS can be lower downstream. For example, the shared stream can run at 30 FPS
for vision, while Camera Bridge publishes to LiveKit at 15 FPS.

## Check Status

```bash
systemctl status camera-share.service
journalctl -u camera-share.service -f
```

Verify the physical camera:

```bash
v4l2-ctl --list-devices
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

Verify the virtual camera:

```bash
ls -l /dev/video10
v4l2-ctl --device=/dev/video10 --all
ffplay /dev/video10
```

## Low-Resource Settings

Start conservative on weak hardware:

```bash
WIDTH=640
HEIGHT=480
FPS=10
INPUT_FORMAT=mjpeg
```

Then increase resolution or FPS only if needed.

## Robot Supervisor Consumers

The current shared camera default is `960x540@30`. Camera bridge can still
publish to LiveKit at a lower FPS, while vision can consume the full 30 FPS
virtual stream.

When using this shared camera, both camera consumers should read `/dev/video10`:

```yaml
camera-bridge:
  device: /dev/video10
  resolution: 960x540
  framerate: 15

vision-controller:
  camera_id: /dev/video10
  camera_resolution: 960x540
  camera_fps: 30
  detection_fps: 30
```

This keeps the source stream fast enough for detection without forcing the
LiveKit camera bridge to publish all 30 frames per second.

## Operations

Restart:

```bash
sudo systemctl restart camera-share.service
```

Stop:

```bash
sudo systemctl stop camera-share.service
```

Disable:

```bash
sudo systemctl disable --now camera-share.service
```

Reload v4l2loopback after changing module options:

```bash
sudo systemctl stop camera-share.service
sudo modprobe -r v4l2loopback
sudo modprobe v4l2loopback
sudo systemctl start camera-share.service
```

## Troubleshooting

If `/dev/video10` does not exist:

```bash
lsmod | grep v4l2loopback
sudo modprobe v4l2loopback
dmesg | grep -i v4l2loopback
```

If `ffmpeg` reports an unsupported input format, check the camera:

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

Then update `/etc/default/camera-share`. For example:

```bash
INPUT_FORMAT=yuyv422
```

If consumers cannot open `/dev/video10`, check permissions:

```bash
ls -l /dev/video10
groups
```

On many distros, the consuming user or service user must be in the `video`
group.
