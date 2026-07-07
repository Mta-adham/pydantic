# Task: Optimise `TypeAdapter.validate_python` for enum types

## Objective

Speed up `TypeAdapter.validate_python` when the target type is a Python `enum.Enum` or `enum.IntEnum`.

## Repository

`pydantic/pydantic` — base commit `ac9e6ee` (see `metadata.json`)

## Files to optimise

- `pydantic/_internal/_std_types_schema.py`
- `pydantic/json_schema.py`

## Performance benchmark

```python
import json, random, timeit
from enum import Enum, IntEnum
from pydantic import TypeAdapter, ValidationError

def setup():
    random.seed(1234567)

    class IntE(IntEnum):
        A=1; B=2; C=3; D=5; E=8; F=13; G=21; H=34; I=55; J=89

    int_adapter = TypeAdapter(IntE)
    int_data = [random.choice([e.value for e in IntE]) if random.random() < 0.6
                else random.randint(100, 1000) for _ in range(20000)]

    class StrE(Enum):
        X='foo'; Y='barbaz'; Z='qux_quux'

    str_adapter = TypeAdapter(StrE)
    str_data = [random.choice([e.value for e in StrE]) if random.random() < 0.6
                else 'invalid' for _ in range(20000)]

    return [('int_enum', int_adapter, int_data), ('str_enum', str_adapter, str_data)]

def experiment(workloads):
    results = {}
    for label, adapter, data in workloads:
        out = []
        for v in data:
            try:
                out.append(adapter.validate_python(v).name)
            except ValidationError:
                out.append('ValidationError')
        results[label] = out
    return results
```

## What to look at

`pydantic/_internal/_std_types_schema.py` — specifically how enum schemas are built for `pydantic-core`. The current implementation generates a Python-based validation schema for enums. Examine whether a more efficient validation path can be used, such as leveraging pydantic-core's built-in enum support if available in the version being used.

Look at what core schema type is used for enum validation and whether a simpler, lower-overhead schema (e.g. direct value lookup) can replace the current approach.

## Correctness constraint

Validation must correctly accept valid enum values, reject invalid ones with `ValidationError`, and respect any `_missing_` classmethod overrides on the enum class.
