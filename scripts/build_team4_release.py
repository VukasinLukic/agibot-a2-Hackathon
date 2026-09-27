"""Build a private-file-free, commit-addressed PC2 archive on the laptop.

Requires git, npm and a clean team4 checkout. No network/SSH to the robot.
PC2 needs neither Node nor a compiler for the frontend. Existing private
runtime files/venvs must be restored separately; this archive is not a disk image.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

from check_git_secrets import check_index, private_path

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "a2-hackathon-team4"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> int:
    os.chdir(ROOT)
    if git("branch", "--show-current") != BRANCH:
        raise SystemExit(f"Build only from {BRANCH}.")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise SystemExit("Commit/review changes first: release must represent one clean Git commit.")
    findings = check_index()
    if findings:
        raise SystemExit("\n".join(findings))
    frontend = ROOT / "robot_supervisor_v2/frontend"
    # Vite env files and process VITE_* values become public JavaScript.
    if any(frontend.glob(".env*")) or any(k.startswith("VITE_") for k in os.environ):
        raise SystemExit("Remove/reconcile frontend .env* and process VITE_* overrides before a reproducible public build.")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("npm is needed on the laptop (not on PC2).")
    subprocess.run([npm, "ci", "--ignore-scripts"], cwd=frontend, check=True)
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True)
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise SystemExit("Build changed tracked/source files; review them and rebuild after committing.")
    sha = git("rev-parse", "HEAD")
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    target = artifacts / f"team4-{sha[:12]}-{time.time_ns()}.tar.gz"
    # Git archive exports committed bytes (LF scripts) and respects export-ignore.
    source_path = target.with_suffix(".source.tar")
    subprocess.run(["git", "archive", "--format=tar", "--output", str(source_path), "HEAD"], check=True)
    try:
        with tarfile.open(source_path) as source, tarfile.open(target, "w:gz") as output:
            for member in source.getmembers():
                if private_path(member.name):
                    raise ValueError(f"Refusing private archive member: {member.name}")
                if member.issym() or member.islnk():
                    raise ValueError(f"Review symlink before shipping: {member.name}")
                output.addfile(member, source.extractfile(member) if member.isfile() else None)
            dist = ROOT / "robot_supervisor_v2/dist"
            for path in sorted(dist.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Symlink in generated frontend; refusing release.")
                if path.is_file():
                    output.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
            metadata = json.dumps({"commit": sha, "branch": BRANCH, "private_files_included": False}, indent=2).encode()
            record = tarfile.TarInfo("TEAM4_RELEASE.json")
            record.size = len(metadata)
            output.addfile(record, io.BytesIO(metadata))
        with target.open("rb") as release_file:
            digest = hashlib.file_digest(release_file, "sha256").hexdigest()
    except Exception:
        # Retain any partial output for inspection, clearly never label it ready.
        print(f"FAILED archive (do not deploy): {target}")
        raise
    finally:
        source_path.unlink(missing_ok=True)
    print(f"READY: {target}\nSHA256: {digest}\nCommit: {sha}")
    print("Transfer private mentor config/.env/speech state separately. This does not restart anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
