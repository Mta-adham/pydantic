# Pydantic GSO optimization benchmark

Artemis-importable repo for pydantic GSO tasks. **Edit only `project/`**.

`./benchmark` and `./test` call the **GSO harness** (`gsobench` → Docker image
`slimshetty/gso:...`). There is no separate eval system — the `eval/` folder is
only a **local workspace** (frozen `baseline/` + reference `expert/` for patching).

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
./compile                              # rebuild patch
./benchmark                            # GSO harness → artemis_results.json
./test                                 # tests_artemis_results.json (reuses report if present)
```

## Commands

| Command | What it does |
|---------|----------------|
| `./compile [task]` | Sync `project/` + build `patch.diff` |
| `./benchmark [task]` | GSO harness perf eval → `artemis_results.json` |
| `./test [task]` | Correctness → `tests_artemis_results.json` (reuses `test-*` or `benchmark-*` report if present; `--rerun` for a fresh test harness) |
| `./reset [task]` | Restore `project/` from `baseline/` (discard edits) |

Omit the task ID to use the active task (`.gso_task_id`).

**Maintenance scripts** (`scripts/`):

| Script | What it does |
|--------|----------------|
| `bash scripts/run_all_tasks.sh` | Full E2E: all tasks compile → benchmark → test + validation |
| `bash scripts/images.sh pull-images [task]` | Pull pinned Docker images |
| `bash scripts/images.sh verify-images [task]` | Verify local images match `benchmark.yaml` digests |
| `bash scripts/images.sh pin-images [task]` | Update digest in `benchmark.yaml` after image rebuild |
| `source scripts/setup.sh` | Create `.venv` and install dependencies |

**Results (repo root):**

| File | Format |
|------|--------|
| `artemis_results.json` | Flat **numeric-only** (for Artemis import) |
| `artemis_results_robust.json` | Nested JSON with string labels and explanations |
| `summary.txt` | Human-readable summary |
| `tests_artemis_results.json` | Correctness / pass-fail (string IDs) |

## `artemis_results.json` encodings

`artemis_results.json` is a single flat object of headline metrics only (every value
is a number). Human-readable detail (including per-test counts in headlines) is in
`summary.txt`; `artemis_results_robust.json` keeps nested labels and explanations.

String task IDs (e.g. `pydantic__pydantic-4a09447`) appear in the robust report and
`summary.txt`. In `artemis_results.json`, `task` is the numeric index from the
**Tasks** table below (`0`–`3`).

### Fields

| Key | Type | Meaning |
|-----|------|---------|
| `task` | index | `0`–`3` — see **Tasks** table |
| `run_id` | hash | `sha256(run_id)[:8] % 10000` |
| `recorded_at` | timestamp | Unix epoch seconds (UTC) |
| `code_changes` | bool | `0` = placeholder / no real edits, `1` = real changes |
| `verdict` | enum | `-1` unavailable · `0` no_change · `1` no_change_near_expert · `2` slower_than_baseline · `3` improved_matches_expert · `4` improved_below_expert · `5` improved |
| `runtime_s_baseline` / `_optimized` / `_expert` | seconds | Geometric-mean wall time |
| `vs_baseline_speedup` | ratio | `baseline ÷ optimized` (>1 = faster) |
| `vs_baseline_percent_faster` | percent | `(speedup − 1) × 100` |
| `vs_baseline_direction` | enum | `0` unchanged · `1` faster · `2` slower |
| `vs_baseline_significant` | bool | `1` = significant vs baseline at 95% |
| `vs_expert_parity_percent` | percent | `expert ÷ optimized × 100` (100 = tie) |
| `vs_expert_runtime_ratio` | ratio | `optimized ÷ expert` |
| `vs_expert_matches_expert` | bool | `1` = within 95% of expert speed |
| `confidence_speedup_ratio_estimate` | ratio | Bootstrap speedup estimate |
| `confidence_speedup_ratio_ci_95_low` / `_high` | ratio | 95% CI for speedup vs baseline |
| `confidence_statistically_significant` | bool | `1` = significant vs baseline |
| `confidence_ci_includes_no_change` | bool | `1` = CI includes 1.0 (no clear change) |
| `confidence_within_measurement_noise` | bool | `1` = likely harness noise |
| `tests_passed` | bool | `1` = harness produced timings |
| `opt_base_passed` | bool | `1` = ≥20% faster than baseline |
| `opt_commit_passed` | bool | `1` = ≥95% of expert speed |

If the harness run fails or is incomplete, only metadata / bool flags are written;
timing and speedup keys are absent (`verdict: -1`).

## Tasks

Index order matches `task` in `artemis_results.json` (sorted `benchmarks/*/`
slug). String task IDs appear in `artemis_results_robust.json` and `summary.txt`.

| Index | Task ID | API | File(s) to optimize |
|------:|---------|-----|---------------------|
| `0` | `pydantic__pydantic-4a09447` | `GenericModel.__concrete_name__` | `project/pydantic/generics.py` |
| `1` | `pydantic__pydantic-ac9e6ee` | `TypeAdapter.validate_python` | `project/pydantic/_internal/_std_types_schema.py`, `project/pydantic/json_schema.py` |
| `2` | `pydantic__pydantic-addf1f9` | `BaseModel.__setattr__` | `project/pydantic/main.py` |
| `3` | `pydantic__pydantic-c2647ab` | `TypeAdapter.validate_strings` | `project/pydantic/type_adapter.py`, `project/pydantic/_internal/_mock_val_ser.py`, `project/pydantic/_internal/_namespace_utils.py` |

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
  eval/<task>/       local workspace only:
                       baseline/   frozen reference (do not edit)
                       expert/     expert reference (do not edit)
                                     OPTIMIZATION.md — what the expert patch does
                       metadata.json
                       patch.diff + predictions.jsonl  (from compile)
  logs/              GSO harness logs (gitignored)
```

Do not edit `eval/*/baseline/` or `eval/*/expert/`. Grading runs inside GSO's
public Docker images, not from files in this repo.
