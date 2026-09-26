#!/usr/bin/env bash
# TitanSudija: thin wrapper around scripts/titansudija_deploy.py (the real runner).
# Every robot-changing step asks for mentor approval; see the runner docstring.
#   scripts/deploy_and_start.sh                # = up
#   scripts/deploy_and_start.sh status|down|check [--dry-run] [--sync git|copy] ...
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
cmd="${1:-up}"
[[ $# -gt 0 ]] && shift
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" scripts/titansudija_deploy.py "$cmd" "$@"
