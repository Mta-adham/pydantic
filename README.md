# Pydantic GSO optimization benchmark

Artemis-importable repo for pydantic GSO tasks. **Edit only `project/`** — evaluation
uses the standard [GSO harness](https://github.com/gso-bench/gso) and public Docker
images (`slimshetty/gso:...`).

## Setup

```bash
git clone git@github.com:Mta-adham/pydantic.git
cd pydantic
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Requires:** Python 3.12+, Docker, `HF_TOKEN` in `.env` (first-time dataset fetch only)

## Workflow

```bash
./compile pydantic__pydantic-4a09447   # sets .gso_task_id + builds patch
# edit project/pydantic/*.py (see Tasks table)
./compile                              # rebuild patch (active task)
./benchmark                            # GSO harness → artemis_results.json
./test --from-benchmark                # correctness from benchmark report
```

`commands/compile` and `./pydantic compile` are equivalent wrappers.

## Commands

| Command | What it does |
|---------|----------------|
| `./compile [task]` | Sync `project/` + build `patch.diff` |
| `./benchmark [task]` | GSO Docker perf eval → `artemis_results.json` |
| `./test [task]` | Correctness (`--from-benchmark` recommended) |
| `commands/reset [task]` | Restore `project/` from baseline |
| `commands/pull-images [task]` | Pull pinned GSO eval images |

Omit the task ID to use the active task (`.gso_task_id`).

**Results:** `artemis_results.json` at repo root (flat numeric metrics) and
`eval/active/output/` (full reports).

## Tasks

| Task ID | API | File(s) to optimize |
|---------|-----|---------------------|
| `pydantic__pydantic-addf1f9` | `BaseModel.__setattr__` | `project/pydantic/main.py` |
| `pydantic__pydantic-4a09447` | `GenericModel.__concrete_name__` | `project/pydantic/generics.py` |
| `pydantic__pydantic-ac9e6ee` | `TypeAdapter.validate_python` | `project/pydantic/_internal/_std_types_schema.py`, `project/pydantic/json_schema.py` |
| `pydantic__pydantic-c2647ab` | `TypeAdapter.validate_strings` | `project/pydantic/type_adapter.py`, `project/pydantic/_internal/_mock_val_ser.py`, `project/pydantic/_internal/_namespace_utils.py` |

## Layout

```text
pydantic/
  project/           edit pydantic source here (agent-visible)
  compile            build patch from project/ edits
  benchmark          GSO harness performance eval
  test               GSO harness correctness eval
  .gso_task_id       active GSO task + pinned eval image digest
  scripts/           harness glue (workflow, env, hub)
  benchmarks/        per-task GSO image pins (benchmark.yaml)
  eval/              generated workspaces (gitignored)
  logs/              harness logs (gitignored)
```

Do not edit `eval/*/baseline/`. Expert grading runs inside GSO's Docker images, not
in this repo.
