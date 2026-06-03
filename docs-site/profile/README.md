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
result. Problems that would require exponential if/else trees become a single
vectorized evaluation. Every evaluation explains itself: not just "matched" or
"didn't match," but which dimensions matched, which were wildcards, and which
excluded.

## Installation

```bash
pip install mountainash-rules
```

mountainash-rules depends on the core mountainash expression engine, which
provides backend portability across Polars, Narwhals, and Ibis.

## Use Cases

### The Pricing Engine

A pricing team maintains discount and eligibility rules as a table -- customer
segment, geography, product tier, contract terms. The rule engine evaluates
directly against the production database. The accumulator resolves overlapping
discounts, computing all valid combinations of applicable rules. Every
customer's price comes with a full explanation of which rules applied and why,
making pricing justification straightforward for both internal review and
customer-facing communication.

### The Compliance Evaluator

Regulatory conditions become rule dimensions. Evaluate a portfolio against
compliance rules with full explainability -- which conditions matched, which
were neutral, which excluded. Export the rule definitions as DMN decision tables
(via mountainash-rules-babel) for regulatory review. The rules, the evaluation,
and the audit trail are one unified system rather than three disconnected
processes.

### The Feature Flag Engine

Feature flags defined as rule tables -- user segment, region, plan tier, beta
status -- evaluate against the production database in one vectorized pass. No
external feature flag service to maintain. When flags grow complex, with
percentage rollouts interacting with plan restrictions and regional overrides,
the accumulator handles the combinations that would otherwise require nested
conditional logic.

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
values are compared to rule metadata. Eleven match strategies cover the full
range of real-world business logic: exact match, ranges, greater-than and
less-than comparisons, prefix, suffix, contains, regex, set membership, and set
exclusion. Wildcard dimensions match everything, enabling graduated specificity
where a general rule applies unless a more specific one exists.

### Single-Context Rule Evaluation

Pass a context dictionary to the expression rules engine and receive a result
containing every surviving rule, ranked by specificity. Surviving rules are
automatically ranked so that a rule matching three dimensions explicitly is more
specific than one matching two and wildcarding the third. The best match is
always the most specific, and ties resolve deterministically. Access the best
match, count survivors, or iterate the full ranked list.

### Built-In Explainability

Every evaluation result carries a per-dimension ternary breakdown -- match,
unknown, or non-match -- so you can see exactly which dimensions contributed to
a rule's survival and which were neutral. Call explain on any rule to get the
full picture. Regulatory audit, pricing justification, eligibility appeals --
the explanation is always available without additional processing or post-hoc
analysis.

### Accumulator Engine and Rule Lattices

The accumulator engine handles problems where you need valid combinations of
rules rather than a single winner. Build a lattice from your rule set, then
apply a context to retrieve accumulated values with full provenance showing
which rules contributed to each combination. Partitioned lattices, created by
marking dimensions as context keys, keep large rule sets fast and
memory-efficient by building a separate lattice per partition and selecting the
correct one at evaluation time.

### Backend Portability

Because match strategies compile to mountainash expressions, and those
expressions compile to Polars, Narwhals, and Ibis, rules evaluate on any backend
the expression engine reaches. Store rule tables in Snowflake. Evaluate against
DuckDB. Persist results to Iceberg. The rule definitions do not change. The same
rules, the same evaluation logic, the same explainability -- regardless of where
the data lives.

## Architecture

mountainash-rules provides two complementary evaluation architectures that share
a unified dimension model and expression system. The filter engine compiles each
dimension's match strategy into a backend-agnostic expression, then evaluates
those compiled expressions in a single vectorized pass that produces ternary
match columns, survival flags, and specificity scores. The accumulator engine
assigns each rule a unique prime number, represents rule combinations as the
product of their constituent primes, and iteratively expands valid combinations
while pruning dominated entries via modular arithmetic.

Both engines are backend-agnostic through the mountainash relations and
expressions layers. All DataFrame operations flow through the Relation protocol,
which abstracts over concrete backends. A three-valued logic system (match,
unknown, non-match) handles missing or wildcard rule values gracefully, with
sentinel values automatically mapped to the unknown state. This ternary
foundation ensures that specificity scoring, survival filtering, and
explainability all behave consistently regardless of the backend executing the
evaluation.

## Contributing

mountainash-rules is designed for extensibility across match strategies,
aggregate operations, and backends. The codebase follows consistent patterns
that make it straightforward to add new capabilities while maintaining
cross-backend correctness. New match strategies follow a clear path: add the
enum value, add validation logic, implement the compile method, and add a
corresponding test class. The test suite uses cross-backend parameterisation to
verify consistent behaviour across all supported DataFrame backends, and new
strategies and features should follow these patterns.

## Maintaining

The ternary logic invariant -- where 1 represents a match, 0 represents unknown,
and -1 represents a non-match -- is the foundation of evaluation correctness
across both engines. Sentinels map to the unknown state through the ternary
column wrapper, and specificity is computed by counting only definite matches.
This invariant must hold across all match strategies and all backends, and
verifying it should be the first concern when modifying evaluation logic.

The filter engine follows a six-step evaluation pipeline: bind context columns,
add ternary match columns, compute survival and specificity scores, filter
non-survivors, sort and rank by specificity, and drop internal columns. Each
step is a pure DataFrame transformation, making the pipeline straightforward to
trace and debug. The accumulator engine's prime-product encoding supports
approximately 500 rules per partition, with overflow guards in place. Partitions
with more rules will raise an error, a limit inherent to the encoding that
should be communicated clearly to users working with large rule sets.

When modifying engine internals, run the full cross-backend test suite --
including the dedicated backend purity tests and accumulator backend tests -- to
verify consistent behaviour. The test suite also includes regression tests that
capture previously-discovered edge cases. Keeping these green across all
backends is the primary quality gate for changes to the evaluation pipeline.

