from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


_SOURCEMAP_REFERENCE = "//# sourceMappingURL="
# The outputs of `npm run build:next`. tests/static/test_chunk_sync.py keeps this list
# equal to every other list of the chunks.
_BUNDLES = (
    "next.min.js",
    "next.scripts.min.js",
    "next.sse.min.js",
    "next.csrf.min.js",
    "next.poll.min.js",
    "next.dev.min.js",
)


def _verify_no_sourcemap_reference(bundle: Path) -> None:
    """Refuse to package a bundle that points at a map the artifact leaves out.

    Manifest storage rewrites the reference at `collectstatic` time and fails on the
    missing target, so a linked map breaks every install made from the artifact.
    """
    if _SOURCEMAP_REFERENCE in bundle.read_text(encoding="utf-8"):
        msg = (
            f"{bundle} carries a {_SOURCEMAP_REFERENCE} comment, and the artifact "
            "ships no map. Build the bundle with esbuild --sourcemap=external."
        )
        raise RuntimeError(msg)


def _verify_built(outputs: list[Path]) -> None:
    """Refuse a missing bundle or one that links a map the artifact leaves out."""
    for output in outputs:
        if not output.exists():
            msg = f"Expected {output} after npm run build:next, but it is missing."
            raise RuntimeError(msg)
        _verify_no_sourcemap_reference(output)


class NextJsBuildHook(BuildHookInterface):
    """Compile the client runtime and its lazy chunks via esbuild."""

    PLUGIN_NAME = "next-js-build"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        """Run the npm build before Hatchling copies files into the artifact."""
        if version == "editable":
            return

        root = Path(self.root)
        bundles = root / "next" / "static" / "next"
        outputs = [bundles / name for name in _BUNDLES]

        if os.environ.get("NEXT_DJ_SKIP_JS_BUILD"):
            for output in outputs:
                if output.exists():
                    _verify_no_sourcemap_reference(output)
            return

        npm = shutil.which("npm")
        if npm is None:
            if all(output.exists() for output in outputs):
                _verify_built(outputs)
                return
            msg = (
                f"npm is required to build {', '.join(_BUNDLES)} in next/static/next. "
                "Install Node.js or set NEXT_DJ_SKIP_JS_BUILD=1 when the "
                "bundles are already present."
            )
            raise RuntimeError(msg)

        if not (root / "node_modules").exists():
            subprocess.run([npm, "ci"], cwd=root, check=True)  # noqa: S603
        subprocess.run([npm, "run", "build:next"], cwd=root, check=True)  # noqa: S603

        _verify_built(outputs)
