#!/usr/bin/env bash
# Core driver: prepare | compile | benchmark | test for all pydantic tasks.
#
# Usage:
#   ./scripts/run.sh prepare [instance_id] [--force]
#   ./scripts/run.sh compile [instance_id] [--force]
#   ./scripts/run.sh benchmark [instance_id] [--reuse-report] [--keep-image] ...
#   ./scripts/run.sh test [instance_id] [--from-benchmark] [--keep-image] ...

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"
pydantic_export_paths

COMMAND="${1:-}"
shift || true

INSTANCE_IDS=()
PREPARE_ARGS=()
PASS_ARGS=()

usage() {
    cat <<'EOF'
Pydantic performance benchmark — prepare, compile, benchmark, test.

Usage (from repos/pydantic/):
  ./pydantic prepare [instance_id] [--force]     # setup eval + checkout project/
  ./pydantic compile [instance_id] [--force]     # prepare if needed, then build patch
  ./pydantic benchmark [instance_id] [--reuse-report] [--keep-image]
  ./pydantic test [--from-benchmark] [instance_id] [--keep-image]
  ./pydantic reset [instance_id]                 # discard edits, restore project/

  commands/compile, commands/benchmark, …          # same as above

Without instance_id: runs all tasks in benchmarks/*/benchmark.yaml.
EOF
    echo ""
    echo "Tasks:"
    pydantic_list_tasks | awk -F'\t' '{printf "  %s\n", $1}'
    exit "${1:-0}"
}

[[ -n "$COMMAND" ]] || usage 1
case "$COMMAND" in
    prepare|compile|benchmark|test|reset) ;;
    -h|--help) usage 0 ;;
    *)
        echo "Unknown command: $COMMAND (expected prepare, compile, benchmark, test, or reset)" >&2
        usage 1
        ;;
esac

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prepare)
            echo "Note: compile always prepares first; --prepare is no longer required." >&2
            shift
            ;;
        --force)
            PREPARE_ARGS+=(--force)
            shift
            ;;
        pydantic__*)
            INSTANCE_IDS+=("$1")
            shift
            ;;
        *)
            PASS_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ ${#INSTANCE_IDS[@]} -eq 0 ]]; then
    mapfile -t INSTANCE_IDS < <(pydantic_task_ids)
fi

if [[ ${#INSTANCE_IDS[@]} -eq 0 ]]; then
    echo "No tasks in ${PYDANTIC_ROOT}/benchmarks/" >&2
    exit 1
fi

pydantic_activate

run_prepare() {
    local iid="$1"
    local quiet="${2:-0}"
    if [[ "$quiet" == "1" ]]; then
        GSO_QUIET_PREPARE=1 GSO_WORKSPACE_ROOT="${GSO_WORKSPACE_ROOT}" \
            "${GSO_SCRIPTS}/compile_patch.sh" "$iid" --prepare "${PREPARE_ARGS[@]}"
    else
        GSO_WORKSPACE_ROOT="${GSO_WORKSPACE_ROOT}" \
            "${GSO_SCRIPTS}/compile_patch.sh" "$iid" --prepare "${PREPARE_ARGS[@]}"
    fi
}

print_comparison_summary() {
    local iid="$1"
    local run_id="${2:-benchmark-${iid}}"
    local eval_dir
    eval_dir=$(GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}" GSO_PROJECT_ROOT="${GSO_PROJECT_ROOT}" \
        python3 -c "
import sys
sys.path.insert(0, '${GSO_ROOT}/examples')
from local_patch_workflow import workspace_dir
print(workspace_dir('${iid}'))
")
    local summary="${eval_dir}/output/summary.txt"

    if [[ -f "$summary" ]]; then
        echo ""
        echo "--- baseline vs optimized (${iid}) ---"
        cat "$summary"
    fi
}

failures=()
echo "=== pydantic ${COMMAND}: ${#INSTANCE_IDS[@]} task(s) ==="
echo "Project:  ${GSO_PROJECT_ROOT}/"
if [[ ${#INSTANCE_IDS[@]} -gt 1 ]]; then
    export GSO_ALLOW_TASK_SWITCH=1
fi
GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}" GSO_PROJECT_ROOT="${GSO_PROJECT_ROOT}" \
    python3 -c "
import sys
sys.path.insert(0, '${GSO_ROOT}/examples')
from local_patch_workflow import format_active_task_status
print(format_active_task_status())
" 2>/dev/null || true

for iid in "${INSTANCE_IDS[@]}"; do
    echo ""
    echo "---------- ${iid} ----------"
    case "$COMMAND" in
        prepare)
            if ! run_prepare "$iid"; then
                failures+=("$iid")
            fi
            ;;
        compile)
            if ! run_prepare "$iid" 1; then
                failures+=("$iid")
                continue
            fi
            if ! GSO_WORKSPACE_ROOT="${GSO_WORKSPACE_ROOT}" \
                "${GSO_SCRIPTS}/compile_patch.sh" "$iid"; then
                failures+=("$iid")
            fi
            ;;
        benchmark)
            if ! GSO_WORKSPACE_ROOT="${GSO_WORKSPACE_ROOT}" \
                "${GSO_SCRIPTS}/benchmark_patches.sh" "$iid" "${PASS_ARGS[@]}"; then
                failures+=("$iid")
            else
                print_comparison_summary "$iid"
            fi
            ;;
        test)
            if ! GSO_WORKSPACE_ROOT="${GSO_WORKSPACE_ROOT}" \
                "${GSO_SCRIPTS}/test_patches.sh" "$iid" "${PASS_ARGS[@]}"; then
                failures+=("$iid")
            else
                print_comparison_summary "$iid"
            fi
            ;;
        reset)
            if ! GSO_WORKSPACE_ROOT="${GSO_WORKSPACE_ROOT}" \
                GSO_PROJECT_ROOT="${GSO_PROJECT_ROOT}" pydantic_run_py reset "$iid"; then
                failures+=("$iid")
            fi
            ;;
    esac
done

echo ""
if ((${#failures[@]} > 0)); then
    echo "Failed (${#failures[@]}): ${failures[*]}"
    exit 1
fi
echo "All pydantic ${COMMAND} steps succeeded (${#INSTANCE_IDS[@]} task(s))."
