#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python3"

if [[ ! -x "$PY" ]]; then
  echo "Creating virtual environment..."
  python3 -m venv "$VENV"
fi

"$PY" -m pip install -U pip --disable-pip-version-check
"$PY" -m pip install -r "$ROOT/requirements.txt"

exec "$PY" "$ROOT/main.py"

