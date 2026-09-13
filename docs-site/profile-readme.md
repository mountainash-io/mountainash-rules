# mountainash-rules

**A vectorized rule engine that evaluates on any backend, explains every
decision, and solves combinatorial problems that simpler engines cannot
express.**

## Vision

Business rules -- pricing tiers, eligibility matrices, compliance conditions,
feature flags, configuration logic -- live at the heart of every data-driven
organisation. They determine who qualifies, what price applies, which features
appear, and how regulations are met. Yet most rule engines force you to choose
between expressiveness and performance, between portability and explainability.

mountainash-rules evaluates business rules as tabular data against whatever
backend holds the data, with vectorized performance and built-in explainability.
Rule definitions are tabular data. Match strategies compile to mountainash
expressions. Those expressions compile to Polars in a notebook, DuckDB in a
pipeline, Snowflake in production, or ClickHouse for analytics. The rules do not
move to the data -- the expressions compile to the backend.

When overlapping rules interact -- overlapping discount tiers, interacting
eligibility conditions, complex regulatory criteria -- the accumulator engine
pre-computes all consistent combinations and traces the provenance of every
result, and can persist that computation to disk so production requests never
rebuild it. Every evaluation explains itself: not just "matched" or
"didn't match," but which dimensions matched, which were wildcards, and which
excluded.

## Installation

```bash
pip install mountainash-rules
```

mountainash-rules depends on the core mountainash expression engine, which
provides backend portability across Polars, Narwhals, and Ibis.

Licensed under Apache-2.0.

## Use Cases

### The Pricing Engine

A pricing team maintains discount and eligibility rules as a table -- customer
segment, geography, product tier, contract terms. The rule engine evaluates
directly against the production database, scoring a single customer or the
whole customer base in one batch call. The accumulator resolves overlapping
discounts, computing all valid combinations of applicable rules. Every
customer's price comes with a full explanation of which rules applied and why,
making pricing justification straightforward for both internal review and
customer-facing communication.

### The Compliance Evaluator

Regulatory conditions become rule dimensions. Evaluate a portfolio against
compliance rules with full explainability -- which conditions matched, which
were neutral, which excluded. A `unique` hit policy can assert that exactly one
ruling applies per case, raising immediately if the rule set is ambiguous.
Export the rule definitions as DMN decision tables (via mountainash-rules-babel)
for regulatory review. The rules, the evaluation, and the audit trail are one
unified system rather than three disconnected processes.

### The Feature Flag Engine

Feature flags defined as rule tables -- user segment, region, plan tier, beta
status -- evaluate against the production database in one vectorized pass. No
external feature flag service to maintain. When flags grow complex, with
percentage rollouts interacting with plan restrictions and regional overrides,
the accumulator handles the combinations that would otherwise require nested
conditional logic, with the resulting lattice built once and persisted for
every subsequent request.

### The Conformed Business Rule Library

A central team publishes rule sets as a versioned Python library -- pricing
rules, eligibility rules, compliance rules. Any team in the organisation imports
the library and evaluates against their own backend. The rules are the same
everywhere. The explainability is the same everywhere. Consistency stops being
an aspiration and becomes an architectural guarantee.

## Key Capabilities

### Defining Dimensions

Dimensions describe the axes your rules operate on. Each dimension pairs a data
column with a match strategy, giving you fine-grained control over how context
values are compared to rule metadata. Twelve match strategies cover the full
range of real-world business logic: exact match, an exact-key strategy for
partition routing, ranges, greater-than and less-than comparisons, prefix,
suffix, contains, per-row regex, context-level regex, set membership, and set
exclusion. Wildcard dimensions match everything, enabling graduated specificity
where a general rule applies unless a more specific one exists. Dimension
metadata round-trips to and from YAML.

### Single-Context and Batch Rule Evaluation

Pass a context dictionary to the expression rules engine and receive a result
containing every surviving rule, ranked by specificity. Surviving rules are
automatically ranked so that a rule matching three dimensions explicitly is more
specific than one matching two and wildcarding the third. Need to score a whole
population at once? `evaluate_batch()` cross-joins many contexts against the
rules in a single vectorized pass and ranks survivors per context, with
accessors for best matches, per-context counts, and matched/unmatched context
IDs.

### Hit Policies

Control how many survivors an evaluation returns. `collect` (the default)
returns every survivor; `unique` raises if more than one rule survives;
`first` and `priority` narrow to a single row; `any` raises on conflicting
outputs; `rule_order` preserves table order. Hit policies live on the
dimension metadata or can be supplied per call, and can be re-applied
post-hoc over an untruncated result.

### Built-In Explainability

Every evaluation result carries a per-dimension ternary breakdown -- match,
unknown, or non-match -- so you can see exactly which dimensions contributed to
a rule's survival and which were neutral. Call explain on any rule to get the
full picture, or call the engine's own `explain()` to score every rule in the
table against a context at once, with no survival filtering. Regulatory audit,
pricing justification, eligibility appeals -- the explanation is always
available without additional processing or post-hoc analysis.

### Accumulator Engine, Rule Lattices, and Persistence

The accumulator engine handles problems where you need valid combinations of
rules rather than a single winner. Build a lattice from your rule set, then
apply a context to retrieve accumulated values -- sum, min, max, or product --
with full provenance showing which rules contributed to each combination.
Partitioned lattices, created by marking dimensions as context keys, keep large
rule sets fast and memory-efficient by building a separate lattice per
partition and routing each context to the correct one via the same ternary and
specificity semantics that power ordinary rule evaluation. A built lattice can
be saved to disk as a parquet-and-manifest snapshot and reloaded later, so
production evaluation never has to rebuild it.

### Backend Portability

Because match strategies compile to mountainash expressions, and those
expressions compile to Polars, Narwhals, and Ibis, rules evaluate on any backend
the expression engine reaches. Store rule tables in Snowflake. Evaluate against
DuckDB. Persist results to Iceberg. The rule definitions do not change. The same
rules, the same evaluation logic, the same explainability -- regardless of where
the data lives.

## Architecture

mountainash-rules provides two complementary evaluation architectures that share
a unified dimension model and expression system, organised as `core/`
(backend-agnostic building blocks) plus `engines/filter/` and
`engines/accumulator/` (dependency direction is one-way: accumulator depends on
filter, which depends on core; core never imports from engines). The filter
engine compiles each dimension's match strategy into a backend-agnostic
expression, then evaluates those compiled expressions in a single vectorized
pass that produces ternary match columns, survival flags, and specificity
scores -- the same compiled pipeline backs single-context evaluation, batch
evaluation, and the unfiltered `explain()` view. The accumulator engine assigns
each rule a unique prime number, represents rule combinations as the product of
their constituent primes, and iteratively expands valid combinations while
pruning dominated entries via modular arithmetic; a `LatticeIndex` layer routes
partitioned contexts to the correct lattice using the same ternary logic.

Both engines are backend-agnostic through the mountainash relations and
expressions layers. All DataFrame operations flow through the Relation
protocol, which abstracts over concrete backends. A three-valued logic system
(match, unknown, non-match) handles missing or wildcard rule values gracefully,
with sentinel values automatically mapped to the unknown state -- including
in-band sentinels for set-typed dimensions, which cannot rely on `null` as a
portable wildcard representation. This ternary foundation ensures that
specificity scoring, survival filtering, and explainability all behave
consistently regardless of the backend executing the evaluation.

## Contributing

mountainash-rules is designed for extensibility across match strategies,
aggregate operations, hit policies, and backends. The codebase follows
consistent patterns that make it straightforward to add new capabilities while
maintaining cross-backend correctness. New match strategies follow a clear
path: add the enum value, add validation logic, implement the compile method,
and add a corresponding test class. New aggregate operations extend the
`AggregateOp` enum and the accumulator's level-expansion logic; new hit
policies extend the `HitPolicy` enum and the ordering/assertion logic in
`core/hit_policy.py`. The test suite mirrors the source package layout
one-to-one and uses cross-backend parameterisation to verify consistent
behaviour across all supported DataFrame backends -- new strategies and
features should follow these patterns.

## Maintaining

The ternary logic invariant -- where 1 represents a match, 0 represents unknown,
and -1 represents a non-match -- is the foundation of evaluation correctness
across both engines. Sentinels map to the unknown state through the ternary
column wrapper, and specificity is computed by counting only definite matches.
This invariant must hold across all twelve match strategies and all backends,
and verifying it should be the first concern when modifying evaluation logic.

The filter engine's compiled pipeline -- bind context columns, add ternary
match columns, compute survival and specificity scores -- is shared by
single-context evaluation, batch evaluation, and `explain()`. Each step is a
pure DataFrame transformation, making the pipeline straightforward to trace and
debug. The accumulator engine's prime-product encoding is bounded by two
independent limits: at most ~15 rules can combine before int64 overflow
(intrinsic, raises `LatticeWidthExceededError`), and at most
`MAX_RULES_PER_PARTITION` (10,000) rules can share a partition (a policy
constant, the prime table size). Both limits should be communicated clearly to
users working with large rule sets.

Backend purity is enforced package-wide by a parametrised test with zero
per-module exemptions; only three import-line `# allow:` tags exist for
capabilities mountainash does not yet expose in a backend-agnostic form (the
per-row regex fallback, the accumulator's empty-build schema seed, and the
lattice snapshot's parquet read). Any new native-backend import must either use
one of mountainash's existing abstractions or add and justify a fourth
documented exemption.

When modifying engine internals, run the full cross-backend test suite --
including the dedicated backend purity tests and accumulator backend tests --
to verify consistent behaviour. The test suite also includes regression tests
that capture previously-discovered edge cases. Keeping these green across all
backends is the primary quality gate for changes to the evaluation pipeline.
