#!/usr/bin/env bash
# pull | verify | pin — Docker images from benchmarks/*/benchmark.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"
pydantic_export_paths
pydantic_activate

COMMAND="${1:-}"
shift || true

INSTANCE_IDS=()
FORCE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        pydantic__*) INSTANCE_IDS+=("$1"); shift ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 1
            ;;
    esac
done

usage() {
    cat <<'EOF'
Usage:
  ./pydantic images pull [instance_id ...]
  ./pydantic images verify [--force] [instance_id ...]
  ./pydantic images pin [instance_id ...]

Without instance_id: all tasks in benchmarks/*/benchmark.yaml
EOF
    exit "${1:-0}"
}

[[ -n "$COMMAND" ]] || usage 1
case "$COMMAND" in
    pull|verify|pin) ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown command: $COMMAND" >&2; usage 1 ;;
esac

if [[ ${#INSTANCE_IDS[@]} -eq 0 ]]; then
    mapfile -t INSTANCE_IDS < <(
        GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}" \
            python3 "${PYDANTIC_ROOT}/scripts/hub.py" list \
            | awk -F'\t' '{print $1}'
    )
fi

if [[ ${#INSTANCE_IDS[@]} -eq 0 ]]; then
    echo "No tasks in ${PYDANTIC_ROOT}/benchmarks/" >&2
    exit 1
fi

EXTRA=()
[[ "$FORCE" == true && "$COMMAND" == verify ]] && EXTRA+=(--force)

for id in "${INSTANCE_IDS[@]}"; do
    echo "=== ${COMMAND} ${id} ==="
    GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}" \
        python3 "${PYDANTIC_ROOT}/scripts/hub.py" "$COMMAND" "$id" "${EXTRA[@]}"
done

echo ""
echo "Done."
