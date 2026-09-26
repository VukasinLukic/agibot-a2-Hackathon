"""Show arbitrary text on the AgiBot A2 Ultra head screen.

The head screen has no "draw text" API. Its only content channel is an
emoticon clip: ``RcEmoticonPlayerService/PlayerEmoticon`` resolves an emoticon
id to a file path and publishes it on ``/rc/interaction/emoticonplayer``, and
the face app (``com.agibot.aimmaster.face``, running on the x86) decodes that
file. So "display text" means "render a clip and play it".

To keep that cheap we register one reusable custom emoticon slot and then only
rewrite the clip behind it. The registered copy on the Orin stays frozen so the
resource manager's own file check keeps passing; the copy the face app actually
decodes lives on the x86 and is what gets overwritten per flash.

See ``docs/agibot/head_screen.md`` for the full mechanism.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from robot_services.gestures.robot_enable import robot_enabled

LOG = logging.getLogger("agibot_head_screen")

_DEFAULT_RC_URL = "http://192.168.100.100:59001"
_DEFAULT_RESOURCE_SERVICE_URL = "http://127.0.0.1:51049"
_DEFAULT_SETTING_SERVICE_URL = "http://127.0.0.1:51048"
_DEFAULT_FACE_HOST = "192.168.100.100"
_DEFAULT_FFMPEG = "/home/agi/.local/share/com.agibot.aimmaster.face/3rd_party/ffmpeg"
_DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Fallbacks if the configured default face cannot be resolved: the English
# wake-word prompt, then the generic "normal" face.
_FALLBACK_DEFAULT_EMOTICON_IDS = (31, 11)

_DEFAULT_EMOTICON_PATH_CONFIG_KEY = "master.face.default_emoticon_path"

# Anything outside this set is dropped before the text reaches the renderer.
# The text can originate from an LLM tool call, so it is never trusted.
_SAFE_TEXT_RE = re.compile(r"[^0-9A-Za-z \-:.,'+/()!?%°]")
_MAX_PRIMARY_CHARS = 24
_MAX_SECONDARY_CHARS = 32


@dataclass(frozen=True)
class AgibotHeadScreenConfig:
    """Where the head screen lives and how to draw on it."""

    rc_url: str = _DEFAULT_RC_URL
    resource_service_url: str = _DEFAULT_RESOURCE_SERVICE_URL
    setting_service_url: str = _DEFAULT_SETTING_SERVICE_URL

    # The face app runs on the x86 motor controller, on its own filesystem, so
    # the clip it decodes has to be written over there.
    face_host: str = _DEFAULT_FACE_HOST
    face_ssh_user: str = "agi"
    face_ssh_key: str = "~/.ssh/agibot_rsa"
    ffmpeg_path: str = _DEFAULT_FFMPEG
    font_path: str = _DEFAULT_FONT
    video_encoder: str = "libopenh264"

    slot_name: str = "emoticon_ct_message"
    slot_resource_id: int = 901
    slot_display_name: str = "CT message"

    # Emoticon clips on this robot are 800x480.
    frame_width: int = 800
    frame_height: int = 480
    fps: int = 25

    background_color: str = "black"
    primary_color: str = "0x35D6F5"
    secondary_color: str = "0x9FE8FF"
    primary_font_size: int = 170
    secondary_font_size: int = 46

    request_timeout_s: float = 5.0
    render_timeout_s: float = 20.0
    ssh_connect_timeout_s: int = 5
    # A fresh ssh handshake per flash costs more than the render itself, so the
    # connection is multiplexed and kept warm between flashes. Must stay short:
    # the resolved socket path has a ~108 byte limit.
    ssh_control_path: str = "/tmp/.ssh-a2face-%C"
    ssh_control_persist_s: int = 300


class AgibotEmoticonScreenController:
    """Flashes short text on the A2 head screen, then restores the default face.

    A flash never auto-expires: ``PlayerEmoticon`` loops its clip forever, so
    the default face has to be played back explicitly. Overlapping flashes are
    serialised and only the newest one owns the restore.
    """

    def __init__(self, cfg: AgibotHeadScreenConfig | None = None) -> None:
        self.cfg = cfg or AgibotHeadScreenConfig()
        self._lock = threading.Lock()
        self._slot_emoticon_id: int | None = None
        self._provision_failed = False
        self._default_emoticon_id: int | None = None
        self._flash_generation = 0

    # ---------------------------------------------------------------- public

    def flash_text(
        self,
        primary: str,
        secondary: str = "",
        *,
        duration_s: float = 2.0,
    ) -> dict[str, Any]:
        """Render ``primary``/``secondary``, show it, then restore the face.

        Blocks for roughly ``duration_s`` plus render time, so callers on an
        event loop should hand this to a worker thread.
        """

        primary_text = _sanitize(primary, _MAX_PRIMARY_CHARS)
        secondary_text = _sanitize(secondary, _MAX_SECONDARY_CHARS)
        if not primary_text:
            return {"status": "skipped", "reason": "empty_text"}

        if not robot_enabled():
            LOG.info(
                "Head screen flash skipped (robot disabled). Would show %r / %r.",
                primary_text,
                secondary_text,
            )
            return {"status": "skipped", "reason": "robot_disabled"}

        emoticon_id = self._ensure_slot()
        if emoticon_id is None:
            return {"status": "skipped", "reason": "slot_unavailable"}

        with self._lock:
            self._flash_generation += 1
            generation = self._flash_generation

            try:
                self._render_clip(primary_text, secondary_text, duration_s)
            except Exception as exc:
                LOG.warning("Head screen render failed for %r: %s", primary_text, exc)
                return {"status": "error", "reason": f"render_failed: {exc}"}

            try:
                self._player_emoticon(emoticon_id)
            except Exception as exc:
                LOG.warning("Head screen play failed for %r: %s", primary_text, exc)
                return {"status": "error", "reason": f"play_failed: {exc}"}

        time.sleep(max(0.0, duration_s))

        # A newer flash took over while this one was on screen; let that one
        # own the restore so we don't cut it short.
        if generation != self._flash_generation:
            return {
                "status": "ok",
                "primary": primary_text,
                "secondary": secondary_text,
                "restored": False,
                "reason": "superseded",
            }

        restored = self.restore_default_face()
        return {
            "status": "ok",
            "primary": primary_text,
            "secondary": secondary_text,
            "emoticon_id": emoticon_id,
            "restored": restored,
        }

    def restore_default_face(self) -> bool:
        """Play the configured default face again."""

        default_id = self._resolve_default_emoticon_id()
        if default_id is None:
            LOG.warning("Cannot restore default face: no default emoticon id resolved.")
            return False
        try:
            self._player_emoticon(default_id)
        except Exception as exc:
            LOG.warning("Default face restore failed (id=%s): %s", default_id, exc)
            return False
        return True

    def provision(self) -> int | None:
        """Ensure the reusable emoticon slot exists. Returns its play id."""

        slot = self._ensure_slot()
        if slot is not None:
            self.warm_up()
        return slot

    def warm_up(self) -> bool:
        """Open the multiplexed ssh connection so the first flash isn't slow."""

        try:
            self._run_on_face_host("true", timeout_s=self.cfg.render_timeout_s)
        except Exception as exc:
            LOG.info("Head screen warm-up failed (first flash will be slower): %s", exc)
            return False
        return True

    # ------------------------------------------------------------- rendering

    def _render_clip(self, primary: str, secondary: str, duration_s: float) -> None:
        cfg = self.cfg
        out_path = self._slot_clip_path()
        work_dir = f"/tmp/{cfg.slot_name}_render"
        tmp_out = f"{work_dir}/clip.mp4"
        primary_file = f"{work_dir}/primary.txt"
        secondary_file = f"{work_dir}/secondary.txt"

        duration = max(0.5, float(duration_s))
        height = cfg.frame_height
        primary_size = self._fit_font_size(primary, cfg.primary_font_size)
        secondary_size = self._fit_font_size(secondary, cfg.secondary_font_size)

        # drawtext reads the strings from files so the text never has to survive
        # filtergraph escaping (a bare ':' in "10:30" would split the options).
        if secondary:
            layers = [
                self._drawtext(
                    secondary_file,
                    cfg.secondary_color,
                    secondary_size,
                    y=int(height * 0.25),
                ),
                self._drawtext(
                    primary_file,
                    cfg.primary_color,
                    primary_size,
                    y=int(height * 0.42),
                ),
            ]
        else:
            layers = [
                self._drawtext(
                    primary_file,
                    cfg.primary_color,
                    primary_size,
                    y=None,
                )
            ]
        video_filter = ",".join(layers)

        script = "\n".join(
            [
                "set -e",
                f"mkdir -p {shlex.quote(work_dir)}",
                f"printf '%s' {shlex.quote(primary)} > {shlex.quote(primary_file)}",
                f"printf '%s' {shlex.quote(secondary)} > {shlex.quote(secondary_file)}",
                " ".join(
                    [
                        shlex.quote(cfg.ffmpeg_path),
                        # -nostdin matters: without it ffmpeg reads the rest of this
                        # script off the shared stdin and the shell loses lines.
                        "-nostdin -y -hide_banner -loglevel error",
                        "-f lavfi -i",
                        shlex.quote(
                            f"color=c={cfg.background_color}:"
                            f"s={cfg.frame_width}x{cfg.frame_height}:"
                            f"r={cfg.fps}:d={duration:.2f}"
                        ),
                        "-vf",
                        shlex.quote(video_filter),
                        "-c:v",
                        shlex.quote(cfg.video_encoder),
                        "-pix_fmt yuv420p -movflags +faststart",
                        shlex.quote(tmp_out),
                    ]
                ),
                # The face app may be mid-read on the live clip; renaming into
                # place keeps it from ever seeing a half-written file.
                f"mv -f {shlex.quote(tmp_out)} {shlex.quote(out_path)}",
            ]
        )

        self._run_on_face_host(script, timeout_s=cfg.render_timeout_s)

    def _fit_font_size(self, text: str, base_size: int) -> int:
        """Shrink the font until the line fits the panel width.

        drawtext cannot wrap or scale to fit, so a long line would simply run
        off both edges. Estimated from DejaVu Sans Bold's average advance
        (~0.62em), which only has to be close enough to keep text on screen.
        """

        if not text:
            return base_size
        usable_width = self.cfg.frame_width * 0.94
        estimated = 0.62 * base_size * len(text)
        if estimated <= usable_width:
            return base_size
        return max(16, int(usable_width / (0.62 * len(text))))

    def _drawtext(self, textfile: str, color: str, size: int, *, y: int | None) -> str:
        y_expr = "(h-text_h)/2" if y is None else str(y)
        return ":".join(
            [
                "drawtext=fontfile=" + self.cfg.font_path,
                "textfile=" + textfile,
                "fontcolor=" + color,
                f"fontsize={size}",
                "x=(w-text_w)/2",
                f"y={y_expr}",
            ]
        )

    def _ssh_options(self) -> list[str]:
        cfg = self.cfg
        return [
            "-i",
            cfg.face_ssh_key,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={cfg.ssh_connect_timeout_s}",
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={cfg.ssh_control_path}",
            "-o",
            f"ControlPersist={cfg.ssh_control_persist_s}",
        ]

    def _run_on_face_host(self, script: str, *, timeout_s: float) -> str:
        cfg = self.cfg
        command = [
            "ssh",
            *self._ssh_options(),
            f"{cfg.face_ssh_user}@{cfg.face_host}",
            "bash -s",
        ]
        result = subprocess.run(
            command,
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"face host command failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    # ---------------------------------------------------------- provisioning

    def _slot_clip_path(self) -> str:
        return (
            f"/agibot/data/resources/custom/emoticon/{self.cfg.slot_name}/emoticon.mp4"
        )

    def _ensure_slot(self) -> int | None:
        if self._slot_emoticon_id is not None:
            return self._slot_emoticon_id
        if self._provision_failed:
            return None

        try:
            existing = self._find_registered_slot()
        except Exception as exc:
            LOG.warning("Emoticon slot lookup failed; head screen unavailable. %s", exc)
            self._provision_failed = True
            return None

        if existing is not None:
            self._slot_emoticon_id = existing
            LOG.info(
                "Head screen using existing emoticon slot '%s' (id=%s)",
                self.cfg.slot_name,
                existing,
            )
            return existing

        try:
            created = self._register_slot()
        except Exception as exc:
            LOG.warning("Emoticon slot registration failed; head screen unavailable. %s", exc)
            self._provision_failed = True
            return None

        if created is None:
            self._provision_failed = True
            return None

        self._slot_emoticon_id = created
        LOG.info(
            "Head screen registered emoticon slot '%s' (id=%s)",
            self.cfg.slot_name,
            created,
        )
        return created

    def _find_registered_slot(self) -> int | None:
        for emoticon in self._get_emoticons():
            if emoticon.get("emoticon_name") == self.cfg.slot_name:
                emoticon_id = emoticon.get("emoticon_id")
                return int(emoticon_id) if emoticon_id is not None else None
        return None

    def _register_slot(self) -> int | None:
        """Stage a placeholder clip and register it as a custom emoticon.

        ``CreateResource`` installs from a staging directory on this host, so
        the placeholder has to be rendered on the face host and copied back
        before the call. Only ever runs once per robot.
        """

        cfg = self.cfg
        staging_dir = f"/agibot/data/resources/tmp/{cfg.slot_name}"
        remote_dir = f"/tmp/{cfg.slot_name}_provision"

        # Render the placeholder plus the cover/thumbnail the resource manager
        # expects, then read them back over the same ssh transport.
        colour = f"color=c={cfg.background_color}:s={cfg.frame_width}x{cfg.frame_height}:r={cfg.fps}:d=2"
        ffmpeg = shlex.quote(cfg.ffmpeg_path)
        script = "\n".join(
            [
                "set -e",
                f"mkdir -p {shlex.quote(remote_dir)}",
                f"{ffmpeg} -nostdin -y -hide_banner -loglevel error -f lavfi -i {shlex.quote(colour)} "
                f"-c:v {shlex.quote(cfg.video_encoder)} -pix_fmt yuv420p -movflags +faststart "
                f"{shlex.quote(remote_dir + '/emoticon.mp4')}",
                f"cp -f {shlex.quote(remote_dir + '/emoticon.mp4')} "
                f"{shlex.quote(remote_dir + '/thumbnail.mp4')}",
                f"{ffmpeg} -nostdin -y -hide_banner -loglevel error -i "
                f"{shlex.quote(remote_dir + '/emoticon.mp4')} -frames:v 1 "
                f"{shlex.quote(remote_dir + '/cover.png')}",
                f"md5sum {shlex.quote(remote_dir + '/emoticon.mp4')} | cut -d' ' -f1",
            ]
        )
        md5 = self._run_on_face_host(script, timeout_s=cfg.render_timeout_s).strip()
        if not md5:
            raise RuntimeError("placeholder md5 not reported by face host")

        self._copy_back_from_face_host(remote_dir, staging_dir)

        payload = {
            "header": {},
            "resource": {
                "resource_id": cfg.slot_resource_id,
                "resource_name": cfg.slot_name,
                "duration": 2000,
                "resource_type": "RESOURCE_TYPE_EMOTICON",
                "resource_path": self._slot_clip_path(),
                "source": "custom",
                "charge_type": "CHARGE_TYPE_FREE",
                "is_change": True,
                "md5": md5,
                "description": "Reusable slot for agent-rendered head screen text",
                "tags": [],
                "scenes": [],
                "emoticon_extra_info": {
                    "display_name": {
                        "en_US": cfg.slot_display_name,
                        "zh_CN": cfg.slot_display_name,
                    },
                    "emoticon_file_url": f"custom/emoticon/{cfg.slot_name}/emoticon.mp4",
                    "thumbnail_file_url": f"custom/emoticon/{cfg.slot_name}/thumbnail.mp4",
                    "cover_file_url": f"custom/emoticon/{cfg.slot_name}/cover.png",
                },
                "usagerights": ["Agent", "AimMaster"],
                "descriptions": {
                    "en_US": "Reusable slot for agent-rendered head screen text",
                    "zh_CN": "Reusable slot for agent-rendered head screen text",
                },
            },
        }
        url = (
            f"{cfg.resource_service_url}"
            "/rpc/aimdk.protocol.ResourceService/CreateResource"
        )
        with httpx.Client(timeout=cfg.request_timeout_s) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
        code = str((body.get("header") or {}).get("code", ""))
        if code not in {"0", ""}:
            raise RuntimeError(f"CreateResource rejected the slot: {body}")

        return self._find_registered_slot()

    def _copy_back_from_face_host(self, remote_dir: str, local_dir: str) -> None:
        cfg = self.cfg
        os.makedirs(local_dir, exist_ok=True)
        target = f"{cfg.face_ssh_user}@{cfg.face_host}"
        command = [
            "scp",
            *self._ssh_options(),
            # Listed individually: brace expansion is not available here, and
            # newer scp defaults to SFTP mode where the remote shell never
            # sees the path at all.
            f"{target}:{remote_dir}/emoticon.mp4",
            f"{target}:{remote_dir}/thumbnail.mp4",
            f"{target}:{remote_dir}/cover.png",
            f"{local_dir}/",
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=cfg.render_timeout_s, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"staging copy failed (rc={result.returncode}): {result.stderr.strip()}"
            )

    # ------------------------------------------------------------------ rpcs

    def _player_emoticon(self, emoticon_id: int) -> dict[str, Any]:
        url = (
            f"{self.cfg.rc_url}"
            "/rpc/aimdk.protocol.RcEmoticonPlayerService/PlayerEmoticon"
        )
        payload = {"header": {}, "emoticon_id": int(emoticon_id), "is_need_data": False}
        with httpx.Client(timeout=self.cfg.request_timeout_s) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
        code = str((body.get("header") or {}).get("code", ""))
        if code not in {"0", ""}:
            raise RuntimeError(f"PlayerEmoticon({emoticon_id}) failed: {body}")
        return body

    def _get_emoticons(self) -> list[dict[str, Any]]:
        url = (
            f"{self.cfg.resource_service_url}"
            "/rpc/aimdk.protocol.ResourceService/GetEmoticon"
        )
        with httpx.Client(timeout=self.cfg.request_timeout_s) as client:
            response = client.post(url, json={"header": {}})
            response.raise_for_status()
            body = response.json()
        return body.get("emoticons") or []

    def _resolve_default_emoticon_id(self) -> int | None:
        if self._default_emoticon_id is not None:
            return self._default_emoticon_id

        default_path = None
        try:
            default_path = self._read_default_emoticon_path()
        except Exception as exc:
            LOG.info("Could not read %s: %s", _DEFAULT_EMOTICON_PATH_CONFIG_KEY, exc)

        emoticons: list[dict[str, Any]] = []
        try:
            emoticons = self._get_emoticons()
        except Exception as exc:
            LOG.info("Could not list emoticons while resolving default face: %s", exc)

        if default_path:
            for emoticon in emoticons:
                if emoticon.get("emoticon_path") == default_path:
                    self._default_emoticon_id = int(emoticon["emoticon_id"])
                    return self._default_emoticon_id

        known_ids = {
            int(emoticon["emoticon_id"])
            for emoticon in emoticons
            if emoticon.get("emoticon_id") is not None
        }
        for fallback in _FALLBACK_DEFAULT_EMOTICON_IDS:
            if not known_ids or fallback in known_ids:
                self._default_emoticon_id = fallback
                return fallback
        return None

    def _read_default_emoticon_path(self) -> str | None:
        url = (
            f"{self.cfg.setting_service_url}"
            "/rpc/aimdk.protocol.RobotFunctionSettingService/ConfigControl"
        )
        payload = {
            "header": {},
            "Operation": "ConfigControlOperation_GetConfig",
            "configs": [{"key": _DEFAULT_EMOTICON_PATH_CONFIG_KEY, "value": "", "extra": ""}],
        }
        with httpx.Client(timeout=self.cfg.request_timeout_s) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
        for entry in body.get("configs") or []:
            if entry.get("key") == _DEFAULT_EMOTICON_PATH_CONFIG_KEY:
                return entry.get("value") or None
        return None


def _sanitize(text: str, max_chars: int) -> str:
    collapsed = re.sub(r"\s+", " ", _SAFE_TEXT_RE.sub(" ", str(text or ""))).strip()
    return collapsed[:max_chars]


__all__ = ["AgibotHeadScreenConfig", "AgibotEmoticonScreenController"]
