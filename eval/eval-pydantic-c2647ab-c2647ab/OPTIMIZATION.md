# Expert optimization: `TypeAdapter.validate_strings`

**API:** `TypeAdapter.validate_strings`  
**Files:** `pydantic/type_adapter.py`, `pydantic/_internal/_mock_val_ser.py`,
`pydantic/_internal/_namespace_utils.py`

## Task intent

GSO problem statement / commit message:

```text
Add `rebuild()` method for `TypeAdapter` and simplify `defer_build` patterns (#10537)

Co-authored-by: MarkusSintonen <12939780+MarkusSintonen@users.noreply.github.com>
```

## Constraints

This is a **drop-in replacement** task:

- Keep the public API under test (`TypeAdapter.validate_strings`) unchanged: same signatures, return types, and observable behavior for all valid inputs.
- Do not rename public functions, classes, or modules; do not change import paths callers rely on.
- Do not add new required dependencies.
- Limit edits to the file(s) listed above unless a minimal supporting change is strictly necessary for correctness or performance of the target API.

## Summary

Builds the TypeAdapter's validator once at construction and removes per-call frame
introspection from validation methods, making `validate_strings` and related entry
points much cheaper on repeated calls.

## Changes

1. **Eager core attrs at `__init__`** — `core_schema`, `validator`, and `serializer`
   are built in `_init_core_attrs()` during construction (or set to mocks when
   `defer_build=True`). Replaces `@cached_property` accessors that rebuilt on first
   use and walked the stack via `@_frame_depth`.

2. **Removed `@_frame_depth` from hot methods** — `validate_python`, `validate_json`,
   `validate_strings`, `dump_python`, `dump_json`, and others no longer pay frame-walk
   overhead on every call.

3. **`set_type_adapter_mocks()`** — New helper in `_mock_val_ser.py` for deferred
   builds: installs `MockCoreSchema` / `MockValSer` on the adapter with rebuild
   callbacks, mirroring the model path.

4. **`rebuild()` API** — Explicit rebuild with namespace resolution when forward refs
   could not be resolved at init. `pydantic_complete` tracks whether real
   validator/serializer instances are ready.

5. **Namespace resolver comments** — Minor `_namespace_utils.py` documentation for
   TypeAdapter parent-namespace handling (supports correct schema build, not a perf
   change by itself).

## Why it's faster

The baseline lazily initialized the validator through `cached_property` and used
`_frame_depth` decorators to resolve namespaces on each public method call.
`validate_strings` is called many times in the benchmark; the expert version holds a
ready `SchemaValidator` on `self.validator` and calls it directly with no stack walk.
