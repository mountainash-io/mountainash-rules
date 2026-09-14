"""Build and stage the real native extension for wheels and editable installs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import typing as t
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_PACKAGE_DIR = Path("src") / "mountainash_rules"


class NativeExtensionBuildHook(BuildHookInterface):
    """Preserve Hatch versioning while producing interpreter-specific native wheels."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, t.Any]) -> None:
        if self.target_name != "wheel":
            return
        build_data["infer_tag"] = True
        build_data["pure_python"] = False
        suffix = sysconfig.get_config_var("EXT_SUFFIX")
        if not suffix:
            raise RuntimeError("could not determine target interpreter EXT_SUFFIX")

        artifact = self._cargo_build()
        destination = Path(self.root) / _PACKAGE_DIR / f"_native{suffix}"
        # A failed build/copy must not destroy a working editable extension.
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix="_native-build-", suffix=".tmp", delete=False
        ) as temporary:
            staged = Path(temporary.name)
        try:
            shutil.copyfile(artifact, staged)
            os.chmod(staged, 0o755)
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)

        # Admit exactly this interpreter's generated binary despite .gitignore.
        # Other editable interpreters can coexist; their binaries never leak
        # into this wheel. Python source and the native typing stub remain.
        build_data["artifacts"].append(destination.relative_to(self.root).as_posix())

    def clean(self, versions: list[str]) -> None:
        package_dir = Path(self.root) / _PACKAGE_DIR
        if package_dir.is_dir():
            for artifact in package_dir.glob("_native.*"):
                if artifact.suffix in {".so", ".pyd", ".dylib"}:
                    artifact.unlink()

    def _cargo_build(self) -> Path:
        manifest = Path(self.root) / "Cargo.toml"
        if not manifest.is_file():
            raise FileNotFoundError(f"native source manifest missing: {manifest}")
        cargo = shutil.which("cargo")
        if cargo is None:
            raise RuntimeError(
                "Source builds of mountainash_rules require Cargo (https://rustup.rs). "
                "Use a compatible prebuilt wheel for end-user installation; "
                "there is no pure-Python language fallback."
            )
        environment = dict(os.environ)
        environment["PYO3_PYTHON"] = sys.executable
        command = [
            cargo,
            "build",
            "--release",
            "--locked",
            "--manifest-path",
            str(manifest),
            "--features",
            "extension-module",
            "--message-format=json-render-diagnostics",
        ]
        self.app.display_info("Building mountainash_rules._native with Cargo")
        result = subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        artifacts = []
        for line in result.stdout.splitlines():
            message = json.loads(line)
            if message.get("reason") == "compiler-message":
                rendered = message.get("message", {}).get("rendered")
                if rendered:
                    sys.stderr.write(rendered)
            if (
                message.get("reason") == "compiler-artifact"
                and message.get("target", {}).get("name") == "_native"
            ):
                artifacts.extend(
                    Path(filename)
                    for filename in message["filenames"]
                    if filename.endswith((".so", ".dylib", ".dll"))
                )
        if result.returncode:
            raise RuntimeError(
                f"native Cargo build failed with exit code {result.returncode}"
            )
        if len(artifacts) != 1 or not artifacts[0].is_file():
            raise RuntimeError(
                f"Cargo did not produce exactly one native extension: {artifacts}"
            )
        return artifacts[0]
