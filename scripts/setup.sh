#!/usr/bin/env bash
# Create a fresh .venv and install dependencies (Python 3.12+).
#
#   source scripts/setup.sh    # recommended — activates in your shell
#   ./scripts/setup.sh         # install only; run: source .venv/bin/activate

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -euo pipefail
    _SOURCED=0
else
    set -uo pipefail
    _SOURCED=1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== setup (${ROOT}) ==="

[[ -d .venv ]] && rm -rf .venv

if command -v uv &>/dev/null; then
    uv venv .venv --python 3.12
    uv pip install -r requirements.txt
else
    PYTHON="${PYTHON:-python3.12}"
    command -v "$PYTHON" &>/dev/null || PYTHON=python3
    "$PYTHON" -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

.venv/bin/python -c "import gso, yaml; print('OK')"

if [[ "$_SOURCED" -eq 1 ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    echo "Activated: $(python --version)"
else
    echo ""
    echo "Installed. Activate with:  source .venv/bin/activate"
    echo "Or re-run:                 source scripts/setup.sh"
fi
