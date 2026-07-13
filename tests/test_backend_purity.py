"""Enforces backend-purity for the rules engine source files.

The engine reaches DataFrames only through mountainash.relations and per-row
data only through mountainash.expressions. Direct backend imports are forbidden
in engine.py, result.py, and compiler.py — except for explicitly-allowed lines
marked with `# allow: <reason>`.
"""

import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).parent.parent / "src" / "mountainash_rules"
PROHIBITED_PACKAGES = ("polars", "ibis", "narwhals")
SHIM_FILES = {  # deprecation shims, removed next cycle — exempt
    "constants.py", "dimension.py", "context.py", "compiler.py",
    "result.py", "hit_policy.py", "batch_result.py", "engine.py",
    "accumulator_engine.py", "accumulator_compiler.py",
    "accumulator_result.py", "lattice.py", "aggregate.py", "primes.py",
}
ALLOW_PATTERN = re.compile(r"#\s*allow:\s*\w+")


def _pure_files():
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT)
        if rel.name.startswith("__"):
            continue
        if len(rel.parts) == 1 and rel.name in SHIM_FILES:
            continue
        yield str(rel)


@pytest.mark.parametrize("filename", list(_pure_files()))
def test_no_direct_backend_imports(filename: str):
    source = (SRC_ROOT / filename).read_text()
    violations = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for pkg in PROHIBITED_PACKAGES:
            if (
                stripped.startswith(f"import {pkg}")
                or stripped.startswith(f"from {pkg}")
                or stripped.startswith(f"import {pkg}.")
                or stripped.startswith(f"from {pkg}.")
            ):
                if ALLOW_PATTERN.search(line):
                    continue  # explicit opt-out for documented exceptions
                violations.append(f"{filename}:{lineno}: {stripped}")
    assert not violations, (
        f"Backend-impure imports in {filename}:\n" + "\n".join(violations)
    )
