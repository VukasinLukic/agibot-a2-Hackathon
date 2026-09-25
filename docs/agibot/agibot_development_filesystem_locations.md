# Robot Custom Code Locations

## A2 Ultra

Run custom/high-level developer code on the **ORIN** computer.

Recommended location:

```bash id="w8pjhv"
/agibot/data/home/agi/Desktop/<project_name>
```

Example:

```bash id="nmzjn9"
/agibot/data/home/agi/Desktop/my_app
```

For Docker-based projects, keep Docker data under:

```bash id="tte2wn"
/agibot/data/home/agi/Desktop/docker
```

Avoid using system or robot-control locations for app code:

```bash id="g2v7qf"
/agibot/software
/opt/ros
/var/lib/docker
```

Do not run normal high-level custom code on the x86 motion-control computer unless specifically doing vendor-approved low-level motion-control development.

---

## X2 Ultra — PC2 / Development Computing Unit

Use **PC2** as the default place for custom AimDK / ROS / application code.

PC2 is the normal secondary-development machine.

Login user is typically:

```bash id="edaa4n"
run
```

Recommended project location:

```bash id="4f4n22"
$HOME/Desktop/<project_name>
```

On X2, `$HOME` may resolve to:

```bash id="oi458t"
/agibot/data/home/agi
```

So the practical full path is usually:

```bash id="i61xya"
/agibot/data/home/agi/Desktop/<project_name>
```

Example SDK/app layout:

```bash id="q4nmqo"
/agibot/data/home/agi/Desktop/my_x2_app/aimdk
```

Use `$HOME` in scripts when possible:

```bash id="lvt7k4"
mkdir -p "$HOME/Desktop/my_x2_app"
cd "$HOME/Desktop/my_x2_app"
```

Avoid placing SDK/app code directly under:

```bash id="lzrbrc"
$HOME/aimdk*
```

Avoid using **PC1**, the Motion Control Computing Unit, as a build or runtime environment for custom developer code.

---

## X2 Ultra — PC3 / Interaction Computing Unit

Use **PC3** when the custom code needs direct access to interaction-side hardware or services, especially audio input/output devices.

PC3 is not the default place for general AimDK application code, but it is appropriate for:

```bash id="j3bqz0"
custom audio device control
custom microphone/speaker handling
custom voice-system integration
low-level ALSA/PulseAudio/PipeWire/audio-device experiments
small local audio bridge or daemon processes
```

PC3 address:

```bash id="1h66ye"
10.0.1.42
```

Recommended code location on PC3:

```bash id="7l2b29"
$HOME/Desktop/<project_name>
```

If `$HOME` resolves the same way as PC2, this will typically be:

```bash id="pb40hu"
/agibot/data/home/agi/Desktop/<project_name>
```

Before choosing the final PC3 path, verify the actual user and home directory:

```bash id="4rjs4d"
whoami
echo "$HOME"
pwd
readlink -f ~
df -h "$HOME"
```

Preferred PC3 layout:

```bash id="j1ejvi"
$HOME/Desktop/audio_agent
$HOME/Desktop/audio_agent/src
$HOME/Desktop/audio_agent/logs
$HOME/Desktop/audio_agent/.venv
```

Avoid putting long-running custom code in temporary or system locations:

```bash id="fp3duh"
/tmp
/var/tmp
/agibot/software
/opt/ros
/usr
```

Use `/var/tmp` only for temporary runtime data, test files, sockets, or logs that can be regenerated.

Recommended architecture for X2:

```bash id="xjz9gr"
PC2: main application / orchestration / AimDK robot control
PC3: small audio-local process for direct audio device control
PC1: avoid for custom code
```

If the built-in interaction module occupies the audio streams, disable or reconfigure it using the vendor-provided interaction-mode procedure before running your own voice/audio stack.

---

## X2 Ultra — Practical Rule

Use this split:

```bash id="ys96lu"
# General robot app code
PC2:$HOME/Desktop/<project_name>

# Audio-device-specific code
PC3:$HOME/Desktop/<project_name>

# Do not use for secondary-development runtime
PC1
```

For PC3 audio work, prefer a small service/daemon that exposes a simple interface back to the PC2 application, instead of moving the entire application stack onto PC3.
