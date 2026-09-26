# Agibot A2 Ultra head screen

How the face display works and how to put our own text on it.

Verified live on the A2 Ultra on 2026-08-25.

## There is no "draw text" API

The screen has exactly one content channel: an **emoticon clip**. Everything the
face shows — the idle face, the wake-word prompt, the tic-tac-toe board — is an
`.mp4` played by id. There is no RPC that takes a string and draws it.

So "display text" means "render a clip and play it".

## The chain

```text
PlayerEmoticon(id)
  -> rc_module            x86  192.168.100.100:59001
  -> ResourceService/GetResource   orin 127.0.0.1:51049   (id -> file path)
  -> publish {emoticon_id, emoticon_path} on /rc/interaction/emoticonplayer  (mqtt)
  -> face app             x86, com.agibot.aimmaster.face
  -> its bundled ffmpeg decodes that path to rawvideo bgra
```

- The face app is a Flutter Linux binary, `/agibot/software/v0/aim-master/face_watchdog`.
  It shells out to its own ffmpeg at
  `/home/agi/.local/share/com.agibot.aimmaster.face/3rd_party/ffmpeg`.
- Clips are **800x480**, h264, `yuv420p`. That ffmpeg has `drawtext`
  (libfreetype) and `libopenh264`, so it can render our text itself — no
  encoder needs to be installed on the Orin.
- Watch what the face is showing right now:
  ```bash
  ssh -i ~/.ssh/agibot_rsa agi@192.168.100.100 \
    "ps -eo args --no-headers | grep aimmaster.face/3rd_party/ffmpeg | grep -v grep"
  ```

### Two filesystems

The Orin and the x86 each have their own `/agibot` (different disks). The face
app reads the **x86** copy. `ResourceService` runs on the **Orin** and validates
the **Orin** copy. That split is what makes the cheap update path work:

- the Orin copy stays frozen, so the resource manager's file/md5 check keeps
  passing and `PlayerEmoticon` keeps resolving;
- the x86 copy is overwritten per flash, and is what actually gets shown.

No re-registration per flash, no database churn.

## Gotchas

**Only `PlayerEmoticon` is implemented on the rc module.** Every other
`RcEmoticonPlayerService` method — `CreateEmoticon`, `EditEmoticon`,
`BeginSendEmoticonStream`, `DeleteEmoticon`, `GetEmoticonList`, … — returns
HTTP 500 `code: 1002` (handler registered, not implemented). Those belong to
`skillpilot`, which is not running on this robot (last log: March 2026). Use
`ResourceService` on the Orin for anything other than playback.

**A played clip loops forever.** It does not expire and it does not fall back to
the idle face. The default face has to be played again explicitly. The
configured default is:

```bash
curl -s -X POST http://127.0.0.1:51048/rpc/aimdk.protocol.RobotFunctionSettingService/ConfigControl \
  -H 'Content-Type: application/json' \
  -d '{"header":{},"Operation":"ConfigControlOperation_GetConfig",
       "configs":[{"key":"master.face.default_emoticon_path","value":"","extra":""}]}'
# -> /agibot/data/resources/default/emoticon/emoticon_prompt_words_en/emoticon.mp4
```

**Custom emoticon ids are offset by +10000.** A resource registered with
`resource_id: 901` is played as `10901`. `ResourceService/GetEmoticon` reports
the already-offset id; the `custom.db` row keeps the raw one.

**`CreateResource` installs from a staging directory.** The files must be at
`/agibot/data/resources/tmp/<name>/` **on the Orin** before the call, or it
returns success and then fails every read with `Resource file error` (1441808).
It moves them to `/agibot/data/resources/custom/emoticon/<name>/` and writes
`description.yaml`.

**`ffmpeg` needs `-nostdin`.** When the render script is piped to `bash -s` over
ssh, ffmpeg otherwise eats the remaining script lines off the shared stdin and
the shell never runs the final rename.

**Text must not be interpolated into the filtergraph.** A bare `:` in `10:30`
splits `drawtext`'s options. Use `textfile=` and write the string to a file.

## Built-in emoticons

31 of them, ids 1-31, all `is_change=false`. Full list:

```bash
curl -s -X POST http://127.0.0.1:51049/rpc/aimdk.protocol.ResourceService/GetEmoticon \
  -H 'Content-Type: application/json' -d '{"header":{}}'
```

Useful ones: `11` normal/idle, `21` thinking, `31` English wake-word prompt
(the current default face), `15` silence, `23` human voice input.

The two "prompt words" clips (`1`, `31`) are the only built-ins that show text,
and it is baked into the video — `31` reads `call me "Hi Luka"`. If the remote
control appeared to "display text", this is most likely what it played.

## Our usage

`robot_services/screen_manip/` owns this. Selection goes through
`humanoid_platform.get_head_screen_spec()` so a robot model without a head
screen backend disables itself cleanly.

```python
from robot_services.screen_manip import show_message, show_message_async

show_message("10:30", "Belgrade", duration_s=2.0)      # blocking
await show_message_async("10:30", "Belgrade")           # off the event loop
```

One reusable slot, `emoticon_ct_message`, is registered once (auto-provisioned
on first use) and its clip is rewritten per message. `provision_screen()` does
that registration up front and opens the multiplexed ssh connection to the face
host, which is worth doing at startup: a cold ssh handshake costs ~1.7s, a warm
one ~0.4s, against a ~0.2s render.

Text is sanitized to a safe character set and length-capped before it reaches
the renderer, because it can originate from an LLM tool call.

Config knobs: `HEAD_SCREEN_ENABLED`, `HEAD_SCREEN_FLASH_DURATION_S`. The
`ROBOT_ENABLE=0` / `AUDIO_TARGET=host` gate that no-ops gestures no-ops screen
flashes the same way.
