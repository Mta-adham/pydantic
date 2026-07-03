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
| `vs_baseline_speedup` | ratio | `baseline ÷ optimized` (>1 = faster, `<1` = slower, `1` = same) |
| `vs_baseline_percent_faster` | percent | `(speedup − 1) × 100` (negative = slower) |
| `vs_baseline_direction` | enum | `0` unchanged · `1` faster · `2` slower |
| `vs_baseline_significant` | bool | `1` = significant vs baseline at 95% |
| `vs_expert_parity_percent` | percent | `expert ÷ optimized × 100` (100 = tie) |
| `vs_expert_runtime_ratio` | ratio | `optimized ÷ expert` |
| `vs_expert_matches_expert` | bool | `1` = within 95% of expert speed |
| `confidence_speedup_ratio_estimate` | ratio | Best-guess speedup from all timing samples (same scale as `vs_baseline_speedup`) |
| `confidence_speedup_ratio_ci_95_low` / `_high` | ratio | 95% confidence interval for that speedup (see below) |
| `confidence_statistically_significant` | bool | `1` = CI does **not** include `1.0` (real change) |
| `confidence_ci_includes_no_change` | bool | `1` = CI includes `1.0` (no clear win/loss) |
| `confidence_within_measurement_noise` | bool | `1` = treat the measured change as harness noise |
| `tests_passed` | count | Number of perf tests that completed successfully |
| `tests_total` | count | Total perf tests in the harness for this task |

All tests passed when `tests_passed == tests_total` and `tests_total > 0`. GSO
pass gates (`opt_base` / `opt_commit`) and string labels live in
`artemis_results_robust.json` and `summary.txt`.

If the harness run fails or is incomplete, only metadata / counts are written;
timing and speedup keys are absent (`verdict: -1`, `tests_passed: 0`).

### Speedup ratios and confidence

Every `*_speedup*` / `confidence_speedup_ratio_*` value is a **ratio**, not seconds:

```text
speedup = baseline_runtime ÷ optimized_runtime
```

Examples: `2.0` = 2× faster, `1.2` = 20% faster, `1.0` = same speed, `0.99` ≈ 1% slower.

`vs_baseline_speedup` is the harness headline (geometric mean across tests). The
`confidence_speedup_ratio_*` fields answer a different question: **how sure are we
that optimized is really different from baseline?**

The harness runs each perf test **multiple times** (typically 5 iterations). Those
timings are not identical — they bounce around because of CPU, cache, and Docker
noise. The confidence fields are built from that variation:

1. Collect all baseline and optimized timing samples.
2. Resample them many times (bootstrap) and recompute the speedup each time.
3. `confidence_speedup_ratio_estimate` = best-guess speedup from the samples.
4. `confidence_speedup_ratio_ci_95_low` / `_high` = the middle 95% of those
   resampled speedups (a plausible range for the true speedup).

If every run took exactly the same time, the CI would collapse to a single point.
A **wide** interval means timings varied a lot relative to the measured change.

**How to read significance:** check whether the interval includes `1.0` (no change).

| Example CI | Meaning |
|------------|---------|
| `0.82` – `1.20` | Includes `1.0` → not significant; treat as noise |
| `1.15` – `1.40` | Entirely above `1.0` → reliably faster |
| `0.70` – `0.90` | Entirely below `1.0` → reliably slower |

That is why a tiny headline change (e.g. `vs_baseline_percent_faster: -0.14`) can
still be noise: the confidence interval is wide enough that “no real change” remains
plausible.

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
