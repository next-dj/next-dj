from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.contrib.staticfiles.storage import staticfiles_storage

from next.static import StaticFilesBackend


if TYPE_CHECKING:
    from collections.abc import Mapping


class ViteManifestBackend(StaticFilesBackend):
    """Dev/prod-aware backend for Vite-bundled co-located Vue assets.

    With DEV_ORIGIN set the backend resolves `.vue` assets to the Vite
    dev server so HMR works through `@vitejs/plugin-vue`. Without
    DEV_ORIGIN the Vite manifest is read to find hashed built output
    and URL resolution is delegated to Django staticfiles. Rendering
    is handled by `render_module_tag` because the `vue` kind registers
    against the module renderer in `apps.py`.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Initialise from OPTIONS: DEV_ORIGIN, VITE_ROOT, MANIFEST_PATH."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._dev_origin: str = opts.get("DEV_ORIGIN", "")
        self._vite_root: str = opts.get("VITE_ROOT", "")
        self._manifest_path: str = opts.get("MANIFEST_PATH", "")
        self._manifest_data: dict[str, Any] | None = None

    def register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        """Return the URL for a discovered asset file."""
        if kind != "vue":
            return super().register_file(source_path, logical_name, kind)

        if self._dev_origin:
            return self._build_dev_url(source_path)

        if self._manifest_path:
            return self._resolve_from_manifest(source_path)

        msg = (
            f"Cannot serve {source_path.name}: no Vite manifest and no "
            "VITE_DEV_ORIGIN. Run `npm run build` once or set "
            "VITE_DEV_ORIGIN=http://localhost:5173 with `npm run dev` "
            "running."
        )
        raise RuntimeError(msg)

    def _build_dev_url(self, source_path: Path) -> str:
        if self._vite_root:
            try:
                rel = source_path.relative_to(Path(self._vite_root))
            except ValueError:
                pass
            else:
                return f"{self._dev_origin}/{rel}"
        return f"{self._dev_origin}/{source_path.name}"

    def _resolve_from_manifest(self, source_path: Path) -> str:
        manifest = self._load_manifest()
        if manifest is None:
            msg = (
                f"Vite manifest is missing at {self._manifest_path}. "
                "Run `npm install && npm run build` from the example "
                "root, or start the Vite dev server and set "
                "VITE_DEV_ORIGIN=http://localhost:5173."
            )
            raise RuntimeError(msg)
        key = self._manifest_key(source_path)
        entry = manifest.get(key)
        if entry:
            built = entry["file"]
            return str(staticfiles_storage.url(f"polls/dist/{built}"))
        msg = (
            f"Vite manifest does not contain {key}. The build is "
            "stale. Re-run `npm run build` to refresh the manifest."
        )
        raise RuntimeError(msg)

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
            return None
        with path.open() as f:
            self._manifest_data = json.load(f)
        return self._manifest_data
