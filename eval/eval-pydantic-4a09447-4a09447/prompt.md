You are optimizing `GenericModel.__concrete_name__` in this repository checkout.

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
Fix generics creation time and allow model name reusing (#2078)

* preserve progress

* make get_caller_module_name much faster
combine get_caller_module_name and is_call_from_module in get_caller_frame_info

* fix coverage

* add changes file
```

## Objective

Speed up the creation of concrete `GenericModel` instances (e.g. `MyGeneric[dict]`) in pydantic v1.

## Scope

Start on the hot path in these files (change others only if strictly necessary):

- `pydantic/generics.py`

## Performance benchmark

GSO scores this task with the harness below (`timeit` microbenchmarks with warm-up inside Docker).

```python
import json, random, timeit
from typing import Any, Dict, List, TypeVar, Generic
import requests
from pydantic.generics import GenericModel

T = TypeVar('T')

def setup():
    posts = requests.get('https://jsonplaceholder.typicode.com/posts').json()
    return posts

def experiment(data):
    class MyGeneric(GenericModel, Generic[T]):
        value: T

    ConcreteModel = MyGeneric[dict]
    instances = [ConcreteModel(value=item) for item in data]
    return {
        'concrete_model_name': ConcreteModel.__name__,
        'num_instances': len(instances),
        'first_instance': instances[0].dict() if instances else {},
    }
```

## Hints

In `pydantic/generics.py`, generic model creation calls two helper functions:

```python
model_module = get_caller_module_name() or cls.__module__
...
if is_call_from_module():
    ...
```

Both functions call `inspect.stack()[2].frame` independently:

```python
def get_caller_module_name():
    import inspect
    previous_caller_frame = inspect.stack()[2].frame  # expensive
    ...

def is_call_from_module():
    import inspect
    previous_caller_frame = inspect.stack()[2].frame  # same frame, called again
    ...
```

`inspect.stack()` is slow because it captures the full call stack with source code for every frame. It is called twice per generic model creation even though both functions access the same frame.

The issue suggests merging `get_caller_module_name` and `is_call_from_module` into a single helper that inspects the caller frame once.

Each `inspect.stack()` walks the full stack and materializes frame records — the benchmark instantiates many concrete models in a tight loop.

Global name registration for pickling at module scope must keep working; the issue also mentions allowing model name reuse.

## Anti-patterns

- Optimizing import-time or cold paths the benchmark never executes.
- Micro-opts that do not change the hot loop shown above.
- Skipping `./test` — a fast but broken patch scores zero.
- Reading or copying from `expert/` — that is the scoring reference, not input.

## Constraints

- **Drop-in replacement:** keep the public API under test unchanged (signatures, return types, errors, observable behavior).
- Do not rename public symbols or change import paths callers rely on.
- Do not add new required dependencies.
- **Correctness:** The behaviour of `GenericModel[SomeType]` must remain identical: correct module attribution, correct pickling support (global reference registration), and correct caching via `_generic_types_cache`.
