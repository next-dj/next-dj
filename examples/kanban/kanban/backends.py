from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.contrib.staticfiles.storage import staticfiles_storage

from next.static import StaticFilesBackend


if TYPE_CHECKING:
    from collections.abc import Mapping


logger = logging.getLogger(__name__)


class ViteManifestBackend(StaticFilesBackend):
    """Dev/prod-aware backend for Vite-bundled co-located JSX assets.

    With DEV_ORIGIN set, jsx assets resolve to the Vite dev server so
    HMR works. Without DEV_ORIGIN, the Vite manifest is read to find
    hashed built output and URL resolution is delegated to Django
    staticfiles. Rendering is handled by the framework built-in
    `render_module_tag` because jsx is registered under the `module`
    renderer in `apps.py`.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Initialise from OPTIONS: DEV_ORIGIN, VITE_ROOT, MANIFEST_PATH."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._dev_origin: str = opts.get("DEV_ORIGIN", "")
        self._vite_root: str = opts.get("VITE_ROOT", "")
        self._manifest_path: str = opts.get("MANIFEST_PATH", "")
        self._manifest_data: dict[str, Any] | None = None
        self._manifest_missing_warned = False

    def register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        """Return the URL for a discovered asset file."""
        if kind != "jsx":
            return super().register_file(source_path, logical_name, kind)

        if self._dev_origin:
            return self._build_dev_url(source_path)

        if self._manifest_path:
            return self._resolve_from_manifest(source_path, logical_name)

        return super().register_file(source_path, logical_name, kind)

    def _build_dev_url(self, source_path: Path) -> str:
        if self._vite_root:
            try:
                rel = source_path.relative_to(Path(self._vite_root))
            except ValueError:
                pass
            else:
                return f"{self._dev_origin}/{rel}"
        return f"{self._dev_origin}/{source_path.name}"

    def _resolve_from_manifest(self, source_path: Path, logical_name: str) -> str:
        manifest = self._load_manifest()
        if manifest is None:
            return super().register_file(source_path, logical_name, "jsx")
        key = self._manifest_key(source_path)
        entry = manifest.get(key)
        if entry:
            built = entry["file"]
            return str(staticfiles_storage.url(f"kanban/dist/{built}"))
        return super().register_file(source_path, logical_name, "jsx")

    def _manifest_key(self, source_path: Path) -> str:
        if self._vite_root:
            try:
                return str(source_path.relative_to(Path(self._vite_root)))
            except ValueError:
                pass
        return source_path.name

    def _load_manifest(self) -> dict[str, Any] | None:
        if self._manifest_data is not None:
            return self._manifest_data
        path = Path(self._manifest_path)
        if not path.exists():
            if not self._manifest_missing_warned:
                logger.warning(
                    "Vite manifest not found at %s. Falling back to staticfiles. "
                    "Run `npm run build` to generate it.",
                    path,
                )
                self._manifest_missing_warned = True
            return None
        with path.open() as f:
            self._manifest_data = json.load(f)
        return self._manifest_data
