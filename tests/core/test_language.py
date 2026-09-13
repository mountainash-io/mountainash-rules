"""Regressions for the package-root exact-language API (StringLanguage et al.).

These exercise the real `mountainash_rules` root API end-to-end through the
native Rust boundary: observable behavior, error categories/attributes,
limits typing, Unicode transport and canonical wire identity. The bounded
exhaustive conformance corpus (regex-automata oracle comparison, malformed
wire matrix, resource-category matrix) lives in
`native/language_tests.rs`; it is deliberately not duplicated here.
"""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from mountainash_rules import (
    LanguageLimits,
    LanguageResourceError,
    LanguageSyntaxError,
    LanguageWireError,
    RegexOptions,
    StringLanguage,
)

GENEROUS = LanguageLimits(
    max_input_bytes=1_000_000,
    max_nesting=64,
    max_nfa_states=10_000,
    max_states=10_000,
    max_transitions=200_000,
    max_work=100_000_000,
)


class TestRegexOptions:
    def test_non_boolean_flag_rejected(self):
        with pytest.raises(TypeError):
            RegexOptions(case_insensitive=1)


class TestLanguageLimits:
    def test_zero_limits_are_a_valid_request_not_a_default(self):
        # Zero is an exhaustion request, not an implicit usable default.
        zero = LanguageLimits(
            max_input_bytes=0,
            max_nesting=0,
            max_nfa_states=0,
            max_states=0,
            max_transitions=0,
            max_work=0,
        )
        with pytest.raises(LanguageResourceError):
            StringLanguage.empty(limits=zero)

    def test_max_nesting_above_256_rejected(self):
        with pytest.raises(ValueError):
            LanguageLimits(
                max_input_bytes=100,
                max_nesting=257,
                max_nfa_states=100,
                max_states=100,
                max_transitions=100,
                max_work=100,
            )

    def test_negative_counter_rejected(self):
        with pytest.raises(ValueError):
            dataclasses.replace(GENEROUS, max_work=-1)

    def test_boolean_counter_rejected(self):
        with pytest.raises(TypeError):
            dataclasses.replace(GENEROUS, max_input_bytes=True)

    def test_non_integer_counter_rejected(self):
        with pytest.raises(TypeError):
            dataclasses.replace(GENEROUS, max_states=1.5)

    def test_counter_above_signed_64_bit_max_rejected(self):
        with pytest.raises(ValueError):
            dataclasses.replace(GENEROUS, max_states=2**63)


class TestStringLanguageConstruction:
    def test_direct_instantiation_rejected(self):
        with pytest.raises(TypeError):
            StringLanguage()

    def test_non_string_pattern_rejected(self):
        with pytest.raises(TypeError):
            StringLanguage.regex(123, limits=GENEROUS)

    def test_non_language_limits_rejected(self):
        with pytest.raises(TypeError):
            StringLanguage.regex("abc", limits="not-limits")

    def test_non_regex_options_rejected(self):
        with pytest.raises(TypeError):
            StringLanguage.regex("abc", limits=GENEROUS, options="not-options")

    def test_from_json_requires_bytes(self):
        with pytest.raises(TypeError):
            StringLanguage.from_json("not-bytes", limits=GENEROUS)


class TestStringLanguageBehavior:
    def test_regex_search_semantics(self):
        lang = StringLanguage.regex(r"abc", limits=GENEROUS)
        assert lang.accepts("zabc!", limits=GENEROUS) is True
        assert lang.accepts("xyz", limits=GENEROUS) is False

    def test_regex_anchors_are_not_defeated_by_search_wrapper(self):
        lang = StringLanguage.regex(r"^abc$", limits=GENEROUS)
        assert lang.accepts("zabc!", limits=GENEROUS) is False
        assert lang.accepts("abc", limits=GENEROUS) is True

    def test_literal_never_interprets_regex_syntax(self):
        lang = StringLanguage.literal(".*", limits=GENEROUS)
        assert lang.accepts(".*", limits=GENEROUS) is True
        assert lang.accepts("xyz", limits=GENEROUS) is False

    def test_prefix_suffix_contains(self):
        prefix = StringLanguage.prefix("foo", limits=GENEROUS)
        assert prefix.accepts("foobar", limits=GENEROUS) is True
        assert prefix.accepts("barfoo", limits=GENEROUS) is False

        suffix = StringLanguage.suffix("foo", limits=GENEROUS)
        assert suffix.accepts("barfoo", limits=GENEROUS) is True
        assert suffix.accepts("foobar", limits=GENEROUS) is False

        contains = StringLanguage.contains("foo", limits=GENEROUS)
        assert contains.accepts("xxfooyy", limits=GENEROUS) is True
        assert contains.accepts("bar", limits=GENEROUS) is False

    def test_empty_and_universal(self):
        empty = StringLanguage.empty(limits=GENEROUS)
        assert empty.is_empty(limits=GENEROUS) is True
        assert empty.witness(limits=GENEROUS) is None

        universal = StringLanguage.universal(limits=GENEROUS)
        assert universal.is_empty(limits=GENEROUS) is False
        assert universal.witness(limits=GENEROUS) == ""
        assert universal.accepts("anything at all", limits=GENEROUS) is True


class TestStringLanguageAlgebra:
    def test_intersection_with_complement_is_empty(self):
        lang = StringLanguage.regex(r"[ab]", limits=GENEROUS)
        complement = lang.complement(limits=GENEROUS)
        inter = lang.intersection(complement, limits=GENEROUS)
        assert inter.is_empty(limits=GENEROUS) is True

    def test_union_accepts_either_side(self):
        cat = StringLanguage.regex(r"^cat$", limits=GENEROUS)
        dog = StringLanguage.regex(r"^dog$", limits=GENEROUS)
        union = cat.union(dog, limits=GENEROUS)
        assert union.accepts("cat", limits=GENEROUS) is True
        assert union.accepts("dog", limits=GENEROUS) is True
        assert union.accepts("fish", limits=GENEROUS) is False
        assert union.cardinality(10, limits=GENEROUS) == 2

    def test_difference_residual_witness_is_consistent(self):
        contains_foo = StringLanguage.regex(r"foo", limits=GENEROUS)
        bounded_foo = StringLanguage.regex(r"\bfoo\b", limits=GENEROUS)
        residual = contains_foo.difference(bounded_foo, limits=GENEROUS)
        assert residual.is_empty(limits=GENEROUS) is False
        witness = residual.witness(limits=GENEROUS)
        assert witness is not None
        assert residual.accepts(witness, limits=GENEROUS) is True
        assert contains_foo.accepts(witness, limits=GENEROUS) is True
        assert bounded_foo.accepts(witness, limits=GENEROUS) is False

    def test_algebra_other_operand_must_be_string_language(self):
        lang = StringLanguage.regex(r"a", limits=GENEROUS)
        with pytest.raises(TypeError):
            lang.intersection("not-a-language", limits=GENEROUS)


class TestStringLanguageCardinalityAndWitness:
    def test_exact_finite_cardinality(self):
        lang = StringLanguage.regex(r"^a[bc]$", limits=GENEROUS)
        assert lang.cardinality(10, limits=GENEROUS) == 2

    def test_infinite_language_caps_at_bound(self):
        lang = StringLanguage.regex(r"^a*$", limits=GENEROUS)
        assert lang.cardinality(5, limits=GENEROUS) == 5
        assert lang.witness(limits=GENEROUS) == ""

    def test_witness_is_shortest_then_lexicographic(self):
        lang = StringLanguage.regex(r"[bc]|a", limits=GENEROUS)
        assert lang.witness(limits=GENEROUS) == "a"

    def test_cardinality_bound_rejects_bool_and_negative(self):
        lang = StringLanguage.regex(r"a", limits=GENEROUS)
        with pytest.raises(TypeError):
            lang.cardinality(True, limits=GENEROUS)
        with pytest.raises(ValueError):
            lang.cardinality(-1, limits=GENEROUS)


class TestStringLanguageUnicodeTransport:
    def test_supplementary_plane_and_combining_marks_round_trip(self):
        lang = StringLanguage.regex(r"\x{1F600}\x{301}?", limits=GENEROUS)
        assert lang.accepts("\U0001f600", limits=GENEROUS) is True
        assert lang.accepts("\U0001f600\u0301", limits=GENEROUS) is True
        assert lang.accepts("\U0001f600x", limits=GENEROUS) is True
        assert lang.accepts("x\u0301", limits=GENEROUS) is False

    def test_witness_returns_correct_non_ascii_python_string(self):
        lang = StringLanguage.literal("\u03b2", limits=GENEROUS)  # beta
        assert lang.witness(limits=GENEROUS) == "\u03b2"

    def test_unicode_word_boundary_flag_default_on(self):
        lang = StringLanguage.regex(r"\b\x{3B2}\b", limits=GENEROUS)
        assert lang.accepts("!\u03b2!", limits=GENEROUS) is True
        ascii_only = StringLanguage.regex(
            r"\bβ\b", limits=GENEROUS, options=RegexOptions(unicode=False)
        )
        assert ascii_only.accepts("!\u03b2!", limits=GENEROUS) is False
        assert ascii_only.accepts("aβa", limits=GENEROUS) is True

    def test_lone_surrogate_is_not_lossily_replaced_at_native_boundary(self):
        with pytest.raises(UnicodeError):
            StringLanguage.literal("\ud800", limits=GENEROUS)

    def test_explicit_case_folding_changes_native_membership(self):
        folded = StringLanguage.regex(
            "σ", limits=GENEROUS, options=RegexOptions(case_insensitive=True)
        )
        exact = StringLanguage.regex("σ", limits=GENEROUS)
        assert folded.accepts("Σ", limits=GENEROUS)
        assert not exact.accepts("Σ", limits=GENEROUS)


class TestStringLanguageWireIdentity:
    def test_to_json_from_json_round_trip_preserves_behavior_and_bytes(self):
        lang = StringLanguage.regex(r"[\p{Greek}&&[^\p{Lu}]]+", limits=GENEROUS)
        wire = lang.to_json(limits=GENEROUS)
        reloaded = StringLanguage.from_json(wire, limits=GENEROUS)
        assert reloaded.to_json(limits=GENEROUS) == wire
        for sample in ("\u03b1\u03b2", "X\u03b1\u03b2Y", "ABC"):
            assert reloaded.accepts(sample, limits=GENEROUS) == lang.accepts(
                sample, limits=GENEROUS
            )

    def test_equivalent_spellings_share_canonical_wire_and_language_id(self):
        via_regex = StringLanguage.regex(r"^cat$", limits=GENEROUS)
        via_literal = StringLanguage.literal("cat", limits=GENEROUS)
        assert via_regex.to_json(limits=GENEROUS) == via_literal.to_json(
            limits=GENEROUS
        )
        assert via_regex.language_id(limits=GENEROUS) == via_literal.language_id(
            limits=GENEROUS
        )

    def test_language_id_matches_independently_computed_digest(self):
        lang = StringLanguage.regex(r"^abc$", limits=GENEROUS)
        wire = lang.to_json(limits=GENEROUS)
        expected = "language:1:" + hashlib.sha256(b"language:1\0" + wire).hexdigest()
        assert lang.language_id(limits=GENEROUS) == expected

    def test_malformed_wire_bytes_raise_wire_error(self):
        with pytest.raises(LanguageWireError):
            StringLanguage.from_json(b"not json at all", limits=GENEROUS)

    def test_wrong_schema_version_raises_wire_error(self):
        wire = (
            b'{"schema_version":2,"alphabet":"unicode_scalars","start":0,'
            b'"accepting":[2],"transitions":[[0,0,96,1],[0,97,97,2],'
            b"[0,98,55295,1],[0,57344,1114111,1],[1,0,55295,1],"
            b"[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"
        )
        with pytest.raises(LanguageWireError):
            StringLanguage.from_json(wire, limits=GENEROUS)


class TestStringLanguageErrorCategories:
    def test_invalid_regex_syntax_raises_syntax_error(self):
        with pytest.raises(LanguageSyntaxError) as failure:
            StringLanguage.regex("(", limits=GENEROUS)
        assert isinstance(failure.value, ValueError)

    def test_unsupported_dialect_construct_raises_syntax_error(self):
        with pytest.raises(LanguageSyntaxError):
            StringLanguage.regex(r"(a)\1", limits=GENEROUS)  # numeric backreference

    def test_resource_error_is_a_runtime_error_with_resource_and_limit(self):
        tiny_states = dataclasses.replace(GENEROUS, max_states=0)
        with pytest.raises(LanguageResourceError) as excinfo:
            StringLanguage.empty(limits=tiny_states)
        assert isinstance(excinfo.value, RuntimeError)
        assert excinfo.value.resource == "states"
        assert excinfo.value.limit == 0

    def test_resource_error_input_bytes_category(self):
        tiny_bytes = dataclasses.replace(GENEROUS, max_input_bytes=2)
        with pytest.raises(LanguageResourceError) as excinfo:
            StringLanguage.regex("abc", limits=tiny_bytes)
        assert excinfo.value.resource == "input_bytes"
        assert excinfo.value.limit == 2

    def test_resource_error_nesting_category(self):
        tiny_nesting = dataclasses.replace(GENEROUS, max_nesting=1)
        with pytest.raises(LanguageResourceError) as excinfo:
            StringLanguage.regex(r"(?:(?:(?:a)))", limits=tiny_nesting)
        assert excinfo.value.resource == "nesting"
        assert excinfo.value.limit == 1
