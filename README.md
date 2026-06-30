# Pydantic optimization benchmark

Digest-pinned benchmark hub for pydantic GSO tasks. Edit code in `project/`, run
evaluation in Docker.

## Setup

From the parent gso repo (recommended):

```bash
cd gso
uv venv && source .venv/bin/activate && uv sync
cd repos/pydantic
```

Or optional hub-local env: `source scripts/setup.sh`

**Requires:** Python 3.12+, Docker, `HF_TOKEN` in `gso/.env`

Commands use **whatever Python environment is active** in your shell.

## Workflow

```bash
TASK=pydantic__pydantic-4a09447

commands/pull-images $TASK
commands/compile  $TASK
# edit project/pydantic/*.py
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

**Results:** `eval/active/output/summary.txt` or `artemis_results.json`

## Tasks

| Task ID |
|---------|
| `pydantic__pydantic-addf1f9` |
| `pydantic__pydantic-4a09447` |
| `pydantic__pydantic-ac9e6ee` |
| `pydantic__pydantic-c2647ab` |

## Layout

```text
pydantic/
  commands/          compile, benchmark, test, …
  pydantic           same as commands/pydantic
  project/           edit pydantic source here
  benchmarks/        task defs + pinned digests
  eval/              per-task workspaces + results
```

Do not edit `eval/*/baseline/`. Evaluation runs in Docker, not locally.
