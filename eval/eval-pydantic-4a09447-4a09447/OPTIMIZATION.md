# Expert optimization: `GenericModel.__concrete_name__`

**API:** `GenericModel.__concrete_name__`  
**File:** `pydantic/generics.py`

## Task intent

GSO problem statement / commit message:

```text
Fix generics creation time and allow model name reusing (#2078)

* preserve progress

* make get_caller_module_name much faster
combine get_caller_module_name and is_call_from_module in get_caller_frame_info

* fix coverage

* add changes file
```

## Constraints

This is a **drop-in replacement** task:

- Keep the public API under test (`GenericModel.__concrete_name__`) unchanged: same signatures, return types, and observable behavior for all valid inputs.
- Do not rename public functions, classes, or modules; do not change import paths callers rely on.
- Do not add new required dependencies.
- Limit edits to the file(s) listed above unless a minimal supporting change is strictly necessary for correctness or performance of the target API.

## Summary

Speeds up generic model creation by replacing expensive `inspect` stack walking with
cheaper frame access and simplifying global name registration.

## Changes

1. **`get_caller_frame_info()` replaces two helpers** — Merges `get_caller_module_name()`
   and `is_call_from_module()` into one call using `sys._getframe(2)` instead of
   `inspect.stack()` / `inspect.getmodule()`. Returns `(module_name, called_globally)`
   in a single frame lookup.

2. **Cheaper module name** — Reads `__name__` from the caller frame's `f_globals`
   instead of resolving the module object via inspect.

3. **Simpler global registration** — When a generic model is created at module scope,
   registers it in `sys.modules[...].__dict__` with a `setdefault` loop that appends
   `_` to the name on conflict, instead of raising on the first name clash.

## Why it's faster

`inspect.stack()` allocates a full stack trace on every `GenericModel` subclass
instantiation. `sys._getframe` reads one frame directly. The combined helper also
avoids duplicate stack walks that the baseline performed separately for module name
and global-scope detection.
