#!/usr/bin/env python3
"""TitanSudija deploy and start runner (laptop side, standard library only).

Automates docs/table_tennis_plan/12_POKRETANJE_NA_ROBOTU.md from the operator
laptop. Safety rules come from 10_TITANSUDIJA_ROBOT_CONTEXT.md section 0:

* read-only steps run on their own;
* every step that changes the robot (code, .env, tmux, Supervisor API writes)
  asks for mentor approval, typed at the keyboard; there is no --yes;
* physical steps (ARM, starting the Supervisor, speech test) also ask for the
  E-stop confirmation and the word MENTOR;
* the running Supervisor is never restarted or sent keys (no Ctrl+C in its
  tmux pane); a restart is printed as a mentor instruction and then awaited;
* tokens live in table_tennis/var/robot_tokens.env (gitignored) and on the
  robot's .env; they are never printed, logged or passed on a command line.

Usage (from the repo root):
    python scripts/titansudija_deploy.py check            # local tests only
    python scripts/titansudija_deploy.py status           # read-only robot + API status
    python scripts/titansudija_deploy.py up [--sync git|copy] [--vision-cmd "..."]
    python scripts/titansudija_deploy.py down             # teardown (vision back on, DISARM)
    add --dry-run to print what would happen without touching the robot.
"""

from __future__ import annotations

import argparse
import base64
import inspect
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
VAR_DIR = REPO_ROOT / "table_tennis" / "var"
TOKENS_FILE = VAR_DIR / "robot_tokens.env"
STATE_FILE = VAR_DIR / "deploy_state.json"
LOG_FILE = VAR_DIR / "deploy_log.jsonl"

DEFAULT_ROBOT = "agi@192.168.2.50"
DEFAULT_SUPERVISOR = "http://192.168.2.50:8070"
DEFAULT_REMOTE_DIR = "/agibot/humanoid-platform"
PC2_INTERNAL_IP = "192.168.100.110"
PC1_INTERNAL_IP = "192.168.100.100"
HOST_KEY_ED25519 = "SHA256:5xSjsXk2r1ORLQpku2I1Jj80e614b9hycWLl3407S5M"
SUPERVISOR_SESSION = "robot_supervisor"
VISION_SESSION = "tt_vision"

TOKEN_KEYS = ("TT_OPERATOR_TOKEN", "TT_VISION_TOKEN", "TT_ROBOT_TOKEN", "TT_PERSONA_TOKEN")
FIXED_ENV = {
    "TABLE_TENNIS_ENABLED": "1",
    "TABLE_TENNIS_CONFIG": "table_tennis/config.local.yaml",
    "TT_AUTH_MODE": "token",
}
# Safe first-visit config (12_POKRETANJE section 5): mock until real transports are reviewed.
CONFIG_LOCAL_YAML = """\
# Written by scripts/titansudija_deploy.py. mode: real only after mentor review
# of the real transports (12_POKRETANJE_NA_ROBOTU.md section 5).
mode: mock
server:
  host: 127.0.0.1
  port: 8099
storage:
  db_path: table_tennis/var/titansudija.sqlite
features:
  automatic_scoring: false
auth:
  mode: token
"""

HTTP_TIMEOUT_S = 3.0
IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------- output

if IS_WINDOWS:
    os.system("")  # enables ANSI colours in Windows terminals

_COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def step(title: str) -> None:
    print("\n" + _c("1;36", f"▶ {title}"))


def ok(msg: str) -> None:
    print(_c("32", "  ✔ ") + msg)


def warn(msg: str) -> None:
    print(_c("33", "  ⚠ ") + msg)


def fail(msg: str) -> None:
    print(_c("31", "  ✘ ") + msg)


def info(msg: str) -> None:
    print("    " + msg)


def mentor_note(msg: str) -> None:
    print(_c("1;35", "  [MENTOR] ") + msg)


class Abort(Exception):
    """Stop the run; the message says why and what to do."""


# ------------------------------------------------------------ pure helpers
# merge_env is also shipped to the robot as source (see remote_env_merge), so it
# must stay self-contained: no imports or module-level names.

def parse_env(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def merge_env(existing: str, wanted: dict) -> tuple:
    """Append missing keys; never overwrite. Returns (text, added, conflicts)."""
    current = {}
    for line in existing.splitlines():
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key, _, value = raw.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            current[key] = value
    added, conflicts, lines = [], [], []
    for key, value in wanted.items():
        if key not in current:
            added.append(key)
            lines.append(f"{key}={value}")
        elif current[key] != value:
            conflicts.append(key)
    text = existing
    if lines:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "# TitanSudija (scripts/titansudija_deploy.py)\n" + "\n".join(lines) + "\n"
    return text, added, conflicts


def resolve_tokens(local: dict, remote: dict, new_token: Callable[[], str]) -> tuple:
    """Pick one value per token. Robot values win (the running Supervisor uses them).

    Returns (tokens, generated_keys, pulled_keys). Raises Abort when local and robot
    disagree, or when two actors would share a token.
    """
    tokens, generated, pulled = {}, [], []
    for key in TOKEN_KEYS:
        lv, rv = local.get(key), remote.get(key)
        if lv and rv and lv != rv:
            raise Abort(f"{key} se razlikuje na laptopu i na robotu; dogovoriti sa mentorom koji važi")
        if rv:
            tokens[key] = rv
            if not lv:
                pulled.append(key)
        elif lv:
            tokens[key] = lv
        else:
            tokens[key] = new_token()
            generated.append(key)
    if len(set(tokens.values())) != len(tokens):
        raise Abort("dva aktera imaju isti token; backend to odbija (svaki token mora biti različit)")
    return tokens, generated, pulled


# ------------------------------------------------------------------ runner

class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.dry = args.dry_run
        self.robot = args.robot
        self.api = args.supervisor_url.rstrip("/")
        self.remote_dir = args.remote_dir
        self.state = self._load_state()
        self.env_changed = False
        self.facts: dict = {}
        self.tokens: dict = parse_env(TOKENS_FILE.read_text(encoding="utf-8")) if TOKENS_FILE.exists() else {}

    # ---------------------------------------------------------- state/log

    def _load_state(self) -> dict:
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        if self.dry:
            return
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def log(self, event: str, **fields: Any) -> None:
        if self.dry:
            return
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}) + "\n")

    # --------------------------------------------------------------- gates

    def gate(self, title: str, details: list, physical: bool = False) -> bool:
        """Ask for approval of a step that changes the robot. False means skipped."""
        print(_c("1;33", f"  ┌ Potrebna dozvola mentora: {title}"))
        for line in details:
            print(_c("33", "  │ ") + line)
        if self.dry:
            print(_c("33", "  └ --dry-run: preskačem"))
            return False
        if not sys.stdin.isatty():
            raise Abort("korak traži potvrdu mentora, a ulaz nije terminal; pokreni interaktivno")
        if physical:
            if input(_c("1;31", "  │ Mentor je prisutan, E-stop je u ruci i prostor oko robota je slobodan? [da/ne] ")).strip().lower() != "da":
                print(_c("33", "  └ preskočeno"))
                return False
            answer = input(_c("1;31", "  └ Mentor kuca MENTOR za potvrdu: ")).strip()
            approved = answer == "MENTOR"
        else:
            approved = input(_c("33", "  └ Mentor odobrava? [da/ne] ")).strip().lower() == "da"
        if not approved:
            warn("preskočeno")
        self.log("gate", title=title, approved=approved, physical=physical)
        return approved

    # ----------------------------------------------------------------- ssh

    def _ssh_base(self) -> list:
        cmd = ["ssh", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5"]
        if not IS_WINDOWS:
            # one TCP/auth handshake for the whole run (Windows OpenSSH lacks multiplexing)
            cm = Path.home() / ".ssh" / "cm-titansudija-%r@%h-%p"
            cmd += ["-o", "ControlMaster=auto", "-o", f"ControlPath={cm}", "-o", "ControlPersist=120"]
        return cmd + [self.robot]

    def ssh(self, remote_cmd: str, stdin: Optional[bytes] = None, check: bool = True, timeout: float = 60) -> str:
        try:
            proc = subprocess.run(self._ssh_base() + [remote_cmd], input=stdin, capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise Abort("ssh nije instaliran (Windows: OpenSSH Client; Linux/macOS: openssh-client)")
        except subprocess.TimeoutExpired:
            raise Abort(f"ssh nije završio za {timeout:.0f} s; proveriti LAN (laptop 192.168.2.119/24)")
        out = proc.stdout.decode("utf-8", "replace")
        if check and proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise Abort(f"ssh komanda nije uspela ({proc.returncode}): {err[-1] if err else remote_cmd}")
        return out

    def in_repo(self, cmd: str) -> str:
        return f"cd {shlex.quote(self.remote_dir)} && {cmd}"

    # ---------------------------------------------------------------- http

    def http(self, method: str, path: str, body: Optional[dict] = None, token: Optional[str] = None) -> tuple:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.api + path, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as res:
                status, raw = res.status, res.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise Abort(f"{method} {path}: Supervisor nedostupan ({exc})")
        ms = (time.monotonic() - started) * 1000
        try:
            payload = json.loads(raw.decode("utf-8") or "null")
        except ValueError:
            payload = raw.decode("utf-8", "replace")[:200]
        info(_c("2", f"{method} {path} -> {status} ({ms:.0f} ms)"))
        return status, payload

    # ================================================================ steps

    def local_checks(self) -> None:
        step("Lokalne provere (testovi, ugovor, fixtures)")
        checks = [
            ("contract check", [sys.executable, "-m", "table_tennis.contracts.generate", "--check"]),
            ("fixtures check", [sys.executable, "-m", "table_tennis.sim.fixtures", "--check"]),
            ("pytest", [sys.executable, "-m", "pytest", "tests/table_tennis", "-q", "-p", "no:cacheprovider"]),
        ]
        for name, cmd in checks:
            started = time.monotonic()
            try:
                # no stdin: a test waiting on input must fail, not hang the deploy
                proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                                      stdin=subprocess.DEVNULL, timeout=600)
            except subprocess.TimeoutExpired:
                raise Abort(f"{name} nije završio za 10 min")
            tail = (proc.stdout.strip().splitlines() or [""])[-1]
            if proc.returncode != 0:
                fail(f"{name}: {tail}")
                print(proc.stdout[-2000:] + proc.stderr[-2000:])
                raise Abort(f"{name} ne prolazi; na robot ide samo provereni main")
            ok(f"{name} ({time.monotonic() - started:.1f} s): {tail}")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT,
                               capture_output=True, text=True).stdout.strip()
        self.state["local_sha"] = sha
        ok(f"lokalni commit {sha[:10]}" + (_c("33", " (ima necommitovanih izmena)") if dirty else ""))
        if dirty:
            warn("necommitovane izmene ne idu preko --sync git; commit + push pre deploy-a")

    def verify_host_key(self) -> None:
        step("Identitet robota (host key)")
        try:
            scan = subprocess.run(["ssh-keyscan", "-t", "ed25519", "-T", "5", self.robot.split("@")[-1]],
                                  capture_output=True, timeout=10)
            fp = subprocess.run(["ssh-keygen", "-lf", "-"], input=scan.stdout, capture_output=True, timeout=5)
            fingerprint = fp.stdout.decode().split()[1] if fp.returncode == 0 and fp.stdout else ""
        except (OSError, subprocess.TimeoutExpired, IndexError):
            fingerprint = ""
        if not fingerprint:
            warn("ssh-keyscan/ssh-keygen nije dao otisak; oslanjam se na known_hosts (StrictHostKeyChecking=yes)")
        elif fingerprint != HOST_KEY_ED25519:
            raise Abort(f"host key {fingerprint} nije očekivani {HOST_KEY_ED25519}: prekini i zovi mentora")
        else:
            ok("host key odgovara dokumentovanom ED25519 otisku")

    def robot_identity(self) -> None:
        step("Read-only provera PC2 i checkout-a")
        out = self.ssh(self.in_repo(
            "echo IPS:$(hostname -I); echo SHA:$(git rev-parse HEAD); "
            "echo DIRTY:$(git status --porcelain --untracked-files=no | wc -l); "
            f"tmux has-session -t {SUPERVISOR_SESSION} 2>/dev/null && echo SUP:yes || echo SUP:no; "
            f"tmux has-session -t {VISION_SESSION} 2>/dev/null && echo VIS:yes || echo VIS:no; "
            "test -f .env && echo ENV:yes || echo ENV:no; "
            "test -f table_tennis/config.local.yaml && echo CFG:yes || echo CFG:no"
        ))
        facts = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
        ips = facts.get("IPS", "").split()
        if PC1_INTERNAL_IP in ips or PC2_INTERNAL_IP not in ips:
            raise Abort(f"ovo nije PC2 (hostname -I: {' '.join(ips)}); odmah exit i pitati mentora")
        ok(f"PC2 potvrđen ({PC2_INTERNAL_IP})")
        remote_sha = facts.get("SHA", "").strip()
        self.state.setdefault("robot_sha_before", remote_sha)
        self.state["robot_sha_now"] = remote_sha
        ok(f"checkout na robotu: {remote_sha[:10]} (zapisano za povratak: {self.state['robot_sha_before'][:10]})")
        if facts.get("DIRTY", "0").strip() != "0":
            warn("na robotu postoje izmenjeni praćeni fajlovi (tuđe izmene?); ne diram ih")
        self.facts = facts
        info(f"tmux {SUPERVISOR_SESSION}: {facts.get('SUP')}, {VISION_SESSION}: {facts.get('VIS')}, "
             f".env: {facts.get('ENV')}, config.local.yaml: {facts.get('CFG')}")
        self._save_state()
        self.log("robot_identity", sha=remote_sha)

    def sync_code(self) -> None:
        mode = self.args.sync
        step(f"Sinhronizacija koda na robot (--sync {mode})")
        if mode == "skip":
            info("preskočeno (podrazumevano); način deploy-a bira mentor: --sync git ili --sync copy")
            return
        local_sha = self.state.get("local_sha") or ""
        if mode == "git":
            if self.facts.get("DIRTY", "0").strip() != "0":
                raise Abort("robot checkout ima izmenjene praćene fajlove; git switch bi ih ugrozio. Pitati mentora.")
            pushed = subprocess.run(["git", "branch", "-r", "--contains", local_sha], cwd=REPO_ROOT,
                                    capture_output=True, text=True).stdout.strip()
            if not pushed:
                raise Abort(f"commit {local_sha[:10]} nije na remote-u; prvo push timskog main-a")
            cmd = self.in_repo(f"git fetch {shlex.quote(self.args.git_remote)} main && "
                               f"git switch --detach {shlex.quote(local_sha)}")
            details = [f"na robotu: git fetch {self.args.git_remote} main && git switch --detach {local_sha[:10]}",
                       f"povratak: git switch --detach {self.state.get('robot_sha_before', '?')[:10]}"]
            if self.gate("prebacivanje checkout-a na provereni commit", details):
                self.ssh(cmd, timeout=180)
                ok(f"robot je na {local_sha[:10]}")
                self.log("sync_git", sha=local_sha)
            return
        # copy: only our package and the frontend feature, with a backup first
        paths = ["table_tennis", "robot_supervisor_v2/frontend/src/features/table-tennis"]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        details = [f"kopiram: {', '.join(paths)} (bez var/, __pycache__, config.local.yaml)",
                   f"backup na robotu: .tt_backups/{stamp}.tgz"]
        if not self.gate("kopiranje table_tennis koda na robot", details):
            return
        tar = subprocess.run(
            ["tar", "-czf", "-", "--exclude=__pycache__", "--exclude=table_tennis/var",
             "--exclude=table_tennis/config.local.yaml", *paths],
            cwd=REPO_ROOT, capture_output=True,
        )
        if tar.returncode != 0:
            raise Abort("tar nije uspeo lokalno: " + tar.stderr.decode(errors="replace")[-200:])
        self.ssh(self.in_repo(f"mkdir -p .tt_backups && tar -czf .tt_backups/{stamp}.tgz "
                              f"--ignore-failed-read {' '.join(paths)} 2>/dev/null; tar -xzf -"),
                 stdin=tar.stdout, timeout=180)
        ok(f"kopirano ({len(tar.stdout) // 1024} KiB), backup .tt_backups/{stamp}.tgz")
        self.log("sync_copy", backup=stamp)

    def configure_env(self) -> None:
        step("Konfiguracija .env i tokeni")
        remote_env = parse_env(self.ssh(self.in_repo("cat .env 2>/dev/null || true")))
        tokens, generated, pulled = resolve_tokens(self.tokens, remote_env, lambda: secrets.token_urlsafe(24))
        for key in pulled:
            ok(f"{key}: preuzet sa robota u lokalni {TOKENS_FILE.relative_to(REPO_ROOT)}")
        for key in generated:
            ok(f"{key}: generisan novi (vrednost se ne ispisuje)")
        if pulled or generated:
            self._write_local_tokens(tokens)
        self.tokens = tokens

        wanted = dict(FIXED_ENV, **tokens)
        _, missing, conflicts = merge_env(self._env_text_hint(remote_env), wanted)
        for key in conflicts:
            warn(f"{key} na robotu ima drugu vrednost; ne menjam je (proveriti sa mentorom)")
        if not missing:
            ok(".env na robotu već ima sve TitanSudija ključeve")
        else:
            details = [f"dodajem u {self.remote_dir}/.env (bez prepisivanja): {', '.join(missing)}",
                       "backup: .env.bak-<vreme>; tokeni idu preko ssh stdin, ne u komandnoj liniji",
                       "Supervisor čita .env samo pri startu: posle ovoga treba restart (radi mentor)"]
            if self.gate("dopuna .env na robotu", details):
                result = self.remote_env_merge(wanted)
                ok(f".env dopunjen: {', '.join(result['added']) or 'ništa'}")
                self.env_changed = bool(result["added"])
                self.log("env_merge", added=result["added"], conflicts=result["conflicts"])

        if self.facts.get("CFG") == "no":
            if self.gate("pravljenje table_tennis/config.local.yaml (mode: mock)",
                         ["bezbedan početni config: mode mock, automatic_scoring false, auth token",
                          "mode: real tek kad mentor pregleda prave transporte"]):
                self.ssh(self.in_repo("test -f table_tennis/config.local.yaml || cat > table_tennis/config.local.yaml"),
                         stdin=CONFIG_LOCAL_YAML.encode())
                ok("config.local.yaml napravljen")
                self.env_changed = True

    @staticmethod
    def _env_text_hint(values: dict) -> str:
        return "\n".join(f"{k}={v}" for k, v in values.items())

    def _write_local_tokens(self, tokens: dict) -> None:
        if self.dry:
            return
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        TOKENS_FILE.write_text("".join(f"{k}={v}\n" for k, v in tokens.items()), encoding="utf-8")
        try:
            os.chmod(TOKENS_FILE, 0o600)
        except OSError:
            pass

    def remote_env_merge(self, wanted: dict) -> dict:
        """Run merge_env on the robot; secrets travel on stdin only."""
        source = inspect.getsource(merge_env) + (
            "\nimport json, sys, time, pathlib, os\n"
            "wanted = json.load(sys.stdin)\n"
            "p = pathlib.Path('.env')\n"
            "old = p.read_text(encoding='utf-8') if p.exists() else ''\n"
            "text, added, conflicts = merge_env(old, wanted)\n"
            "if added:\n"
            "    if p.exists():\n"
            "        b = pathlib.Path('.env.bak-' + time.strftime('%Y%m%d-%H%M%S'))\n"
            "        b.write_text(old, encoding='utf-8'); os.chmod(b, 0o600)\n"
            "    tmp = pathlib.Path('.env.tt-tmp'); tmp.write_text(text, encoding='utf-8'); os.chmod(tmp, 0o600)\n"
            "    tmp.replace(p)\n"
            "print(json.dumps({'added': added, 'conflicts': conflicts}))\n"
        )
        encoded = base64.b64encode(source.encode()).decode()
        out = self.ssh(self.in_repo(f"python3 -c \"import base64;exec(base64.b64decode('{encoded}'))\""),
                       stdin=json.dumps(wanted).encode())
        return json.loads(out.strip().splitlines()[-1])

    def supervisor(self) -> None:
        step(f"Supervisor (tmux {SUPERVISOR_SESSION})")
        running = self.facts.get("SUP") == "yes"
        if not running:
            if self.gate("pokretanje Supervisora", [
                f"cd {self.remote_dir} && ./run_robot_supervisor_v2.sh",
                "skripta pravi tmux sesiju i ne radi ništa ako sesija već postoji",
                "Supervisor sluša celu mrežu i pokreće servise robota",
            ], physical=True):
                self.ssh(self.in_repo("./run_robot_supervisor_v2.sh"), timeout=60)
                ok("start poslat")
            else:
                raise Abort("Supervisor ne radi; dalji koraci nemaju smisla")
        elif self.env_changed:
            mentor_note("`.env`/config je promenjen, a Supervisor ga čita samo pri startu. Restart radi MENTOR:")
            info(f"ssh {self.robot}   →   tmux attach -t {SUPERVISOR_SESSION}")
            info("Ctrl+C u toj sesiji (samo mentor), pa: ./run_robot_supervisor_v2.sh ; izlaz: Ctrl+b pa d")
            if not self.dry:
                input(_c("35", "    Pritisni Enter kada mentor kaže da je restart gotov... "))
        else:
            ok("sesija radi; ne diram je (nikad Ctrl+C iz skripte)")
        self.wait_health()

    def wait_health(self, timeout_s: float = 90) -> None:
        if self.dry:
            return
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                status, payload = self.http("GET", "/api/health")
                if status == 200 and isinstance(payload, dict) and payload.get("status") == "ok":
                    ok("Supervisor /api/health: ok")
                    return
            except Abort as exc:
                if time.monotonic() > deadline:
                    raise
                info(_c("2", str(exc)))
            if time.monotonic() > deadline:
                raise Abort("Supervisor se nije javio na /api/health; log: tmux robot_supervisor / robot_supervisor_boot.log")
            time.sleep(1.0)

    def table_tennis_health(self) -> None:
        step("TitanSudija API (/api/table-tennis/health)")
        token = self.tokens.get("TT_OPERATOR_TOKEN")
        if not token:
            raise Abort("nema TT_OPERATOR_TOKEN lokalno; pokreni `up` da ga preuzme/generiše")
        status, payload = self.http("GET", "/api/table-tennis/health", token=token)
        if status == 404:
            raise Abort("404: feature nije montiran (TABLE_TENNIS_ENABLED=1 + token u .env, pa restart Supervisora)")
        if status in (401, 403):
            raise Abort(f"{status}: operator token ne odgovara onom koji Supervisor koristi")
        if status != 200 or not isinstance(payload, dict):
            raise Abort(f"neočekivan odgovor {status}: {payload}")
        ok(f"mode={payload.get('mode')} simulated={payload.get('simulated')} auth={payload.get('auth_mode')} "
           f"automatic_scoring={payload.get('automatic_scoring_enabled')}")
        for name, cap in (payload.get("capabilities") or {}).items():
            mark = ok if cap.get("available") else warn
            mark(f"{name}: available={cap.get('available')} simulated={cap.get('simulated')} {cap.get('detail') or ''}")
        if payload.get("mode") == "real":
            warn("mode=real: pravi izlazi su aktivni; svaki pokret robota samo uz mentora")

    def speech(self) -> None:
        step("Govor (livekit, voice-agent, audio-bridge)")
        if not self.gate("pokretanje speech servisa", [
            "POST /api/services/start-speech",
            "AIMA EM gasi `agent` i `hal_audio` pre audio-bridge-a (docs/audio_bridge.md)",
        ]):
            return
        status, payload = self.http("POST", "/api/services/start-speech")
        if status != 200:
            raise Abort(f"start-speech: {status} {payload}")
        ok("speech servisi pokrenuti")
        if self.args.speech_test and self.gate("testna rečenica na zvučniku", [
            'POST /api/conversation/command {"text": "TitanSudija je spreman."}',
        ], physical=True):
            status, payload = self.http("POST", "/api/conversation/command", {"text": "TitanSudija je spreman."})
            (ok if status == 200 else warn)(f"testna rečenica: {status}")

    def vision_off(self) -> None:
        step("Auto-razgovor na detekciju osoba (vision controller)")
        status, payload = self.http("GET", "/api/vision")
        if status != 200 or not isinstance(payload, dict):
            warn(f"GET /api/vision: {status}; preskačem")
            return
        enabled = bool(payload.get("enabled"))
        if not enabled:
            ok("već isključen")
            return
        if self.gate("isključivanje auto-razgovora tokom meča", [
            'PATCH /api/vision {"enabled": false}  (igrači su stalno u kadru)',
            "vraća se sa `down` na prethodno stanje",
        ]):
            self.state["vision_enabled_before"] = enabled
            self._save_state()
            status, payload = self.http("PATCH", "/api/vision", {"enabled": False})
            (ok if status == 200 and not payload.get("enabled") else warn)(f"vision enabled={payload.get('enabled')}")
            self.log("vision_off")

    def arm(self) -> None:
        step("ARM (gasi idle animaciju, napaja noge)")
        status, payload = self.http("GET", "/api/nav/arm")
        if status == 200 and isinstance(payload, dict) and payload.get("held"):
            ok("ARM već aktivan")
            return
        mentor_note("Idle animacija ne staje kada je neko blizu. ARM radi mentor, uz E-stop.")
        if not self.gate("ARM robota", [
            "POST /api/nav/arm (drži walking action dok se ne uradi DISARM)",
            "niko ne sme biti na dohvat robota; robot mora biti bezbedno postavljen",
        ], physical=True):
            warn("ARM nije urađen: idle animacija se može vratiti za ~10 s")
            return
        status, payload = self.http("POST", "/api/nav/arm")
        if status != 200:
            raise Abort(f"ARM nije uspeo: {status} {payload}")
        status, payload = self.http("GET", "/api/nav/arm")
        if isinstance(payload, dict) and payload.get("held"):
            ok("ARM potvrđen (held=true)")
            self.log("arm")
        else:
            warn(f"ARM poslat, ali status ne potvrđuje held: {payload}")

    def vision_process(self) -> None:
        step(f"Vision proces za lopticu (tmux {VISION_SESSION})")
        cmd = self.args.vision_cmd
        if not cmd:
            info("preskočeno: nema --vision-cmd (ulaznu tačku i kameru određuje grana comp-vision)")
            info("fallback: operater dodeljuje poene ručno (point.award)")
            return
        if self.facts.get("VIS") == "yes":
            ok("sesija već radi; ne pravim drugu (jedan producer po meču)")
            return
        inner = (f"cd {shlex.quote(self.remote_dir)} && "
                 "set -a && source <(sed -E 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*/\\1=/' .env) && set +a && "
                 f"source {shlex.quote(self.args.vision_venv)}/bin/activate && exec {cmd}")
        if self.gate("pokretanje vision procesa", [
            f"tmux new-session -d -s {VISION_SESSION}",
            f"komanda: {cmd}",
            "TT_VISION_TOKEN dolazi iz .env na robotu (ne iz komandne linije)",
        ]):
            self.ssh(f"tmux new-session -d -s {VISION_SESSION} {shlex.quote('bash -lc ' + shlex.quote(inner))}")
            time.sleep(1.0)
            alive = self.ssh(f"tmux has-session -t {VISION_SESSION} 2>/dev/null && echo yes || echo no").strip()
            (ok if alive == "yes" else warn)(f"{VISION_SESSION}: {'radi' if alive == 'yes' else 'ugašen odmah; vidi log'}")
            self.log("vision_start", cmd=cmd)

    def operator_summary(self) -> None:
        step("Spremno za operatera")
        host = self.api.split("//", 1)[-1]
        info(f"UI: {_c('1', self.api)}  → tab Table tennis")
        info(f"operator token: u {TOKENS_FILE.relative_to(REPO_ROOT)} (TT_OPERATOR_TOKEN); prenosi se privatno")
        info("tok: novi meč → poziv/manual arrival (mentor potvrđuje put) → armiraj razmenu → potvrdi poene")
        info("fallback: vision ne radi → ručni poen; gest ne radi → ekran + glas; ekran ne radi → web scoreboard")
        mentor_note(f"na kraju: python scripts/titansudija_deploy.py down  (Supervisor {host})")

    # ---------------------------------------------------------- teardown

    def down(self) -> None:
        step("Gašenje TitanSudija dela (bez gašenja Supervisora)")
        facts = self.ssh(f"tmux has-session -t {VISION_SESSION} 2>/dev/null && echo yes || echo no").strip()
        if facts == "yes" and self.gate(f"gašenje tmux sesije {VISION_SESSION}", [f"tmux kill-session -t {VISION_SESSION} (samo naša sesija)"]):
            self.ssh(f"tmux kill-session -t {VISION_SESSION}")
            ok(f"{VISION_SESSION} ugašen")
        before = self.state.get("vision_enabled_before")
        if before is not None and self.gate("vraćanje auto-razgovora", [f'PATCH /api/vision {{"enabled": {str(before).lower()}}}']):
            status, _ = self.http("PATCH", "/api/vision", {"enabled": bool(before)})
            if status == 200:
                ok("vision vraćen na prethodno stanje")
                self.state.pop("vision_enabled_before", None)
                self._save_state()
        status, payload = self.http("GET", "/api/nav/arm")
        if isinstance(payload, dict) and payload.get("held"):
            mentor_note("DISARM vraća idle animaciju; mentor odlučuje da li i kada.")
            if self.gate("DISARM", ["POST /api/nav/disarm?restore_idle=true"], physical=True):
                status, _ = self.http("POST", "/api/nav/disarm?restore_idle=true")
                (ok if status == 200 else warn)(f"DISARM: {status}")
        sha = self.state.get("robot_sha_before")
        if sha:
            mentor_note(f"povratak koda (ako mentor traži): git switch --detach {sha[:10]} ; uklanjanje TABLE_TENNIS_* iz .env; restart radi mentor")

    def status(self) -> None:
        self.robot_identity()
        self.wait_health(timeout_s=0)
        if self.tokens.get("TT_OPERATOR_TOKEN"):
            try:
                self.table_tennis_health()
            except Abort as exc:
                warn(str(exc))
        for path in ("/api/vision", "/api/nav/arm"):
            self.http("GET", path)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="TitanSudija deploy/start (mentor-gated)")
    parser.add_argument("command", choices=["check", "status", "up", "down"])
    parser.add_argument("--robot", default=DEFAULT_ROBOT)
    parser.add_argument("--supervisor-url", default=DEFAULT_SUPERVISOR)
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    parser.add_argument("--sync", choices=["skip", "git", "copy"], default="skip")
    parser.add_argument("--git-remote", default="origin", help="remote na robotu za --sync git")
    parser.add_argument("--vision-cmd", default="", help="komanda vision procesa (grana comp-vision)")
    parser.add_argument("--vision-venv", default=".venv", help="venv na robotu za vision proces")
    parser.add_argument("--speech-test", action="store_true", help="posle starta izgovori testnu rečenicu")
    parser.add_argument("--skip-tests", action="store_true", help="preskoči lokalne testove (ne preporučuje se)")
    parser.add_argument("--dry-run", action="store_true", help="samo prikaži, ne menjaj robota")
    args = parser.parse_args(argv)

    runner = Runner(args)
    started = time.monotonic()
    try:
        if args.command == "check":
            runner.local_checks()
        elif args.command == "status":
            runner.status()
        elif args.command == "down":
            runner.down()
        else:
            if args.skip_tests:
                warn("lokalni testovi preskočeni (--skip-tests)")
            else:
                runner.local_checks()
            runner.verify_host_key()
            runner.robot_identity()
            runner.sync_code()
            runner.configure_env()
            runner.supervisor()
            runner.table_tennis_health()
            runner.speech()
            runner.vision_off()
            runner.arm()
            runner.vision_process()
            runner.operator_summary()
    except Abort as exc:
        fail(str(exc))
        runner.log("abort", reason=str(exc))
        return 1
    except KeyboardInterrupt:
        fail("prekinuto (Ctrl+C); ništa nije vraćeno automatski, proveri `status`")
        return 130
    print("\n" + _c("1;32", f"Gotovo za {time.monotonic() - started:.1f} s") + (_c("33", " (dry-run)") if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
