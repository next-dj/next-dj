from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils.autoreload import StatReloader

from next.conf import next_framework_settings
from next.server import (
    NextStatReloader,
    _dedupe_watch_specs,
    _registered_extra_watch_specs,
    get_framework_filesystem_roots_for_linking,
    iter_all_autoreload_watch_specs,
    iter_default_autoreload_watch_specs,
    register_autoreload_watch_spec,
)


def _paths_matched_by_reloader_globs(reloader: StatReloader) -> set[Path]:
    """Paths returned by ``Path.glob`` for each registered ``watch_dir`` pair.

    This matches the subset of files Django's :meth:`~StatReloader.snapshot_files`
    tracks for mtime changes from next-registered globs (excluding Python modules).
    """
    out: set[Path] = set()
    for directory, patterns in reloader.directory_globs.items():
        for pattern in patterns:
            out.update(p.resolve() for p in directory.glob(pattern))
    return out


class TestNextStatReloader:
    """Tests for NextStatReloader."""

    def test_subclasses_stat_reloader(self) -> None:
        """NextStatReloader is a subclass of Django StatReloader."""
        assert issubclass(NextStatReloader, StatReloader)

    def test_tick_is_generator(self) -> None:
        """tick() yields and can be iterated."""
        reloader = NextStatReloader()
        with patch.object(reloader, "snapshot_files", return_value=iter([])):
            gen = reloader.tick()
            first = next(gen)
            assert first is None

    def test_tick_calls_snapshot_files(self) -> None:
        """tick() uses snapshot_files for mtime check."""
        reloader = NextStatReloader()
        with patch.object(
            reloader, "snapshot_files", return_value=iter([])
        ) as mock_snapshot:
            gen = reloader.tick()
            next(gen)
            mock_snapshot.assert_called()

    @pytest.mark.parametrize(
        ("reloader_tick_scenario", "num_ticks", "expect"),
        [
            pytest.param("route_set_grows", 2, "notify_path", id="route_set_grows"),
            pytest.param(
                "no_notify_first_tick", 1, "no_notify", id="no_notify_first_tick"
            ),
            pytest.param(
                "route_set_unchanged", 2, "no_notify", id="route_set_unchanged"
            ),
            pytest.param("watch_raises", 1, "ok", id="watch_raises"),
            pytest.param("mtime_change", 2, "notify_path", id="mtime_change"),
        ],
        indirect=["reloader_tick_scenario"],
    )
    def test_tick_scenario_notify_behavior(
        self,
        reloader_tick_scenario,
        num_ticks: int,
        expect: str,
    ) -> None:
        """tick() under each patched scenario matches expected notify behavior."""
        reloader, payload = reloader_tick_scenario
        gen = reloader.tick()
        for _ in range(num_ticks):
            next(gen)
        if expect == "notify_path":
            mock_notify, expected_path = payload
            mock_notify.assert_called_once_with(expected_path)
        elif expect == "no_notify":
            mock_notify = payload
            mock_notify.assert_not_called()
        elif expect == "ok":
            assert payload is None


class TestServerAutoreloadWatchApi:
    """Public autoreload helpers live on ``next.server`` with ``NextStatReloader``."""

    def test_register_autoreload_watch_spec_then_iter_all(self) -> None:
        """Extra registration is deduplicated in ``iter_all_autoreload_watch_specs``."""
        root = Path("/tmp/next_autoreload_extra_test")
        try:
            register_autoreload_watch_spec(root, "**/plugin.py")
            register_autoreload_watch_spec(root, "**/plugin.py")
            with patch(
                "next.server.iter_default_autoreload_watch_specs",
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


class TestDjxNotInStatReloaderGlobMatches:
    """``.djx`` files are not among paths matched by next's ``watch_dir`` globs."""

    def test_glob_matched_paths_exclude_djx_alongside_page_and_components(
        self, tmp_path: Path
    ) -> None:
        """Mimic production globs under a pages root. No ``.djx`` is glob-matched."""
        pages = tmp_path / "pages"
        home = pages / "home"
        home.mkdir(parents=True)
        (home / "page.py").write_text("#")
        (home / "template.djx").write_text("<p/>")
        chip = pages / "_components" / "chip"
        chip.mkdir(parents=True)
        (chip / "component.djx").write_text("<span/>")
        (chip / "component.py").write_text("#")
        (pages / "_components" / "solo.djx").write_text("<div/>")

        reloader = NextStatReloader()
        reloader.watch_dir(pages, "**/page.py")
        reloader.watch_dir(pages, "**/_components/**/component.py")

        matched = _paths_matched_by_reloader_globs(reloader)
        assert (home / "template.djx").resolve() not in matched
        assert (chip / "component.djx").resolve() not in matched
        assert (pages / "_components" / "solo.djx").resolve() not in matched
        assert (home / "page.py").resolve() in matched
        assert (chip / "component.py").resolve() in matched

    def test_changing_only_djx_does_not_call_notify_file_changed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Editing ``.djx`` does not go through ``notify_file_changed`` (no process reload)."""
        page = tmp_path / "p" / "page.py"
        page.parent.mkdir(parents=True)
        page.write_text("#")
        djx = tmp_path / "p" / "template.djx"
        djx.write_text("a")

        reloader = NextStatReloader()
        reloader.watch_dir(tmp_path, "**/page.py")
        notified: list[Path] = []

        def record_notify(p: Path) -> None:
            notified.append(Path(p))

        monkeypatch.setattr(reloader, "notify_file_changed", record_notify)

        # Restrict the parent StatReloader snapshot to ``page.py`` only.
        # Real runserver also skips ``.djx`` because it is not matched by next's ``watch_dir`` globs.
        with (
            patch("next.server.get_pages_directories_for_watch", return_value=[]),
            patch("next.server.scan_pages_tree", return_value=iter([])),
            patch.object(
                reloader,
                "snapshot_files",
                return_value=iter([(page.resolve(), 1000.0)]),
            ),
        ):
            gen = reloader.tick()
            next(gen)
            djx.write_text("changed")
            next(gen)

        assert notified == []
