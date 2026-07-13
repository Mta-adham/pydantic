# Pydantic GSO optimization benchmark

Artemis-importable repo for pydantic GSO tasks. **Edit only `project/`**.

`./compile`, `./benchmark`, and `./test` all rebuild a patch from current
`project/` edits vs frozen `eval/<task>/baseline/`, then (for benchmark/test)
run the **GSO Docker harness** for that task’s pinned image. The harness does
**not** mount `project/` — it applies your `predictions.jsonl` patch inside Docker.

## Setup

```bash
git clone git@github.com:Mta-adham/pydantic.git
cd pydantic
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# or: source scripts/setup.sh
```

**Requires:** Python 3.12+, Docker, `HF_TOKEN` in `.env` (first-time dataset fetch only)

```bash
bash scripts/images.sh pull-images    # once per machine / after digest change
bash scripts/images.sh verify-images
```

## Workflow

```bash
./compile pydantic__pydantic-4a09447   # sets .gso_task_id + syncs project/ to task base
# edit project/pydantic/*.py (see Tasks table)
./benchmark                            # re-diffs project/ → Docker harness → results
./test                                 # re-diffs project/; correctness JSON (reuses report if present)
```

After editing `project/`, run `./benchmark` directly — a second `./compile` is optional.
Uncommitted edits are preserved when `project/` is already on the task base commit
(same-task re-runs do not wipe your work).

### Success criteria (read `summary.txt`)

| Gate | Meaning |
|------|---------|
| `code_changes: yes` | Real patch evaluated (not placeholder) |
| `opt_base_passed` | ≥ ~20% faster than baseline (weak gate) |
| `matches_expert` / `opt_commit` | ≥ ~95% of expert speed (full success) |
| `correctness_passed` | Functional tests passed |

A ~1.2× win with low expert parity is only partial; many tasks need ~4–5× vs baseline
to match expert.

## Commands

| Command | What it does |
|---------|----------------|
| `./compile [task]` | Sync `project/` + build `patch.diff` / `predictions.jsonl` |
| `./benchmark [task]` | Prepare + re-diff `project/` → patch → GSO perf eval → `artemis_results*.json` + `summary.txt` |
| `./test [task]` | Prepare + re-diff `project/` → correctness → `tests_artemis_results.json` (reuses `test-*` or `benchmark-*` report if present; `--rerun` for a fresh test harness) |
| `./reset [task]` | Restore `project/` from `baseline/` (discard edits) |

Omit the task ID to use the active task (`.gso_task_id`).

Useful flags: `./benchmark --keep-image` (skip image delete), `--no-pull`, `--reuse-report`
(warns: timings may not match a newly rebuilt patch).

**Maintenance scripts** (`scripts/`):

| Script | What it does |
|--------|----------------|
| `bash scripts/run_all_tasks.sh` | Full E2E: all tasks compile → benchmark → test + validation |
| `bash scripts/test_all_commands.sh` | Same style E2E with output checks |
| `bash scripts/test_project_patch_refresh.sh` | Verify every task: edits survive compile and land in the harness patch |
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
| `tests_artemis_results.json` | Correctness + `code_changes` / `verdict` |

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| Docker `409` container name conflict | Stale harness container; re-run `./benchmark` (cleanup is automatic) or `docker rm -f $(docker ps -aq --filter name=gso.eval.)` |
| `code_changes: no` after editing | Edit wasn’t under that task’s files in `project/`, or patch is placeholder-only |
| Missing `:latest` image | `bash scripts/images.sh pull-images <task>` then `./benchmark --keep-image` |
| Wrong task / wrong commit | `./compile <task_id>` before editing |

## `artemis_results.json` encodings

`artemis_results.json` is a single flat object of headline metrics only (every value
is a number). Human-readable detail (including per-test counts in headlines) is in
`summary.txt`; `artemis_results_robust.json` keeps nested labels and explanations.

String task IDs (e.g. `pydantic__pydantic-4a09447`) appear in the robust report and
`summary.txt`. In `artemis_results.json`, `task` is the numeric index from the
**Tasks** table below (`0`–`3`).

**Use the GSO harness for timing.** Run `./benchmark` (not external `time.time()`
wrappers). The harness runs each task's `timeit` microbenchmarks inside Docker with
warm-up iterations and fixed measurement windows; `runtime_s_*` and speedup fields
are derived from that report.

### Eval metrics framework

Speed alone is not enough. `artemis_results.json` reports the metrics below so you can
track correctness and GSO Opt@1 gates alongside wall-clock performance.

| Metric | `artemis_results.json` key(s) | Why it matters |
|--------|-------------------------------|----------------|
| Wall-clock runtime (primary) | `runtime_s_baseline`, `runtime_s_optimized`, `runtime_s_expert` | What GSO scores on (harness geometric mean) |
| Speedup vs baseline | `vs_baseline_speedup` | Normalised across tasks of different durations |
| Expert parity | `vs_expert_parity_percent` | How close to expert speed (100 = matches expert) |
| Correctness | `correctness_passed`, `perf_completion_rate` | A fast but broken patch scores 0 (`correctness_passed: 0`) |
| Memory usage | `memory_measured`, `memory_mb_baseline/optimized/expert` | Some tasks are memory-bound; `memory_measured: 0` when harness has no RSS data |
| Opt@1 (expert threshold) | `opt_base_passed`, `opt_at_1` | GSO gates: beat baseline (≥1.2× GM) and match expert (harmonic mean > 0.95×) |

`tests_passed` / `tests_total` count perf microbenchmarks completed when correctness
passes. `perf_completion_rate` is their percentage (`100` = full harness run).

`tests_artemis_results.json` repeats the `eval` block (string IDs) for correctness-only
imports. Full timing detail lives in `artemis_results_robust.json` → `summary.eval`.

### Fields

| Key | Type | Meaning |
|-----|------|---------|
| `task` | index | `0`–`3` — see **Tasks** table |
| `run_id` | hash | `sha256(run_id)[:8] % 10000` |
| `recorded_at` | timestamp | Unix epoch seconds (UTC) |
| `code_changes` | bool | `0` = placeholder / no real edits, `1` = real changes |
| `verdict` | enum | `-1` unavailable · `0` no_change · `1` no_change_near_expert · `2` slower_than_baseline · `3` improved_matches_expert · `4` improved_below_expert · `5` improved |
| `runtime_s_baseline` / `_optimized` / `_expert` | seconds | Geometric-mean wall time across tests |
| `runtime_s_baseline_stddev` / `_optimized_stddev` | seconds | Std-dev of per-test means (spread across tests, not iterations) |
| `runtime_s_optimized_min` | seconds | Fastest single per-test mean (best-case) |
| `runtime_s_gap_to_expert` | seconds | `optimized − expert` (negative = faster than expert) |
| `vs_baseline_speedup` | ratio | `baseline ÷ optimized` (>1 = faster, <1 = slower, 1 = same) |
| `vs_baseline_significant` | bool | `1` = significant vs baseline at 95% |
| `vs_expert_parity_percent` | percent | `expert ÷ optimized × 100` (100 = matches expert speed) |
| `expert_vs_baseline_speedup` | ratio | `baseline ÷ expert` (>1 = expert faster than baseline) |
| `vs_baseline_memory_reduction_pct` | percent | Memory reduction vs baseline; `-1` when not measured |
| `vs_expert_memory_parity_pct` | percent | Memory parity vs expert; `-1` when not measured |
| `confidence_speedup_ratio_estimate` | ratio | Best-guess speedup from all timing samples (same scale as `vs_baseline_speedup`) |
| `confidence_speedup_ratio_ci_95_low` / `_high` | ratio | 95% confidence interval for that speedup (see below) |
| `confidence_statistically_significant` | bool | `1` = CI does **not** include `1.0` (real change) |
| `confidence_ci_includes_no_change` | bool | `1` = CI includes `1.0` (no clear win/loss) |
| `confidence_within_measurement_noise` | bool | `1` = treat the measured change as harness noise |
| `tests_passed` | count | Perf microbenchmarks completed (when correctness passes) |
| `tests_total` | count | Total perf microbenchmarks in the harness for this task |
| `correctness_passed` | bool | `1` = GSO functional/correctness gate passed |
| `patch_applied` | bool | `1` = patch applied successfully in harness |
| `harness_ran` | bool | `1` = baseline timing block completed |
| `perf_completion_rate` | percent | `tests_passed ÷ tests_total × 100` |
| `opt_base_passed` | bool | `1` = beat baseline (GSO `opt_base`: ≥1.2× GM speedup) |
| `opt_at_1` | bool | `1` = match expert (GSO Opt@1 threshold: harmonic mean > 0.95×) |
| `opt_p_at_1_p{N}` | bool | Probabilistic Opt@1 at percentile N of patch distribution (`p0`–`p100`) |
| `memory_measured` | bool | `1` = peak RSS measured; `0` = measurement unavailable |
| `memory_mb_baseline` / `_optimized` / `_expert` | MiB | Peak RSS per phase; `-1` when `memory_measured: 0` |

All tests passed when `tests_passed == tests_total`, `tests_total > 0`, and
`correctness_passed == 1`. GSO pass gates and string labels also live in
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

That is why a tiny headline change (e.g. `vs_baseline_speedup: 1.001`) can still be
noise: the confidence interval is wide enough that “no real change” remains plausible.

## Tasks

Index order matches `task` in `artemis_results.json` (sorted `eval/*/` dirs).
String task IDs appear in `artemis_results_robust.json` and `summary.txt`.

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
  compile            sync task + build patch from project/
  benchmark          re-diff project/ + GSO harness performance eval
  test               re-diff project/ + GSO harness correctness eval
  .gso_task_id       active GSO task + pinned eval image digest
  scripts/           harness glue (workflow, env, hub)
  eval/<task>/       per-task dir:
                       benchmark.yaml   committed (image pin + timeout + task metadata)
                       prompt.md        not committed (Artemis / local)
                       OPTIMIZATION.md  not committed (Artemis / local)
                       baseline/        runtime (gitignored)
                       expert/          runtime (gitignored)
                       metadata.json, patch.diff, predictions.jsonl  (gitignored)
  logs/              GSO harness logs (gitignored)
```

Do not edit `eval/*/baseline/` or `eval/*/expert/`. Grading runs inside GSO's
public Docker images from your patch, not by mounting this repo’s `project/`.
