You are optimizing `TypeAdapter.validate_strings` in this repository checkout.

Edit files under `project/`. The unchanged baseline is in `baseline/` for reference.

## Success criteria

Your patch is scored by the GSO harness against **baseline** and a hidden **expert** reference. You may not reach expert speed — the goal is to get as close as possible while staying correct.

After each attempt, check repo-root `artemis_results.json`:

| Goal | Field | Target |
|------|-------|--------|
| Correct | `correctness_passed` | `1` |
| Faster than baseline | `opt_base_passed` | `1` (≥1.2× GM speedup vs baseline) |
| Near expert | `opt_commit_passed` or `vs_expert_parity_percent` | `1` or ≥95 |

Use harness timings (`runtime_s_*`, `vs_baseline_speedup`) — not ad-hoc `time.time()`.

## Workflow

1. Read the benchmark and locate the hot path in `project/`.
2. Make a focused change; preserve observable behavior.
3. Run `./compile` → `./test` → `./benchmark`.
4. Read `artemis_results.json`; iterate until gains plateau.

## Issue

```text
Add `rebuild()` method for `TypeAdapter` and simplify `defer_build` patterns (#10537)

Co-authored-by: MarkusSintonen <12939780+MarkusSintonen@users.noreply.github.com>
```

## Objective

Speed up `TypeAdapter.validate_strings` by reducing overhead in the `TypeAdapter` initialisation and validation path.

## Scope

Start on the hot path in these files (change others only if strictly necessary):

- `pydantic/type_adapter.py`
- `pydantic/_internal/_mock_val_ser.py`
- `pydantic/_internal/_namespace_utils.py`

## Performance benchmark

GSO scores this task with the harness below (`timeit` microbenchmarks with warm-up inside Docker).

```python
import json, os, random, timeit
from pydantic import TypeAdapter

def setup():
    adapter = TypeAdapter(int)
    random.seed(42)
    test_data = [str(random.randint(0, 10000)) for _ in range(10000)]
    return adapter, test_data

def experiment(adapter, test_data):
    converted = [adapter.validate_strings(x) for x in test_data]
    return {
        'converted': converted,
        'stats': {'sum': sum(converted), 'min': min(converted),
                  'max': max(converted), 'count': len(converted)},
    }
```

## Hints

`TypeAdapter` in `pydantic/type_adapter.py` supports deferred build (`defer_build=True`) for cases where the type schema cannot be fully resolved at construction time. When `defer_build` is active, the adapter uses mock validators that attempt to rebuild on first use.

Look at how `rebuild()` works and whether the rebuild mechanism introduces unnecessary overhead on each call to `validate_strings` — particularly the namespace resolution and frame inspection done to find type annotations.

The benchmark reuses one `TypeAdapter(int)` and calls `validate_strings` 10,000 times — eliminate **per-call** overhead, not just slow `__init__`.

Search for `@cached_property`, lazy validator build, or frame/namespace inspection decorators on methods like `validate_strings`.

After the adapter is fully built, the hot path should call the core validator directly with no stack walks.

## Anti-patterns

- Optimizing import-time or cold paths the benchmark never executes.
- Micro-opts that do not change the hot loop shown above.
- Skipping `./test` — a fast but broken patch scores zero.
- Reading or copying from `expert/` — that is the scoring reference, not input.

## Constraints

- **Drop-in replacement:** keep the public API under test unchanged (signatures, return types, errors, observable behavior).
- Do not rename public symbols or change import paths callers rely on.
- Do not add new required dependencies.
- **Correctness:** `validate_strings` must correctly coerce string inputs to the target type (e.g. `"42"` → `42` for `TypeAdapter(int)`). The `rebuild()` method must correctly resolve deferred types when called explicitly.
