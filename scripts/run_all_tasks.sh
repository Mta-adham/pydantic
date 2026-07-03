#!/usr/bin/env bash
# Run compile → benchmark → test for every task and validate GSO harness outputs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"
pydantic_export_paths
pydantic_load_env
cd "${SCRIPT_DIR}/.."

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

    echo "--- benchmark ---"
    if ! ./benchmark "${iid}"; then
        failures+=("benchmark:${iid}")
        continue
    fi

    echo "--- test ---"
    if ! ./test "${iid}" --from-benchmark; then
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
numeric = hub / "artemis_results.json"

errors = []
if robust.get("instance_id") != iid:
    errors.append(f"robust instance_id={robust.get('instance_id')!r}")
got = (robust.get("provenance") or {}).get("image_digest")
if got != expected_digest:
    errors.append(f"digest {got} != {expected_digest}")
if tests.get("instance_id") != iid:
    errors.append(f"tests instance_id={tests.get('instance_id')!r}")
if not numeric.is_file():
    errors.append("missing artemis_results.json")

code_changes = (robust.get("patch") or {}).get("code_changes", True)
if code_changes and tests.get("test_passed") is not True:
    errors.append(f"test_passed={tests.get('test_passed')!r}")

run_id = f"benchmark-{iid}"
logs = list((hub / "logs" / "run_evaluation").rglob(f"*{run_id}*.report.json"))
harness_report = str(logs[0]) if logs else None
if not harness_report:
    errors.append("no harness report under logs/run_evaluation/")

if errors:
    print("VALIDATION ERRORS:", "; ".join(errors))
    raise SystemExit(1)
print(f"OK digest={got[:20]}... test_passed={tests.get('test_passed')} report={harness_report}")
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
