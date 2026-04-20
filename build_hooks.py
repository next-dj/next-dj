"""Hatchling build hook that compiles `next.min.js` before packaging.

The hook runs `npm ci` and `npm run build:next` during wheel and sdist
builds so the published artifact always contains a fresh bundle. Local
development still uses the Makefile targets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class NextJsBuildHook(BuildHookInterface):
    """Compile `next/static/next/next.ts` to `next.min.js` via esbuild."""

    PLUGIN_NAME = "next-js-build"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        """Run the npm build before Hatchling copies files into the artifact."""
        if os.environ.get("NEXT_DJ_SKIP_JS_BUILD"):
            return

        root = Path(self.root)
        output = root / "next" / "static" / "next" / "next.min.js"

        npm = shutil.which("npm")
        if npm is None:
            msg = (
                "npm is required to build next/static/next/next.min.js. "
                "Install Node.js or set NEXT_DJ_SKIP_JS_BUILD=1 when the "
                "bundle is already present."
            )
            if output.exists():
                return
            raise RuntimeError(msg)

        if not (root / "node_modules").exists():
            subprocess.run([npm, "ci"], cwd=root, check=True)
        subprocess.run([npm, "run", "build:next"], cwd=root, check=True)

        if not output.exists():
            msg = f"Expected {output} after npm run build:next, but it is missing."
            raise RuntimeError(msg)
