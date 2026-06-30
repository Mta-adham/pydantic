# Pydantic optimization benchmark

Self-contained, digest-pinned benchmark hub for pydantic GSO tasks. Edit code in
`project/`, run evaluation in Docker.

## Setup

```bash
git clone git@github.com:Mta-adham/pydantic.git
cd pydantic
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Optional: `source scripts/setup.sh` (creates `.venv` and installs deps).

**Requires:** Python 3.12+, Docker, `HF_TOKEN` in `.env` (hub root)

Commands use **whatever Python environment is active** in your shell.

## Workflow

```bash
TASK=pydantic__pydantic-4a09447

commands/pull-images $TASK
commands/compile  $TASK
# edit the file(s) for this task under project/ (see Tasks table)
commands/compile  $TASK
commands/benchmark $TASK
commands/test $TASK --from-benchmark
```

Same via `./pydantic compile …` etc.

## Commands

| Command | What it does |
|---------|----------------|
| `commands/compile [task]` | Checkout `project/` + build `patch.diff` |
| `commands/prepare [task]` | Setup eval workspace only |
| `commands/benchmark [task]` | Docker performance eval |
| `commands/test [task]` | Correctness tests (`--from-benchmark`) |
| `commands/reset [task]` | Restore `project/` from baseline |
| `commands/pull-images [task]` | Pull pinned Docker images |
| `commands/verify-images [task]` | Check image digests |
| `commands/pin-images [task]` | Pin digest in `benchmark.yaml` |

`compile` includes prepare — you don't need `prepare` to switch tasks.

**Results:** `eval/active/output/artemis_results.json` (all-numeric metrics),
`eval/active/output/artemis_results_robust.json` (full strings + provenance),
or hub-root `artemis_results.json` (numeric copy from latest benchmark).

Numeric `artemis_results.json` is a **flat** map of metric name → finite number
(e.g. `runtime_s_baseline`, `vs_baseline_speedup`, `per_test_0_speedup`). No
nested objects. `instance_id` = task index, `run_id` = 4-digit code,
`recorded_at` = Unix timestamp, booleans as `0`/`1`.

## Tasks

Edit the listed file(s) under `project/` for each task.

| Task ID | API | File(s) to optimize |
|---------|-----|---------------------|
| `pydantic__pydantic-addf1f9` | `BaseModel.__setattr__` | `project/pydantic/main.py` |
| `pydantic__pydantic-4a09447` | `GenericModel.__concrete_name__` | `project/pydantic/generics.py` |
| `pydantic__pydantic-ac9e6ee` | `TypeAdapter.validate_python` | `project/pydantic/_internal/_std_types_schema.py`, `project/pydantic/json_schema.py` |
| `pydantic__pydantic-c2647ab` | `TypeAdapter.validate_strings` | `project/pydantic/type_adapter.py`, `project/pydantic/_internal/_mock_val_ser.py`, `project/pydantic/_internal/_namespace_utils.py` |

## Layout

```text
pydantic/
  commands/          compile, benchmark, test, …
  pydantic           same as commands/pydantic
  scripts/           workflow, env, hub helpers
  project/           edit pydantic source here
  benchmarks/        task defs + pinned digests
  eval/              per-task workspaces + results
  logs/              harness logs (created on benchmark/test)
```

Do not edit `eval/*/baseline/`. Evaluation runs in Docker, not locally.

All scripts and workflow logic live inside this repo. The only external runtime
dependency is the `gsobench` Python package (installed via `requirements.txt`).
