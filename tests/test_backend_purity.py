"""Enforces backend-purity for the rules engine source files.

The engine reaches DataFrames only through mountainash.relations and per-row
data only through mountainash.expressions. Direct backend imports are forbidden
in every module under src/mountainash_rules/ — except for explicitly-allowed
lines marked with `# allow: <reason>`.
"""

import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).parent.parent / "src" / "mountainash_rules"
PROHIBITED_PACKAGES = ("polars", "ibis", "narwhals", "pyarrow")
ALLOW_PATTERN = re.compile(r"#\s*allow:\s*\w+")


def _pure_files():
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT)
        if rel.name.startswith("__"):
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


def test_every_source_module_is_checked():
    on_disk = {
        str(path.relative_to(SRC_ROOT))
        for path in SRC_ROOT.rglob("*.py")
        if not path.name.startswith("__")
    }
    assert on_disk == set(_pure_files())
    assert {"decision_table/build.py", "decision_table/apply.py", "context_validation.py"} <= on_disk


def test_pyarrow_is_prohibited():
    assert "pyarrow" in PROHIBITED_PACKAGES
