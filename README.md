# Pydantic optimization benchmark

Digest-pinned benchmark hub for pydantic GSO tasks. Edit code in `project/`, run
evaluation in Docker.

## Quick start

```bash
git clone git@github.com:Mta-adham/pydantic.git
cd pydantic

source scripts/setup.sh
./pydantic pull-images
./pydantic compile pydantic__pydantic-4a09447
```

**Requires:** Python 3.12+, Docker, `HF_TOKEN` (or `HF_READ_TOKEN`)

## Setup

```bash
source scripts/setup.sh
```

Creates a fresh `.venv`, installs dependencies, and activates it in your shell.

| How | Effect |
|-----|--------|
| `source scripts/setup.sh` | Install + activate (use this) |
| `./scripts/setup.sh` | Install only — then run `source .venv/bin/activate` |

Inside the gso monorepo (`gso/repos/pydantic/`), setup also runs `pip install -e ../..`.

`./pydantic` always uses `repos/pydantic/.venv` — even if the parent gso `.venv` is active.

## Workflow

```bash
TASK=pydantic__pydantic-4a09447

./pydantic compile  $TASK    # checkout project/ + build patch
# edit project/pydantic/*.py
./pydantic compile  $TASK    # rebuild patch
./pydantic benchmark $TASK   # Docker perf eval
./pydantic test $TASK --from-benchmark
```

## Commands

Run `./pydantic --help` for the full list.

| Command | What it does |
|---------|----------------|
| `compile [task]` | Prepare workspace + write `patch.diff` and `predictions.jsonl` |
| `prepare [task]` | Setup only: checkout `project/`, create eval workspace |
| `benchmark [task]` | Apply patch in Docker and measure performance |
| `test [task]` | Run correctness tests (`--from-benchmark` to reuse last run) |
| `reset [task]` | Discard edits in `project/` |
| `pull-images [task]` | Pull pinned Docker images |
| `verify-images [task]` | Check local images match pinned digests (`--force` to re-check) |
| `pin-images [task]` | Record current registry digest in `benchmark.yaml` |

`[task]` is optional — omit to run all tasks in `benchmarks/`.

**Results:** `eval/active/output/summary.txt` or `artemis_results.json` at hub root.

**Active task:** `cat .active_task`

From the parent gso repo: `./scripts/pydantic.sh compile …`

## Tasks

| Task ID | Eval folder |
|---------|-------------|
| `pydantic__pydantic-addf1f9` | `eval/eval-pydantic-addf1f9-addf1f9/` |
| `pydantic__pydantic-4a09447` | `eval/eval-pydantic-4a09447-4a09447/` |
| `pydantic__pydantic-ac9e6ee` | `eval/eval-pydantic-ac9e6ee-ac9e6ee/` |
| `pydantic__pydantic-c2647ab` | `eval/eval-pydantic-c2647ab-c2647ab/` |

## Layout

```text
pydantic/                     hub root — run commands here
  pydantic                    CLI entry point
  scripts/
    setup.sh                  create + activate .venv
    env.sh                    shared helpers
    run.sh                    workflow commands
    images.sh                 Docker image commands
    hub.py                    task definitions + image digests
  requirements.txt
  .venv/                      Python env (gitignored)
  benchmarks/*/benchmark.yaml task defs + pinned image digests
  project/                    pydantic source — edit here
  eval/                       per-task workspaces + results
  artemis_results.json        latest benchmark result
```

## What to edit

| Path | Role |
|------|------|
| `project/` | Edit pydantic source. Checked out to each task's base commit on `compile`. |
| `eval/*/baseline/` | Frozen reference — do not edit |
| `eval/*/output/` | Benchmark and test results |

`project/` is vendored in this repo. First `compile` replaces it with a git clone
for task switching (`project/.git/` is gitignored). Evaluation runs in Docker, not
against your local tree.
