from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


_SOURCEMAP_REFERENCE = "//# sourceMappingURL="


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


class NextJsBuildHook(BuildHookInterface):
    """Compile `next/client/next.ts` to `next.min.js` via esbuild."""

    PLUGIN_NAME = "next-js-build"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        """Run the npm build before Hatchling copies files into the artifact."""
        if version == "editable":
            return

        root = Path(self.root)
        output = root / "next" / "static" / "next" / "next.min.js"

        if os.environ.get("NEXT_DJ_SKIP_JS_BUILD"):
            if output.exists():
                _verify_no_sourcemap_reference(output)
            return

        npm = shutil.which("npm")
        if npm is None:
            if output.exists():
                _verify_no_sourcemap_reference(output)
                return
            msg = (
                "npm is required to build next/static/next/next.min.js. "
                "Install Node.js or set NEXT_DJ_SKIP_JS_BUILD=1 when the "
                "bundle is already present."
            )
            raise RuntimeError(msg)

        if not (root / "node_modules").exists():
            subprocess.run([npm, "ci"], cwd=root, check=True)  # noqa: S603
        subprocess.run([npm, "run", "build:next"], cwd=root, check=True)  # noqa: S603

        if not output.exists():
            msg = f"Expected {output} after npm run build:next, but it is missing."
            raise RuntimeError(msg)
        _verify_no_sourcemap_reference(output)
