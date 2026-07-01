#!/usr/bin/env bash
# Workflow commands: prepare | compile | benchmark | test | reset
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"

pydantic_export_paths
pydantic_load_env

COMMAND="${1:?command required}"
shift

TASK_IDS=()
FORCE=false
EXTRA=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        pydantic__*) TASK_IDS+=("$1"); shift ;;
        *) EXTRA+=("$1"); shift ;;
    esac
done

if [[ ${#TASK_IDS[@]} -eq 0 ]]; then
    case "$COMMAND" in
        compile|benchmark|test|prepare|reset)
            active="$(pydantic_active_task_id)"
            if [[ -z "$active" ]]; then
                echo "No active task. Prepare one first, e.g.:" >&2
                echo "  ./compile pydantic__pydantic-4a09447" >&2
                exit 1
            fi
            TASK_IDS=("$active")
            ;;
        *)
            mapfile -t TASK_IDS < <(pydantic_task_ids)
            ;;
    esac
fi
if [[ ${#TASK_IDS[@]} -eq 0 ]]; then
    echo "No tasks in ${PYDANTIC_ROOT}/benchmarks/" >&2
    exit 1
fi

PREPARE_EXTRA=()
[[ "$FORCE" == true ]] && PREPARE_EXTRA+=(--force)

run_prepare() {
    local task="$1"
    local quiet="${2:-0}"
    local args=(setup "$task" "${PREPARE_EXTRA[@]}")
    if [[ "$quiet" == "1" ]]; then
        GSO_QUIET_PREPARE=1 pydantic_workflow "${args[@]}"
    else
        pydantic_workflow "${args[@]}"
    fi
}

show_summary() {
    local task="$1"
    local summary
    summary="$(pydantic_eval_dir "$task")/output/summary.txt"
    if [[ -f "$summary" ]]; then
        echo ""
        echo "--- ${task} ---"
        cat "$summary"
    fi
}

failures=()

echo "=== ${COMMAND} (${#TASK_IDS[@]} task(s)) ==="
echo "project/: ${GSO_PROJECT_ROOT}/"
[[ ${#TASK_IDS[@]} -gt 1 ]] && export GSO_ALLOW_TASK_SWITCH=1
pydantic_print_status

for task in "${TASK_IDS[@]}"; do
    echo ""
    echo ">> ${task}"
    case "$COMMAND" in
        prepare)
            run_prepare "$task" || failures+=("$task")
            ;;
        compile)
            run_prepare "$task" 1 || { failures+=("$task"); continue; }
            pydantic_workflow patch "$task" \
                --model-name local-edit \
                --placeholder-on-unchanged || failures+=("$task")
            ;;
        benchmark)
            if pydantic_workflow benchmark "$task" "${EXTRA[@]}"; then
                show_summary "$task"
            else
                failures+=("$task")
            fi
            ;;
        test)
            if pydantic_workflow test "$task" "${EXTRA[@]}"; then
                show_summary "$task"
            else
                failures+=("$task")
            fi
            ;;
        reset)
            GSO_PROJECT_ROOT="${GSO_PROJECT_ROOT}" \
                pydantic_workflow reset "$task" || failures+=("$task")
            ;;
        *)
            echo "Unknown command: $COMMAND" >&2
            exit 1
            ;;
    esac
done

echo ""
if ((${#failures[@]} > 0)); then
    echo "Failed: ${failures[*]}"
    exit 1
fi
echo "Done (${#TASK_IDS[@]} task(s))."
