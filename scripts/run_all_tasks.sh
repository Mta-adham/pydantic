#!/usr/bin/env bash
# Run compile → benchmark → test for every task and validate GSO harness outputs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"
pydantic_export_paths
pydantic_load_env
cd "${SCRIPT_DIR}/.."

PY="${PY:-python3}"
export GSO_WORKSPACE_ROOT="${PYDANTIC_ROOT}"
export GSO_PROJECT_ROOT="${PYDANTIC_ROOT}/project"

verify_task_state() {
    local iid="$1"
    "${PY}" - "${iid}" "${PYDANTIC_ROOT}/scripts/workflow.py" <<'PY'
import importlib.util, json, subprocess, sys
from pathlib import Path

wf = sys.argv[2]
spec = importlib.util.spec_from_file_location("gso_workflow", wf)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

iid = sys.argv[1]
root = m.benchmark_root()
active = m.read_active_instance_id()
if active != iid:
    print(f"active task {active!r} != {iid!r}")
    sys.exit(1)
if not m.project_commit_matches_task(iid):
    print("project/ commit mismatch")
    sys.exit(1)
meta = json.loads((m.workspace_dir(iid) / "metadata.json").read_text())
if meta.get("instance_id") != iid:
    print("metadata instance_id mismatch")
    sys.exit(1)
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(root)/"project",
                      capture_output=True, text=True, check=True).stdout.strip()
print(f"active={active} project_head={head[:12]}")
PY
}

mapfile -t TASKS < <(pydantic_task_ids)
echo "=== Running ${#TASKS[@]} task(s) ==="

failures=()

for iid in "${TASKS[@]}"; do
    echo ""
    echo "========== ${iid} =========="

    echo "--- compile ---"
    if ! ./compile "${iid}"; then
        failures+=("compile:${iid}")
        continue
    fi
    if ! verify_task_state "${iid}"; then
        failures+=("task_state:${iid}")
        continue
    fi
    echo "  OK: task state after compile"

    echo "--- benchmark ---"
    if ! ./benchmark "${iid}"; then
        failures+=("benchmark:${iid}")
        continue
    fi

    echo "--- test ---"
    if ! ./test "${iid}"; then
        failures+=("test:${iid}")
        continue
    fi

    if ! python3 - "${iid}" "${PYDANTIC_ROOT}" <<'PY'
import json, sys
from pathlib import Path
import yaml

iid, hub_s = sys.argv[1:3]
hub = Path(hub_s)
slug = iid.split("__", 1)[-1]
defn = yaml.safe_load((hub / "benchmarks" / slug / "benchmark.yaml").read_text())
expected_digest = defn["target"]["digest"]

robust = json.loads((hub / "artemis_results_robust.json").read_text())
tests = json.loads((hub / "tests_artemis_results.json").read_text())
numeric = json.loads((hub / "artemis_results.json").read_text())

errors = []
if robust.get("instance_id") != iid:
    errors.append(f"robust instance_id={robust.get('instance_id')!r}")
got = (robust.get("provenance") or {}).get("image_digest")
if got != expected_digest:
    errors.append(f"digest {got} != {expected_digest}")
if tests.get("instance_id") != iid:
    errors.append(f"tests instance_id={tests.get('instance_id')!r}")
if not numeric:
    errors.append("empty artemis_results.json")

code_changes = (robust.get("patch") or {}).get("code_changes", True)
if code_changes and tests.get("test_passed") is not True:
    errors.append(f"test_passed={tests.get('test_passed')!r}")

if not numeric.get("tests_total"):
    errors.append("numeric missing tests_total")
elif numeric.get("tests_passed") != numeric.get("tests_total"):
    errors.append(
        f"tests_passed={numeric.get('tests_passed')} != "
        f"tests_total={numeric.get('tests_total')}"
    )
if "runtime_s_baseline" not in numeric:
    errors.append("numeric missing runtime_s_baseline")

run_id = f"benchmark-{iid}"
logs = list((hub / "logs" / "run_evaluation").rglob(f"*{run_id}*.report.json"))
harness_report = str(logs[0]) if logs else None
test_harness_report = tests.get("harness_report")
if not test_harness_report or not Path(test_harness_report).is_file():
    errors.append(f"missing tests harness_report: {test_harness_report!r}")
if not harness_report:
    errors.append("no benchmark harness report under logs/run_evaluation/")

if errors:
    print("VALIDATION ERRORS:", "; ".join(errors))
    raise SystemExit(1)
print(f"OK digest={got[:20]}... test_passed={tests.get('test_passed')} benchmark_report={harness_report} test_report={test_harness_report}")
PY
    then
        failures+=("validate:${iid}")
        continue
    fi

    echo "  OK: ${iid}"
done

echo ""
echo "=== Done: ${#failures[@]} failure(s) ==="
if ((${#failures[@]} > 0)); then
    printf '  %s\n' "${failures[@]}"
    exit 1
fi
echo "All tasks passed."
