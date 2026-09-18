# mountainash-rules

![Python](https://img.shields.io/badge/python-3.12-blue) ![License](https://img.shields.io/badge/license-Apache--2.0-green) ![Backend](https://img.shields.io/badge/backends-7-purple)

Business rules that live in code are slow to change — every update requires a code review, a deploy, and a release cycle. This engine moves the logic into **metadata**: rules are rows in a DataFrame (or a database table), and the engine evaluates them against a context without any rule-specific code. Change a rule, reload the DataFrame, and the new behaviour applies immediately. No redeploy. No code change. No branching logic to maintain.

The engine evaluates rules against a context and ranks matches by specificity. Define dimensions with match strategies (exact, range, regex, prefix, set membership, etc.), pass a context, and get back the most specific matching rules — ranked, explained, and ready to use.

Built on [mountainash](https://github.com/mountainash-io/mountainash) expressions for backend-agnostic evaluation. Rules compile once; contexts evaluate in a single vectorised pass. Supports Polars, Pandas, Ibis (DuckDB, SQLite, Polars), and Narwhals backends with zero code changes.

### Why metadata, not code?

- **Rules change faster than code.** Pricing tiers, eligibility criteria, fraud thresholds — these are business decisions that shift weekly. When rules are data, a product owner can update them in a database and the engine picks up the change on the next evaluation. No PR, no deploy, no downtime.
- **Rules are auditable.** Every rule is a row with a name, dimensions, and match criteria. You can diff two rule sets, version them in a table, and explain exactly why a context matched — because the engine tracks per-dimension ternary results (match / unknown / non-match) for every rule.
- **Rules scale without branching.** A hand-coded rule system with 2,000 rules is unmaintainable. A DataFrame with 2,000 rows is just data. The engine evaluates all of them in one vectorised pass regardless of count.

## Installation

End-user installations use interpreter/platform-specific wheels containing the
Rust extension; no Rust toolchain is required to install a compatible wheel.
There is no pure-Python fallback. Source and editable builds require Cargo and
a Rust toolchain. `hatch build` builds the extension through the existing Hatch
backend; `cargo test --locked --lib` exercises its language regressions.

The release matrix targets CPython 3.10–3.14 and PyPy 3.10/3.11 on Linux
x86_64/aarch64, macOS and Windows x86_64. macOS arm64 supports CPython;
PyPy uses macOS x86_64. Local artifact verification does not certify the
entire release matrix; CI builds and smoke-tests each configured artifact.

Development must resolve `mountainash` at delivered merge `b9c0ab4ab6380712c89f27c8d083f658278d4593` or a descendant, alongside its configured sibling dependencies. Do not infer an unreleased Mountainash version from this requirement; native wheels do not resolve the existing publication-chain gap.

## Exact String Languages

`StringLanguage` provides an independent Rust-backed language API. It does not
replace the filter engine's regex implementation or enable disjoint accumulator
cells; that integration remains deferred.

```python
from mountainash_rules import LanguageLimits, StringLanguage

limits = LanguageLimits(
    max_input_bytes=2_000_000,
    max_nesting=64,
    max_nfa_states=50_000,
    max_states=10_000,
    max_transitions=500_000,
    max_work=200_000_000,
)
words = StringLanguage.regex(r"\A(?:cat|car)\z", limits=limits)
only_cat = words.difference(StringLanguage.literal("car", limits=limits), limits=limits)
assert only_cat.witness(limits=limits) == "cat"
assert only_cat.cardinality(10, limits=limits) == 1
loaded = StringLanguage.from_json(only_cat.to_json(limits=limits), limits=limits)
assert loaded.language_id(limits=limits) == only_cat.language_id(limits=limits)
```

Regex construction recognizes complete Unicode-scalar strings containing a search
match. The pinned dialect is `regex-syntax 0.8.10` with Unicode 16.0.0; `RegexOptions`
controls case folding, multiline, dot/newline, CRLF, greed, Unicode and whitespace
flags. Inline flags retain their scope. Backreferences, lookahead and lookbehind
are syntax errors. Literal, prefix, suffix and contains constructors never
interpret their input as regex syntax.

Languages support union, intersection, difference, complement, membership,
emptiness, shortest/scalar-lexicographic witnesses and capped cardinality.
Complement excludes surrogate codepoints and does not represent missing context.
Canonical `language-1` JSON uses complete scalar ranges, minimal reachable states
and BFS numbering. Loading rejects duplicate/unknown fields and noncanonical
graphs; it does not compile a source pattern.

Every operation takes explicit limits. Input-byte limits also bound JSON and
witness output; work covers construction, traversal and conservative Unicode
expansion reservations, not elapsed time. Nesting is limited to 256. Exhaustion
raises `LanguageResourceError` with `resource` and `limit`, never a partial answer.
Malformed patterns and wires raise `LanguageSyntaxError` and `LanguageWireError`.
The example ceilings are illustrative, not benchmarked deployment defaults.
Native dependency and Unicode notices are included in `THIRD_PARTY_LICENSES`.

## Quick Start

```python
import polars as pl
from mountainash_rules import (
    ExpressionRulesEngine, Dimension, DimensionsMetadata, MatchStrategy,
)
from pydantic import BaseModel

# 1. Define your rules as a DataFrame
rules = pl.DataFrame({
    "rule_name": ["premium_au", "standard", "fallback"],
    "region":    ["AU",         "AU",       "<NA>"],       # <NA> = wildcard
    "spend_min": [1000,         0,          -999999999],   # -999999999 = wildcard
    "spend_max": [9999,         999,        -999999999],
})

# 2. Declare how each dimension matches
metadata = DimensionsMetadata(dimensions=[
    Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
    Dimension(
        dimension_name="spend", match_strategy=MatchStrategy.RANGE, data_type=int,
        range_min_field="spend_min", range_max_field="spend_max",
    ),
])

# 3. Build the engine (compiles expressions once)
engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)

# 4. Evaluate a context
class CustomerContext(BaseModel):
    region: str
    spend: int

result = engine.evaluate(CustomerContext(region="AU", spend=1500))

print(result.count)                # 2 — premium_au and fallback survive
print(result.best_match)           # premium_au (matches both dimensions)
print(result.explain("premium_au"))  # {"region": 1, "spend": 1} — both match
print(result.explain("fallback"))    # {"region": 0, "spend": 0} — both wildcard
```

## Match Strategies

| Strategy | Rule Column | Description |
|----------|-------------|-------------|
| `EXACT` | Scalar value | Context value equals rule value |
| `NOT_EQUAL` | Scalar value | Context value differs from rule value |
| `RANGE` | Two columns (min/max) | Context value within [min, max] |
| `GREATER_THAN` | Threshold | Context value > rule threshold |
| `LESS_THAN` | Threshold | Context value < rule threshold |
| `PREFIX` | String | Context value starts with rule value |
| `SUFFIX` | String | Context value ends with rule value |
| `CONTAINS` | Substring | Context value contains rule value |
| `REGEX` | Pattern column | Rule column holds a per-row pattern; search semantics |
| `CONTEXT_REGEX` | Literal pattern on Dimension metadata | Global context validator shared by all rules |
| `SET_MEMBERSHIP` | List | Context value is in rule's list |
| `SET_EXCLUSION` | List | Context value is not in rule's list |

Wildcard values (`<NA>` for strings, `-999999999` for numerics, and typed date/datetime sentinels) produce an UNKNOWN result — the rule is not eliminated but scores lower on specificity.

Missing context and explicit context sentinels earn no specificity in ordinary predicates, including PREFIX/SUFFIX/CONTAINS/per-row REGEX. Boolean absence is null, never a string marker. Strict `CONTEXT_REGEX` guards reject missing input; `EXACT_KEY` permits only rule-side wildcards against missing context. See [missing-context semantics](docs/user-quickstart.md#context-with-missing-fields).

### Boolean context inputs

Boolean dimensions use the semantic default `BooleanCoercion.NONE` (`0`): native `True`/`False` and missing/`None` are admitted. The standalone filter can admit additional concrete input domains with explicit flags:

| Flag | Value | Additional original input domain |
|------|-------|----------------------------------|
| `BINARY_NUMBERS` | `1` | Finite numeric `0` or `1` only |
| `BOOLEAN_TEXT` | `2` | ASCII-whitespace-trimmed, ASCII-case `true`/`false`, or text `1`/`0` |
| `NUMERIC_TRUTHINESS` | `4` | Any finite number (`0` is false; any other value is true) |

Combine actual enum flags with `|` for the standalone filter. `AccumulatorEngine` accepts only the actual `BooleanCoercion.NONE` member; every other flag, combination or policy type raises `ValueError` at construction.

```python
from mountainash_rules import (
    AccumulatorEngine, BooleanCoercion, ExpressionRulesEngine,
)

boolean_inputs = (
    BooleanCoercion.BOOLEAN_TEXT | BooleanCoercion.NUMERIC_TRUTHINESS
)
filter_engine = ExpressionRulesEngine(
    rules=rules, dimension_metadata=metadata, boolean_coercion=boolean_inputs,
)
accumulator_engine = AccumulatorEngine(
    dimension_metadata=metadata, boolean_coercion=BooleanCoercion.NONE,
    limits=limits,  # application-owned ExactLimits
)
```

Admission always examines the original input domain; it never chains conversions. Thus numeric `2` is true under `NUMERIC_TRUTHINESS`, but text `"2"` is invalid even when text and truthiness flags are combined. `None` or a missing field is absence, while concrete numeric `0` requires a numeric flag and empty text is invalid under every policy. An invalid concrete value raises `ValueError` naming the context field and active policy. Backend capability failures remain explicit; Boolean admission does not substitute a fallback backend.

Semantic scalars include Python `bool` and NumPy `bool_`; numeric coercion covers Python/NumPy integers and floats, not Decimal, complex, bytes, or arbitrary truthiness protocols. Recognized backend nulls remain absent; concrete non-finite numbers are invalid. Input is interpreted after any caller-model or dataframe ingestion conversion.

## Hit Policies

How many survivors come back, and in what order, is a **hit policy**: `collect` (default — all survivors ranked by specificity), `unique` (at most one survivor), `first` / `rule_order` (rule-definition order), `priority` (salience descending), and `any` (survivors must agree on output tuples, including nulls). Accepts `HitPolicy` members or exact lowercase strings; invalid values raise `ValueError`. Configure the policy on metadata or override it per evaluation.

```python
result = engine.evaluate(context, hit_policy=HitPolicy.FIRST)
```

Output declarations are validated against the original rule schema at construction. Metadata-backed engines use `DimensionsMetadata.output_fields`; an empty list infers outputs from non-condition columns. Expressions-only engines must supply the keyword-only `output_fields=["price", ...]` to use ANY—there is no guessed output schema. PRIORITY requires an existing field and non-null values on surviving rules. UNIQUE/ANY assertions run before limits or specificity filters.

`result.select(policy)` is available only while the complete candidate set is retained. Any supplied limit/threshold or FIRST/PRIORITY/ANY selection marks the result potentially incomplete—even a no-op limit or singleton selection—and subsequent selection raises `ValueError`. Re-evaluate without those operations to choose another policy. See [policy and selection semantics](docs/user-quickstart.md#policies-output-schemas-and-re-selection).

## Batch Evaluation

Score thousands of contexts in one vectorised pass instead of looping:

```python
contexts = pl.DataFrame({"customer_id": [...], "region": [...], "spend": [...]})
batch = engine.evaluate_batch(contexts, context_id_field="customer_id")

batch.best_matches          # minimum retained rank per context (not necessarily 1)
batch.counts_per_context    # survivors per context
batch.for_context("C042")   # single-context RuleResult
```

Contexts are conformed to the rules backend. `chunk_size` is `None` or a positive Python integer (not Boolean). Chunking bounds each contexts × rules evaluation, not total input staging, accumulated results, or violation diagnostics. Typed empty contexts and typed empty rules preserve the normal result schema, including when chunking is enabled.

Filter batch rows are ordered by `__context_id` then `__rank`. Ranks describe the policy ordering before caller filters: if rank 1 is removed, rank 2 may be the best remaining match. Top-N applies to remaining positions without rewriting those ranks. `for_context()` preserves both rank order and the batch's re-selection restrictions.

Caller ID columns must exist and contain globally unique, non-null values, validated globally before conversion, partition routing or chunking. Rows always expose **`__context_id`**; `batch.context_id_field` records the source field (`"customer_id"` above), not an echoed context column. Without a source field, generated IDs are original zero-based input positions, assigned once before partitioning or chunking. Their stability is within an evaluation, not across unordered database queries.

Boolean batch admission validates the complete submitted input before policy evaluation, chunk work, or accumulator index routing. A late invalid concrete Boolean therefore raises its `ValueError` even if an earlier row would violate UNIQUE/ANY, has no route, or produces no retained result.

`matched_context_ids` and `counts_per_context` describe retained rows after limits. `unmatched_context_ids(original_contexts)` returns submitted IDs absent from those rows in sorted order; custom IDs require the original non-null, unique source column. Generated IDs require the original input order and row count. `for_context()` cannot distinguish an unmatched submitted ID from one never submitted. Pass the original, unmodified contexts to unmatched access; a missing genuine caller-ID field remains an error, not a positional fallback.


### Input boundaries — correctness update

Filter construction requires exactly one nonempty dimension definition: metadata or expressions, never both (even when one is empty). Empty shared metadata remains serializable; zero rules with a valid schema remain supported.

On `evaluate`, `evaluate_batch`, and `explain`, `dimensions=None` means all configured dimensions. An explicit projection must be a nonempty list of distinct names; empty/duplicate/malformed projections raise `ValueError`, while well-formed unknown names retain `KeyError`. Requested order controls dimension presentation, not ranking.

`top_n`, `top_n_per_context`, and `min_specificity` accept only `None` or nonnegative Python integers, excluding Boolean values. Zero top-N and thresholds above the active dimension count yield empty results **after** policy assertions. Invalid configuration is still rejected on empty input. These explicit failures replace previously inconsistent fallback or backend errors.

## Accumulator Engine

`AccumulatorEngine` builds immutable **exact-cell** artifacts. It is separate
from the filter engine: a build is authorized from analyzed source material,
and an application resolves a contract-bound context against native cells. This
guide covers the available E6–E8 interfaces only; it does not assert that E8
is complete or make a release or consumer-completeness claim.

The application owns every declaration and limit. It supplies native source
rows with stable UUIDs, `DimensionsMetadata`, complete native `Aggregate`
declarations, domains, predicate/language envelopes, routing,
contracts/profiles, a validation policy, and an `ExactLimits` instance. Native
aggregates require `output_name`, `data_type`, and
`numeric_semantics="numeric-1"` together; the output name is qualified (for
example, `pricing.total`). There is no package default for `ExactLimits`.

The source gate is deliberately a real workflow, not a constructor shortcut:

1. Call `analyze_sources(rows, ...)` with the application's complete
   declarations and explicit `limits`.
2. Send its findings to the application's review process. If warnings are
   accepted, that process creates `WarningApproval` records naming the selected
   report, finding IDs, authority and actor, and a scope covering the findings.
   `attach_warning_approvals(bundle, approvals, limits=limits)` validates and
   stores those records; it never approves a warning itself.
3. Call `validate_build_input(rows, bundle=..., analysis_input_id=...,
   source_report_id=..., approvals=..., ...)` with the same current source
   material and declarations. It recomputes identities, replays retained
   evidence, and rejects errors, incomplete checks, or warnings without
   matching scoped approval.
4. Pass the resulting `ValidatedBuildInput` as `validation=` to `build()` or
   `build_all()`.

For a clean report, the approval sequence is empty. Do not fabricate approvals,
reports, limits, source UUIDs, domains, policy, trust, or a successful outcome.

```python
from mountainash_rules import AccumulatorEngine, ValidatedBuildInput

def build_current_partition(
    engine: AccumulatorEngine,
    source_rows,
    validation: ValidatedBuildInput,
    partition_key: dict[str, object] | None = None,
):
    return engine.build(
        source_rows,
        validation=validation,
        partition_key=partition_key,
    )
```

`AccumulatorEngine` itself requires `DimensionsMetadata`, complete native
aggregate declarations, and explicit application-owned limits:

```python
engine = AccumulatorEngine(
    dimension_metadata=metadata,
    aggregates=aggregates,
    limits=limits,
)
lattice = engine.build(rows, validation=validated_build)
```

When context-key dimensions are declared, `build_all(rows,
validation=validated_build)` produces the declared partitions, and
`build(..., partition_key=...)` requires exactly every declared key. Reuse
`engine.index(lattices)` for routed calls. Each application call names the
authorized contract and profile:

```python
result = engine.apply(
    lattice,
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=dont_care,
)
```

The labels and masks must be admitted by an actual binding/profile.
`dont_care` is an explicit request mask and is permitted only where that
profile declares it.

An `AccumulatorResult` is an outcome, not an ordered legacy result frame.
Inspect `status`, `reason`, `binding_id`, `contract_id`, `profile_id`,
`values`, `cell_id`, `contributor_ids`, `may_have_no_match`, `observations`,
and `issues`. A `decision` has established values; an unresolved or no-match
outcome does not. Candidate-mode profiles expose `candidate_cells` and
`candidate_contributors` without promoting them to a decision. `lineage`
describes definite requested-output contributors only; use
`candidate_lineage(cell_id)` for a candidate cell.

`Lattice.save(directory, limits=limits)` and
`Lattice.load(directory, limits=limits)` are bounded native snapshot
operations. A native snapshot contains exactly 11 files: `manifest.yaml`,
eight Parquet relations (`lattice`, `sources`, `contributors`, `scopes`,
`scope_keys`, `source_maps`, `vectors`, and `words`), plus
`predicates.json` and `validation.json`. A loaded lattice is served through a
compatible `AccumulatorEngine`: its dimensions and aggregate declarations must
agree with the compiled artifact, while the caller still supplies explicit
operation limits.

`lattice.with_binding(binding, evidence=evidence, limits=limits)` validates
complete portable binding evidence and returns an immutable view. It neither
modifies the original lattice nor grants permission merely because evidence was
loaded. Use `artifact_kind` and typed `partition_identity` to inspect a native
artifact rather than legacy `is_composed`. Flat legacy/imported lattices remain
inspection-only; they are not valid inputs to exact build, bind, route, or
apply operations.

### Phase5 migration handoff

The remaining external migration is owned outside this documentation change:
Babel's base `LatticeView`, CSV/DMN exporters and importers, and manifest
dispatch; plus the service registry, model, route, configuration, and fixtures.
Those owners must migrate their own bound-artifact handling. They are not
modified here, and E8 is not claimed complete.

## Serialisable Metadata

`DimensionsMetadata` round-trips through YAML, so dimension definitions can live beside the rule data instead of in code:

```python
metadata.to_yaml_file("dimensions.yaml")
metadata = DimensionsMetadata.from_yaml_file("dimensions.yaml")
```

The [mountainash-rules-babel](https://github.com/mountainash-io/mountainash-rules-babel) package builds on this to import/export lattices as CSV (with a metadata manifest sidecar) and DMN.

## Backend Support

The engine is backend-agnostic. Pass any supported DataFrame type as `rules`:

| Backend | Type |
|---------|------|
| Polars | `pl.DataFrame` |
| Pandas | `pd.DataFrame` |
| Narwhals (Polars) | `nw.from_native(pl.DataFrame(...))` |
| Narwhals (Pandas) | `nw.from_native(pd.DataFrame(...))` |
| Ibis (DuckDB) | `ibis.duckdb.connect().create_table(...)` |
| Ibis (Polars) | `ibis.polars.connect().create_table(...)` |
| Ibis (SQLite) | `ibis.sqlite.connect().create_table(...)` |

Supported operations produce identical results; backend capability limits still apply:

- SET_MEMBERSHIP/SET_EXCLUSION use `list.t_contains()`. Use Polars or Ibis-DuckDB for engine evaluation: Pandas/Narwhals reject column-valued needles, and SQLite has no list column type.
- PREFIX/SUFFIX/CONTAINS require column-valued string predicates, unsupported by Pandas, Narwhals-Pandas and the Ibis-Polars translator.
- Ibis-Polars cannot execute the engine's row indexing. Expression-level support does not imply engine-level support.
- Per-row REGEX retains its Polars-native implementation.

Polars is recommended for performance.

## Installation

```bash
# Development install with hatch
git clone https://github.com/mountainash-io/mountainash-rules.git
cd mountainash-rules
hatch env create
```

Resolve the configured `mountainash` dependency at delivered merge `b9c0ab4ab6380712c89f27c8d083f658278d4593` or a descendant, together with `mountainash-data` and `mountainash-settings` (see `hatch.toml`). This requirement names a delivered dependency revision, not an invented release version or checkout layout.

Set strategies require that merge's `list.t_contains()` API; older snapshots using scalar `t_is_in(list_column)` are incompatible. Keep resolved dependency revisions and installed packages aligned when comparing local results with CI; an existing Hatch environment can contain older non-editable dependency copies.

## Textbook

The [production textbook](https://docs.mountainash.io/mountainash-rules/) is
built from `main`; the [development textbook](https://docs.mountainash.io/mountainash-rules/dev/)
is built from `develop`. Each push to either branch builds both snapshots and
publishes them together. A failed build leaves the previous paired site live.

For initial activation, merge the textbook changes into both branches and
configure Pages, its environment, and the shared custom domain first. Then set
the repository Actions variable `TEXTBOOK_PUBLISHING_ENABLED` to `true` and
manually dispatch `deploy-textbook.yml`. Until enabled, publishing runs are
skipped; a one-sided bootstrap cannot deploy an incomplete site.

The source artifacts live together in this repository:

- `docs-site/profile/`: package profile and source provenance.
- `docs-site/learning-graph/`: canonical graph and FAQ artifacts.
- `docs-site/site/`: MkDocs configuration, textbook Markdown, and refresh state.

Preview locally without installing the source package or sibling repositories:

```bash
uv run --no-project --with-requirements docs-site/requirements.txt \
  python -m mkdocs serve --config-file docs-site/site/mkdocs.yml
```

Refreshes are manual. Load `textbook-refresh` from the central
`hiivmind-documentation-profile` tooling project and supply this repository's
absolute root as `source_repo`, starting with `mode: check`. For a separate
profile update, supply `docs-site/profile/` as the profiler's explicit output.
Do not regenerate content merely to publish it or advance source baselines on
a directory move. Preserve the existing FAQ format; the marker-only FAQ
exporter does not support it and must not overwrite its JSON.

The inherited chapters include specifications for 32 simulations whose HTML
implementations do not yet exist. Their missing embeds are an accepted
pre-existing content gap for this migration, not a working simulation library.

## Development

| Command | Description |
|---------|-------------|
| `hatch run test:test-quick` | Run tests (no coverage) |
| `hatch run test:test` | Run tests with coverage reports |
| `hatch run test:test-target-quick tests/path.py -v` | Run specific tests |
| `hatch run test:test-perf` | Run performance benchmarks |
| `hatch run test:test-perf-save` | Benchmarks + save JSON baseline |
| `hatch run ruff:check` | Lint |
| `hatch run ruff:fix` | Lint + auto-fix |
| `hatch run mypy:check` | Type check |
| `hatch run radon:radon-cc` | Cyclomatic complexity |

Routine local tests and PR CI select **Polars and Ibis-DuckDB**. Other
backend-parametrized cases are deselected before fixture setup; tests without
backend parameters still run. This is a reduced feedback matrix, not full
portability acceptance.

- Full seven-backend release check: `hatch run test:test-target-quick --backends=all`.
- Specific backends: `hatch run test:test-target-quick --backends=polars,pandas`.
- Direct pytest accepts the same `--backends` option; omitting it selects the
  routine pair. Existing benchmark/marker selection remains independent.

## Architecture

The engine uses **signed-integer ternary logic** (-1 = non-match, 0 = unknown, 1 = match) to evaluate each rule dimension independently, then combines results in a single vectorised pass:

1. **Compile** — `DimensionCompiler` converts dimension metadata into backend-agnostic expression templates at construction time.
2. **Bind** — Context values are injected as literal columns alongside the rules DataFrame.
3. **Evaluate** — All dimension expressions execute in one `with_columns` call, producing a ternary value per dimension per rule.
4. **Rank and select** — Rules with any -1 are eliminated. Survivors receive a pre-filter, 1-based policy rank (specificity descending by default); policy assertions precede caller filters and cardinality.

The engine and result layer use only `mountainash.relations` and `mountainash.expressions` — no direct backend imports. See [CLAUDE.md](CLAUDE.md) for full architectural details.

## Mountain Ash Ecosystem

This package is part of the [Mountain Ash](https://github.com/mountainash-io) data framework ecosystem.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.
