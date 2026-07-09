You are optimizing `TypeAdapter.validate_python` in this repository checkout.

Edit files under `project/`. The unchanged baseline is in `baseline/` for reference.

## Workflow

1. Read the benchmark and locate the hot path in `project/`.
2. Make a focused change; preserve observable behavior.
3. Run `./compile` → `./test` → `./benchmark`.
4. Use the harness results to iterate until gains plateau.

## Issue

```text
Move `enum` validation and serialization to Rust (#9064)

Co-authored-by: sydney-runkle <sydneymarierunkle@gmail.com>
Co-authored-by: Sydney Runkle <54324534+sydney-runkle@users.noreply.github.com>
```

## Objective

Speed up `TypeAdapter.validate_python` when the target type is a Python `enum.Enum` or `enum.IntEnum`.

## Scope

Start on the hot path in these files (change others only if strictly necessary):

- `pydantic/_internal/_std_types_schema.py`
- `pydantic/json_schema.py`

## Performance benchmark

GSO scores this task with the harness below (`timeit` microbenchmarks with warm-up inside Docker).

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

## Hints

`pydantic/_internal/_std_types_schema.py` — specifically how enum schemas are built for `pydantic-core`. The current implementation generates a Python-based validation schema for enums. Examine whether a more efficient validation path can be used, such as leveraging pydantic-core's built-in enum support if available in the version being used.

Look at what core schema type is used for enum validation and whether a simpler, lower-overhead schema (e.g. direct value lookup) can replace the current approach.

The issue says move enum validation to **Rust** (pydantic-core). Look for native `enum_schema` rather than Python `after_validator` wrappers on every value.

The benchmark calls `validate_python` 20,000 times per enum adapter — per-value validation dominates, not one-time schema construction.

If you change core schema shape, ensure JSON schema generation stays consistent (`json_schema.py` may need a matching handler).

## Anti-patterns

- Optimizing import-time or cold paths the benchmark never executes.
- Micro-opts that do not change the hot loop shown above.
- Skipping `./test` — a fast but broken patch scores zero.
- Reading or copying from `expert/` — that is the scoring reference, not input.

## Constraints

- **Drop-in replacement:** keep the public API under test unchanged (signatures, return types, errors, observable behavior).
- Do not rename public symbols or change import paths callers rely on.
- Do not add new required dependencies.
- **Correctness:** Validation must correctly accept valid enum values, reject invalid ones with `ValidationError`, and respect any `_missing_` classmethod overrides on the enum class.
