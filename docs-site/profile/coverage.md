# Package Profile Coverage Report

Generated: 2026-05-14  
Source hash: 41d584652aa29b09762aa1140ae64fb640473940  
Package root: src/mountainash_utils_rules/

## Summary

| Metric | Count |
|--------|-------|
| Modules discovered | 14 |
| Modules profiled | 14 |
| Modules ignored | 0 |
| Coverage gaps | 0 |
| Facets written | 5 |

## Module Coverage

| Module | Profile | Doc Priority | Test Coverage |
|--------|---------|--------------|---------------|
| `__init__` | ✅ | essential | — (re-exports only) |
| `__version__` | ✅ | skip-with-reason | — |
| `constants` | ✅ | essential | via test_compiler, test_dimension |
| `dimension` | ✅ | essential | test_dimension.py |
| `compiler` | ✅ | useful | test_compiler.py |
| `engine` | ✅ | essential | test_engine.py, test_integration.py, test_backend_purity.py |
| `result` | ✅ | essential | test_result.py |
| `context` | ✅ | internal-note | test_context.py |
| `accumulator_engine` | ✅ | essential | test_accumulator_engine.py, test_accumulator_apply.py, test_accumulator_edge_cases.py, test_accumulator_benchmarks.py, test_accumulator_backends.py |
| `accumulator_compiler` | ✅ | useful | test_accumulator_compiler.py |
| `accumulator_result` | ✅ | useful | test_accumulator_result.py |
| `aggregate` | ✅ | useful | via accumulator engine tests |
| `lattice` | ✅ | useful | test_lattice.py, test_lattice_properties.py |
| `primes` | ✅ | internal-note | test_primes.py |

## Open Questions Requiring Resolution Before Docs

1. **Aggregate.operation**: Only `'sum'` is implemented. Document the limitation or implement min/max/product before writing the Aggregate reference section.
2. **REGEX pattern scope**: Pattern is metadata-level (one per dimension, not per-row). This is a meaningful constraint users may not expect — needs explicit documentation.
3. **SET_MEMBERSHIP / SET_EXCLUSION backend parity**: Uses a Polars-native workaround. Cross-backend behaviour is not guaranteed; document the caveat.
4. **AccumulatorEngine: string/set strategies unsupported**: Will raise `ValueError` at construction time. Must be documented as a current limitation.
5. **Prime table limit**: Partitions exceeding ~500 rules raise `IndexError`. Not surfaced at the `AccumulatorEngine` API level.
6. **Lattice persistence**: No serialisation protocol. Users who need to cache lattices between process runs must handle this themselves.
7. **Accumulator multi-lattice composition**: Open design question — see `docs/superpowers/discussions/2026-04-29-multi-lattice-composition.md`.

## Missing Profiles

None.

## Orphaned Profiles

None.

## Stale Profiles

None — this is an initial profile; all profiles are current at hash `41d584652aa29b09762aa1140ae64fb640473940`.

## Facets Without User-Facing Module

None.
