# Task: Optimise `BaseModel.__setattr__`

## Objective

Speed up attribute assignment on pydantic v2 `BaseModel` instances by reducing the overhead of `__setattr__`.

## Repository

`pydantic/pydantic` — base commit `addf1f9` (see `metadata.json`)

## Files to optimise

- `pydantic/main.py`
- `pydantic/_internal/_model_construction.py`

## Performance benchmark

```python
import json, math, random, timeit
from pydantic import BaseModel

class InnerModel(BaseModel):
    x: int
    y: int

class TestModel(BaseModel):
    a: int
    b: str
    c: float
    inner: InnerModel
    _priv: int

def setup():
    return TestModel(a=0, b='start', c=0.0, inner=InnerModel(x=0, y=0), _priv=0)

def experiment(instance):
    for i in range(10000):
        instance.a = i
        instance.b = f'value {i}'
        instance.c = i * 0.001
        instance.inner.x = i
        instance.inner.y = i * 2
        instance._priv = i
    return {'a': instance.a, 'b': instance.b, 'c': instance.c,
            'inner': {'x': instance.inner.x, 'y': instance.inner.y}, '_priv': instance._priv}
```

## What to look at

`BaseModel.__setattr__` in `pydantic/main.py` dispatches attribute writes through a chain of `isinstance` checks and dictionary lookups on every single call:

- Is it a class var?
- Is it a model field?
- Should it be validated?
- Is it a private attribute?
- Is it a cached property?
- Is it an extra field?

With `validate_assignment=False` (the default), most of these checks are wasted on every `instance.field = value` call. The experiment performs 60,000 attribute assignments.

Consider how to avoid repeating the same dispatch logic on every call for a given attribute name.

## Correctness constraint

All attribute types (model fields, private attributes, extra fields, cached properties) must continue to work correctly with and without `validate_assignment=True`.
