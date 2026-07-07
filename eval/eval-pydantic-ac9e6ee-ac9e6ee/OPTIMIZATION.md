# Expert optimization: `TypeAdapter.validate_python`

**API:** `TypeAdapter.validate_python`  
**Files:** `pydantic/_internal/_std_types_schema.py`, `pydantic/json_schema.py`

## Task intent

GSO problem statement / commit message:

```text
Move `enum` validation and serialization to Rust (#9064)

Co-authored-by: sydney-runkle <sydneymarierunkle@gmail.com>
Co-authored-by: Sydney Runkle <54324534+sydney-runkle@users.noreply.github.com>
```

## Constraints

This is a **drop-in replacement** task:

- Keep the public API under test (`TypeAdapter.validate_python`) unchanged: same signatures, return types, and observable behavior for all valid inputs.
- Do not rename public functions, classes, or modules; do not change import paths callers rely on.
- Do not add new required dependencies.
- Limit edits to the file(s) listed above unless a minimal supporting change is strictly necessary for correctness or performance of the target API.

## Summary

Routes `Enum` validation through pydantic-core's native `enum_schema` instead of
Python-level validator wrappers, reducing per-value overhead on the hot validation path.

## Changes

1. **Native `core_schema.enum_schema()`** — Replaces the baseline's
   `lax_or_strict_schema` + `no_info_after_validator_function(to_enum, ...)` pattern.
   Enum members and sub-type (`str` / `int` / `float`) are passed directly to
   pydantic-core, which validates in Rust.

2. **Dedicated serialization schema** — Uses `simple_ser_schema` / plain serializer
   for `use_enum_values` instead of an extra Python `after_validator` layer.

3. **`enum_schema` JSON schema handler** — Adds `GenerateJsonSchema.enum_schema()` in
   `json_schema.py` so JSON schema generation matches the new core schema shape
   (enum members, const for single-value enums, correct type keyword).

4. **Empty enum edge case** — Empty enums still get a valid schema via a separate
   `get_json_schema_no_cases` path without the heavy lax/strict wrapper.

## Why it's faster

The baseline converted enum input through Python callables (`to_enum`) on every
validation. The expert path lets pydantic-core validate enum values directly,
which is what `TypeAdapter.validate_python` exercises in the GSO perf tests.
