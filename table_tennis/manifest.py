"""Demo manifest: what exactly is being shown (02_BACKEND.md, last paragraph).

    python -m table_tennis.manifest [--config PATH] [--db PATH] [--json] [--out FILE]

Records commit SHA, configuration mode, schema/model/calibration version and
known limits. Never claims hardware testing: ``hardware_tested`` is always
False; ``--hardware-tested-note`` is recorded verbatim only as a note.
Read-only: never writes generated files and never creates the database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from table_tennis.config import REPO_ROOT, load_settings

SCHEMA_VERSION = "1.0"
DEFAULT_ADAPTERS = {"display": "fake", "speech": "fake", "gesture": "fake", "navigator": "fake"}

KNOWN_LIMITS = [
    "Nista nije testirano na fizickom A2 robotu.",
    "Izlazi (displej, govor, gest, navigacija) su fake/dry-run osim ako adapters kaze drugacije.",
    "Automatsko bodovanje je iskljuceno dok vision benchmark ne prodje.",
    "Samo jedan proces sme da pise u bazu (single writer).",
    "Mec je jedan gem (best_of=1).",
    "Nema vision modela u bootstrap verziji; kalibracija je samo identifikator.",
]


def _git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_info() -> Optional[dict[str, Any]]:
    sha = _git("rev-parse", "HEAD")
    if sha is None:
        return None
    status = _git("status", "--porcelain")
    return {
        "sha": sha,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def _up_to_date(render) -> dict[str, Any]:
    try:
        rendered = render()
    except Exception as exc:  # noqa: BLE001 - report, do not crash the manifest
        return {"up_to_date": None, "error": f"{type(exc).__name__}: {exc}"}
    stale = []
    for path, text in rendered.items():
        p = Path(path)
        try:
            current = p.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != text:
            try:
                stale.append(str(p.relative_to(REPO_ROOT)).replace("\\", "/"))
            except ValueError:
                stale.append(str(p))
    return {"up_to_date": not stale, "stale": sorted(stale)}


def contract_info() -> dict[str, Any]:
    from table_tennis.contracts import generate

    info: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "contract_schema_sha256": None}
    try:
        info["contract_schema_sha256"] = hashlib.sha256(Path(generate.SCHEMA_PATH).read_bytes()).hexdigest()
    except OSError:
        pass
    info["generated"] = _up_to_date(generate.render_all)
    return info


def fixtures_info() -> dict[str, Any]:
    from table_tennis.sim import fixtures

    return _up_to_date(fixtures.render_all)


def latest_match(db_path: str) -> Optional[dict[str, Any]]:
    p = Path(db_path)
    if not p.is_file():
        return None
    uri = p.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT match_id, revision, snapshot_json FROM matches ORDER BY updated_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    try:
        snap = json.loads(row[2])
    except ValueError:
        snap = {}
    return {
        "match_id": row[0],
        "revision": row[1],
        "status": snap.get("status"),
        "calibration_id": snap.get("calibration_id"),
        "assignment_version": snap.get("assignment_version"),
        "persona": snap.get("persona"),
        "scoring_mode": snap.get("scoring_mode"),
    }


def vision_info() -> dict[str, Any]:
    return {"model_version": None, "calibration_version": None, "note": "no vision model in bootstrap"}


def build_manifest(
    config_path: Optional[str] = None,
    db_path: Optional[str] = None,
    hardware_tested_note: Optional[str] = None,
    *,
    env: Optional[dict] = None,
    check_generated: bool = True,
) -> dict[str, Any]:
    settings = load_settings(config_path, env=env)
    db = db_path or settings.storage.db_path
    adapters = getattr(settings, "adapters", None)
    if adapters is None:
        adapters = dict(DEFAULT_ADAPTERS)
    elif hasattr(adapters, "model_dump"):
        adapters = adapters.model_dump(mode="json")
    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "git": git_info(),
        "mode": settings.mode,
        "simulated": settings.simulated,
        "auth_mode": settings.auth.mode,
        "adapters": adapters,
        "automatic_scoring_enabled": settings.features.automatic_scoring,
        "contract": contract_info() if check_generated else {"schema_version": SCHEMA_VERSION},
        "fixtures": fixtures_info() if check_generated else None,
        "db_path": str(db),
        "latest_match": latest_match(db),
        "vision": vision_info(),
        "hardware_tested": False,
        "hardware_tested_note": hardware_tested_note,
        "known_limits": list(KNOWN_LIMITS),
    }
    return manifest


def render_text(m: dict[str, Any]) -> str:
    g = m["git"]
    lines = [
        "A2 table tennis referee: demo manifest",
        f"generated_at: {m['generated_at']}",
        "git: " + (f"{g['sha']} ({g['branch']}, dirty={g['dirty']})" if g else "nepoznato (git nije dostupan)"),
        f"mode: {m['mode']} (simulated={m['simulated']}), auth: {m['auth_mode']}",
        "adapters: " + ", ".join(f"{k}={v}" for k, v in m["adapters"].items()),
        f"automatic_scoring_enabled: {m['automatic_scoring_enabled']}",
    ]
    c = m["contract"]
    lines.append(f"schema_version: {c['schema_version']}, contract sha256: {c.get('contract_schema_sha256')}")
    if "generated" in c:
        lines.append(f"generated files up to date: {c['generated'].get('up_to_date')} {c['generated'].get('stale') or ''}".rstrip())
    if m["fixtures"] is not None:
        lines.append(f"fixtures up to date: {m['fixtures'].get('up_to_date')} {m['fixtures'].get('stale') or ''}".rstrip())
    lm = m["latest_match"]
    if lm:
        lines.append("latest match: " + ", ".join(f"{k}={v}" for k, v in lm.items()))
    else:
        lines.append(f"latest match: nema ({m['db_path']})")
    v = m["vision"]
    lines.append(f"vision model: {v['model_version']}, calibration: {v['calibration_version']} ({v['note']})")
    lines.append(f"hardware_tested: {m['hardware_tested']}")
    if m["hardware_tested_note"]:
        lines.append(f"hardware note (nije potvrda): {m['hardware_tested_note']}")
    lines.append("known limits:")
    lines.extend(f"  - {x}" for x in m["known_limits"])
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m table_tennis.manifest")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--hardware-tested-note")
    args = ap.parse_args(argv)
    m = build_manifest(args.config, args.db, args.hardware_tested_note)
    text = json.dumps(m, indent=2, ensure_ascii=False) + "\n" if args.json else render_text(m)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
