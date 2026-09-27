"""Conservative index guard. Prints locations only, never matching secret text.

Not a complete secret/history audit. Run before committing/pushing; .gitignore
does not protect files already tracked. No third-party scanner uploads content.
"""
from __future__ import annotations
import argparse
from pathlib import PurePosixPath
import subprocess


def private_path(name: str) -> bool:
    path = PurePosixPath(name)
    leaf = path.name.lower()
    if leaf.endswith((".example", ".template", ".example.yaml")):
        return False
    return (
        leaf == ".env" or leaf.startswith((".env.", ".env ")) or leaf.endswith((".pem", ".key", ".env", ".incoming"))
        or ".bak" in leaf
        or leaf.startswith(("id_rsa", "id_ed25519", "titan_ed25519", "authorized_keys"))
        or name.startswith((".deploy-private/", ".envs/", "robot_supervisor_v2/state/", "table_tennis/var/"))
        or name in {"robot_supervisor_v2/config.yaml", "table_tennis/config.local.yaml"}
    )


def check_index() -> list[str]:
    files = subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8").split("\0")
    findings = [f"Private path tracked: {p}" for p in files if p and private_path(p)]
    # Split literals so this scanner does not match its own source.
    patterns = [
        "-----BEGIN " + "(RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----",
        "gh[pousr]_" + "[A-Za-z0-9]{30,}",
        "github_pat_" + "[A-Za-z0-9_]{40,}",
        "sk-proj-" + "[A-Za-z0-9_-]{40,}",
        "AKIA" + "[0-9A-Z]{16}",
    ]
    cmd = ["git", "grep", "--cached", "-n", "-I", "-E"]
    for pattern in patterns:
        cmd.extend(["-e", pattern])
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("git secret scan failed (output withheld).")
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        path, number, _ = line.split(":", 2)
        findings.append(f"Possible credential at {path}:{number} (value hidden)")
    return findings


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    findings = check_index()
    for item in findings:
        print(item)
    if not findings:
        print("Index guard passed. Also manually review new config, archives and arbitrary vendor keys.")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
