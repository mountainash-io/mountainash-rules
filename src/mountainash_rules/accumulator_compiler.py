"""Deprecated shim — moved to mountainash_rules.engines.accumulator.compiler.

Emits DeprecationWarning; remove in the production release after next
(shipped 2026-07; remove no earlier than the 2026-08 production release).
"""

import warnings

from mountainash_rules.engines.accumulator.compiler import *  # noqa: F401,F403
from mountainash_rules.engines.accumulator.compiler import AccumulatorCompiler  # noqa: F401

warnings.warn(
    "mountainash_rules.accumulator_compiler is deprecated; import from "
    "mountainash_rules.engines.accumulator.compiler (or the package root)",
    DeprecationWarning,
    stacklevel=2,
)
