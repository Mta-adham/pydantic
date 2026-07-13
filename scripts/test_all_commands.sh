#!/usr/bin/env bash
# E2E: compile → benchmark → test → reset → compile for every task.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"
pydantic_export_paths
pydantic_load_env
cd "${SCRIPT_DIR}/.."

LOG="${PYDANTIC_ROOT}/logs/test_all_commands.log"
mkdir -p "${PYDANTIC_ROOT}/logs"
exec > >(tee -a "$LOG") 2>&1

echo "=== test_all_commands started $(date -Is) ==="
echo "log: $LOG"

mapfile -t TASKS < <(pydantic_task_ids)
echo "tasks: ${TASKS[*]}"

failures=()

verify_outputs() {
    local iid="$1"
    python3 - "${iid}" "${PYDANTIC_ROOT}" <<'PY'
import json, sys
from pathlib import Path
import yaml

iid, hub_s = sys.argv[1:3]
hub = Path(hub_s)
defn = None
for path in sorted((hub / "eval").glob("*/benchmark.yaml")):
    data = yaml.safe_load(path.read_text()) or {}
    if data.get("instance_id") == iid:
        defn = data
        break
if defn is None:
    raise SystemExit(f"No eval/*/benchmark.yaml for {iid}")
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
if "code_changes" not in tests:
    errors.append("tests_artemis_results.json missing code_changes")
if "verdict" not in tests:
    errors.append("tests_artemis_results.json missing verdict")
if not numeric:
    errors.append("empty artemis_results.json")
if not numeric.get("tests_total"):
    errors.append("numeric missing tests_total")
elif numeric.get("tests_passed") != numeric.get("tests_total"):
    errors.append(
        f"tests_passed={numeric.get('tests_passed')} != "
        f"tests_total={numeric.get('tests_total')}"
    )
if "runtime_s_baseline" not in numeric:
    errors.append("numeric missing runtime_s_baseline")
if "tests" in robust:
    errors.append("robust still has stale tests section")
test_harness_report = tests.get("harness_report")
if not test_harness_report or not Path(test_harness_report).is_file():
    errors.append(f"missing tests harness_report: {test_harness_report!r}")

if errors:
    print("VALIDATION ERRORS:", "; ".join(errors))
    raise SystemExit(1)
print(f"OK outputs for {iid}")
PY
}

verify_reset() {
    local iid="$1"
    python3 - "${iid}" "${PYDANTIC_ROOT}" <<'PY'
import importlib.util, subprocess, sys
from pathlib import Path

wf = Path(sys.argv[2]) / "scripts" / "workflow.py"
spec = importlib.util.spec_from_file_location("wf", wf)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

iid = sys.argv[1]
ws = m.workspace_dir(iid)
baseline = ws / "baseline"
project = m.project_root()
meta_files = m.patch_file_list(m.load_metadata(iid))
for rel in meta_files:
    b = (baseline / rel).read_text()
    p = (project / rel).read_text()
    if b != p:
        print(f"reset failed: {rel} differs from baseline")
        raise SystemExit(1)
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project,
                      capture_output=True, text=True, check=True).stdout.strip()
print(f"OK reset: project matches baseline ({len(meta_files)} file(s)), head={head[:12]}")
PY
}

for iid in "${TASKS[@]}"; do
    echo ""
    echo "========== ${iid} =========="

    for cmd in compile benchmark test; do
        echo "--- ${cmd} ---"
        if ! "./${cmd}" "${iid}"; then
            failures+=("${cmd}:${iid}")
            echo "FAILED: ${cmd} ${iid}"
            continue 2
        fi
    done

    echo "--- validate outputs ---"
    if ! verify_outputs "${iid}"; then
        failures+=("validate:${iid}")
        continue
    fi

    echo "--- reset ---"
    if ! ./reset "${iid}"; then
        failures+=("reset:${iid}")
        continue
    fi
    if ! verify_reset "${iid}"; then
        failures+=("reset_verify:${iid}")
        continue
    fi

    echo "--- compile (after reset) ---"
    if ! ./compile "${iid}"; then
        failures+=("compile_after_reset:${iid}")
        continue
    fi

    echo "  OK: ${iid} (all commands)"
done

echo ""
echo "=== test_all_commands finished $(date -Is) ==="
echo "failures: ${#failures[@]}"
if ((${#failures[@]} > 0)); then
    printf '  %s\n' "${failures[@]}"
    exit 1
fi
echo "All tasks passed all commands."
