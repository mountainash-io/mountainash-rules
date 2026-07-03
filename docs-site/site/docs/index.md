---
title: Mountainash Rules
description: A practitioner's manual for the mountainash-rules vectorized, backend-agnostic business rules engine
---


[← Back to Ecosystem](../)
# Mountainash Rules

Define business rules as tabular data, evaluate them in a single vectorized pass on any backend, and get ranked results with full per-dimension explainability.

## Why a Guided Manual?

The API reference tells you *what* each class and method does. This manual explains *why* the engine works the way it does -- how ternary logic enables wildcard matching, why specificity ranking produces deterministic best-match results, and when to reach for the accumulator engine instead of the expression engine. Understanding the design makes the API intuitive rather than something you memorize.

## What's Inside

- **[Chapters](chapters/index.md)** -- 9 chapters covering foundations through accumulator results, in dependency order
- **[MicroSims](sims/index.md)** -- Interactive simulations for building intuition around key concepts
- **[Learning Graph](learning-graph/index.md)** -- Visual map of how concepts depend on each other

## Who This Is For

Python developers building configurable business logic -- pricing engines, eligibility checkers, configuration selectors, and combinatorial accumulation problems. If you work with rules stored as tabular data and need vectorized evaluation that runs on any backend, start with [About](about.md) to see whether this manual fits your background.
