#!/usr/bin/env bash
# Paths and helpers for the pydantic benchmark hub (self-contained).

pydantic_hub_root() {
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$here/.." && pwd
}

pydantic_export_paths() {
    local hub
    hub="$(pydantic_hub_root)"
    export PYDANTIC_ROOT="$hub"
    export GSO_WORKSPACE_ROOT="$hub"
    export GSO_PROJECT_ROOT="$hub/project"
}

pydantic_workflow_py() {
    local hub="${PYDANTIC_ROOT:-$(pydantic_hub_root)}"
    local wf="$hub/scripts/workflow.py"
    if [[ ! -f "$wf" ]]; then
        echo "workflow not found: $wf" >&2
        return 1
    fi
    echo "$wf"
}

# Load HF_TOKEN etc. from hub .env only.
pydantic_load_env() {
    local hub
    hub="$(pydantic_hub_root)"
    if [[ -f "$hub/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "$hub/.env"
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
    local wf
    wf="$(pydantic_workflow_py)"
    export GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}"
    PYTHONPATH="$(dirname "$wf")${PYTHONPATH:+:$PYTHONPATH}" python3 "$wf" "$@"
}

pydantic_workflow_eval() {
    local py_expr="$1"
    local wf
    wf="$(pydantic_workflow_py)"
    export GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}"
    PYTHONPATH="$(dirname "$wf")" python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('gso_workflow', '${wf}')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
${py_expr}
"
}

pydantic_print_status() {
    pydantic_workflow_eval "print(m.format_active_task_status())" 2>/dev/null || true
}

pydantic_active_task_id() {
    GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}" python3 -c "
import os, yaml
from pathlib import Path
p = Path(os.environ['GSO_WORKSPACE_ROOT']) / '.gso_task_id'
if not p.is_file():
    print('')
else:
    data = yaml.safe_load(p.read_text()) or {}
    print(data.get('instance_id') or '')
"
}
