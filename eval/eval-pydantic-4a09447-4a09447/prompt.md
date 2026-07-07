# Task: Optimise `GenericModel` concrete model creation

## Objective

Speed up the creation of concrete `GenericModel` instances (e.g. `MyGeneric[dict]`) in pydantic v1.

## Repository

`pydantic/pydantic` — base commit `4a094477c6a66ba36e36875d09f3d52475991709^`

## File to optimise

- `pydantic/generics.py`

## Performance benchmark

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

## What to look at

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

## Correctness constraint

The behaviour of `GenericModel[SomeType]` must remain identical: correct module attribution, correct pickling support (global reference registration), and correct caching via `_generic_types_cache`.
