import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import override_settings

from next.checks.common import get_page_roots
from next.conf import next_framework_settings
from next.pages.registry import (
    get_layout_djx_paths_for_watch,
    get_template_djx_paths_for_watch,
)
from next.pages.watch import (
    get_pages_directories_for_watch,
    iter_page_backends_for_watch,
    iter_pages_roots_with_components_folder_names,
)
from next.urls import RouterFactory
from tests.support import (
    MalformedRootsRouter,
    OddComponentsNameRouter,
    RaisingComponentsRouter,
    RaisingRootsRouter,
    RootPagesRouter,
    file_router_config_entry,
    importable_dir,
)


def _write_app(root: Path, name: str) -> Path:
    """Write an importable app package with an empty `pages` tree."""
    app_pages = root / name / "pages"
    app_pages.mkdir(parents=True)
    (root / name / "__init__.py").write_text("")
    return app_pages


def _watch_tracebacks(caplog) -> list[logging.LogRecord]:
    """Return the records the watch helpers logged with a traceback."""
    return [
        record
        for record in caplog.records
        if record.exc_info and record.name == "next.pages.watch"
    ]


def _watched(entry: dict[str, object]) -> list[Path]:
    """Return the directories the watcher observes for one `PAGE_BACKENDS` entry."""
    with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}):
        next_framework_settings.reload()
        return get_pages_directories_for_watch()


class TestGetLayoutDjxPathsForWatch:
    """Tests for get_layout_djx_paths_for_watch()."""

    def test_returns_layout_djx_paths_under_pages_dirs(self, tmp_path) -> None:
        """Returns resolved paths of all layout.djx under given pages dirs."""
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "layout.djx").write_text("<div>a</div>")
        (tmp_path / "a" / "b").mkdir()
        (tmp_path / "a" / "b" / "layout.djx").write_text("<div>b</div>")
        with patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert len(result) == 2
        parent_names = {p.parent.name for p in result}
        assert parent_names == {"a", "b"}
        assert all(p.name == "layout.djx" for p in result)

    def test_returns_empty_when_no_layout_djx(self, tmp_path) -> None:
        """Returns empty set when no layout.djx under pages dirs."""
        with patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert result == set()

    def test_swallows_oserror_on_rglob_layout(self, tmp_path) -> None:
        """When rglob raises OSError (e.g. permission), log and return partial result."""
        with (
            patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch,
            patch.object(Path, "rglob", side_effect=OSError(13, "Permission denied")),
        ):
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert result == set()


class TestGetTemplateDjxPathsForWatch:
    """Tests for get_template_djx_paths_for_watch()."""

    def test_returns_template_djx_paths_under_pages_dirs(self, tmp_path) -> None:
        """Returns resolved paths of all template.djx under given pages dirs."""
        (tmp_path / "x").mkdir()
        (tmp_path / "x" / "template.djx").write_text("x")
        (tmp_path / "x" / "y").mkdir()
        (tmp_path / "x" / "y" / "template.djx").write_text("y")
        with patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert len(result) == 2
        assert all(p.name == "template.djx" for p in result)
        parent_names = {p.parent.name for p in result}
        assert parent_names == {"x", "y"}

    def test_returns_empty_when_no_template_djx(self, tmp_path) -> None:
        """Returns empty set when no template.djx under pages dirs."""
        with patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert result == set()

    def test_swallows_oserror_on_rglob_template(self, tmp_path) -> None:
        """When rglob raises OSError (e.g. permission), log and return partial result."""
        with (
            patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch,
            patch.object(Path, "rglob", side_effect=OSError(13, "Permission denied")),
        ):
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert result == set()


class TestIterPageBackendsForWatch:
    """One router per ``PAGE_BACKENDS`` entry, broken entries skipped."""

    def test_no_backends_when_setting_is_not_a_list(self) -> None:
        """A ``PAGE_BACKENDS`` that is not a list yields no router."""
        mock_nf = SimpleNamespace(PAGE_BACKENDS={})
        with patch("next.backends.next_framework_settings", mock_nf):
            assert list(iter_page_backends_for_watch()) == []

    def test_non_dict_entries_are_skipped(self) -> None:
        """List entries that are not dicts name no backend."""
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": ["not a dict", None]}):
            next_framework_settings.reload()
            assert list(iter_page_backends_for_watch()) == []

    def test_construction_failure_costs_only_its_own_entry(
        self, tmp_path, caplog
    ) -> None:
        """An entry that cannot be built is skipped, the healthy one still answers."""
        root = tmp_path / "shell"
        root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [
                    {"BACKEND": "nonexistent.Backend"},
                    file_router_config_entry(pages_dir=root),
                ]
            }
        ):
            next_framework_settings.reload()
            with caplog.at_level(logging.ERROR, logger="next.pages.watch"):
                backends = list(iter_page_backends_for_watch())
                watched = get_pages_directories_for_watch()

        assert len(backends) == 1
        assert watched == [root.resolve()]
        # Two passes over the same broken entry, one report, because this runs
        # on every reloader tick.
        assert len(_watch_tracebacks(caplog)) == 1

    def test_two_nameless_broken_entries_are_diagnosed_apart(self, caplog) -> None:
        """Entries that name no BACKEND share a name, so position tells them apart."""
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [{}, {}]}):
            next_framework_settings.reload()
            with caplog.at_level(logging.ERROR, logger="next.pages.watch"):
                assert list(iter_page_backends_for_watch()) == []

        assert len(_watch_tracebacks(caplog)) == 2


class TestWatchedRootsFollowThePageRoots:
    """Every tree a router routes is a tree the watcher observes."""

    def test_root_trees_are_watched(self, tmp_path) -> None:
        """A configured ``DIRS`` root is watched."""
        root = tmp_path / "shell"
        root.mkdir()

        assert _watched(file_router_config_entry(pages_dir=root)) == [root.resolve()]

    def test_app_trees_are_watched(self, tmp_path, settings) -> None:
        """An installed app's pages tree is watched."""
        app_pages = _write_app(tmp_path, "shop")
        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop"]
            watched = _watched(file_router_config_entry(app_dirs=True))

        assert watched == [app_pages.resolve()]

    def test_app_trees_of_a_root_only_router_are_left_alone(
        self, tmp_path, settings
    ) -> None:
        """Without ``APP_DIRS`` no app tree is routed, so none is watched."""
        app_pages = _write_app(tmp_path, "shop")
        root = tmp_path / "shell"
        root.mkdir()
        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop"]
            watched = _watched(file_router_config_entry(pages_dir=root))

        assert watched == [root.resolve()]
        assert app_pages.resolve() not in watched

    def test_unrouted_working_directory_pages_are_neither_routed_nor_watched(
        self, tmp_path, monkeypatch
    ) -> None:
        """A tree beside the process is nobody's page root, `next.W002` names it."""
        (tmp_path / "pages").mkdir()
        monkeypatch.chdir(tmp_path)
        entry = file_router_config_entry()

        with patch("next.utils.settings", Mock(BASE_DIR=None)):
            watched = _watched(entry)
            checked = [
                root.path
                for root in get_page_roots(RouterFactory.create_backend(entry))
            ]

        assert watched == []
        assert checked == []

    def test_a_root_two_backends_reach_is_watched_once(self, tmp_path) -> None:
        """Two entries mounting one tree produce one watch directory."""
        root = tmp_path / "shell"
        root.mkdir()
        entry = file_router_config_entry(pages_dir=root)
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry, dict(entry)]}):
            next_framework_settings.reload()
            watched = get_pages_directories_for_watch()

        assert watched == [root.resolve()]

    def test_a_backend_reporting_no_root_is_not_watched(self) -> None:
        """A router that routes from elsewhere contributes no watch directory."""
        with (
            patch.object(
                RouterFactory, "create_backend", return_value=RootPagesRouter([])
            ),
            override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "elsewhere.Backend"}]}
            ),
        ):
            next_framework_settings.reload()
            assert get_pages_directories_for_watch() == []


class TestRouterFailuresNeverReachTheWatcher:
    """`runserver`, `collectstatic` and the finder all read through these helpers."""

    def test_a_raising_tree_listing_costs_only_its_own_backend(self, tmp_path) -> None:
        """One backend that raises leaves the healthy one watched."""
        root = tmp_path / "shell"
        root.mkdir()
        build = RouterFactory.create_backend

        def broken_first(config: dict) -> object:
            if config["BACKEND"] == "broken.Backend":
                return RaisingRootsRouter()
            return build(config)

        with (
            patch.object(RouterFactory, "create_backend", side_effect=broken_first),
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [
                        {"BACKEND": "broken.Backend"},
                        file_router_config_entry(pages_dir=root),
                    ]
                }
            ),
        ):
            next_framework_settings.reload()
            watched = get_pages_directories_for_watch()

        assert watched == [root.resolve()]

    def test_a_raising_components_folder_name_costs_only_its_pairs(
        self, tmp_path
    ) -> None:
        """The pages of that backend stay watched, its component glob does not."""
        root = tmp_path / "shell"
        root.mkdir()
        with (
            patch.object(
                RouterFactory,
                "create_backend",
                return_value=RaisingComponentsRouter([root]),
            ),
            override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "broken.Backend"}]}
            ),
        ):
            next_framework_settings.reload()
            watched = get_pages_directories_for_watch()
            pairs = iter_pages_roots_with_components_folder_names()

        assert watched == [root.resolve()]
        assert pairs == []

    def test_a_malformed_tree_listing_costs_only_its_own_backend(
        self, tmp_path, caplog
    ) -> None:
        """Bare paths instead of `PageRoot` entries are dropped, not dereferenced."""
        root = tmp_path / "shell"
        root.mkdir()
        build = RouterFactory.create_backend

        def malformed_first(config: dict) -> object:
            if config["BACKEND"] == "broken.Backend":
                return MalformedRootsRouter([tmp_path / "unreported"])
            return build(config)

        with (
            patch.object(RouterFactory, "create_backend", side_effect=malformed_first),
            override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [
                        {"BACKEND": "broken.Backend"},
                        file_router_config_entry(pages_dir=root),
                    ]
                }
            ),
        ):
            next_framework_settings.reload()
            with caplog.at_level(logging.ERROR, logger="next.pages.watch"):
                watched = get_pages_directories_for_watch()

        assert watched == [root.resolve()]
        # A wrong shape is no failing source, so it is reported as the wrong
        # type rather than as a raise, and carries no traceback.
        assert _watch_tracebacks(caplog) == []
        assert "of the wrong type" in caplog.records[0].getMessage()

    def test_a_malformed_components_folder_name_costs_only_its_pairs(
        self, tmp_path, caplog
    ) -> None:
        """A name that is not a `str` is dropped, and the pages stay watched."""
        root = tmp_path / "shell"
        root.mkdir()
        with (
            patch.object(
                RouterFactory,
                "create_backend",
                return_value=OddComponentsNameRouter([root]),
            ),
            override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "odd.Backend"}]}
            ),
        ):
            next_framework_settings.reload()
            with caplog.at_level(logging.ERROR, logger="next.pages.watch"):
                for _tick in range(3):
                    pairs = iter_pages_roots_with_components_folder_names()
                watched = get_pages_directories_for_watch()

        reports = [r for r in caplog.records if r.name == "next.pages.watch"]
        assert pairs == []
        assert watched == [root.resolve()]
        assert len(reports) == 1

    def test_the_same_failure_is_logged_once(self, tmp_path, caplog) -> None:
        """These helpers run per reloader tick, so a repeat must stay quiet."""
        with (
            patch.object(
                RouterFactory, "create_backend", return_value=RaisingRootsRouter()
            ),
            override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "broken.Backend"}]}
            ),
        ):
            next_framework_settings.reload()
            with caplog.at_level(logging.ERROR, logger="next.pages.watch"):
                for _tick in range(5):
                    get_pages_directories_for_watch()

        assert len(_watch_tracebacks(caplog)) == 1

    def test_a_settings_reload_lets_the_failure_be_reported_again(
        self, tmp_path, caplog
    ) -> None:
        """A reconfigured project is diagnosed again rather than staying silent."""
        with patch.object(
            RouterFactory, "create_backend", return_value=RaisingRootsRouter()
        ):
            with (
                override_settings(
                    NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "broken.Backend"}]}
                ),
                caplog.at_level(logging.ERROR, logger="next.pages.watch"),
            ):
                next_framework_settings.reload()
                get_pages_directories_for_watch()
                get_pages_directories_for_watch()
            with (
                override_settings(
                    NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "broken.Backend"}]}
                ),
                caplog.at_level(logging.ERROR, logger="next.pages.watch"),
            ):
                next_framework_settings.reload()
                get_pages_directories_for_watch()

        assert len(_watch_tracebacks(caplog)) == 2


class TestIterPagesRootsWithComponentsFolderNames:
    """Pairs ``(pages root, components folder name)`` for autoreload globs."""

    def test_each_root_carries_the_backend_folder_name(self, tmp_path) -> None:
        """The pair names the folder the router registers components from."""
        root = tmp_path / "shell"
        root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [file_router_config_entry(pages_dir=root)],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "widgets",
                    }
                ],
            }
        ):
            next_framework_settings.reload()
            pairs = iter_pages_roots_with_components_folder_names()

        assert pairs == [(root.resolve(), "widgets")]

    def test_backend_without_a_components_folder_contributes_no_pair(
        self, tmp_path
    ) -> None:
        """A router that registers no components is watched for pages only."""
        root = tmp_path / "shell"
        root.mkdir()
        with (
            patch.object(
                RouterFactory, "create_backend", return_value=RootPagesRouter([root])
            ),
            override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "elsewhere.Backend"}]}
            ),
        ):
            next_framework_settings.reload()
            assert get_pages_directories_for_watch() == [root.resolve()]
            assert iter_pages_roots_with_components_folder_names() == []

    def test_a_root_two_backends_reach_pairs_once(self, tmp_path) -> None:
        """One tree mounted twice under one folder name yields one pair."""
        root = tmp_path / "shell"
        root.mkdir()
        entry = file_router_config_entry(pages_dir=root)
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry, dict(entry)]}):
            next_framework_settings.reload()
            pairs = iter_pages_roots_with_components_folder_names()

        assert pairs == [(root.resolve(), "_components")]

    def test_non_dict_entries_are_skipped(self) -> None:
        """Non-dict entries name no backend and produce no pair."""
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": ["not a dict"]}):
            next_framework_settings.reload()
            assert iter_pages_roots_with_components_folder_names() == []
