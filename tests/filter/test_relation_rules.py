"""The filter engine accepts a Mountainash Relation as its rules, like a native frame."""

from __future__ import annotations

from mountainash.relations import relation

from mountainash_rules.engines.filter.engine import ExpressionRulesEngine


def survivor_rows(result) -> list[tuple]:
    rows = relation(result.survivors).to_dicts()
    return sorted((row["rule_name"], row["__specificity"]) for row in rows)


def test_relation_rules_evaluate_like_native_rules(backend_rules_df, basic_metadata):
    native = ExpressionRulesEngine(rules=backend_rules_df, dimension_metadata=basic_metadata)
    wrapped = ExpressionRulesEngine(rules=relation(backend_rules_df), dimension_metadata=basic_metadata)
    context = {"region": "AU", "amount": 50, "code": "PRE-001"}

    assert survivor_rows(wrapped.evaluate(context)) == survivor_rows(native.evaluate(context))
    assert survivor_rows(wrapped.evaluate(context))  # at least one rule survives


def test_relation_rules_evaluate_a_batch_like_native_rules(backend_rules_df, basic_metadata):
    native = ExpressionRulesEngine(rules=backend_rules_df, dimension_metadata=basic_metadata)
    wrapped = ExpressionRulesEngine(rules=relation(backend_rules_df), dimension_metadata=basic_metadata)
    contexts = relation(
        {"id": [1, 2], "region": ["AU", "US"], "amount": [50, 500], "code": ["PRE-1", "XX"]}
    )

    def batch(engine):
        rows = relation(engine.evaluate_batch(contexts, context_id_field="id").survivors).to_dicts()
        return sorted((row["__context_id"], row["rule_name"], row["__specificity"]) for row in rows)

    assert batch(wrapped) == batch(native)
    assert batch(wrapped)
