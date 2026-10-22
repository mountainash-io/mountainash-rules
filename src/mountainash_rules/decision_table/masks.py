"""Contributor membership as dynamic 63-bit words, built with relational operations only.

Each scope numbers its contributing rules by the sorted natural order of their UUIDs
(``ordinal`` 0, 1, ...).  Ordinal ``n`` lives in word ``n // 63`` at bit ``n % 63``, so a
scope of ``N`` contributors needs ``ceil(N / 63)`` int64 words and uses bits 0..62 of
each; bit 63 (the sign bit) is never set, so every word is a non-negative int64.

A cell's word for a rule's bit is the sum of the distinct bit values it holds, read from a
63-row ``(bit_index, bit_value)`` lookup of exact integer literals (``bit_value`` is
bit ``bit_index`` set, written as an integer shift).  No bit is ever extracted or
exponentiated on the host: words are built by joining ordinals to the lookup and aggregating, and decoded by
joining words back to the lookup with a relational bitwise AND.

Contributor identity within a scope is the full tuple of its words.  ``entry_sources_id`` is
the 0-based rank of that tuple among the scope's distinct tuples in sorted natural order, so
the same membership has one id per scope and ids are scope-local.
"""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import Relation, concat, relation

from mountainash_rules.decision_table.fields import SCOPE, SOURCE_UUID

BITS_PER_WORD = 63
BIT_INDEX = "bit_index"
BIT_VALUE = "bit_value"
ENTRY_SOURCES_ID = "entry_sources_id"
WORD_INDEX = "__word_index"
WORD_BIT = "__word_bit"
WORD_VALUE = "__word_value"
WORD_KEY = "__word_key"
# Digits of the largest word (int64 maximum), so zero-padded decimal text sorts like the integer.
WORD_DIGITS = 19


def word_column(index: int) -> str:
    """Name of the ``index``-th mask word column."""
    return f"__word_{index}"


def bit_lookup(like: Relation | None = None) -> Relation:
    """The 63-row ``(bit_index, bit_value)`` lookup; ``bit_value`` has exactly bit ``bit_index`` set.

    With ``like``, the lookup is built on that relation's own backend: the exact integer
    literals are one list exploded from one row of ``like`` (which must not be empty), and
    ``bit_index`` is each literal's rank.  A relation built from host literals lives on
    another backend, and a plan joining it cannot be snapshotted without executing it
    (Mountainash refuses: "use a phase-safe plan"), so construction passes the relation the
    lookup will be joined to.
    """
    values = [1 << index for index in range(BITS_PER_WORD)]
    if like is None:
        return relation([{BIT_INDEX: index, BIT_VALUE: value} for index, value in enumerate(values)])
    ranked = (
        like.limit(1)
        .select(ma.lit(values).alias(BIT_VALUE))
        .explode(BIT_VALUE)
        .with_columns(ma.col(BIT_VALUE).row_number().over(order_by=BIT_VALUE).alias("__rn"))
    )
    first = ranked.group_by().agg(ma.col("__rn").min().alias("__rn0"))
    # One list column is exploded because a two-column explode zips on Polars but cross-joins
    # on Ibis (mountainash backlog #367); bit_index is recovered as the value's rank.
    # row_number starts at 0 or 1 by backend (mountainash backlog #365): subtract the minimum.
    return ranked.cross_join(first).select(
        (ma.col("__rn") - ma.col("__rn0")).cast("int64").alias(BIT_INDEX),
        ma.col(BIT_VALUE).cast("int64").alias(BIT_VALUE),
    )


def rule_ordinals(sources: Relation) -> Relation:
    """Number each scope's contributors by sorted natural UUID.

    ``sources`` has distinct ``(scope, source_uuid)`` rows.  Returns ``(scope, source_uuid,
    __word_index, __word_bit)`` where the 0-based ordinal is ``word_index * 63 + word_bit``.
    ``row_number`` starts at 0 or 1 depending on the backend (mountainash backlog #365), so
    each scope's minimum is subtracted. Integer ``//``/``%`` are inexact on Ibis-DuckDB for
    very large operands (backlog #366); exact here because ordinals stay far below 2^53.
    """
    ranked = sources.unique().with_columns(
        ma.col(SOURCE_UUID).row_number().over(SCOPE, order_by=SOURCE_UUID).alias("__rn")
    )
    first = ranked.group_by(SCOPE).agg(ma.col("__rn").min().alias("__rn0"))
    ordinal = (ma.col("__rn") - ma.col("__rn0")).cast("int64")
    return (
        ranked.join(first, on=SCOPE, how="inner")
        .with_columns(ordinal.alias("__ordinal"))
        .select(
            SCOPE,
            SOURCE_UUID,
            (ma.col("__ordinal") // BITS_PER_WORD).cast("int64").alias(WORD_INDEX),
            (ma.col("__ordinal") % BITS_PER_WORD).cast("int64").alias(WORD_BIT),
        )
    )


def word_count(
    ordinals: Relation, count_rows: t.Callable[[Relation], int] | None = None
) -> int:
    """Number of word columns the widest scope needs (0 when there are no contributors)."""
    # A scope with an ordinal in word k holds ordinals in every word below k, so the distinct
    # word indexes are exactly 0..W-1.  Counting them is the one scalar construction reads.
    distinct = ordinals.select(WORD_INDEX).unique()
    return distinct.count_rows() if count_rows is None else count_rows(distinct)


def membership_words(
    membership: Relation,
    ordinals: Relation,
    words: int,
    keys: t.Sequence[str],
    lookup: Relation | None = None,
) -> Relation:
    """One row per cell ``keys`` with its ``words`` int64 mask words.

    ``membership`` holds distinct ``(keys..., source_uuid)`` rows: each rule once per cell.
    ``lookup`` is the ``bit_lookup`` to join (default: one built on ``membership``'s backend).
    """
    if not words:
        return membership.filter(ma.lit(False)).select(*keys)
    joined = membership.join(ordinals, on=[SCOPE, SOURCE_UUID], how="inner").join(
        bit_lookup(membership) if lookup is None else lookup,
        left_on=WORD_BIT,
        right_on=BIT_INDEX,
        how="inner",
    )
    return joined.group_by(*keys).agg(
        *(
            ma.when(ma.col(WORD_INDEX).eq(index))
            .then(ma.col(BIT_VALUE))
            .otherwise(0)
            .sum()
            .cast("int64")
            .alias(word_column(index))
            for index in range(words)
        )
    )


def word_tuple_ids(cell_words: Relation, words: int) -> Relation:
    """Assign ``entry_sources_id`` by sorted natural word tuple within each scope.

    Returns ``(scope, entry_sources_id, words...)``, one row per distinct word tuple.
    """
    columns = [word_column(index) for index in range(words)]
    if not columns:
        return cell_words.filter(ma.lit(False)).select(
            SCOPE, ma.lit(0).cast("int64").alias(ENTRY_SOURCES_ID)
        )
    distinct = cell_words.select(SCOPE, *columns).unique()
    # Workaround for mountainash backlog #364: a window honours only the first order_by key,
    # so the tuple is ordered through one key: each word as fixed-width decimal text,
    # concatenated. Words are non-negative, so the text order is the integer-tuple order.
    key = ma.col(columns[0]).cast("string").str.zfill(WORD_DIGITS)
    for column in columns[1:]:
        key = key + ma.col(column).cast("string").str.zfill(WORD_DIGITS)
    ranked = distinct.with_columns(key.alias(WORD_KEY)).with_columns(
        ma.col(WORD_KEY).row_number().over(SCOPE, order_by=WORD_KEY).alias("__rn")
    )
    first = ranked.group_by(SCOPE).agg(ma.col("__rn").min().alias("__rn0"))
    return (
        ranked.join(first, on=SCOPE, how="inner")
        .with_columns((ma.col("__rn") - ma.col("__rn0")).cast("int64").alias(ENTRY_SOURCES_ID))
        .select(SCOPE, ENTRY_SOURCES_ID, *columns)
    )


def cell_entry_sources_ids(
    cell_words: Relation, word_ids: Relation, keys: t.Sequence[str], words: int
) -> Relation:
    """``(keys..., entry_sources_id)``: each cell with the id of its word tuple."""
    if not words:
        return cell_words.filter(ma.lit(False)).select(
            *keys, ma.lit(0).cast("int64").alias(ENTRY_SOURCES_ID)
        )
    columns = [word_column(index) for index in range(words)]
    return cell_words.join(word_ids, on=[SCOPE, *columns], how="inner").select(
        *keys, ENTRY_SOURCES_ID
    )


def entry_sources_ids(
    cell_words: Relation, keys: t.Sequence[str], words: int
) -> tuple[Relation, Relation]:
    """``(word_ids, cells)``: ``word_tuple_ids`` and ``cell_entry_sources_ids`` together."""
    word_ids = word_tuple_ids(cell_words, words)
    return word_ids, cell_entry_sources_ids(cell_words, word_ids, keys, words)


def decode_entry_sources(
    word_ids: Relation, ordinals: Relation, words: int, lookup: Relation | None = None
) -> Relation:
    """``entry_sources(scope, entry_sources_id, source_uuid)`` from the words' set bits.

    ``lookup`` is the ``bit_lookup`` to join (default: one built on ``word_ids``' backend).
    """
    if not words:
        return ordinals.filter(ma.lit(False)).select(
            SCOPE, ma.lit(0).cast("int64").alias(ENTRY_SOURCES_ID), SOURCE_UUID
        )
    unpacked = concat(
        [
            word_ids.select(
                SCOPE,
                ENTRY_SOURCES_ID,
                ma.lit(index).cast("int64").alias(WORD_INDEX),
                ma.col(word_column(index)).alias(WORD_VALUE),
            )
            for index in range(words)
        ]
    )
    return (
        unpacked.join(ordinals, on=[SCOPE, WORD_INDEX], how="inner")
        .join(
            bit_lookup(word_ids) if lookup is None else lookup,
            left_on=WORD_BIT,
            right_on=BIT_INDEX,
            how="inner",
        )
        .filter(ma.col(WORD_VALUE).bitwise_and(ma.col(BIT_VALUE)).ne(0))
        .select(SCOPE, ENTRY_SOURCES_ID, SOURCE_UUID)
    )
