#!/usr/bin/env bash
# Create .venv and install hub dependencies (Python 3.12+).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3.12}"
if ! command -v "$PYTHON" &>/dev/null; then
    PYTHON=python3
fi

echo "=== pydantic hub setup (${ROOT}) ==="

if command -v uv &>/dev/null; then
    echo "Using uv to create .venv..."
    uv venv .venv --python 3.12 --clear
    echo "Installing requirements..."
    uv pip install -r requirements.txt
    if [[ -f "${ROOT}/../../pyproject.toml" && -d "${ROOT}/../../examples" ]]; then
        echo "Installing parent gso package (editable)..."
        uv pip install -e ../..
    fi
else
    echo "Using ${PYTHON} -m venv..."
    rm -rf .venv
    "$PYTHON" -m venv .venv
    echo "Installing requirements..."
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
    if [[ -f "${ROOT}/../../pyproject.toml" && -d "${ROOT}/../../examples" ]]; then
        echo "Installing parent gso package (editable)..."
        .venv/bin/pip install -e ../..
    fi
fi

echo ""
.venv/bin/python -c "import gso, yaml; print('OK: gso + yaml importable')"
echo ""
echo "Setup complete. Run commands from ${ROOT}:"
echo "  ./pydantic compile <task_id>"
echo "  ./pydantic benchmark <task_id>"
echo "  ./pydantic test <task_id> --from-benchmark"
