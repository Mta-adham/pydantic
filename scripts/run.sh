#!/usr/bin/env bash
# Workflow commands: compile | benchmark | test | reset
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
ALL_TASKS=false
EXTRA=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        --all) ALL_TASKS=true; shift ;;
        pydantic__*) TASK_IDS+=("$1"); shift ;;
        *) EXTRA+=("$1"); shift ;;
    esac
done

if [[ "$ALL_TASKS" == true ]]; then
    if [[ ${#TASK_IDS[@]} -gt 0 ]]; then
        echo "Do not combine --all with explicit task ids." >&2
        exit 1
    fi
    mapfile -t TASK_IDS < <(pydantic_task_ids)
    if [[ ${#TASK_IDS[@]} -eq 0 ]]; then
        echo "No tasks found under eval/." >&2
        exit 1
    fi
fi

if [[ ${#TASK_IDS[@]} -eq 0 ]]; then
    case "$COMMAND" in
        compile|benchmark|test|reset)
            active="$(pydantic_active_task_id)"
            if [[ -z "$active" ]]; then
                echo "No active task. Prepare one first, e.g.:" >&2
                echo "  ./compile pydantic__pydantic-4a09447" >&2
                echo "  ./compile --all" >&2
                exit 1
            fi
            TASK_IDS=("$active")
            ;;
        *)
            echo "Unknown command: $COMMAND" >&2
            exit 1
            ;;
    esac
fi

if [[ "$COMMAND" == "compile" || "$COMMAND" == "reset" ]] && [[ ${#EXTRA[@]} -gt 0 ]]; then
    echo "Unknown argument(s) for ${COMMAND}: ${EXTRA[*]}" >&2
    echo "Usage: ./${COMMAND} [task-id ...] [--all] [--force]" >&2
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
    local summary="${PYDANTIC_ROOT}/summary.txt"
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
        compile)
            run_prepare "$task" 1 || { failures+=("$task"); continue; }
            pydantic_workflow patch "$task" \
                --model-name local-edit \
                --placeholder-on-unchanged || failures+=("$task")
            ;;
        benchmark)
            # Prepare syncs project/ to the task commit (preserving edits when HEAD matches).
            run_prepare "$task" 1 || { failures+=("$task"); continue; }
            if pydantic_workflow benchmark "$task" "${EXTRA[@]}"; then
                show_summary "$task"
            else
                failures+=("$task")
            fi
            ;;
        test)
            run_prepare "$task" 1 || { failures+=("$task"); continue; }
            pydantic_workflow test "$task" "${EXTRA[@]}" || failures+=("$task")
            ;;
        reset)
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
