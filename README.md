# Pydantic optimization benchmark

Standalone git repository (benchmark hub). Task definitions live in `benchmarks/*/benchmark.yaml`.
Evaluation runs in Docker — not your local `project/` checkout.

Clone the upstream repo into `project/` once:

```bash
git clone https://github.com/pydantic/pydantic.git project
```

## Layout

```text
repos/pydantic/
  pydantic                 ← entry point
  commands/                ← compile, benchmark, test, prepare, …
  benchmarks/              ← task definitions (source of truth)
  project/                 ← pydantic clone (edit here)
  eval/
    active -> …            ← current task
    eval-pydantic-*/       ← per-task results (output/artemis_results.json)
  artemis_results.json     ← latest benchmark copy (hub root, overwritten each run)
  scripts/                 ← env.sh, run.sh, hub.py, images.sh
```

## Commands

From the hub root:

```bash
cd repos/pydantic

./pydantic images pull
./pydantic compile pydantic__pydantic-ac9e6ee   # prepares + builds patch

# edit project/...
./pydantic prepare pydantic__pydantic-4a09447
./pydantic compile pydantic__pydantic-ac9e6ee   # re-compile after edits
./pydantic benchmark pydantic__pydantic-ac9e6ee
./pydantic test pydantic__pydantic-ac9e6ee --from-benchmark

cat eval/active/output/summary.txt
# or latest benchmark JSON at hub root:
cat artemis_results.json
```

Or call scripts directly: `commands/compile`, `commands/benchmark`, etc.

`compile` always prepares first (creates eval workspace, checks out `project/`, sets
active task) — idempotent if already done. Use `prepare` alone when you only want
setup without building a patch yet.

From the parent GSO repo: `./scripts/pydantic.sh compile …`

## Tasks

| Task ID | Folder under `eval/` |
|---------|----------------------|
| `pydantic__pydantic-addf1f9` | `eval-pydantic-addf1f9-addf1f9/` |
| `pydantic__pydantic-4a09447` | `eval-pydantic-4a09447-4a09447/` |
| `pydantic__pydantic-ac9e6ee` | `eval-pydantic-ac9e6ee-ac9e6ee/` |
| `pydantic__pydantic-c2647ab` | `eval-pydantic-c2647ab-c2647ab/` |

Switch tasks with `./pydantic prepare <task_id>` or just `./pydantic compile
<task_id>`. Only the active task can be compiled/benchmarked (see `.active_task`).

Do not edit `eval/*/baseline/` — frozen reference for the harness.
