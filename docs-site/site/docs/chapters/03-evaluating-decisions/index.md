---
title: "Previous chapter: Evaluating, Selecting and Explaining Decisions"
search:
  exclude: true
---

# Previous chapter reference {#legacy-reference-page}

This address belongs to an earlier edition. Its material is now organized into the chapters below. The section links retain earlier URL fragments and point to their current destinations.

## Current chapters {#legacy-chapter-destinations}

- [Chapter 4: Using the Expression Rules Engine](../04-expression-rules-engine/index.md)
- [Chapter 5: Expression Engine Hit Policies, Results and Explanations](../05-expression-results-and-policies/index.md)

## Previous sections {#legacy-section-destinations}

### Evaluating, Selecting and Explaining Decisions {#evaluating-selecting-and-explaining-decisions}

See [the complete book contents](../index.md).

### Construct an engine and evaluate a context {#construct-an-engine-and-evaluate-a-context}

See [ExpressionRulesEngine](../04-expression-rules-engine/index.md#construct-an-engine-and-evaluate-a-context).

### The ExpressionRulesEngine class {#the-expressionrulesengine-class}

See [ExpressionRulesEngine](../04-expression-rules-engine/index.md#construct-an-engine-and-evaluate-a-context).

### Constructing an engine {#constructing-an-engine}

See [Engine Construction](../04-expression-rules-engine/index.md#constructing-an-engine).

### Convenience vs. advanced construction {#convenience-vs-advanced-construction}

See [Convenience vs Advanced Path](../04-expression-rules-engine/index.md#convenience-vs-advanced-construction).

### Evaluating a context in one pass {#evaluating-a-context-in-one-pass}

See [Single-Pass Evaluation](../04-expression-rules-engine/index.md#evaluating-a-context-in-one-pass).

### Understand survival and ranking {#understand-survival-and-ranking}

See [Survival Computation](../04-expression-rules-engine/index.md#understand-survival-and-ranking).

### Survival: the outcome rule over ternary values {#survival-the-outcome-rule-over-ternary-values}

See [Survival Computation](../04-expression-rules-engine/index.md#understand-survival-and-ranking).

### Specificity: how many dimensions matched {#specificity-how-many-dimensions-matched}

See [Specificity Scoring](../04-expression-rules-engine/index.md#specificity-how-many-dimensions-matched).

### Rank: ordering and breaking ties {#rank-ordering-and-breaking-ties}

See [Rank Assignment](../04-expression-rules-engine/index.md#rank-ordering-and-breaking-ties).

### Read the result contract {#read-the-result-contract}

See [RuleResult Class](../04-expression-rules-engine/index.md#read-the-result-contract).

### The RuleResult wrapper {#the-ruleresult-wrapper}

See [RuleResult Class](../04-expression-rules-engine/index.md#read-the-result-contract).

### survivors {#survivors}

See [Survivors Accessor](../04-expression-rules-engine/index.md#survivors).

### best_match {#best_match}

See [Best Match Accessor](../04-expression-rules-engine/index.md#best_match).

### count {#count}

See [Count Accessor](../04-expression-rules-engine/index.md#count).

### active_dimensions {#active_dimensions}

See [Active Dimensions](../04-expression-rules-engine/index.md#active_dimensions).

### Choose and reapply a hit policy {#choose-and-reapply-a-hit-policy}

See [HitPolicy Enum](../05-expression-results-and-policies/index.md#choose-and-reapply-a-hit-policy).

### The HitPolicy enum {#the-hitpolicy-enum}

See [HitPolicy Enum](../05-expression-results-and-policies/index.md#choose-and-reapply-a-hit-policy).

### COLLECT: the default, unfiltered ranking {#collect-the-default-unfiltered-ranking}

See [Collect Policy](../05-expression-results-and-policies/index.md#collect-the-default-unfiltered-ranking).

### UNIQUE: assert at most one survivor {#unique-assert-at-most-one-survivor}

See [Unique Policy](../05-expression-results-and-policies/index.md#unique-assert-at-most-one-survivor).

### FIRST and PRIORITY: pick a single winner {#first-and-priority-pick-a-single-winner}

See [First And Priority Policy](../05-expression-results-and-policies/index.md#first-and-priority-pick-a-single-winner).

### ANY: survivors that must agree {#any-survivors-that-must-agree}

See [Any Policy](../05-expression-results-and-policies/index.md#any-survivors-that-must-agree).

### RULE_ORDER: preserve declaration order {#rule_order-preserve-declaration-order}

See [Rule Order Policy](../05-expression-results-and-policies/index.md#rule_order-preserve-declaration-order).

### HitPolicyViolationError {#hitpolicyviolationerror}

See [HitPolicyViolationError](../05-expression-results-and-policies/index.md#hitpolicyviolationerror).

### Table-level hit policy fields {#table-level-hit-policy-fields}

See [Table-Level Hit Policy Fields](../05-expression-results-and-policies/index.md#table-level-hit-policy-fields).

### Reapplying a policy with RuleResult.select() {#reapplying-a-policy-with-ruleresultselect}

See [RuleResult Select Method](../05-expression-results-and-policies/index.md#reapplying-a-policy-with-ruleresultselect).

### Explain a decision and refine the result {#explain-a-decision-and-refine-the-result}

See [RuleResult Explain Method](../05-expression-results-and-policies/index.md#explain-a-decision-and-refine-the-result).

### RuleResult.explain(): inspect a retained rule's outcomes {#ruleresultexplain-inspect-a-retained-rules-outcomes}

See [RuleResult Explain Method](../05-expression-results-and-policies/index.md#explain-a-decision-and-refine-the-result).

### The ExplainResult class {#the-explainresult-class}

See [ExplainResult Class](../05-expression-results-and-policies/index.md#the-explainresult-class).

### Engine-level explain(): scoring every rule {#engine-level-explain-scoring-every-rule}

See [Engine-Level Explain](../05-expression-results-and-policies/index.md#engine-level-explain-scoring-every-rule).

### Observability columns {#observability-columns}

See [Observability Columns](../04-expression-rules-engine/index.md#observability-columns).

### at_least(): filtering the returned survivors {#at_least-filtering-the-returned-survivors}

See [At Least Filter](../05-expression-results-and-policies/index.md#at_least-filtering-the-returned-survivors).

### top_n: limiting how many survivors return {#top_n-limiting-how-many-survivors-return}

See [Top N Filtering](../05-expression-results-and-policies/index.md#top_n-limiting-how-many-survivors-return).

### min_specificity: excluding weak matches {#min_specificity-excluding-weak-matches}

See [Min Specificity Filter](../05-expression-results-and-policies/index.md#min_specificity-excluding-weak-matches).

### Where this leaves you {#where-this-leaves-you}

See [the complete book contents](../index.md).
