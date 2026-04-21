from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import override_settings

from next.conf import next_framework_settings
from next.server import (
    get_framework_filesystem_roots_for_linking,
    iter_all_autoreload_watch_specs,
    register_autoreload_watch_spec,
)
from next.server.watcher import (
    _dedupe_watch_specs,
    _registered_extra_watch_specs,
    iter_default_autoreload_watch_specs,
)


class TestServerAutoreloadWatchApi:
    """Public autoreload helpers live on ``next.server``."""

    def test_register_autoreload_watch_spec_then_iter_all(self) -> None:
        """Extra registration is deduplicated in ``iter_all_autoreload_watch_specs``."""
        root = Path("/tmp/next_autoreload_extra_test")
        try:
            register_autoreload_watch_spec(root, "**/plugin.py")
            register_autoreload_watch_spec(root, "**/plugin.py")
            with patch(
                "next.server.watcher.iter_default_autoreload_watch_specs",
                return_value=[],
            ):
                specs = iter_all_autoreload_watch_specs()
            matches = [x for x in specs if x == (root, "**/plugin.py")]
            assert len(matches) == 1
        finally:
            _registered_extra_watch_specs.clear()

    def test_get_framework_filesystem_roots_for_linking_returns_paths(self) -> None:
        """Linking helper returns a sorted list of paths."""
        roots = get_framework_filesystem_roots_for_linking()
        assert isinstance(roots, list)
        assert all(isinstance(p, Path) for p in roots)

    def test_dedupe_watch_specs_when_resolve_raises_oserror(self) -> None:
        """Duplicate specs collapse when ``Path.resolve`` fails."""
        mock_path = MagicMock()
        mock_path.resolve.side_effect = OSError("no resolve")
        specs = _dedupe_watch_specs([(mock_path, "*.py"), (mock_path, "*.py")])
        assert len(specs) == 1

    def test_iter_default_includes_component_backend_dirs(self, tmp_path: Path) -> None:
        """``DEFAULT_COMPONENT_BACKENDS`` ``DIRS`` add ``**/component.py`` (not ``.djx``)."""
        comp_root = tmp_path / "shared_components"
        comp_root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [],
                "DEFAULT_COMPONENT_BACKENDS": [
                    "not-a-dict",
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [str(comp_root)],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            specs = iter_default_autoreload_watch_specs()
        next_framework_settings.reload()
        assert all(".djx" not in g for _, g in specs)
        assert any(g == "**/component.py" and p == comp_root for p, g in specs)

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
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [
                            str(custom.resolve()),
                            str(pages_tree.resolve()),
                        ],
                        "OPTIONS": {},
                    },
                ],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            specs = iter_default_autoreload_watch_specs()
        next_framework_settings.reload()
        expected_glob = "**/_/**/component.py"
        for root in (custom.resolve(), pages_tree.resolve()):
            matches = [(p, g) for p, g in specs if p == root and g == expected_glob]
            assert len(matches) == 1
