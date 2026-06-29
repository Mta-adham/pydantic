#!/usr/bin/env bash
# End-to-end check: compile → benchmark → test for each pydantic task.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUB_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"
pydantic_export_paths
cd "${HUB_ROOT}"

PY="${PYDANTIC_ROOT}/.venv/bin/python3"
export GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}"
export GSO_PROJECT_ROOT="${PYDANTIC_ROOT}/project"

failures=()
checks=0

check() {
    local name="$1"
    shift
    checks=$((checks + 1))
    if "$@"; then
        echo "  OK: ${name}"
    else
        echo "  FAIL: ${name}" >&2
        failures+=("${name}")
    fi
}

verify_task_state() {
    local iid="$1"
    "${PY}" - "${iid}" <<'PY'
import json, subprocess, sys
sys.path.insert(0, f"{__import__('os').environ['GSO_ROOT']}/examples")
from local_patch_workflow import (
    benchmark_root, project_commit_matches_task, read_active_instance_id, workspace_dir
)
from pathlib import Path

iid = sys.argv[1]
root = benchmark_root()
active = read_active_instance_id()
if active != iid:
    print(f"active task {active!r} != {iid!r}")
    sys.exit(1)
if not project_commit_matches_task(iid):
    print("project/ commit mismatch")
    sys.exit(1)
link = root / "eval" / "active"
if not link.is_symlink():
    print("eval/active is not a symlink")
    sys.exit(1)
meta = json.loads((workspace_dir(iid) / "metadata.json").read_text())
if meta.get("instance_id") != iid:
    print("metadata instance_id mismatch")
    sys.exit(1)
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(root)/"project",
                      capture_output=True, text=True, check=True).stdout.strip()
print(f"active={active} project_head={head[:12]}")
PY
}

verify_benchmark_output() {
    local iid="$1"
    "${PY}" - "${iid}" "${PYDANTIC_ROOT}" <<'PY'
import json, sys
from pathlib import Path

import yaml

iid, hub = sys.argv[1], Path(sys.argv[2])
slug = iid.split("__", 1)[-1]
defn = yaml.safe_load((hub / "benchmarks" / slug / "benchmark.yaml").read_text())
expected_digest = defn["target"]["digest"]

ws = hub / "eval" / f"eval-{slug.replace('pydantic-', 'pydantic-')}"
# workspace dir naming: eval-pydantic-{short}-{short}
for d in (hub / "eval").iterdir():
    if d.is_dir() and (d / "metadata.json").exists():
        meta = json.loads((d / "metadata.json").read_text())
        if meta.get("instance_id") == iid:
            ws = d
            break

for name in ("output/artemis_results.json",):
    path = ws / name
    if not path.exists():
        print(f"missing {path}")
        sys.exit(1)
    data = json.loads(path.read_text())
    if data.get("instance_id") != iid:
        print(f"wrong instance_id in {name}")
        sys.exit(1)
    prov = data.get("provenance") or {}
    got = prov.get("image_digest")
    if got != expected_digest:
        print(f"digest mismatch in {name}: {got} != {expected_digest}")
        sys.exit(1)

hub_copy = hub / "artemis_results.json"
if hub_copy.exists():
    data = json.loads(hub_copy.read_text())
    if data.get("instance_id") != iid:
        print(f"hub artemis_results.json is for {data.get('instance_id')}, not {iid}")
        sys.exit(1)
    if (data.get("provenance") or {}).get("image_digest") != expected_digest:
        print("hub artemis_results.json digest mismatch")
        sys.exit(1)
print(f"digest={got[:20]}...")
PY
}

verify_test_output() {
    local iid="$1"
    "${PY}" - "${iid}" "${PYDANTIC_ROOT}" <<'PY'
import json, sys
from pathlib import Path

iid, hub = sys.argv[1], Path(sys.argv[2])
for d in (hub / "eval").iterdir():
    if not d.is_dir():
        continue
    meta_path = d / "metadata.json"
    if not meta_path.exists():
        continue
    if json.loads(meta_path.read_text()).get("instance_id") != iid:
        continue
    path = d / "output" / "tests_artemis_results.json"
    if not path.exists():
        print(f"missing {path}")
        sys.exit(1)
    data = json.loads(path.read_text())
    if data.get("instance_id") != iid:
        print("wrong instance_id in tests_artemis_results.json")
        sys.exit(1)
    print(f"test_passed={data.get('summary', {}).get('test_passed')}")
    sys.exit(0)
print("workspace not found")
sys.exit(1)
PY
}

mapfile -t TASKS < <(pydantic_task_ids)
echo "=== Verifying ${#TASKS[@]} task(s) ==="

for iid in "${TASKS[@]}"; do
    echo ""
    echo "========== ${iid} =========="

    echo "--- compile ---"
    if ! ./pydantic compile "${iid}"; then
        failures+=("compile:${iid}")
        continue
    fi
    check "task state after compile" verify_task_state "${iid}"

    echo "--- benchmark ---"
    if ! ./pydantic benchmark "${iid}"; then
        failures+=("benchmark:${iid}")
        continue
    fi
    check "benchmark outputs + digest" verify_benchmark_output "${iid}"

    echo "--- test ---"
    if ! ./pydantic test "${iid}" --from-benchmark; then
        failures+=("test:${iid}")
        continue
    fi
    check "test outputs" verify_test_output "${iid}"
done

echo ""
echo "=== Summary: ${checks} checks, ${#failures[@]} failure(s) ==="
if ((${#failures[@]} > 0)); then
    printf '  %s\n' "${failures[@]}"
    exit 1
fi
echo "All tasks passed."
