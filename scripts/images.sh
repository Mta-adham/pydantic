#!/usr/bin/env bash
# Docker image commands: pull-images | verify-images | pin-images
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"

pydantic_export_paths
pydantic_activate

CMD="${1:?command required}"
shift

case "$CMD" in
    pull-images)   ACTION=pull ;;
    verify-images) ACTION=verify ;;
    pin-images)    ACTION=pin ;;
    *)
        echo "Unknown command: $CMD" >&2
        exit 1
        ;;
esac

TASK_IDS=()
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        pydantic__*) TASK_IDS+=("$1"); shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ ${#TASK_IDS[@]} -eq 0 ]]; then
    mapfile -t TASK_IDS < <(pydantic_task_ids)
fi
if [[ ${#TASK_IDS[@]} -eq 0 ]]; then
    echo "No tasks in ${PYDANTIC_ROOT}/benchmarks/" >&2
    exit 1
fi

EXTRA=()
[[ "$FORCE" == true && "$ACTION" == verify ]] && EXTRA+=(--force)

echo "=== ${CMD} (${#TASK_IDS[@]} task(s)) ==="

for task in "${TASK_IDS[@]}"; do
    echo ">> ${task}"
    GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}" \
        python3 "${PYDANTIC_ROOT}/scripts/hub.py" "$ACTION" "$task" "${EXTRA[@]}"
done

echo ""
echo "Done."
