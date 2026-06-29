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
    if [[ -n "${GSO_ROOT_OVERRIDE:-}" ]]; then
        echo "${GSO_ROOT_OVERRIDE}"
        return
    fi
    local monorepo
    monorepo="$(cd "$root/../.." && pwd)"
    if [[ -f "${monorepo}/pyproject.toml" && -d "${monorepo}/examples" ]]; then
        echo "$monorepo"
        return
    fi
    echo "$monorepo"
}

pydantic_activate() {
    local root
    root="$(_pydantic_root)"
    cd "$root"

    if [[ ! -d ".venv" ]]; then
        echo "Error: Python environment not found at ${root}/.venv" >&2
        echo "From the pydantic hub root, run:" >&2
        echo "  python3 -m venv .venv" >&2
        echo "  source .venv/bin/activate" >&2
        echo "  pip install -r requirements.txt" >&2
        if [[ -f "${root}/../../pyproject.toml" ]]; then
            echo "  pip install -e ../..    # gso monorepo: local harness + workflow" >&2
        fi
        exit 1
    fi

    # shellcheck disable=SC1091
    source .venv/bin/activate
    if [[ -f .env ]]; then
        set -a
        # shellcheck disable=SC1091
        source .env
        set +a
    elif [[ -f "${root}/../../.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "${root}/../../.env"
        set +a
    fi
}

pydantic_export_paths() {
    local root gso_root
    root="$(_pydantic_root)"
    gso_root="$(_pydantic_gso_root)"
    export PYDANTIC_ROOT="$root"
    export GSO_ROOT="$gso_root"
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
