from __future__ import annotations

import os
import tempfile
import threading
import zlib
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]

_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def stable_id(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) & 0x7FFFFFFF


def slug_to_title(slug: str) -> str:
    return slug.replace("_", " ").replace("-", " ").strip().title()


def load_yaml(runtime_path: Path, example_path: Path | None, default: Any = None) -> Any:
    path = runtime_path if runtime_path.exists() else example_path
    if path is None or not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or default


def ensure_runtime_file(runtime_path: Path, example_path: Path) -> None:
    if runtime_path.exists():
        return
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    if example_path.exists():
        runtime_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        runtime_path.write_text("{}\n", encoding="utf-8")


def write_yaml_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    payload,
                    f,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
            os.replace(tmp_name, path)
        finally:
            tmp_path = Path(tmp_name)
            if tmp_path.exists():
                tmp_path.unlink()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_name, path)
        finally:
            tmp_path = Path(tmp_name)
            if tmp_path.exists():
                tmp_path.unlink()


def _lock_for(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[resolved] = lock
        return lock
