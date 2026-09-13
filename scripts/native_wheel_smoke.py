#!/usr/bin/env python3
"""Install a built wheel and exercise its native language component.

This dependency-free check deliberately tests the installed extension, not the
whole DataFrame package. Pass --public-api when package dependencies have been
installed; then any package import or facade failure is fatal too.
"""

from __future__ import annotations

import argparse
import email
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
import sysconfig
from pathlib import Path


def install(wheel: Path) -> importlib.metadata.Distribution:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-deps",
        "--only-binary=:all:",
    ]
    if wheel.is_dir():
        command.extend(["--no-index", "--find-links", str(wheel), "mountainash-rules"])
    elif wheel.is_file() and wheel.suffix == ".whl":
        command.append(str(wheel))
    else:
        raise ValueError(f"expected a wheel or wheelhouse directory: {wheel}")
    subprocess.run(command, check=True)
    distribution = importlib.metadata.distribution("mountainash-rules")
    wheel_metadata = email.message_from_string(distribution.read_text("WHEEL") or "")
    if wheel_metadata.get("Root-Is-Purelib") != "false":
        raise AssertionError("expected a non-pure native wheel")
    tags = wheel_metadata.get_all("Tag", [])
    if not tags or any(tag.endswith("-any") for tag in tags):
        raise AssertionError(f"invalid native wheel tags: {tags}")
    return distribution


def load_native(distribution: importlib.metadata.Distribution):
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not suffix:
        raise RuntimeError("interpreter has no extension suffix")
    expected = f"mountainash_rules/_native{suffix}"
    binaries = [
        str(path)
        for path in distribution.files or []
        if str(path).startswith("mountainash_rules/_native.")
        and str(path).endswith((".so", ".pyd", ".dylib"))
    ]
    if binaries != [expected]:
        raise AssertionError(f"expected exactly {expected}, found {binaries}")
    extension = Path(str(distribution.locate_file(expected)))
    spec = importlib.util.spec_from_file_location(
        "mountainash_rules._native", extension
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load installed native extension: {extension}")
    module = importlib.util.module_from_spec(spec)
    # Register the actual extension so --public-api uses this same module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, str(extension)


def exercise_native(native) -> None:
    limits = native.Limits(2_000_000, 64, 50_000, 10_000, 500_000, 200_000_000)
    flags = (False, False, False, False, False, True, False)
    beta = native.Dfa.compile(r"\bβ\b", flags, limits)
    assert beta.accepts("!β!", limits)
    assert not beta.accepts("γβγ", limits)
    words = native.Dfa.compile(r"\A(?:car|cat)\z", flags, limits)
    residual = words.difference(native.Dfa.literal("car", "exact", limits), limits)
    assert residual.witness(limits) == "cat"
    assert residual.cardinality(10, limits) == 1
    assert words.difference(words, limits).is_empty(limits)
    wire = beta.canonical_json(limits)
    loaded = native.Dfa.from_json(wire, limits)
    assert loaded.canonical_json(limits) == wire
    assert loaded.accepts("!β!", limits)
    exhausted = native.Limits(2_000_000, 64, 50_000, 10_000, 0, 200_000_000)
    try:
        words.complement(exhausted)
    except native.LanguageResourceError as error:
        assert error.resource == "transitions" and error.limit == 0
    else:
        raise AssertionError("native complement ignored its transition limit")


def exercise_public_api() -> None:
    from mountainash_rules import LanguageLimits, StringLanguage

    limits = LanguageLimits(
        max_input_bytes=2_000_000,
        max_nesting=64,
        max_nfa_states=50_000,
        max_states=10_000,
        max_transitions=500_000,
        max_work=200_000_000,
    )
    language = StringLanguage.literal("hello", limits=limits)
    restored = StringLanguage.from_json(language.to_json(limits=limits), limits=limits)
    assert restored.accepts("hello", limits=limits)
    assert not restored.accepts("goodbye", limits=limits)
    assert restored.language_id(limits=limits) == language.language_id(limits=limits)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument(
        "--public-api",
        action="store_true",
        help="also require full package import and facade execution",
    )
    args = parser.parse_args()
    distribution = install(args.wheel.resolve())
    native, extension = load_native(distribution)
    exercise_native(native)
    if args.public_api:
        exercise_public_api()
    print(
        json.dumps(
            {
                "result": "PASS",
                "version": distribution.version,
                "native_extension": extension,
                "native_operations": "verified",
                "public_api": "verified" if args.public_api else "not exercised",
            }
        )
    )


if __name__ == "__main__":
    main()
