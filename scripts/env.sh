#!/usr/bin/env bash
# Environment for the standalone pydantic benchmark project.

_pydantic_root() {
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$here/.." && pwd
}

_pydantic_gso_root() {
    local root
    root="$(_pydantic_root)"
    cd "$root/../.." && pwd
}

pydantic_activate() {
    local gso_root
    gso_root="$(_pydantic_gso_root)"
    cd "$gso_root"

    if [[ ! -d ".venv" ]]; then
        echo "Error: Python environment not found at ${gso_root}/.venv" >&2
        echo "From the parent repo root, run: uv venv && uv sync" >&2
        exit 1
    fi

    # shellcheck disable=SC1091
    source .venv/bin/activate
    if [[ -f .env ]]; then
        set -a
        # shellcheck disable=SC1091
        source .env
        set +a
    fi
}

pydantic_export_paths() {
    local root
    root="$(_pydantic_root)"
    export PYDANTIC_ROOT="$root"
    export GSO_ROOT="$(_pydantic_gso_root)"
    export GSO_WORKSPACE_ROOT="${root}"
    export GSO_PROJECT_ROOT="${root}/project"
    export GSO_SCRIPTS="${GSO_ROOT}/scripts"
}

pydantic_list_tasks() {
    GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}" \
        python3 "${PYDANTIC_ROOT}/scripts/hub.py" list
}

pydantic_task_ids() {
    pydantic_list_tasks | awk -F'\t' '{print $1}'
}

pydantic_run_py() {
    python "${GSO_ROOT}/examples/local_patch_workflow.py" "$@"
}
