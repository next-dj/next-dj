from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from django.test import override_settings

import next.server
from next.conf import next_framework_settings
from next.server import iter_all_autoreload_watch_specs, register_autoreload_watch_spec
from next.server.watcher import (
    _dedupe_watch_specs,
    _iter_default_autoreload_watch_specs,
    _registered_extra_watch_specs,
)
from tests.support.backends import file_components_entry


if TYPE_CHECKING:
    from collections.abc import Callable


SERVER_EXPORTS = {
    "NextStatReloader",
    "get_framework_filesystem_roots_for_linking",
    "iter_all_autoreload_watch_specs",
    "register_autoreload_watch_spec",
    "signals",
}


class TestServerAutoreloadWatchApi:
    """Public autoreload helpers live on ``next.server``."""

    def test_register_autoreload_watch_spec_then_iter_all(self) -> None:
        """Extra registration is deduplicated in ``iter_all_autoreload_watch_specs``."""
        root = Path("/tmp/next_autoreload_extra_test")
        try:
            register_autoreload_watch_spec(root, "**/plugin.py")
            register_autoreload_watch_spec(root, "**/plugin.py")
            with patch(
                "next.server.watcher._iter_default_autoreload_watch_specs",
                return_value=[],
            ):
                specs = iter_all_autoreload_watch_specs()
            matches = [x for x in specs if x == (root, "**/plugin.py")]
            assert len(matches) == 1
        finally:
            _registered_extra_watch_specs.clear()

    def test_dedupe_watch_specs_when_resolve_raises_oserror(self) -> None:
        """Duplicate specs collapse when ``Path.resolve`` fails."""
        mock_path = MagicMock()
        mock_path.resolve.side_effect = OSError("no resolve")
        specs = _dedupe_watch_specs([(mock_path, "*.py"), (mock_path, "*.py")])
        assert len(specs) == 1

    def test_component_backend_dirs_are_watched_in_declaration_order(
        self, tmp_path: Path, apply_component_backends: Callable[[list[Any]], None]
    ) -> None:
        """``COMPONENT_BACKENDS`` ``DIRS`` add ``**/component.py`` (not ``.djx``)."""
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        apply_component_backends(
            [file_components_entry(first), file_components_entry(second)]
        )

        specs = _iter_default_autoreload_watch_specs()

        assert [p for p, glob in specs if glob == "**/component.py"] == [first, second]
        assert all(".djx" not in glob for _, glob in specs)

    def test_iter_default_watches_component_py_under_each_page_root(
        self, tmp_path: Path
    ) -> None:
        """Each directory root in page DIRS gets a component.py glob for COMPONENTS_DIR."""
        custom = tmp_path / "custom"
        pages_tree = tmp_path / "pages_tree"
        custom.mkdir()
        pages_tree.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [str(custom.resolve()), str(pages_tree.resolve())],
                        "OPTIONS": {},
                    }
                ],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_",
                    }
                ],
            }
        ):
            next_framework_settings.reload()
            specs = _iter_default_autoreload_watch_specs()
        next_framework_settings.reload()
        expected_glob = "**/_/**/component.py"
        for root in (custom.resolve(), pages_tree.resolve()):
            matches = [(p, g) for p, g in specs if p == root and g == expected_glob]
            assert len(matches) == 1


class TestServerPublicSurface:
    """Names the ``next.server`` package publishes."""

    def test_exported_names_are_pinned(self) -> None:
        """A dropped name coming back and a new one both have to be decided."""
        assert set(next.server.__all__) == SERVER_EXPORTS
        assert all(hasattr(next.server, name) for name in SERVER_EXPORTS)
