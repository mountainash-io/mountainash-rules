# Rules Engine Conceptual E-R Diagram and Analysis

## Data Relationships

```
CONTEXT (1 row)                    RULES (N rows)                     DIMENSIONS (M definitions)
┌─────────────────┐               ┌─────────────────────────────┐      ┌──────────────────────┐
│ Context Entity  │               │ Rules Panel                 │      │ Dimension Metadata   │
├─────────────────┤               ├─────────────────────────────┤      ├──────────────────────┤
│ DIM_1: "A"     │ ────────────► │ rule_name   │ DIM_1  │...   │ ◄──┤ DIM_1: EXACT        │
│ DIM_2: 25      │               │ rule_1      │ "A"    │...   │      │ DIM_2: RANGE        │
│ DIM_3: "XYZ"   │               │ rule_2      │ "B"    │...   │      │ DIM_3: REGEX        │
│ ...            │               │ rule_3      │ NULL   │...   │      │ ...                 │
└─────────────────┘               │ ...         │ ...    │...   │      └──────────────────────┘
      │                          └─────────────────────────────┘
      │                                     │
      │                          ┌─────────────────────────────┐
      │                          │ Rule State (computed)       │
      │                          ├─────────────────────────────┤
      │                          │ filter_rule_unknown: 2|3|5  │
      │                          │ filter_context_unknown: 2|3|5│
      │                          │ filter_match: 2|3|5         │
      │                          │ cumu_dimension_count: int   │
      │                          │ cumu_soft_match_count: int  │
      │                          │ cumu_hard_match_count: int  │
      │                          │ dropped: bool|null          │
      │                          │ dropped_by_dimension: str   │
      │                          │ keep: bool (final result)   │
      │                          └─────────────────────────────┘
      │
      └─────────► FOR EACH DIMENSION: Context Value vs Rule Values ◄─────┘
                            │
                    ┌───────────────────────────────────────┐
                    │ Dimension Processing Loop             │
                    │                                       │
                    │ 1. apply_filter_rule_unknown()       │
                    │    • Checks if rule value is UNKNOWN │
                    │    • Adds filter_rule_unknown column │
                    │                                       │
                    │ 2. apply_filter_context_unknown()    │
                    │    • Checks if context value UNKNOWN │
                    │    • Adds filter_context_unknown col │
                    │                                       │
                    │ 3. apply_match_filter()              │
                    │    • EXACT/RANGE/REGEX comparison    │
                    │    • Adds filter_match column        │
                    │                                       │
                    │ 4. apply_dimension_filter_flags()    │
                    │    • Updates cumulative counters     │
                    │    • Sets dropped flags              │
                    │    • Mutates: 6 columns per iteration│
                    │                                       │
                    │ 5. save_dimension_intermediate_values │
                    │    • Stores state for observability  │
                    │                                       │
                    └───────────────────────────────────────┘
```

## Data Flow Analysis

### Current Architecture (Dimension-by-Dimension)

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────────┐
│   Context   │    │ Initial Rules    │    │ After DIM_1     │    │ After DIM_2  │
│             │───►│ (N rows)         │───►│ Processing      │───►│ Processing   │ ─►...
│ DIM_1: "A"  │    │ rule_1, rule_2,  │    │ + 6 new columns │    │ + 6 new cols │
│ DIM_2: 25   │    │ rule_3, ...      │    │ + counters      │    │ + counters   │
│ DIM_3: "XY" │    └──────────────────┘    │ + flags         │    │ + flags      │
└─────────────┘                           └─────────────────┘    └──────────────┘

Each dimension processing adds/updates:
• filter_rule_unknown (new column)
• filter_context_unknown (new column)
• filter_match (new column)
• cumu_dimension_count (update)
• cumu_soft_match_count (update)
• cumu_hard_match_count (update)
• dropped (update)
• dropped_by_dimension (update)
• dimension_any_true (temporary)
• dimension_any_false (temporary)
```

## Cardinality Analysis

| Entity | Cardinality | Description |
|--------|-------------|-------------|
| **Context** | 1 | Single context to evaluate |
| **Rules** | N (100s-1000s) | Rule set to match against |
| **Dimensions** | M (typically 3-10) | Dimension definitions |
| **Rule×Dimension intersections** | N×M | Each rule has value for each dimension |
| **Intermediate columns** | N×(3×M + 5) | 3 filter columns per dimension + 5 cumulative |
| **mutate() operations** | M + 2 | One per dimension + initialization + priority |

## Memory and Mutation Analysis

### Current Approach Memory Growth
```
Initial Rules:        N rows × D columns
After Dimension 1:    N rows × (D + 10) columns  [+10 columns per dimension]
After Dimension 2:    N rows × (D + 20) columns
After Dimension M:    N rows × (D + 10M) columns

Final mutate() operations per context evaluation: M + 2
```

### Opportunities for Optimization

#### 1. **Batch Context Extraction** ✅ (Already Implemented)
```python
# Current: Efficient single batch operation
context_values = ContextHelper.get_all_context_values(context=context, dimensions=active_dimensions)
```

#### 2. **Reduce Intermediate Column Creation**
**Current Problem**: Each dimension adds 3 filter columns that are only used for that iteration

**Opportunity**: Use temporary expressions instead of materialized columns
```python
# Instead of:
rules = rules.mutate(filter_rule_unknown=..., filter_context_unknown=..., filter_match=...)
rules = self.apply_dimension_filter_flags(rules=rules, dimension=dimension)

# Could be:
dimension_result = self._evaluate_dimension_inline(rules, dimension, context_value)
rules = rules.mutate(
    cumu_dimension_count=ibis._.cumu_dimension_count + 1,
    cumu_soft_match_count=ibis._.cumu_soft_match_count + dimension_result.soft_matches,
    cumu_hard_match_count=ibis._.cumu_hard_match_count + dimension_result.hard_matches,
    dropped=ibis.ifelse(ibis._.dropped.isnull() & ~dimension_result.any_true, True, ibis._.dropped)
)
```

#### 3. **Single Final Priority Calculation** ✅ (Already Optimal)
Priority calculation is already done once at the end.

#### 4. **Early Termination Optimization**
```python
# After each dimension, check if all rules are dropped
if rules.filter(fc.eq("dropped", False)).count() == 0:
    break  # No rules left to evaluate
```

#### 5. **Regex Pattern Caching** (Previously Identified)
Cache compiled regex patterns to avoid recompilation.

## Recommended Optimizations

### High Impact, Low Risk
1. **Inline Dimension Evaluation**: Eliminate intermediate filter columns
2. **Regex Pattern Caching**: Add `@lru_cache` to pattern compilation
3. **Early Termination**: Stop processing when all rules are dropped

### Medium Impact, Medium Risk
4. **Column Projection**: Only select needed columns during processing
5. **Batch Unknown Detection**: Pre-calculate unknown values for all dimensions

### Lower Priority
6. **Memory-Efficient Counters**: Use smaller integer types for counters
7. **Lazy Evaluation**: Defer expensive operations until final materialization

## Key Insight

The current architecture is **fundamentally sound**. The dimension-by-dimension approach naturally provides:
- **Short-circuiting**: Rules get dropped early
- **Memory locality**: Processing one dimension at a time
- **Debuggability**: Clear intermediate states
- **Scalability**: Linear growth with dimensions

The main optimization opportunity is **reducing intermediate column materialization**, not changing the core sequential processing approach.
