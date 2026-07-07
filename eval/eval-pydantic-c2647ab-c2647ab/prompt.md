# Task: Optimise `TypeAdapter.validate_strings`

## Objective

Speed up `TypeAdapter.validate_strings` by reducing overhead in the `TypeAdapter` initialisation and validation path.

## Repository

`pydantic/pydantic` — base commit `c2647ab` (see `metadata.json`)

## Files to optimise

- `pydantic/type_adapter.py`
- `pydantic/_internal/_mock_val_ser.py`

## Performance benchmark

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

## What to look at

`TypeAdapter` in `pydantic/type_adapter.py` supports deferred build (`defer_build=True`) for cases where the type schema cannot be fully resolved at construction time. When `defer_build` is active, the adapter uses mock validators that attempt to rebuild on first use.

Look at how `rebuild()` works and whether the rebuild mechanism introduces unnecessary overhead on each call to `validate_strings` — particularly the namespace resolution and frame inspection done to find type annotations.

## Correctness constraint

`validate_strings` must correctly coerce string inputs to the target type (e.g. `"42"` → `42` for `TypeAdapter(int)`). The `rebuild()` method must correctly resolve deferred types when called explicitly.
