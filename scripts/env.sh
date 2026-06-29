#!/usr/bin/env bash
# Paths and helpers for the pydantic benchmark hub.

pydantic_hub_root() {
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$here/.." && pwd
}

pydantic_gso_root() {
    local hub="${1:-$(pydantic_hub_root)}"
    if [[ -n "${GSO_ROOT_OVERRIDE:-}" ]]; then
        echo "${GSO_ROOT_OVERRIDE}"
        return
    fi
    local parent
    parent="$(cd "$hub/../.." && pwd)"
    echo "$parent"
}

pydantic_export_paths() {
    local hub
    hub="$(pydantic_hub_root)"
    export PYDANTIC_ROOT="$hub"
    export GSO_ROOT="$(pydantic_gso_root "$hub")"
    export GSO_WORKSPACE_ROOT="$hub"
    export GSO_PROJECT_ROOT="$hub/project"
    export GSO_SCRIPTS="${GSO_ROOT}/scripts"
}

pydantic_activate() {
    local hub
    hub="$(pydantic_hub_root)"
    if [[ ! -d "$hub/.venv" ]]; then
        echo "No .venv found. Run: source scripts/setup.sh" >&2
        exit 1
    fi
    cd "$hub"
    # shellcheck disable=SC1091
    source .venv/bin/activate
    if [[ -f .env ]]; then
        set -a
        # shellcheck disable=SC1091
        source .env
        set +a
    elif [[ -f "$hub/../../.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "$hub/../../.env"
        set +a
    fi
}

pydantic_list_tasks() {
    GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}" python3 "${PYDANTIC_ROOT}/scripts/hub.py" list
}

pydantic_task_ids() {
    pydantic_list_tasks | awk -F'\t' '{print $1}'
}

pydantic_workflow() {
    PYTHONPATH="${GSO_ROOT}/examples${PYTHONPATH:+:$PYTHONPATH}" \
        python3 "${GSO_ROOT}/examples/local_patch_workflow.py" "$@"
}

pydantic_eval_dir() {
    local task="$1"
    PYTHONPATH="${GSO_ROOT}/examples" python3 -c \
        "from local_patch_workflow import workspace_dir; print(workspace_dir('${task}'))"
}

pydantic_print_status() {
    PYTHONPATH="${GSO_ROOT}/examples" python3 -c \
        "from local_patch_workflow import format_active_task_status; print(format_active_task_status())" \
        2>/dev/null || true
}
