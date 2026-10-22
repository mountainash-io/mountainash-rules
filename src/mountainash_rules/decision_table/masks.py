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


def bit_lookup() -> Relation:
    """The 63-row ``(bit_index, bit_value)`` lookup; ``bit_value`` has exactly bit ``bit_index`` set."""
    return relation(
        [{BIT_INDEX: index, BIT_VALUE: 1 << index} for index in range(BITS_PER_WORD)]
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


def word_count(ordinals: Relation) -> int:
    """Number of word columns the widest scope needs (0 when there are no contributors)."""
    # A scope with an ordinal in word k holds ordinals in every word below k, so the distinct
    # word indexes are exactly 0..W-1.  Counting them is the one scalar construction reads.
    return ordinals.select(WORD_INDEX).unique().count_rows()


def membership_words(
    membership: Relation, ordinals: Relation, words: int, keys: t.Sequence[str]
) -> Relation:
    """One row per cell ``keys`` with its ``words`` int64 mask words.

    ``membership`` holds distinct ``(keys..., source_uuid)`` rows: each rule once per cell.
    """
    if not words:
        return membership.filter(ma.lit(False)).select(*keys)
    joined = membership.join(ordinals, on=[SCOPE, SOURCE_UUID], how="inner").join(
        bit_lookup(), left_on=WORD_BIT, right_on=BIT_INDEX, how="inner"
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


def entry_sources_ids(
    cell_words: Relation, keys: t.Sequence[str], words: int
) -> tuple[Relation, Relation]:
    """Assign ``entry_sources_id`` by sorted natural word tuple within each scope.

    Returns ``(word_ids, cells)``: ``word_ids`` is ``(scope, entry_sources_id, words...)``,
    one row per distinct word tuple; ``cells`` is ``(keys..., entry_sources_id)``.
    """
    columns = [word_column(index) for index in range(words)]
    if not columns:
        empty = cell_words.filter(ma.lit(False))
        return (
            empty.select(SCOPE, ma.lit(0).cast("int64").alias(ENTRY_SOURCES_ID)),
            empty.select(*keys, ma.lit(0).cast("int64").alias(ENTRY_SOURCES_ID)),
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
    word_ids = (
        ranked.join(first, on=SCOPE, how="inner")
        .with_columns((ma.col("__rn") - ma.col("__rn0")).cast("int64").alias(ENTRY_SOURCES_ID))
        .select(SCOPE, ENTRY_SOURCES_ID, *columns)
    )
    cells = cell_words.join(word_ids, on=[SCOPE, *columns], how="inner").select(
        *keys, ENTRY_SOURCES_ID
    )
    return word_ids, cells


def decode_entry_sources(word_ids: Relation, ordinals: Relation, words: int) -> Relation:
    """``entry_sources(scope, entry_sources_id, source_uuid)`` from the words' set bits."""
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
        .join(bit_lookup(), left_on=WORD_BIT, right_on=BIT_INDEX, how="inner")
        .filter(ma.col(WORD_VALUE).bitwise_and(ma.col(BIT_VALUE)).ne(0))
        .select(SCOPE, ENTRY_SOURCES_ID, SOURCE_UUID)
    )
