from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.contrib.staticfiles.storage import staticfiles_storage

from next.static import StaticFilesBackend


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest


class ViteManifestBackend(StaticFilesBackend):
    """Dev/prod-aware backend for Vite-bundled co-located JSX assets.

    When DEV_ORIGIN is set, resolves jsx assets to the Vite dev server
    so HMR works. Without DEV_ORIGIN, reads the Vite manifest to find
    hashed built output and delegates URL resolution to Django staticfiles.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Initialise from OPTIONS: DEV_ORIGIN, VITE_ROOT, MANIFEST_PATH."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._dev_origin: str = opts.get("DEV_ORIGIN", "")
        self._vite_root: str = opts.get("VITE_ROOT", "")
        self._manifest_path: str = opts.get("MANIFEST_PATH", "")
        self._manifest_data: dict[str, Any] | None = None
        self._dev_url_map: dict[str, str] = {}

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

    def render_babel_script_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,  # noqa: ARG002
    ) -> str:
        """Render JSX asset as an ES module script tag.

        In DEBUG mode points to the Vite dev server and prepends the React
        Refresh preamble so HMR works when Django, not Vite, serves the HTML.
        In production points to the hashed file resolved via the Vite manifest.
        """
        target = self._dev_url_map.get(url, url)
        if self._dev_origin:
            preamble = (
                f'<script type="module">'
                f'import RefreshRuntime from "{self._dev_origin}/@react-refresh";'
                f"RefreshRuntime.injectIntoGlobalHook(window);"
                f"window.$RefreshReg$ = () => {{}};"
                f"window.$RefreshSig$ = () => (type) => type;"
                f"window.__vite_plugin_react_preamble_installed__ = true;"
                f"</script>"
            )
            return f'{preamble}\n<script type="module" src="{target}"></script>'
        return f'<script type="module" src="{target}"></script>'

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

    def _load_manifest(self) -> dict[str, Any]:
        if self._manifest_data is None:
            with Path(self._manifest_path).open() as f:
                self._manifest_data = json.load(f)
        return self._manifest_data
