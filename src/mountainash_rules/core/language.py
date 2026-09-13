"""Exact Unicode-scalar languages backed by the rules-owned Rust component.

Import public names from :mod:`mountainash_rules`. Every operation receives
explicit resource limits; exhaustion raises rather than returning a partial
language, false membership, or an incomplete witness.
"""

from __future__ import annotations

import hashlib
import sys
import typing as t
from dataclasses import dataclass, field

from mountainash_rules import _native

_COUNTER_MAX = min(sys.maxsize, (1 << 63) - 1)
_FLAG_NAMES = (
    "case_insensitive",
    "multi_line",
    "dot_matches_new_line",
    "crlf",
    "swap_greed",
    "unicode",
    "ignore_whitespace",
)


def _counter(value: int, name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer, not a Boolean or coercible value")
    if not 0 <= value <= _COUNTER_MAX:
        raise ValueError(f"{name} must be in [0, {_COUNTER_MAX}]")


@dataclass(frozen=True, slots=True, kw_only=True)
class LanguageLimits:
    """Required per-operation input, graph and work ceilings.

    ``max_input_bytes`` bounds UTF-8 source/input text, wire input/output and
    witness output. ``max_nesting`` bounds regex nesting and is at most 256.
    Graph counters bound NFA instructions, DFA states and scalar-range edges.
    ``max_work`` bounds the complete native operation, not each phase afresh.
    Work units are implementation-version dependent, not milliseconds or a
    performance promise. Zero is a valid limit and never requests a default.
    """

    max_input_bytes: int
    max_nesting: int
    max_nfa_states: int
    max_states: int
    max_transitions: int
    max_work: int
    _handle: _native.Limits = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        names = (
            "max_input_bytes",
            "max_nesting",
            "max_nfa_states",
            "max_states",
            "max_transitions",
            "max_work",
        )
        values = tuple(getattr(self, name) for name in names)
        for name, value in zip(names, values):
            _counter(value, name)
        if self.max_nesting > 256:
            raise ValueError("max_nesting must be at most 256")
        object.__setattr__(self, "_handle", _native.Limits(*values))


@dataclass(frozen=True, slots=True, kw_only=True)
class RegexOptions:
    """Pinned Rust-regex search options; inline scoped flags remain supported.

    Unicode tables come from regex-syntax 0.8.10 (Unicode 16.0.0), never from
    the host Python or DataFrame backend. UTF-8 safety is always enforced.
    Greed settings affect parsing semantics but not Boolean membership.
    """

    case_insensitive: bool = False
    multi_line: bool = False
    dot_matches_new_line: bool = False
    crlf: bool = False
    swap_greed: bool = False
    unicode: bool = True
    ignore_whitespace: bool = False

    def __post_init__(self) -> None:
        for name in _FLAG_NAMES:
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a Boolean")

    def to_dict(self) -> dict[str, t.Any]:
        """Return explicit authoring semantics for retention alongside a pattern."""
        return {
            "dialect": "rust-regex",
            "dialect_version": _native.REGEX_DIALECT_VERSION,
            "mode": "search",
            "unicode_version": _native.UNICODE_VERSION,
            "flags": {name: getattr(self, name) for name in _FLAG_NAMES},
        }


_DEFAULT_REGEX_OPTIONS = RegexOptions()


def _limits_handle(limits: LanguageLimits) -> _native.Limits:
    if not isinstance(limits, LanguageLimits):
        raise TypeError("limits must be LanguageLimits")
    return limits._handle


def _text(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("language text must be a string")


def _other_handle(other: StringLanguage) -> _native.Dfa:
    if not isinstance(other, StringLanguage):
        raise TypeError("other must be StringLanguage")
    return other._handle


@dataclass(frozen=True, slots=True, init=False, eq=False)
class StringLanguage:
    """An immutable complete, minimal Unicode-scalar DFA.

    Use a named constructor rather than instantiating this class directly.
    All constructors and operations execute in Rust; no Python regex or DFA
    fallback is used. Native graph operations release the GIL. Equivalent
    languages have identical canonical bytes and language IDs, independent of
    their source spelling. Authoring patterns/options are separate metadata;
    a loaded language needs neither its original pattern nor a regex parser.
    """

    _handle: _native.Dfa = field(repr=False)

    def __init__(self) -> None:
        raise TypeError("use a StringLanguage named constructor")

    @classmethod
    def _from_native(cls, handle: _native.Dfa) -> StringLanguage:
        language = object.__new__(cls)
        object.__setattr__(language, "_handle", handle)
        return language

    @classmethod
    def regex(
        cls,
        pattern: str,
        *,
        limits: LanguageLimits,
        options: RegexOptions = _DEFAULT_REGEX_OPTIONS,
    ) -> StringLanguage:
        """Compile strings containing a search match, including zero-width matches."""
        _text(pattern)
        if not isinstance(options, RegexOptions):
            raise TypeError("options must be RegexOptions")
        flags = (
            options.case_insensitive,
            options.multi_line,
            options.dot_matches_new_line,
            options.crlf,
            options.swap_greed,
            options.unicode,
            options.ignore_whitespace,
        )
        return cls._from_native(
            _native.Dfa.compile(pattern, flags, _limits_handle(limits))
        )

    @classmethod
    def literal(cls, text: str, *, limits: LanguageLimits) -> StringLanguage:
        """Accept exactly the literal string, without regex interpretation."""
        _text(text)
        return cls._from_native(
            _native.Dfa.literal(text, "exact", _limits_handle(limits))
        )

    @classmethod
    def prefix(cls, text: str, *, limits: LanguageLimits) -> StringLanguage:
        """Accept every string beginning with the literal, including newlines."""
        _text(text)
        return cls._from_native(
            _native.Dfa.literal(text, "prefix", _limits_handle(limits))
        )

    @classmethod
    def suffix(cls, text: str, *, limits: LanguageLimits) -> StringLanguage:
        """Accept every string ending with the literal, including newlines."""
        _text(text)
        return cls._from_native(
            _native.Dfa.literal(text, "suffix", _limits_handle(limits))
        )

    @classmethod
    def contains(cls, text: str, *, limits: LanguageLimits) -> StringLanguage:
        """Accept every string containing the literal; metacharacters stay literal."""
        _text(text)
        return cls._from_native(
            _native.Dfa.literal(text, "contains", _limits_handle(limits))
        )

    @classmethod
    def empty(cls, *, limits: LanguageLimits) -> StringLanguage:
        """Construct the language accepting no strings."""
        return cls._from_native(_native.Dfa.empty(_limits_handle(limits)))

    @classmethod
    def universal(cls, *, limits: LanguageLimits) -> StringLanguage:
        """Construct the language of all Unicode-scalar strings, including empty."""
        return cls._from_native(_native.Dfa.universal(_limits_handle(limits)))

    @classmethod
    def from_json(cls, data: bytes, *, limits: LanguageLimits) -> StringLanguage:
        """Load strict language-1 JSON; reject malformed or noncanonical graphs.

        Duplicate/unknown keys, noninteger counters, surrogates, gaps/overlaps,
        unreachable or equivalent states and non-BFS numbering are invalid.
        Input whitespace and object-key order need not already be canonical.
        """
        if not isinstance(data, bytes):
            raise TypeError("language JSON must be bytes")
        return cls._from_native(_native.Dfa.from_json(data, _limits_handle(limits)))

    def accepts(self, text: str, *, limits: LanguageLimits) -> bool:
        """Test whole-string membership in this language, not another regex search."""
        _text(text)
        return self._handle.accepts(text, _limits_handle(limits))

    def intersection(
        self, other: StringLanguage, *, limits: LanguageLimits
    ) -> StringLanguage:
        """Return strings accepted by both languages."""
        return self._from_native(
            self._handle.intersection(_other_handle(other), _limits_handle(limits))
        )

    def union(self, other: StringLanguage, *, limits: LanguageLimits) -> StringLanguage:
        """Return strings accepted by either language."""
        return self._from_native(
            self._handle.union(_other_handle(other), _limits_handle(limits))
        )

    def difference(
        self, other: StringLanguage, *, limits: LanguageLimits
    ) -> StringLanguage:
        """Return strings accepted here but not by the other language."""
        return self._from_native(
            self._handle.difference(_other_handle(other), _limits_handle(limits))
        )

    def complement(self, *, limits: LanguageLimits) -> StringLanguage:
        """Complement relative to all Unicode-scalar strings, never missing values."""
        return self._from_native(self._handle.complement(_limits_handle(limits)))

    def is_empty(self, *, limits: LanguageLimits) -> bool:
        """Determine exact emptiness; resource exhaustion is never empty."""
        return self._handle.is_empty(_limits_handle(limits))

    def witness(self, *, limits: LanguageLimits) -> str | None:
        """Return the shortest scalar-lexicographic witness, or None only if empty."""
        return self._handle.witness(_limits_handle(limits))

    def cardinality(self, bound: int, *, limits: LanguageLimits) -> int:
        """Return min(actual cardinality, bound); infinite languages return bound."""
        _counter(bound, "bound")
        return self._handle.cardinality(bound, _limits_handle(limits))

    def to_json(self, *, limits: LanguageLimits) -> bytes:
        """Return canonical-json-1 bytes for the canonical language-1 graph."""
        return self._handle.canonical_json(_limits_handle(limits))

    def language_id(self, *, limits: LanguageLimits) -> str:
        """Hash canonical language bytes with the versioned language domain prefix."""
        digest = hashlib.sha256(b"language:1\0")
        digest.update(self.to_json(limits=limits))
        return "language:1:" + digest.hexdigest()
