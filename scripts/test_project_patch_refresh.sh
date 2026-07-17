#!/usr/bin/env bash
# Verify compile (and patch rebuild used by benchmark/test) always captures
# current project/ edits for every task — without wiping them on prepare.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"
pydantic_export_paths
pydantic_load_env
cd "${PYDANTIC_ROOT}"

MARKER="GSO_PROJECT_EDIT_MARKER_$$"
failures=()

mapfile -t TASKS < <(pydantic_task_ids)
echo "=== test_project_patch_refresh (${#TASKS[@]} tasks) ==="
echo "marker: $MARKER"

verify_task() {
    local iid="$1"
    "$(pydantic_python)" - "$iid" "${PYDANTIC_ROOT}" "${MARKER}" <<'PY'
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

iid, hub_s, marker = sys.argv[1:4]
hub = Path(hub_s)
os.environ["GSO_PROJECT_ROOT"] = str(hub / "project")
os.environ["GSO_WORKSPACE_ROOT"] = str(hub)

wf = hub / "scripts" / "workflow.py"
spec = importlib.util.spec_from_file_location("wf", wf)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

meta = m.load_metadata(iid)
rel_files = m.patch_file_list(meta, iid) or list(meta.get("files", []))
if not rel_files:
    raise SystemExit("no patch files in metadata")
rel = rel_files[0]
target = m.project_root() / rel
original = target.read_text()
if rel.endswith((".c", ".h", ".cpp", ".hpp")):
    injection = f"\n/* {marker} */\n"
else:
    injection = f"\n# {marker}\n"
target.write_text(original + injection)

proc = subprocess.run(["./compile", iid], cwd=hub, text=True, capture_output=True)
if proc.returncode != 0:
    target.write_text(original)
    print(proc.stdout)
    print(proc.stderr)
    raise SystemExit(f"compile after edit failed: {proc.returncode}")

after_compile = target.read_text()
if marker not in after_compile:
    target.write_text(original)
    raise SystemExit(
        f"BUG: ./compile wiped project/ edit in {rel} "
        "(force-checkout discarded uncommitted changes)"
    )

patch_text = (m.workspace_dir(iid) / "patch.diff").read_text()
pred = json.loads((m.workspace_dir(iid) / "predictions.jsonl").read_text().splitlines()[0])
if marker not in patch_text or marker not in pred.get("model_patch", ""):
    target.write_text(original)
    raise SystemExit(
        f"BUG: compile patch missing project/ edit marker in {rel}"
    )

m.build_patch(iid, model_name="local-edit", placeholder_on_unchanged=True)
patch2 = (m.workspace_dir(iid) / "patch.diff").read_text()
if marker not in patch2:
    target.write_text(original)
    raise SystemExit("BUG: build_patch (benchmark/test path) missing project/ edit")

meta_patch = m._patch_metadata(iid)
if not meta_patch.get("code_changes"):
    target.write_text(original)
    raise SystemExit(f"BUG: code_changes=False after real edit: {meta_patch}")

target.write_text(original)
m.build_patch(iid, model_name="local-edit", placeholder_on_unchanged=True)
print(f"OK {iid}: compile preserves edits; patch/predictions include project/ change ({rel})")
PY
}

for iid in "${TASKS[@]}"; do
    echo ""
    echo ">> ${iid}"
    if ! ./compile "${iid}"; then
        failures+=("compile:${iid}")
        continue
    fi
    if ! verify_task "${iid}"; then
        failures+=("inject_or_verify:${iid}")
        continue
    fi
done

echo ""
if ((${#failures[@]} > 0)); then
    echo "Failed: ${failures[*]}"
    exit 1
fi
echo "All tasks: project/ edits survive compile and appear in harness patch."
