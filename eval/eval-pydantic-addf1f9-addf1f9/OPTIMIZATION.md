# Expert optimization: `BaseModel.__setattr__`

**API:** `BaseModel.__setattr__`  
**File:** `pydantic/main.py`

## Task intent

GSO problem statement / commit message:

```text
Improve `__setattr__` performance of Pydantic models by caching setter functions (#10868)
```

## Constraints

This is a **drop-in replacement** task:

- Keep the public API under test (`BaseModel.__setattr__`) unchanged: same signatures, return types, and observable behavior for all valid inputs.
- Do not rename public functions, classes, or modules; do not change import paths callers rely on.
- Do not add new required dependencies.
- Limit edits to the file(s) listed above unless a minimal supporting change is strictly necessary for correctness or performance of the target API.

## Summary

Memoizes per-attribute `__setattr__` dispatch so repeated assignments to the same field
name take a fast path instead of re-running the full attribute classification logic.

## Changes

1. **Handler memoization** — `__setattr__` stores resolved handlers in
   `__pydantic_setattr_handlers__`. The first assignment to a given attribute name
   computes the handler; later assignments call it directly.

2. **`_SIMPLE_SETATTR_HANDLERS` table** — Common cases (model field, private attr,
   `cached_property`, `validate_assignment`, extra field) map to pre-built callables
   instead of inline branching on every call.

3. **`_get_setattr_handler()`** — Classifies the attribute once (ClassVar, private,
   property, frozen check, extra vs model field) and returns either a memoizable
   handler or `None` when the attribute name is dynamic (e.g. freeform `extra` keys).

## Why it's faster

The baseline re-evaluated `isinstance(attr, property)`, config flags, and field lookups
on every `__setattr__` call. Hot loops that set the same fields repeatedly (common in
benchmarks and real apps) now pay classification cost only once per attribute name.
