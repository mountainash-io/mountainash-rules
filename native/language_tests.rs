//! Behavior regressions for `crate::language`, the backend-independent
//! exact-language component (frozen Rust API: see
//! `local://native-language-contract.txt`).
//!
//! Cases are adapted from the scratch conformance driver evidence
//! (`scalar_gate` programme) onto the frozen `compile(pattern, flags,
//! limits)` / per-call-`Limits` API, and grounded independently against
//! `regex_automata::meta::Regex` (search-semantics oracle) and
//! `regex_syntax::ast::parse::Parser` (pinned-dialect grammar authority)
//! rather than hand-asserted expectations.
//!
//! Resource identifiers (`LanguageError::Resource { resource, .. }`) are
//! pinned per cross-slice clarification from Main: `input_bytes`,
//! `nesting`, `nfa_states`, `states`, `transitions`, `work`.

use crate::language::{
    compile, empty, literal, universal, Dfa, Flags, LanguageError, Limits, LiteralKind,
};
use regex_automata::util::syntax::Config;

// ---------------------------------------------------------------------
// Limits
// ---------------------------------------------------------------------

/// Generous limits for ordinary compiles/operations in this file.
const GENEROUS: Limits = Limits {
    max_input_bytes: 1_000_000,
    max_nesting: 64,
    max_nfa_states: 10_000,
    max_states: 10_000,
    max_transitions: 200_000,
    max_work: 100_000_000,
};

/// Isolated single-resource ceilings: every field generous except the one
/// under test, so a failure can be attributed to exactly one resource
/// identifier rather than "one of several simultaneously tiny counters".
const TINY_INPUT_BYTES: Limits = Limits {
    max_input_bytes: 4,
    ..GENEROUS
};
const TINY_NESTING: Limits = Limits {
    max_nesting: 2,
    ..GENEROUS
};
const TINY_STATES: Limits = Limits {
    max_states: 1,
    ..GENEROUS
};
const TINY_TRANSITIONS: Limits = Limits {
    max_transitions: 1,
    ..GENEROUS
};
const TINY_WORK: Limits = Limits {
    max_work: 1,
    ..GENEROUS
};

fn default_flags() -> Flags {
    Flags::default()
}

// ---------------------------------------------------------------------
// Independent oracle (regex-automata meta::Regex), configured from the
// same `Flags` the candidate `compile` call uses so flag-differential
// cases are grounded, not asserted from memory.
// ---------------------------------------------------------------------

fn oracle(pattern: &str, flags: Flags) -> regex_automata::meta::Regex {
    let cfg = Config::new()
        .case_insensitive(flags.case_insensitive)
        .multi_line(flags.multi_line)
        .dot_matches_new_line(flags.dot_matches_new_line)
        .crlf(flags.crlf)
        .swap_greed(flags.swap_greed)
        .unicode(flags.unicode)
        .ignore_whitespace(flags.ignore_whitespace);
    regex_automata::meta::Regex::builder()
        .syntax(cfg)
        .build(pattern)
        .unwrap_or_else(|e| panic!("oracle failed to build pattern {:?}: {}", pattern, e))
}

struct Case {
    pattern: &'static str,
    haystack: &'static str,
    expected: bool,
    note: &'static str,
}

fn assert_case(flags: Flags, limits: Limits, c: &Case) {
    let oracle_actual = oracle(c.pattern, flags).is_match(c.haystack);
    assert_eq!(
        oracle_actual, c.expected,
        "oracle grounding disagrees with hand expectation: pattern={:?} haystack={:?} note={}",
        c.pattern, c.haystack, c.note
    );
    let dfa = compile(c.pattern, flags, limits).unwrap_or_else(|e| {
        panic!(
            "compile failed pattern={:?} note={}: {}",
            c.pattern, c.note, e
        )
    });
    let actual = dfa.accepts(c.haystack, limits).unwrap_or_else(|e| {
        panic!(
            "accepts failed pattern={:?} note={}: {}",
            c.pattern, c.note, e
        )
    });
    assert_eq!(
        actual, c.expected,
        "pattern={:?} haystack={:?} note={}",
        c.pattern, c.haystack, c.note
    );
}

// ---------------------------------------------------------------------
// Section 1: core search/anchor/boundary/CRLF/empty-match/Unicode-category/
// class-algebra/repetition cases (default flags: unicode on, everything
// else off).
// ---------------------------------------------------------------------

#[test]
fn core_search_and_anchor_semantics() {
    let cases = [
        Case {
            pattern: r"abc",
            haystack: "zabc!",
            expected: true,
            note: "unanchored search",
        },
        Case {
            pattern: r"^abc$",
            haystack: "zabc!",
            expected: false,
            note: "anchors not defeated by search wrapper",
        },
        Case {
            pattern: r"a*",
            haystack: "",
            expected: true,
            note: "empty match/search and EOI",
        },
        Case {
            pattern: r"(?<word>ab){2,3}?",
            haystack: "zzababq",
            expected: true,
            note: "capture + lazy bounded repetition preserves language",
        },
    ];
    for c in &cases {
        assert_case(default_flags(), GENEROUS, c);
    }
}

#[test]
fn word_boundary_variants() {
    let cases = [
        Case {
            pattern: r"\b\x{3B2}\b",
            haystack: "!\u{3B2}!",
            expected: true,
            note: "Unicode word boundary, non-ASCII word char",
        },
        Case {
            pattern: r"\b\x{3B2}\b",
            haystack: "!\u{3B2}\u{3B3}!",
            expected: false,
            note: "adjacent Unicode word char defeats boundary",
        },
        Case {
            pattern: r"(?-u:\b)\x{3B2}(?-u:\b)",
            haystack: "!\u{3B2}!",
            expected: false,
            note: "ASCII-scoped inline boundary differs from Unicode boundary",
        },
        Case {
            pattern: r"\Bfoo\B",
            haystack: "xfooy",
            expected: true,
            note: "negated boundary holds mid-word",
        },
        Case {
            pattern: r"\bfoo\b",
            haystack: "xfooy",
            expected: false,
            note: "contrast: full boundary fails mid-word",
        },
        Case {
            pattern: r"!\b{start}!",
            haystack: "!!",
            expected: false,
            note: "start requires following word char",
        },
        Case {
            pattern: r"!\b{start-half}!",
            haystack: "!!",
            expected: true,
            note: "start-half ignores following char",
        },
        Case {
            pattern: r"!\b{end}!",
            haystack: "!!",
            expected: false,
            note: "end requires preceding word char",
        },
        Case {
            pattern: r"!\b{end-half}!",
            haystack: "!!",
            expected: true,
            note: "end-half ignores preceding char",
        },
        Case {
            pattern: r"\<a",
            haystack: "a!",
            expected: true,
            note: "angle alias for start boundary",
        },
        Case {
            pattern: r"a\>",
            haystack: "!a",
            expected: true,
            note: "angle alias for end boundary",
        },
    ];
    for c in &cases {
        assert_case(default_flags(), GENEROUS, c);
    }
}

#[test]
fn crlf_never_split_by_inline_line_anchor() {
    let cases = [
        Case {
            pattern: r"(?mR)^foo$",
            haystack: "\r\nfoo\r\n",
            expected: true,
            note: "CRLF multiline anchors",
        },
        Case {
            pattern: r"(?mR)^$",
            haystack: "\r\n",
            expected: true,
            note: "line boundaries never split CR/LF",
        },
        Case {
            pattern: r"\r(?mR:$)\n",
            haystack: "\r\n",
            expected: false,
            note: "CRLF mode: $ never matches between \\r and \\n",
        },
        Case {
            pattern: r"\r(?m:$)\n",
            haystack: "\r\n",
            expected: true,
            note: "contrast: plain m (no R) splits CRLF at $",
        },
    ];
    for c in &cases {
        assert_case(default_flags(), GENEROUS, c);
    }
}

#[test]
fn empty_matches() {
    let cases = [
        Case {
            pattern: r"^$",
            haystack: "",
            expected: true,
            note: "anchored empty match on empty haystack",
        },
        Case {
            pattern: r"^$",
            haystack: "a",
            expected: false,
            note: "anchored empty match fails on nonempty haystack",
        },
        Case {
            pattern: r"",
            haystack: "xyz",
            expected: true,
            note: "unanchored empty pattern matches anywhere",
        },
    ];
    for c in &cases {
        assert_case(default_flags(), GENEROUS, c);
    }
}

#[test]
fn unicode_categories_and_casefold() {
    let cases = [
        Case {
            pattern: r"\p{Lu}+",
            haystack: "ABC",
            expected: true,
            note: "uppercase category",
        },
        Case {
            pattern: r"\p{Lu}+",
            haystack: "abc",
            expected: false,
            note: "lowercase excluded from Lu",
        },
        Case {
            pattern: r"(?i)\p{Lu}+",
            haystack: "abc",
            expected: true,
            note: "case-insensitive flag folds category match",
        },
        Case {
            pattern: r"\p{Lu}",
            haystack: "\u{10D50}",
            expected: true,
            note: "Unicode 16.0 Garay capital letter is Lu",
        },
        Case {
            pattern: r"(?i:\x{3C3})",
            haystack: "\u{3C2}",
            expected: true,
            note: "scoped Unicode simple case fold sigma/final-sigma",
        },
        Case {
            pattern: r"[\p{Greek}&&[^\p{Lu}]]+",
            haystack: "X\u{3B1}\u{3B2}Y",
            expected: true,
            note: "Unicode class intersection + negation",
        },
    ];
    for c in &cases {
        assert_case(default_flags(), GENEROUS, c);
    }
}

#[test]
fn class_algebra_and_repetition() {
    let cases = [
        Case {
            pattern: r"[a-z--[aeiou]]+",
            haystack: "xxbcdyy",
            expected: true,
            note: "class difference",
        },
        Case {
            pattern: r"[a-c~~[b-d]]+",
            haystack: "ad",
            expected: true,
            note: "symmetric difference {a,d}",
        },
        Case {
            pattern: r"[a-c~~[b-d]]+",
            haystack: "b",
            expected: false,
            note: "symmetric difference excludes shared b",
        },
        Case {
            pattern: r"a{2,4}",
            haystack: "aaa",
            expected: true,
            note: "bounded repetition within range",
        },
        Case {
            pattern: r"a{2,4}",
            haystack: "a",
            expected: false,
            note: "bounded repetition below minimum",
        },
        Case {
            pattern: r"a{2,4}?",
            haystack: "aaaa",
            expected: true,
            note: "lazy bounded repetition still full search language",
        },
    ];
    for c in &cases {
        assert_case(default_flags(), GENEROUS, c);
    }
}

// ---------------------------------------------------------------------
// Section 2: pinned-dialect rejection. Grounded against
// regex_syntax::ast::parse::Parser as well as the candidate compiler so a
// rejection is a real dialect boundary, not an accidental compiler bug.
// ---------------------------------------------------------------------

struct RejectCase {
    pattern: &'static str,
    note: &'static str,
}

fn assert_unsupported(c: &RejectCase) {
    let ast_result = regex_syntax::ast::parse::Parser::new().parse(c.pattern);
    assert!(
        ast_result.is_err(),
        "dialect grounding: pattern={:?} note={} unexpectedly parses under regex-syntax AST",
        c.pattern,
        c.note
    );
    let compiled = compile(c.pattern, default_flags(), GENEROUS);
    assert!(
        compiled.is_err(),
        "pattern={:?} note={} unexpectedly compiled",
        c.pattern,
        c.note
    );
    assert!(matches!(compiled.unwrap_err(), LanguageError::Syntax(_)));
}

#[test]
fn unsupported_syntax_rejected() {
    let cases = [
        RejectCase {
            pattern: r"(?0)a",
            note:
                "PCRE-style recursion syntax; (?R) is the pinned-dialect CRLF flag, not recursion",
        },
        RejectCase {
            pattern: r"(a)\1",
            note: "numeric backreference",
        },
        RejectCase {
            pattern: r"(?=foo)",
            note: "lookahead",
        },
        RejectCase {
            pattern: r"(?<=foo)",
            note: "lookbehind",
        },
        RejectCase {
            pattern: r"(?P<n>a)(?P=n)",
            note: "named backreference",
        },
    ];
    for c in &cases {
        assert_unsupported(c);
    }
}

// ---------------------------------------------------------------------
// Section 3: explicit `Flags` struct control surface (as distinct from
// inline group modifiers exercised above). Each flag is grounded against
// an oracle configured identically, so parity with the inline-flag cases
// above is demonstrated rather than assumed.
// ---------------------------------------------------------------------

#[test]
fn flag_case_insensitive() {
    let flags = Flags {
        case_insensitive: true,
        ..Flags::default()
    };
    assert_case(
        flags,
        GENEROUS,
        &Case {
            pattern: r"ABC",
            haystack: "abc",
            expected: true,
            note: "top-level case_insensitive flag",
        },
    );
    assert_case(
        Flags::default(),
        GENEROUS,
        &Case {
            pattern: r"ABC",
            haystack: "abc",
            expected: false,
            note: "contrast: case_insensitive off",
        },
    );
}

#[test]
fn flag_multi_line() {
    let flags = Flags {
        multi_line: true,
        ..Flags::default()
    };
    assert_case(
        flags,
        GENEROUS,
        &Case {
            pattern: r"^b",
            haystack: "a\nb",
            expected: true,
            note: "multi_line: ^ matches after \\n",
        },
    );
    assert_case(
        Flags::default(),
        GENEROUS,
        &Case {
            pattern: r"^b",
            haystack: "a\nb",
            expected: false,
            note: "contrast: multi_line off, ^ only matches string start",
        },
    );
}

#[test]
fn flag_dot_matches_new_line() {
    let flags = Flags {
        dot_matches_new_line: true,
        ..Flags::default()
    };
    assert_case(
        flags,
        GENEROUS,
        &Case {
            pattern: r"a.b",
            haystack: "a\nb",
            expected: true,
            note: "dot_matches_new_line: . matches \\n",
        },
    );
    assert_case(
        Flags::default(),
        GENEROUS,
        &Case {
            pattern: r"a.b",
            haystack: "a\nb",
            expected: false,
            note: "contrast: . excludes \\n by default",
        },
    );
}

#[test]
fn flag_crlf_struct_controls_line_anchor_without_inline_group() {
    // CRLF mode permits an empty line before CR; LF-only mode still sees CR
    // as content. A nonempty line must not acquire a match inside its CRLF.
    let crlf_on = Flags {
        multi_line: true,
        crlf: true,
        ..Flags::default()
    };
    let crlf_off = Flags {
        multi_line: true,
        crlf: false,
        ..Flags::default()
    };
    assert_case(
        crlf_on,
        GENEROUS,
        &Case {
            pattern: r"^$",
            haystack: "\r\nx",
            expected: true,
            note: "CRLF mode recognizes the initial empty line",
        },
    );
    assert_case(
        crlf_off,
        GENEROUS,
        &Case {
            pattern: r"^$",
            haystack: "\r\nx",
            expected: false,
            note: "LF-only mode sees CR as line content",
        },
    );
    assert_case(
        crlf_on,
        GENEROUS,
        &Case {
            pattern: r"^$",
            haystack: "x\r\ny",
            expected: false,
            note: "CRLF never creates an empty line inside the pair",
        },
    );
}

#[test]
fn flag_ignore_whitespace() {
    let flags = Flags {
        ignore_whitespace: true,
        ..Flags::default()
    };
    assert_case(
        flags,
        GENEROUS,
        &Case {
            pattern: "a b   c # trailing comment\n",
            haystack: "abc",
            expected: true,
            note: "ignore_whitespace: unescaped whitespace/comments dropped",
        },
    );
    assert_case(
        Flags::default(),
        GENEROUS,
        &Case {
            pattern: "a b",
            haystack: "a b",
            expected: true,
            note: "contrast: whitespace is literal by default",
        },
    );
    assert_case(
        Flags::default(),
        GENEROUS,
        &Case {
            pattern: "a b",
            haystack: "ab",
            expected: false,
            note: "contrast: default mode requires the literal space",
        },
    );
}

#[test]
fn flag_unicode_toggle_changes_word_boundary() {
    let unicode_on = Flags {
        unicode: true,
        ..Flags::default()
    };
    let unicode_off = Flags {
        unicode: false,
        ..Flags::default()
    };
    assert_case(
        unicode_on,
        GENEROUS,
        &Case {
            pattern: r"\b\x{3B2}\b",
            haystack: "!\u{3B2}!",
            expected: true,
            note: "unicode=true: beta counts as a word char for \\b",
        },
    );
    assert_case(
        unicode_off,
        GENEROUS,
        &Case {
            pattern: r"\bβ\b",
            haystack: "!β!",
            expected: false,
            note: "ASCII boundaries treat the literal beta as non-word",
        },
    );
    assert_case(
        unicode_off,
        GENEROUS,
        &Case {
            pattern: r"\bβ\b",
            haystack: "aβa",
            expected: true,
            note: "ASCII word neighbors establish both boundaries around beta",
        },
    );
}

#[test]
fn flag_swap_greed_is_language_preserving() {
    // swap_greed reciprocally swaps quantifier greediness; it changes which
    // span a *search* prefers, never the *set* of accepted strings. A
    // lazy-quantifier pattern with swap_greed=true must therefore compile to
    // the exact same canonical language as its greedy counterpart with
    // swap_greed=false.
    let greedy = compile(r"a+", Flags::default(), GENEROUS).expect("compile a+");
    let swapped_lazy = compile(
        r"a+?",
        Flags {
            swap_greed: true,
            ..Flags::default()
        },
        GENEROUS,
    )
    .expect("compile a+? swap_greed=true");
    assert_eq!(
        greedy.canonical_json(GENEROUS).unwrap(),
        swapped_lazy.canonical_json(GENEROUS).unwrap(),
        "swap_greed must not change the accepted language, only search preference"
    );
}

// ---------------------------------------------------------------------
// Section 4: literal-language safety. `literal`/prefix/suffix/contains must
// never interpret their text as regex syntax.
// ---------------------------------------------------------------------

#[test]
fn literal_exact_never_interprets_regex_metacharacters() {
    let dfa = literal("a.b", LiteralKind::Exact, GENEROUS).expect("literal exact");
    assert!(dfa.accepts("a.b", GENEROUS).unwrap());
    assert!(
        !dfa.accepts("axb", GENEROUS).unwrap(),
        "'.' must not act as a wildcard"
    );
    assert!(!dfa.accepts("a\nb", GENEROUS).unwrap());

    let dfa = literal(".*", LiteralKind::Exact, GENEROUS).expect("literal exact");
    assert!(dfa.accepts(".*", GENEROUS).unwrap());
    assert!(
        !dfa.accepts("anything", GENEROUS).unwrap(),
        "'.*' must not act as a wildcard-star"
    );
}

#[test]
fn literal_prefix_suffix_contains_semantics() {
    let prefix = literal("foo", LiteralKind::Prefix, GENEROUS).expect("literal prefix");
    assert!(prefix.accepts("foobar", GENEROUS).unwrap());
    assert!(prefix.accepts("foo", GENEROUS).unwrap());
    assert!(!prefix.accepts("barfoo", GENEROUS).unwrap());
    assert!(!prefix.accepts("fo", GENEROUS).unwrap());

    let suffix = literal("foo", LiteralKind::Suffix, GENEROUS).expect("literal suffix");
    assert!(suffix.accepts("barfoo", GENEROUS).unwrap());
    assert!(!suffix.accepts("foobar", GENEROUS).unwrap());

    let contains = literal("foo", LiteralKind::Contains, GENEROUS).expect("literal contains");
    assert!(contains.accepts("xxfooyy", GENEROUS).unwrap());
    assert!(!contains.accepts("bar", GENEROUS).unwrap());
}

#[test]
fn literal_empty_text_edge_cases() {
    // Exact("") is the singleton language {""}.
    let exact_empty = literal("", LiteralKind::Exact, GENEROUS).expect("literal exact empty");
    assert!(exact_empty.accepts("", GENEROUS).unwrap());
    assert!(!exact_empty.accepts("x", GENEROUS).unwrap());
    assert_eq!(exact_empty.cardinality(10, GENEROUS).unwrap(), 1);
    assert_eq!(exact_empty.witness(GENEROUS).unwrap().as_deref(), Some(""));

    // Prefix/Suffix/Contains("") admit every string: the universal language.
    let universal_dfa = universal(GENEROUS).expect("universal");
    let universal_wire = universal_dfa.canonical_json(GENEROUS).unwrap();
    for kind in [
        LiteralKind::Prefix,
        LiteralKind::Suffix,
        LiteralKind::Contains,
    ] {
        let dfa = literal("", kind, GENEROUS).expect("literal empty text");
        assert_eq!(
            dfa.canonical_json(GENEROUS).unwrap(),
            universal_wire,
            "{:?}(\"\") must equal the universal language",
            kind
        );
    }
}

// ---------------------------------------------------------------------
// Section 5: bounded exhaustive-corpus comparison against
// regex_automata::meta::Regex. Explicit small alphabet, short words; NOT
// exhaustive over Unicode, but a reproducible, bounded, meaningful sample.
// ---------------------------------------------------------------------

const ALPHABET: [char; 11] = [
    'a',
    '!',
    '\u{3B2}',
    '\u{3B3}',
    '\u{3C3}',
    '\u{3C2}',
    '\u{301}',
    '\n',
    '\r',
    '\u{1F600}',
    '\u{10D50}',
];
const MAX_WORD_LEN: usize = 3;

fn generate_words(alphabet: &[char], max_len: usize) -> Vec<String> {
    let mut words = vec![String::new()];
    let mut frontier = vec![String::new()];
    for _ in 0..max_len {
        let mut next = Vec::with_capacity(frontier.len() * alphabet.len());
        for w in &frontier {
            for c in alphabet {
                let mut nw = w.clone();
                nw.push(*c);
                words.push(nw.clone());
                next.push(nw);
            }
        }
        frontier = next;
    }
    words
}

#[test]
fn bounded_corpus_agrees_with_oracle() {
    let words = generate_words(&ALPHABET, MAX_WORD_LEN);
    let patterns: [&str; 8] = [
        r"\b\x{3B2}\b",
        r"\B\x{3B2}\B",
        r"(?mR)^$",
        r"(?i)\x{3C3}",
        r"[\p{Greek}&&[^\p{Lu}]]+",
        r"\p{L}",
        r"a{2,4}",
        r"[a-z--[aeiou]]+",
    ];
    let flags = default_flags();
    let mut total = 0usize;
    for pattern in patterns {
        let re = oracle(pattern, flags);
        let dfa = compile(pattern, flags, GENEROUS)
            .unwrap_or_else(|e| panic!("corpus pattern {:?} failed to compile: {}", pattern, e));
        for w in &words {
            let expected = re.is_match(w.as_str());
            let actual = dfa.accepts(w.as_str(), GENEROUS).unwrap_or_else(|e| {
                panic!("accepts failed pattern={:?} word={:?}: {}", pattern, w, e)
            });
            assert_eq!(
                actual, expected,
                "pattern={:?} word={:?} upstream={} candidate={}",
                pattern, w, expected, actual
            );
            total += 1;
        }
    }
    assert_eq!(total, patterns.len() * words.len());
}

// ---------------------------------------------------------------------
// Section 6: canonical structure equality for semantically equivalent
// spellings (minimization + canonical BFS numbering must erase spelling
// differences).
// ---------------------------------------------------------------------

struct EquivPair {
    a: &'static str,
    b: &'static str,
    note: &'static str,
}

fn assert_equivalent(p: &EquivPair) {
    let da = compile(p.a, default_flags(), GENEROUS)
        .unwrap_or_else(|e| panic!("compile a={:?} note={}: {}", p.a, p.note, e));
    let db = compile(p.b, default_flags(), GENEROUS)
        .unwrap_or_else(|e| panic!("compile b={:?} note={}: {}", p.b, p.note, e));
    assert_eq!(
        da.canonical_json(GENEROUS).unwrap(),
        db.canonical_json(GENEROUS).unwrap(),
        "a={:?} b={:?} note={} must canonicalize identically",
        p.a,
        p.b,
        p.note
    );
}

#[test]
fn canonical_equivalence_for_semantically_equal_spellings() {
    let pairs = [
        EquivPair {
            a: r"\<a\>",
            b: r"\b{start}a\b{end}",
            note: "angle aliases vs explicit start/end word boundary",
        },
        EquivPair {
            a: r"[a-c]",
            b: r"[abc]",
            note: "range vs explicit set",
        },
        EquivPair {
            a: r"a|a|a",
            b: r"a",
            note: "redundant alternation collapses through minimization",
        },
        EquivPair {
            a: r"(?:ab)",
            b: r"ab",
            note: "non-capturing group is semantically transparent",
        },
        EquivPair {
            a: r"[a-z--[aeiou]]",
            b: r"[bcdfghjklmnpqrstvwxyz]",
            note: "class difference equals explicit enumeration",
        },
        EquivPair {
            a: r"a{2,2}",
            b: r"aa",
            note: "exact repetition equals literal concatenation",
        },
        EquivPair {
            a: r"(?i)A",
            b: r"[Aa]",
            note: "case-insensitive single letter equals its simple-fold class",
        },
    ];
    for p in &pairs {
        assert_equivalent(p);
    }
}

// ---------------------------------------------------------------------
// Section 7: wire roundtrip / repeat-compile determinism.
// ---------------------------------------------------------------------

#[test]
fn wire_roundtrip_and_repeat_compile_are_deterministic() {
    let patterns = [
        r"\b\x{3B2}\b",
        r"(?mR)^$",
        r"[\p{Greek}&&[^\p{Lu}]]+",
        r"a{2,4}",
    ];
    for pattern in patterns {
        let d1 = compile(pattern, default_flags(), GENEROUS)
            .unwrap_or_else(|e| panic!("compile failed pattern={:?}: {}", pattern, e));
        let w1 = d1.canonical_json(GENEROUS).unwrap();

        let d2 = Dfa::from_json(&w1, GENEROUS).unwrap_or_else(|e| {
            panic!(
                "from_json(canonical_json()) failed pattern={:?}: {}",
                pattern, e
            )
        });
        let w2 = d2.canonical_json(GENEROUS).unwrap();
        assert_eq!(
            w1, w2,
            "pattern={:?} wire must be stable through a decode/encode roundtrip",
            pattern
        );

        let d1b = compile(pattern, default_flags(), GENEROUS)
            .unwrap_or_else(|e| panic!("repeat compile failed pattern={:?}: {}", pattern, e));
        let w1b = d1b.canonical_json(GENEROUS).unwrap();
        assert_eq!(
            w1, w1b,
            "pattern={:?} repeat compile must be deterministic",
            pattern
        );
    }
}

// ---------------------------------------------------------------------
// Section 8: strict malformed-wire rejection (`Dfa::from_json`).
// ---------------------------------------------------------------------

/// Hand-built canonical wire for the exact language {"a"}: 3 states,
/// BFS-numbered from start=0 (0=start, 1=reject sink, 2=accept),
/// transitions sorted by (from, lower), surrogates (0xD800..=0xDFFF)
/// excluded from the alphabet as required. No whitespace, matching the
/// documented canonical-JSON shape.
const BASELINE_WIRE: &str = concat!(
    r#"{"schema_version":1,"alphabet":"unicode_scalars","start":0,"accepting":[2],"#,
    r#""transitions":[[0,0,96,1],[0,97,97,2],[0,98,55295,1],[0,57344,1114111,1],"#,
    r#"[1,0,55295,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
);

#[test]
fn baseline_wire_positive_control_behaves_as_language_a() {
    let dfa = Dfa::from_json(BASELINE_WIRE.as_bytes(), GENEROUS)
        .unwrap_or_else(|e| panic!("hand-built baseline wire rejected: {}", e));
    assert!(dfa.accepts("a", GENEROUS).unwrap());
    assert!(!dfa.accepts("", GENEROUS).unwrap());
    assert!(!dfa.accepts("b", GENEROUS).unwrap());
    assert!(!dfa.accepts("aa", GENEROUS).unwrap());
}

fn assert_wire_rejected(name: &str, wire: &str) {
    let result = Dfa::from_json(wire.as_bytes(), GENEROUS);
    assert!(
        result.is_err(),
        "variant={name} should be rejected but was accepted"
    );
    assert!(
        matches!(result.unwrap_err(), LanguageError::InvalidWire(_)),
        "variant={name} must be reported as InvalidWire"
    );
}

#[test]
fn malformed_wire_structural_variants_rejected() {
    // unknown field
    assert_wire_rejected(
        "unknown_field",
        r#"{"schema_version":1,"alphabet":"unicode_scalars","start":0,"accepting":[2],"extra":true,"transitions":[[0,0,96,1],[0,97,97,2],[0,98,55295,1],[0,57344,1114111,1],[1,0,55295,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );

    // bad target: point a transition's "to" at a nonexistent state
    assert_wire_rejected(
        "bad_target",
        r#"{"schema_version":1,"alphabet":"unicode_scalars","start":0,"accepting":[2],"transitions":[[0,0,96,1],[0,97,97,99],[0,98,55295,1],[0,57344,1114111,1],[1,0,55295,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );

    // surrogate transition: widen a bound down into 0xD800..=0xDFFF
    assert_wire_rejected(
        "surrogate_transition",
        r#"{"schema_version":1,"alphabet":"unicode_scalars","start":0,"accepting":[2],"transitions":[[0,0,96,1],[0,97,97,2],[0,98,55295,1],[1,55296,1114111,1],[1,0,55295,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );

    // gap: shrink an upper bound, leaving codepoints uncovered
    assert_wire_rejected(
        "gap",
        r#"{"schema_version":1,"alphabet":"unicode_scalars","start":0,"accepting":[2],"transitions":[[0,0,96,1],[0,97,97,2],[0,98,55295,1],[0,57344,1114111,1],[1,0,55290,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );

    // overlap: widen a lower bound so it overlaps a sibling transition from
    // the same state
    assert_wire_rejected(
        "overlap",
        r#"{"schema_version":1,"alphabet":"unicode_scalars","start":0,"accepting":[2],"transitions":[[0,0,96,1],[0,97,97,2],[0,90,55295,1],[0,57344,1114111,1],[1,0,55295,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );

    // duplicate accepting IDs
    assert_wire_rejected(
        "duplicate_accepting_ids",
        r#"{"schema_version":1,"alphabet":"unicode_scalars","start":0,"accepting":[2,2],"transitions":[[0,0,96,1],[0,97,97,2],[0,98,55295,1],[0,57344,1114111,1],[1,0,55295,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );

    // wrong canonical numbering: isomorphic relabeling (swap state IDs 1 and
    // 2) describes the same language but violates BFS discovery order from
    // start=0.
    assert_wire_rejected(
        "wrong_canonical_numbering",
        r#"{"schema_version":1,"alphabet":"unicode_scalars","start":0,"accepting":[1],"transitions":[[0,0,96,2],[0,97,97,1],[0,98,55295,2],[0,57344,1114111,2],[1,0,55295,2],[1,57344,1114111,2],[2,0,55295,2],[2,57344,1114111,2]]}"#,
    );
}

#[test]
fn malformed_wire_duplicate_json_keys_rejected() {
    // Decoding through a generic JSON map can erase duplicate keys. The
    // language decoder must reject them before graph validation.
    let wire = concat!(
        r#"{"schema_version":1,"schema_version":1,"alphabet":"unicode_scalars","start":0,"#,
        r#""accepting":[2],"transitions":[[0,0,96,1],[0,97,97,2],[0,98,55295,1],"#,
        r#"[0,57344,1114111,1],[1,0,55295,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );
    assert_wire_rejected("duplicate_json_key", wire);
}

#[test]
fn malformed_wire_noncanonical_scalar_types_rejected() {
    // schema_version as a float rather than an integer.
    let float_schema_version = concat!(
        r#"{"schema_version":1.0,"alphabet":"unicode_scalars","start":0,"accepting":[2],"#,
        r#""transitions":[[0,0,96,1],[0,97,97,2],[0,98,55295,1],[0,57344,1114111,1],"#,
        r#"[1,0,55295,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );
    assert_wire_rejected("float_schema_version", float_schema_version);

    // start as a bool rather than an integer state id.
    let bool_start = concat!(
        r#"{"schema_version":1,"alphabet":"unicode_scalars","start":false,"accepting":[2],"#,
        r#""transitions":[[0,0,96,1],[0,97,97,2],[0,98,55295,1],[0,57344,1114111,1],"#,
        r#"[1,0,55295,1],[1,57344,1114111,1],[2,0,55295,1],[2,57344,1114111,1]]}"#,
    );
    assert_wire_rejected("bool_start", bool_start);
}

#[test]
fn from_json_is_insensitive_to_object_key_order() {
    // Grounded by the implemented Python facade's documented contract
    // ("Input whitespace and object-key order need not already be
    // canonical", `core/language.py::StringLanguage.from_json`): JSON
    // objects are unordered by spec and duplicate-key detection does not
    // require a fixed key order to implement, so reordering keys (without
    // duplicating any) must still be accepted.
    let reordered = concat!(
        r#"{"start":0,"schema_version":1,"transitions":[[0,0,96,1],[0,97,97,2],"#,
        r#"[0,98,55295,1],[0,57344,1114111,1],[1,0,55295,1],[1,57344,1114111,1],"#,
        r#"[2,0,55295,1],[2,57344,1114111,1]],"accepting":[2],"alphabet":"unicode_scalars"}"#,
    );
    let dfa = Dfa::from_json(reordered.as_bytes(), GENEROUS)
        .unwrap_or_else(|e| panic!("reordered-key wire rejected: {}", e));
    assert!(dfa.accepts("a", GENEROUS).unwrap());
    assert!(!dfa.accepts("b", GENEROUS).unwrap());
}

// ---------------------------------------------------------------------
// Section 9: resource-limit enforcement, across every phase the contract
// calls out: pre-parser input bytes, nesting, HIR/alphabet/NFA expansion,
// algebra, complement, wire decoding, and output bytes (canonical_json /
// witness). Never a partial/truncated result: only an explicit
// `LanguageError::Resource` with a pinned resource identifier.
// ---------------------------------------------------------------------

fn assert_resource<T>(
    result: Result<T, LanguageError>,
    expected_resource: &str,
    expected_limit: usize,
    context: &str,
) {
    match result {
        Ok(_) => panic!("{context}: expected Resource({expected_resource}) error"),
        Err(LanguageError::Resource { resource, limit }) => {
            assert_eq!(
                resource, expected_resource,
                "{context}: unexpected resource identifier"
            );
            assert_eq!(limit, expected_limit, "{context}: unexpected limit value");
        }
        Err(other) => {
            panic!("{context}: expected Resource({expected_resource}) error, got {other}")
        }
    }
}

#[test]
fn resource_input_bytes_gate_precedes_parsing() {
    // Syntactically invalid (unbalanced parens) AND too long for
    // `max_input_bytes`. Per contract, "Input and nesting limits precede
    // parsing": the failure must be Resource(input_bytes), never Syntax,
    // proving the byte-length gate runs before the parser would otherwise
    // detect the syntax error.
    let long_and_invalid = "(".repeat(50) + "not-balanced-and-way-too-long-for-the-tiny-budget";
    assert!(long_and_invalid.len() > TINY_INPUT_BYTES.max_input_bytes);
    let result = compile(&long_and_invalid, default_flags(), TINY_INPUT_BYTES);
    assert_resource(
        result,
        "input_bytes",
        TINY_INPUT_BYTES.max_input_bytes,
        "input-bytes-precedes-parsing",
    );
}

#[test]
fn resource_nesting_is_enforced() {
    let shallow_ok = Limits {
        max_nesting: 10,
        ..GENEROUS
    };
    let nested = "(?:(?:(?:(?:(?:a)))))"; // 5 levels of non-capturing group nesting
    assert!(compile(nested, default_flags(), shallow_ok).is_ok());
    let result = compile(nested, default_flags(), TINY_NESTING);
    assert_resource(result, "nesting", TINY_NESTING.max_nesting, "nesting");
}

#[test]
fn resource_unicode_class_expansion_is_accounted() {
    // Contract: "Do not claim hardening while Unicode/HIR/alphabet
    // expansion is unaccounted." We deliberately do not pin *which*
    // resource identifier fires here (nfa_states vs states vs transitions
    // is a legitimate implementation choice for how a class's intervals
    // are charged) -- only that a budget generous enough for a tiny ASCII
    // class of the same shape must NOT also be generous enough once that
    // class is inflated to a huge Unicode property class, or else
    // Unicode/alphabet expansion is not being charged against any budget
    // at all.
    let modest = Limits {
        max_states: 32,
        max_transitions: 64,
        ..GENEROUS
    };
    assert!(
        compile(r"[a-c]+", default_flags(), modest).is_ok(),
        "a tiny ASCII class must fit a modest graph budget"
    );
    let result = compile(r"\p{L}+", default_flags(), modest);
    assert!(
        matches!(result, Err(LanguageError::Resource { .. })),
        "the same modest budget must not silently also fit a huge Unicode property \
         class of identical shape; Unicode/HIR/alphabet expansion must be charged \
         against the graph budget, got {:?}",
        result.map(|_| ())
    );

    // Bounded repetition must also be charged against the same budgets,
    // not permitted to unroll for free regardless of repeat count.
    assert!(compile(r"a{2,4}", default_flags(), modest).is_ok());
    let result = compile(r"[a-z]{50,100}", default_flags(), modest);
    assert!(
        matches!(result, Err(LanguageError::Resource { .. })),
        "a large bounded-repetition count must not silently fit the same modest budget \
         that a small repetition count fits, got {:?}",
        result.map(|_| ())
    );
}

#[test]
fn resource_algebra_and_complement_are_enforced() {
    let a = compile(r"[a-m]", default_flags(), GENEROUS).unwrap();
    let b = compile(r"[g-z]", default_flags(), GENEROUS).unwrap();

    // Product construction needs strictly more states than a 1-state
    // budget allows.
    assert_resource(
        a.intersection(&b, TINY_STATES),
        "states",
        TINY_STATES.max_states,
        "intersection",
    );
    assert_resource(
        a.union(&b, TINY_STATES),
        "states",
        TINY_STATES.max_states,
        "union",
    );
    assert_resource(
        a.difference(&b, TINY_STATES),
        "states",
        TINY_STATES.max_states,
        "difference",
    );

    // Complement preserves the input's state graph (only accepting flips),
    // so it must still be checked against the *result's* own state budget:
    // `a` already has more than one state, so a 1-state complement budget
    // must fail too.
    assert_resource(
        a.complement(TINY_STATES),
        "states",
        TINY_STATES.max_states,
        "complement",
    );
}

#[test]
fn resource_from_json_decoding_is_enforced() {
    // Structural: the 3-state baseline wire cannot fit a 1-state budget.
    let result = Dfa::from_json(BASELINE_WIRE.as_bytes(), TINY_STATES);
    assert_resource(
        result,
        "states",
        TINY_STATES.max_states,
        "from_json structural states",
    );

    // Input bytes: the wire text itself is bounded before it is even
    // parsed as JSON.
    assert!(BASELINE_WIRE.len() > TINY_INPUT_BYTES.max_input_bytes);
    let result = Dfa::from_json(BASELINE_WIRE.as_bytes(), TINY_INPUT_BYTES);
    assert_resource(
        result,
        "input_bytes",
        TINY_INPUT_BYTES.max_input_bytes,
        "from_json input bytes",
    );
}

#[test]
fn resource_output_bytes_bound_canonical_json_and_witness() {
    // A moderately large automaton's canonical JSON is certainly larger
    // than 4 bytes; structural limits are generous so only the output-byte
    // budget can be the cause of failure.
    let dfa = compile(r"[\p{Greek}&&[^\p{Lu}]]+", default_flags(), GENEROUS).unwrap();
    let full = dfa.canonical_json(GENEROUS).unwrap();
    assert!(full.len() > TINY_INPUT_BYTES.max_input_bytes);
    let result = dfa.canonical_json(TINY_INPUT_BYTES);
    assert_resource(
        result,
        "input_bytes",
        TINY_INPUT_BYTES.max_input_bytes,
        "canonical_json output bytes",
    );
    // No partial/truncated payload must ever be observable on this path:
    // the only successful return type is the full `Vec<u8>`, so a Resource
    // error is the only possible failure signal.

    // A literal whose only witness is long enough to exceed a tiny output
    // budget, constructed under generous limits, then queried for a
    // witness under a tiny output-bytes-only budget.
    let long_literal = "a".repeat(64);
    let dfa = literal(&long_literal, LiteralKind::Exact, GENEROUS).unwrap();
    let result = dfa.witness(TINY_INPUT_BYTES);
    assert_resource(
        result,
        "input_bytes",
        TINY_INPUT_BYTES.max_input_bytes,
        "witness output bytes",
    );
}

#[test]
fn resource_failure_does_not_poison_operands_or_later_operations() {
    let a = compile(r"[a-m]", default_flags(), GENEROUS).unwrap();
    let b = compile(r"[g-z]", default_flags(), GENEROUS).unwrap();
    assert_resource(
        a.intersection(&b, TINY_STATES),
        "states",
        TINY_STATES.max_states,
        "failed intersection",
    );
    assert!(a.accepts("a", GENEROUS).unwrap());
    assert!(!b.accepts("a", GENEROUS).unwrap());
    let overlap = a.intersection(&b, GENEROUS).unwrap();
    assert!(overlap.accepts("g", GENEROUS).unwrap());
    assert!(!overlap.accepts("a", GENEROUS).unwrap());
}

#[test]
fn resource_matching_text_is_bounded_independent_of_automaton_size() {
    // `accepts` must bound the *matched text* by max_input_bytes even when
    // the automaton itself is trivial (the universal language).
    let dfa = universal(GENEROUS).unwrap();
    let long_text = "x".repeat(64);
    assert!(long_text.len() > TINY_INPUT_BYTES.max_input_bytes);
    let result = dfa.accepts(&long_text, TINY_INPUT_BYTES);
    assert_resource(
        result,
        "input_bytes",
        TINY_INPUT_BYTES.max_input_bytes,
        "accepts matching-text bytes",
    );
}

#[test]
fn resource_work_budget_is_charged_even_for_trivial_compiles() {
    // max_work is a shared whole-operation step budget; with every
    // structural counter generous but max_work=1, even the smallest
    // nontrivial compile (a single literal character, which still needs a
    // start/accept/reject-sink automaton) must exceed it.
    let result = compile(r"a", default_flags(), TINY_WORK);
    assert_resource(
        result,
        "work",
        TINY_WORK.max_work,
        "trivial compile under a 1-unit work budget",
    );
}

#[test]
fn resource_transitions_budget_is_enforced_by_surrogate_split_alone() {
    // Any complete DFA state's "everything else" transition necessarily
    // splits into (at least) two rows around the excluded surrogate block
    // (0xD800..=0xDFFF), per the wire format contract. A 1-transition
    // budget must therefore fail even for the smallest nontrivial pattern.
    let result = compile(r"^a$", default_flags(), TINY_TRANSITIONS);
    assert_resource(
        result,
        "transitions",
        TINY_TRANSITIONS.max_transitions,
        "single-state surrogate-split self-loop",
    );
}

// ---------------------------------------------------------------------
// Section 10: exact algebra checks.
// ---------------------------------------------------------------------

#[test]
fn algebra_difference_of_language_with_itself_is_empty() {
    let l = compile(r"^abc$", default_flags(), GENEROUS).unwrap();
    let diff = l.difference(&l, GENEROUS).unwrap();
    assert!(diff.is_empty(GENEROUS).unwrap());
    assert!(diff.witness(GENEROUS).unwrap().is_none());
    assert_eq!(diff.cardinality(10, GENEROUS).unwrap(), 0);
}

#[test]
fn algebra_intersection_with_complement_is_empty() {
    let a = compile(r"[ab]", default_flags(), GENEROUS).unwrap();
    let comp = a.complement(GENEROUS).unwrap();
    let inter = a.intersection(&comp, GENEROUS).unwrap();
    assert!(inter.is_empty(GENEROUS).unwrap());
}

#[test]
fn algebra_bounded_word_is_subset_of_contains_with_nonempty_residual() {
    let contains_foo = compile(r"foo", default_flags(), GENEROUS).unwrap();
    let bounded_foo = compile(r"\bfoo\b", default_flags(), GENEROUS).unwrap();

    // \bfoo\b - foo is empty: subset proof.
    let subset_check = bounded_foo.difference(&contains_foo, GENEROUS).unwrap();
    assert!(subset_check.is_empty(GENEROUS).unwrap());

    // foo - \bfoo\b is nonempty, and its witness lies in `foo` but not in
    // `\bfoo\b`.
    let residual = contains_foo.difference(&bounded_foo, GENEROUS).unwrap();
    assert!(!residual.is_empty(GENEROUS).unwrap());
    let witness = residual
        .witness(GENEROUS)
        .unwrap()
        .expect("residual witness");
    assert!(residual.accepts(&witness, GENEROUS).unwrap());
    assert!(contains_foo.accepts(&witness, GENEROUS).unwrap());
    assert!(!bounded_foo.accepts(&witness, GENEROUS).unwrap());
}

#[test]
fn algebra_de_morgan_union_matches_direct_union() {
    let cat = compile(r"^cat$", default_flags(), GENEROUS).unwrap();
    let dog = compile(r"^dog$", default_flags(), GENEROUS).unwrap();

    let derived = cat
        .complement(GENEROUS)
        .unwrap()
        .intersection(&dog.complement(GENEROUS).unwrap(), GENEROUS)
        .unwrap()
        .complement(GENEROUS)
        .unwrap();
    let direct = cat.union(&dog, GENEROUS).unwrap();

    assert_eq!(
        derived.canonical_json(GENEROUS).unwrap(),
        direct.canonical_json(GENEROUS).unwrap()
    );
    assert!(direct.accepts("cat", GENEROUS).unwrap());
    assert!(direct.accepts("dog", GENEROUS).unwrap());
    assert!(!direct.accepts("fish", GENEROUS).unwrap());
    assert_eq!(direct.cardinality(10, GENEROUS).unwrap(), 2);
}

#[test]
fn algebra_complement_of_universal_is_empty_and_vice_versa() {
    let top = universal(GENEROUS).unwrap();
    let bottom = top.complement(GENEROUS).unwrap();
    assert!(bottom.is_empty(GENEROUS).unwrap());
    assert!(bottom.witness(GENEROUS).unwrap().is_none());

    let empty_dfa = empty(GENEROUS).unwrap();
    let back_to_top = empty_dfa.complement(GENEROUS).unwrap();
    assert_eq!(
        back_to_top.canonical_json(GENEROUS).unwrap(),
        top.canonical_json(GENEROUS).unwrap()
    );
}

#[test]
fn algebra_unsatisfiable_class_intersection_is_empty() {
    let dfa = compile(r"[a&&b]+", default_flags(), GENEROUS).unwrap();
    assert!(dfa.is_empty(GENEROUS).unwrap());
    assert!(dfa.witness(GENEROUS).unwrap().is_none());
    assert_eq!(dfa.cardinality(10, GENEROUS).unwrap(), 0);
}

// ---------------------------------------------------------------------
// Section 11: cardinality (exact / capped / zero), witness ordering
// (shortest then scalar-codepoint lexicographic, not UTF-16 code-unit
// order), and the "dead cycle does not imply infinite" invariant.
// ---------------------------------------------------------------------

#[test]
fn cardinality_exact_single_and_small_finite() {
    let d = compile(r"^abc$", default_flags(), GENEROUS).unwrap();
    assert_eq!(d.cardinality(10, GENEROUS).unwrap(), 1);
    assert_eq!(d.witness(GENEROUS).unwrap().as_deref(), Some("abc"));

    let d = compile(r"^a[bc]$", default_flags(), GENEROUS).unwrap();
    assert_eq!(d.cardinality(10, GENEROUS).unwrap(), 2);
}

#[test]
fn cardinality_infinite_language_is_capped_at_bound() {
    let d = compile(r"^a*$", default_flags(), GENEROUS).unwrap();
    assert_eq!(
        d.cardinality(5, GENEROUS).unwrap(),
        5,
        "infinite language must cap exactly at bound"
    );
    assert_eq!(d.witness(GENEROUS).unwrap().as_deref(), Some(""));
}

#[test]
fn cardinality_zero_for_disjoint_intersection() {
    let a = compile(r"^a$", default_flags(), GENEROUS).unwrap();
    let b = compile(r"^b$", default_flags(), GENEROUS).unwrap();
    let inter = a.intersection(&b, GENEROUS).unwrap();
    assert!(inter.is_empty(GENEROUS).unwrap());
    assert!(inter.witness(GENEROUS).unwrap().is_none());
    assert_eq!(inter.cardinality(10, GENEROUS).unwrap(), 0);
}

#[test]
fn cardinality_finite_count_below_and_above_bound() {
    let d = compile(r"^[abc][xyz]$", default_flags(), GENEROUS).unwrap();
    assert_eq!(
        d.cardinality(5, GENEROUS).unwrap(),
        5,
        "9 actual, bound=5 caps at 5"
    );
    assert_eq!(
        d.cardinality(20, GENEROUS).unwrap(),
        9,
        "9 actual, bound=20 returns exact count"
    );
}

#[test]
fn witness_is_shortest_then_codepoint_lexicographic() {
    let d = compile(r"[bc]|a", default_flags(), GENEROUS).unwrap();
    assert_eq!(
        d.witness(GENEROUS).unwrap().as_deref(),
        Some("a"),
        "a=0x61 < b=0x62 < c=0x63"
    );
}

#[test]
fn witness_orders_by_true_scalar_value_not_utf16_surrogate_order() {
    // U+FFFC (single BMP code unit, non-surrogate) < U+1F600 (supplementary
    // plane; its UTF-16 lead surrogate 0xD800 numerically precedes 0xFFFC
    // as a code *unit*, which would be the wrong answer under UTF-16
    // code-unit ordering). The correct witness is U+FFFC by true codepoint
    // value.
    let d = compile(r"\x{FFFC}|\x{1F600}", default_flags(), GENEROUS).unwrap();
    assert_eq!(d.witness(GENEROUS).unwrap().as_deref(), Some("\u{FFFC}"));
}

#[test]
fn dead_cycle_does_not_imply_infinite_cardinality() {
    // The hand-built baseline wire for {"a"} contains a literal cycle: its
    // reject-sink state (id 1) self-loops over the entire alphabet. That
    // cycle can never reach an accepting state, so it must not make the
    // reported cardinality infinite/capped; the exact language is the
    // singleton {"a"}.
    let dfa = Dfa::from_json(BASELINE_WIRE.as_bytes(), GENEROUS).unwrap();
    assert_eq!(
        dfa.cardinality(10, GENEROUS).unwrap(),
        1,
        "a self-looping dead/reject state must not be counted as a productive cycle"
    );

    // Contrast: `^a*$` has a genuinely productive cycle (the accepting
    // state loops back to itself on 'a'), which correctly reports as
    // infinite (capped at bound).
    let productive = compile(r"^a*$", default_flags(), GENEROUS).unwrap();
    assert_eq!(
        productive.cardinality(3, GENEROUS).unwrap(),
        3,
        "a genuinely productive cycle is reported as infinite, capped at bound"
    );
}

// ---------------------------------------------------------------------
// Section 12: empty()/universal() are single-state canonical forms
// (one reachable state; two transition rows because of the surrogate
// gap split), and are exact complements of each other.
// ---------------------------------------------------------------------

fn distinct_state_ids(transitions: &serde_json::Value) -> std::collections::BTreeSet<i64> {
    let mut ids = std::collections::BTreeSet::new();
    for row in transitions.as_array().expect("transitions array") {
        let row = row.as_array().expect("transition row");
        ids.insert(row[0].as_i64().expect("from"));
        ids.insert(row[3].as_i64().expect("to"));
    }
    ids
}

#[test]
fn empty_and_universal_are_single_state_canonical_forms() {
    let empty_dfa = empty(GENEROUS).unwrap();
    let empty_wire: serde_json::Value =
        serde_json::from_slice(&empty_dfa.canonical_json(GENEROUS).unwrap()).unwrap();
    assert_eq!(empty_wire["start"], 0);
    assert_eq!(
        empty_wire["accepting"].as_array().unwrap().len(),
        0,
        "empty language accepts nothing"
    );
    assert_eq!(
        distinct_state_ids(&empty_wire["transitions"]),
        std::collections::BTreeSet::from([0])
    );
    assert_eq!(
        empty_wire["transitions"].as_array().unwrap().len(),
        2,
        "surrogate gap splits the one self-loop into two rows"
    );

    let universal_dfa = universal(GENEROUS).unwrap();
    let universal_wire: serde_json::Value =
        serde_json::from_slice(&universal_dfa.canonical_json(GENEROUS).unwrap()).unwrap();
    assert_eq!(universal_wire["start"], 0);
    assert_eq!(
        universal_wire["accepting"].as_array().unwrap(),
        &vec![serde_json::json!(0)],
        "universal language accepts everything from its single state"
    );
    assert_eq!(
        distinct_state_ids(&universal_wire["transitions"]),
        std::collections::BTreeSet::from([0])
    );
    assert_eq!(universal_wire["transitions"].as_array().unwrap().len(), 2);
}

#[test]
fn empty_input_membership_still_requires_work() {
    let language = universal(GENEROUS).unwrap();
    let exhausted = Limits {
        max_work: 0,
        ..GENEROUS
    };
    assert_resource(
        language.accepts("", exhausted),
        "work",
        0,
        "empty membership",
    );
}
