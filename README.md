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
./test --from-benchmark                # correctness from benchmark report
```

## Commands

| Command | What it does |
|---------|----------------|
| `./compile [task]` | Sync `project/` + build `patch.diff` |
| `./benchmark [task]` | GSO harness perf eval → `artemis_results.json` |
| `./test [task]` | GSO harness correctness → `tests_artemis_results.json` |

Omit the task ID to use the active task (`.gso_task_id`).

**Results (repo root):**

| File | Format |
|------|--------|
| `artemis_results.json` | Flat **numeric-only** (for Artemis import) |
| `artemis_results_robust.json` | Nested JSON with string labels and explanations |
| `summary.txt` | Human-readable summary |
| `tests_artemis_results.json` | Correctness / pass-fail (string IDs) |

## `artemis_results.json` encodings

`artemis_results.json` is a single flat object: every value is a number. Keys use
underscore prefixes (`vs_baseline_…`, `per_test_0_…`, `timings_baseline_times_0_1`, …).
String fields from the robust report (headlines, `comparison` text, etc.) are omitted.

For readable labels, use `artemis_results_robust.json` or `summary.txt`.

### Categorical fields

| Key | Type | Values |
|-----|------|--------|
| `instance_id` | task index | `0`–`3` — see **Tasks** table (`Index` column) |
| `run_id` | hash | `sha256(run_id)[:8] % 10000` (e.g. `benchmark-pydantic__pydantic-4a09447` → `3874`) |
| `model_name` | enum | `0` = `local-edit` |
| `recorded_at` | timestamp | Unix epoch seconds (UTC) |
| `code_changes` | bool | `0` = no real edits (placeholder patch), `1` = real code changes |
| `verdict` | enum | `-1` unavailable · `0` no_change · `1` no_change_near_expert · `2` slower_than_baseline · `3` improved_matches_expert · `4` improved_below_expert · `5` improved |
| `vs_baseline_direction` | enum | `0` unchanged · `1` faster · `2` slower (also on `per_test_N_vs_baseline_direction`) |
| `vs_baseline_significant` | bool | `1` = statistically significant vs baseline at 95% |
| `vs_expert_matches_expert` | bool | `1` = within 95% of expert speed (also on `per_test_N_vs_expert_matches_expert`) |
| `confidence_ci_includes_no_change` | bool | `1` = 95% CI for speedup ratio includes 1.0 |
| `confidence_statistically_significant` | bool | `1` = significant vs baseline at 95% |
| `confidence_within_measurement_noise` | bool | `1` = treated as harness noise, not a real change |
| `tests_passed` | bool | `1` = harness completed and produced timings |
| `opt_base_passed` | bool | `1` = ≥20% faster than baseline (GSO `opt_base`) |
| `opt_commit_passed` | bool | `1` = ≥95% of expert speed (GSO `opt_commit`) |
| `harness_metrics_opt_base_passed` | bool | same as `opt_base_passed` |
| `harness_metrics_opt_commit_passed` | bool | same as `opt_commit_passed` |

### Numeric metrics (seconds, ratios, percents)

| Key prefix | Meaning |
|------------|---------|
| `runtime_s_baseline` / `_optimized` / `_expert` | Geometric-mean wall time (seconds) |
| `vs_baseline_speedup` | `baseline_time ÷ optimized_time` (>1 = faster) |
| `vs_baseline_percent_faster` | `(speedup − 1) × 100` |
| `vs_baseline_time_saved_s` | `baseline − optimized` (seconds) |
| `vs_expert_parity_percent` | `expert_time ÷ optimized_time × 100` (100 = tie) |
| `vs_expert_runtime_ratio` | `optimized_time ÷ expert_time` |
| `vs_expert_time_delta_s` | `optimized − expert` (seconds) |
| `confidence_speedup_ratio_*` | Bootstrap estimate / 95% CI for baseline÷optimized speedup |
| `confidence_tests_faster` / `_total` | Per-test count faster than baseline |
| `harness_metrics_speedup_vs_baseline_gm` | GSO geometric-mean speedup vs baseline |
| `harness_metrics_speedup_vs_expert_gm` | GSO geometric-mean speedup vs expert |
| `harness_metrics_speedup_expert_vs_baseline_gm` | Expert speedup vs baseline |
| `per_test_N_*` | Same metrics for perf test index `N` |
| `timings_baseline_times_N_M` | Raw sample `M` for test `N` (seconds); same for `_optimized_` / `_expert_` |
| `harness_metrics_per_test_speedups_*` | Per-test speedup arrays from the grader |

If the harness run fails or is incomplete, only metadata / bool flags are written;
timing and speedup keys are absent (`verdict: -1`).

## Tasks

Index order matches `instance_id` in `artemis_results.json` (sorted `benchmarks/*/`
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
