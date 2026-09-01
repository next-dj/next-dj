from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.utils import autoreload
from django.utils.autoreload import (
    StatReloader as DjangoStatReloader,
    autoreload_started,
)

from next.apps import autoreload as next_autoreload, components as next_components
from next.components import DummyBackend, FileComponentsBackend, components_manager
from next.pages import loaders as pages_loaders
from next.pages.watch import get_pages_directories_for_watch
from next.server import NextStatReloader
from next.static import get_static_manager
from next.urls import RouterFactory, router_manager
from tests.support import MalformedRootsRouter, RaisingRootsRouter, importable_dir


if TYPE_CHECKING:
    from pathlib import Path


def _page_backend_entry(
    *, dirs: list[str] | None = None, app_dirs: bool = False
) -> dict[str, object]:
    """One ``PAGE_BACKENDS`` entry for the file router."""
    return {
        "BACKEND": "next.urls.FileRouterBackend",
        "PAGES_DIR": "pages",
        "APP_DIRS": app_dirs,
        "DIRS": list(dirs or []),
        "OPTIONS": {},
    }


def _page_watch_paths(sender: object) -> list[Path]:
    """Return the directories registered for the ``page.py`` glob."""
    return [path for path, glob in sender.watch_calls if glob == "**/page.py"]


def _component_backend_config(
    root: Path, component_name: str, marker: Path
) -> dict[str, object]:
    """Write a component tree whose ``component.py`` touches ``marker`` on import."""
    comp_dir = root / "_components" / component_name
    comp_dir.mkdir(parents=True)
    (comp_dir / "component.djx").write_text("<div/>")
    (comp_dir / "component.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
    )
    return {"DIRS": [str(root / "_components")], "COMPONENTS_DIR": "_components"}


class TestNextFrameworkConfig:
    """Tests for NextFrameworkConfig.ready() behavior."""

    def test_ready_patches_stat_reloader(self) -> None:
        """After app load, django.utils.autoreload.StatReloader is NextStatReloader."""
        assert autoreload.StatReloader is NextStatReloader

    def test_autoreload_started_watches_the_configured_pages_directory(
        self, mock_autoreload_sender, tmp_path
    ) -> None:
        """A ``DIRS`` root reaches ``watch_dir`` under the ``page.py`` glob."""
        root = tmp_path / "shell"
        root.mkdir()
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [_page_backend_entry(dirs=[str(root)])]}
        ):
            autoreload_started.send(sender=mock_autoreload_sender)

        assert _page_watch_paths(mock_autoreload_sender) == [root.resolve()]

    def test_autoreload_started_watches_an_app_pages_tree(
        self, mock_autoreload_sender, tmp_path, settings
    ) -> None:
        """An installed app's tree reaches ``watch_dir`` when ``APP_DIRS`` is on."""
        app_pages = tmp_path / "shop" / "pages"
        app_pages.mkdir(parents=True)
        (tmp_path / "shop" / "__init__.py").write_text("")
        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop"]
            with override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": [_page_backend_entry(app_dirs=True)]}
            ):
                autoreload_started.send(sender=mock_autoreload_sender)

        assert _page_watch_paths(mock_autoreload_sender) == [app_pages.resolve()]

    def test_autoreload_started_leaves_an_unrouted_app_tree_alone(
        self, mock_autoreload_sender, tmp_path, settings
    ) -> None:
        """Without ``APP_DIRS`` the app tree routes nothing, so it never reaches watch_dir."""
        app_pages = tmp_path / "shop" / "pages"
        app_pages.mkdir(parents=True)
        (tmp_path / "shop" / "__init__.py").write_text("")
        root = tmp_path / "shell"
        root.mkdir()
        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop"]
            with override_settings(
                NEXT_FRAMEWORK={
                    "PAGE_BACKENDS": [_page_backend_entry(dirs=[str(root)])]
                }
            ):
                autoreload_started.send(sender=mock_autoreload_sender)

        assert _page_watch_paths(mock_autoreload_sender) == [root.resolve()]

    def test_autoreload_started_survives_a_malformed_router(
        self, mock_autoreload_sender, tmp_path
    ) -> None:
        """`runserver` boots through a backend that answers the wrong shape."""
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
                        _page_backend_entry(dirs=[str(root)]),
                    ]
                }
            ),
        ):
            autoreload_started.send(sender=mock_autoreload_sender)

        assert _page_watch_paths(mock_autoreload_sender) == [root.resolve()]

    def test_autoreload_started_survives_a_router_that_raises(
        self, mock_autoreload_sender, tmp_path
    ) -> None:
        """`runserver` boots through a third-party backend whose tree listing fails."""
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
                        _page_backend_entry(dirs=[str(root)]),
                    ]
                }
            ),
        ):
            autoreload_started.send(sender=mock_autoreload_sender)

        assert _page_watch_paths(mock_autoreload_sender) == [root.resolve()]

    def test_autoreload_started_never_registers_djx_globs(
        self, mock_autoreload_sender
    ) -> None:
        """``watch_dir`` patterns from next must not match ``.djx`` (lazy templates)."""
        autoreload_started.send(sender=mock_autoreload_sender)
        for _path, glob in mock_autoreload_sender.watch_calls:
            assert ".djx" not in glob, f"unexpected djx glob: {glob!r}"


class TestARouterReloadDropsThePageRootMemos:
    """Three layers memoise what the routers report, and a reload moves all three."""

    def test_every_page_root_memo_reads_the_tree_that_appeared(self, tmp_path) -> None:
        """A reload from code touches no setting, so each memo needs the signal.

        The watch layer holds the routers off `DEBUG`, and the loaders and the
        static manager hold what those routers reported.
        """
        with override_settings(
            BASE_DIR=tmp_path,
            DEBUG=False,
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [_page_backend_entry()]},
        ):
            manager = get_static_manager()
            assert get_pages_directories_for_watch() == []
            assert pages_loaders._page_roots() == ()
            assert manager.page_roots() == ()

            (tmp_path / "pages").mkdir()
            router_manager.reload()
            tree = (tmp_path / "pages").resolve()

            assert get_pages_directories_for_watch() == [tree]
            assert pages_loaders._page_roots() == (tree,)
            assert manager.page_roots() == (tree,)


class TestAutoreloadInstallIdempotent:
    """`install()` is safe to call repeatedly and logs unknown overrides."""

    def test_second_install_is_noop(self) -> None:

        before = autoreload.StatReloader
        next_autoreload.install()
        assert autoreload.StatReloader is before

    def test_install_warns_on_incompatible_override(self, caplog) -> None:

        original = autoreload.StatReloader

        class Unrelated:
            pass

        try:
            autoreload.StatReloader = Unrelated  # type: ignore[misc]
            with caplog.at_level("WARNING", logger="next.apps.autoreload"):
                next_autoreload.install()
            assert autoreload.StatReloader is Unrelated
            assert any(
                "not a StatReloader subclass" in rec.message for rec in caplog.records
            )
        finally:
            autoreload.StatReloader = original  # type: ignore[misc]
            assert issubclass(autoreload.StatReloader, DjangoStatReloader)

    def test_uninstall_restores_original_and_disconnects(self) -> None:
        """`uninstall()` puts back the previous `StatReloader` and detaches the signal."""
        # Grab the true Django `StatReloader` class from `NextStatReloader.__bases__`
        # because the module attribute has already been monkey-patched by `ready()`.
        real_django_stat_reloader = NextStatReloader.__bases__[0]

        class Placeholder(real_django_stat_reloader):  # type: ignore[misc,valid-type]
            pass

        original = autoreload.StatReloader
        autoreload.StatReloader = Placeholder  # type: ignore[misc]
        next_autoreload._state.original_reloader = None
        next_autoreload._state.watcher_connected = False
        try:
            next_autoreload.install()
            assert autoreload.StatReloader is NextStatReloader
            assert next_autoreload._state.original_reloader is Placeholder
            next_autoreload.uninstall()
            assert autoreload.StatReloader is Placeholder
            # Calling uninstall() a second time is a no-op (both guards fall through).
            next_autoreload.uninstall()
        finally:
            autoreload.StatReloader = original  # type: ignore[misc]
            next_autoreload._state.original_reloader = None
            next_autoreload._state.watcher_connected = False
            next_autoreload.install()


class TestStaticfilesInstall:
    """``next.apps.staticfiles.install`` wires the static files finder."""

    def test_next_static_files_finder_in_finders(self) -> None:
        """``NextStaticFilesFinder`` is present in ``STATICFILES_FINDERS`` after ready()."""
        finders = getattr(settings, "STATICFILES_FINDERS", [])
        assert "next.static.NextStaticFilesFinder" in finders


class TestTemplatesInstall:
    """``next.apps.templates.install`` adds template tag builtins."""

    def test_template_builtins_include_next_tags(self) -> None:
        """next.templatetags modules are present in ``TEMPLATES[0].OPTIONS.builtins``."""
        builtins = settings.TEMPLATES[0].get("OPTIONS", {}).get("builtins", [])
        assert "next.templatetags.components" in builtins
        assert "next.templatetags.forms" in builtins
        assert "next.templatetags.next_static" in builtins


class TestComponentsInstall:
    """``next.apps.components.install`` discovers component backends on startup."""

    def test_components_manager_backends_loaded(self) -> None:
        """``components_manager._backends`` is populated after ``ready()``."""
        assert components_manager._backends is not None

    def test_install_discovers_and_imports_component_modules(
        self, tmp_path: Path
    ) -> None:
        """``install()`` populates the registry and imports each ``component.py``."""
        marker = tmp_path / "imported.txt"
        config = _component_backend_config(tmp_path, "widget", marker)
        try:
            with override_settings(NEXT_FRAMEWORK={"COMPONENT_BACKENDS": [config]}):
                next_components.install()
                backend = components_manager._backends[0]
                assert isinstance(backend, FileComponentsBackend)
                assert len(backend._registry) == 1
            assert marker.read_text() == "loaded"
        finally:
            components_manager.reload()

    def test_install_asks_every_backend_through_the_contract(self) -> None:
        # A backend resolving names on demand implements no eager pass, and the
        # hook it inherits is a public one, not a private attribute probed for.
        config = {"BACKEND": "next.components.DummyBackend"}
        try:
            with override_settings(NEXT_FRAMEWORK={"COMPONENT_BACKENDS": [config]}):
                next_components.install()
                assert isinstance(components_manager._backends[0], DummyBackend)
        finally:
            components_manager.reload()

    def test_install_lazy_discovers_without_importing(self, tmp_path: Path) -> None:
        """With ``LAZY_COMPONENT_MODULES`` ``install()`` discovers but defers imports."""
        marker = tmp_path / "imported.txt"
        config = _component_backend_config(tmp_path, "lazy_widget", marker)
        try:
            with override_settings(
                NEXT_FRAMEWORK={
                    "COMPONENT_BACKENDS": [config],
                    "LAZY_COMPONENT_MODULES": True,
                }
            ):
                next_components.install()
                backend = components_manager._backends[0]
                assert isinstance(backend, FileComponentsBackend)
                assert len(backend._registry) == 1
                assert not marker.exists()
        finally:
            components_manager.reload()
