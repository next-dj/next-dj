from __future__ import annotations

import ast
import gc
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from unittest.mock import patch

import pytest
from django.apps import apps
from django.conf import settings
from django.core.signals import setting_changed
from django.db.backends.signals import connection_created
from django.test import override_settings
from django.urls import Resolver404, resolve
from django.utils import autoreload
from django.utils.autoreload import (
    StatReloader as DjangoStatReloader,
    autoreload_started,
)

import next as next_package
from next.apps import (
    autodiscover as next_autodiscover,
    autoreload as next_autoreload,
    components as next_components,
    staticfiles as next_staticfiles,
    templates as next_templates,
)
from next.apps.areas import CHECK_MODULES, MANIFESTS
from next.components import FileComponentsBackend, components_manager
from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.defer.assembly import inline_sink
from next.defer.connections import connection_sources
from next.defer.manager import defer_manager
from next.deps import resolver
from next.deps.introspect import _signature_cache
from next.deps.resolver import apply_resolver_setting
from next.discovery import get_pages_directories_for_watch
from next.manifest import Bind
from next.pages import loaders as pages_loaders
from next.pages.ports import PageScanImpl
from next.ports import (
    PortSlot,
    deferrals_slot,
    page_scan_slot,
    page_scripts,
    page_views_slot,
    route_access_slot,
    seo_routes_slot,
    server_load_slot,
    static_assets_slot,
)
from next.seo.manager import seo_manager
from next.seo.ports import SeoRoutesImpl
from next.server import NextStatReloader
from next.static import get_static_manager
from next.static.ports import StaticAssetsImpl
from next.urls import route_manager
from next.urls.manager import seo_routes_version
from next.urls.ports import RouteAccessImpl
from next.urls.signals import routes_reloaded
from next.views import page_view
from tests.support import (
    DUMMY_COMPONENTS_BACKEND,
    DummyComponentsBackend,
    MalformedRootsRouter,
    RaisingRootsRouter,
    importable_dir,
    routed,
    throwaway_apps,
    write_tree,
)


if TYPE_CHECKING:
    from django.dispatch import Signal


_PROCESS_SLOTS = (
    page_scan_slot,
    route_access_slot,
    seo_routes_slot,
    static_assets_slot,
)


def _page_backend_entry(
    *, dirs: list[str] | None = None, app_dirs: bool = False
) -> dict[str, object]:
    """One ``PAGE_BACKENDS`` entry for the file router."""
    return {
        "BACKEND": "next.urls.FilePageBackend",
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

    def test_the_config_holds_the_check_catalogue(self) -> None:
        assert apps.get_app_config("next").check_modules is CHECK_MODULES

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
        """Without ``APP_DIRS`` the app tree routes nothing and reaches no watch_dir."""
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
        build = RouteAccessImpl().create_backend

        def malformed_first(config: dict) -> object:
            if config["BACKEND"] == "broken.Backend":
                return MalformedRootsRouter([tmp_path / "unreported"])
            return build(config)

        with (
            patch.object(
                RouteAccessImpl, "create_backend", side_effect=malformed_first
            ),
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
        build = RouteAccessImpl().create_backend

        def broken_first(config: dict) -> object:
            if config["BACKEND"] == "broken.Backend":
                return RaisingRootsRouter()
            return build(config)

        with (
            patch.object(RouteAccessImpl, "create_backend", side_effect=broken_first),
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
        """A reload from code touches no setting, so each memo needs the signal."""
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
            route_manager.reload()
            tree = (tmp_path / "pages").resolve()

            assert get_pages_directories_for_watch() == [tree]
            assert pages_loaders._page_roots() == (tree,)
            assert manager.page_roots() == ((tree, "pages"),)

    def test_a_reload_drops_the_memos_keyed_by_a_callable(self) -> None:
        """A saved `page.py` mints new functions, so the DI memos go with it."""

        def fn(a: int = 1) -> None:
            return None

        resolver.resolve_dependencies(fn)
        assert _signature_cache
        assert resolver._plan_cache
        route_manager.reload()
        assert not _signature_cache
        assert not resolver._plan_cache


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
        # `ready()` patched the module attribute, so read Django's class off the bases.
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
        """``NextStaticFilesFinder`` sits in ``STATICFILES_FINDERS`` after ready()."""
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
        # A backend resolving names on demand has no eager pass, only an inherited hook.
        config = {"BACKEND": DUMMY_COMPONENTS_BACKEND}
        try:
            with override_settings(NEXT_FRAMEWORK={"COMPONENT_BACKENDS": [config]}):
                next_components.install()
                assert isinstance(
                    components_manager._backends[0], DummyComponentsBackend
                )
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


def _appending(log: Path, entry: str) -> str:
    """Return a module body that appends ``entry`` to ``log`` when it executes."""
    return (
        "from pathlib import Path\n\n"
        f"with Path({str(log)!r}).open('a') as log:\n"
        f"    log.write({entry + chr(10)!r})\n"
    )


class TestAppModulesLoadBeforeComponents:
    """``ready()`` imports every app ``deps.py`` before any ``component.py`` runs."""

    def test_deps_module_runs_before_an_eager_component_module(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "order.log"
        root = tmp_path / "_components"
        probe = root / "probe"
        probe.mkdir(parents=True)
        (probe / "component.djx").write_text("<div/>")
        (probe / "component.py").write_text(_appending(log, "component"))
        backend = {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}
        (tmp_path / "apps").mkdir()
        config = apps.get_app_config("next")
        try:
            with (
                throwaway_apps(tmp_path / "apps") as make_app,
                override_settings(
                    NEXT_FRAMEWORK={
                        "COMPONENT_BACKENDS": [backend],
                        "LAZY_COMPONENT_MODULES": False,
                    }
                ),
            ):
                make_app(deps=_appending(log, "deps"))
                config.ready()
        finally:
            components_manager.reload()
        assert log.read_text().splitlines() == ["deps", "component"]


class _Recorded:
    """A port binding or startup step that notes its run before doing it."""

    def __init__(self, made: list[str], name: str, step: object) -> None:
        self._made, self._name, self._step = made, name, step

    def apply(self) -> None:
        self._made.append(self._name)
        self._step.apply()

    def __call__(self) -> None:
        self._made.append(self._name)
        self._step()


def _recorded(made: list[str]) -> tuple[object, ...]:
    """Return every manifest with its bindings and startup steps noting their runs."""
    return tuple(
        replace(
            manifest,
            ports=tuple(
                _Recorded(made, f"{manifest.name}.port", binding)
                for binding in manifest.ports
            ),
            startup=tuple(
                _Recorded(made, step.__qualname__, step) for step in manifest.startup
            ),
        )
        for manifest in MANIFESTS
    )


class TestDependencyResolverInstall:
    """``ready()`` composes every area before any step that imports user modules."""

    INSTALLS: ClassVar[tuple[object, ...]] = (
        next_autoreload,
        next_templates,
        next_staticfiles,
        next_autodiscover,
        next_components,
    )

    def test_ready_applies_the_setting_before_it_imports_user_modules(self) -> None:
        """The resolver and every port come ahead of every step that imports user code."""
        config = apps.get_app_config("next")
        made: list[str] = []
        with ExitStack() as stack:
            stack.enter_context(patch("next.apps.config.MANIFESTS", _recorded(made)))
            for module in self.INSTALLS:
                stack.enter_context(
                    patch.object(
                        module,
                        "install",
                        side_effect=lambda name=module.__name__: made.append(name),
                    )
                )
            config.ready()
        installs = [module.__name__ for module in self.INSTALLS]
        composed = made[: -len(installs)]
        assert made[-len(installs) :] == installs
        assert "apply_resolver_setting" in composed
        assert composed.count("seo.port") == 1
        assert composed.index("seo.port") < composed.index("SeoRoutesVersion.move")

    def test_the_deps_area_applies_the_resolver_setting(self) -> None:
        [deps] = [manifest for manifest in MANIFESTS if manifest.name == "deps"]
        assert deps.startup == (apply_resolver_setting,)

    def test_ready_binds_every_port_before_discovery_can_fail(self) -> None:
        """A discovery failure leaves no process behind with an unbound port."""
        config = apps.get_app_config("next")
        with (
            patch.object(next_components, "install", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            config.ready()
        bound = [binding for manifest in MANIFESTS for binding in manifest.ports]
        assert all(binding.slot.get() is binding.impl for binding in bound)

    @pytest.mark.parametrize(
        ("slot", "bound"),
        [
            pytest.param(deferrals_slot, inline_sink, id="deferrals"),
            pytest.param(page_views_slot, page_view, id="views"),
            pytest.param(server_load_slot, defer_manager, id="load"),
        ],
    )
    def test_ready_binds_the_composed_callables(
        self, slot: PortSlot[object], bound: object
    ) -> None:
        """The sink, the file router's view factory and the shed's pool load."""
        apps.get_app_config("next").ready()
        assert slot.get() is bound

    def test_a_second_ready_rebinds_the_ports_without_doubling_the_wiring(self) -> None:
        """Django readies an app once, but a test process calls `ready` repeatedly."""
        config = apps.get_app_config("next")
        connected = len(routes_reloaded.receivers)

        config.ready()

        assert len(routes_reloaded.receivers) == connected
        named = [p.name for manifest in MANIFESTS for p in manifest.providers]
        assert sorted(provider.name for provider in page_scripts) == sorted(named)
        assert [type(slot.get()) for slot in _PROCESS_SLOTS] == [
            PageScanImpl,
            RouteAccessImpl,
            SeoRoutesImpl,
            StaticAssetsImpl,
        ]

    def test_ready_bumps_the_seo_routes_version(self) -> None:
        """URL patterns built before the SEO port was bound lack the SEO routes."""
        before = seo_routes_version.value
        apps.get_app_config("next").ready()
        assert seo_routes_version.value not in {0, before}

    def test_patterns_built_before_ready_gain_the_seo_routes(
        self, tmp_path: Path
    ) -> None:
        """An app that resolves a URL in its own `ready()` cannot hide the sitemap."""
        slot = PortSlot["SeoRoutesImpl"]("seo routes port")
        manifests = tuple(
            replace(manifest, ports=(Bind(slot, SeoRoutesImpl()),))
            if manifest.name == "seo"
            else manifest
            for manifest in MANIFESTS
        )
        with (
            routed(write_tree(tmp_path / "pages", sitemap="")),
            patch("next.apps.config.MANIFESTS", manifests),
            patch("next.urls.manager.seo_routes_slot", slot),
        ):
            with pytest.raises(Resolver404):
                resolve("/sitemap.xml")
            apps.get_app_config("next").ready()
            assert resolve("/sitemap.xml").route == "sitemap.xml"

    def test_a_router_reload_resets_the_seo_manager(self) -> None:
        """The SEO routes follow the routers, so a router reload drops their memo."""
        apps.get_app_config("next").ready()
        before = seo_manager.version

        route_manager.reload()

        assert seo_manager.version != before


def _live_count(signal: Signal) -> int:
    """Count the receivers still alive, since a collected weak one drops at random."""
    gc.collect()
    signal._clear_dead_receivers()
    return len(signal.receivers)


class TestReceiversConnectInReady:
    """Every invalidation receiver is wired by `ready()`, once, never at import time."""

    @pytest.mark.parametrize(
        "signal",
        [setting_changed, settings_reloaded, routes_reloaded, connection_created],
    )
    def test_a_second_ready_adds_no_receiver(self, signal) -> None:
        before = _live_count(signal)
        apps.get_app_config("next").ready()
        assert _live_count(signal) == before

    def test_a_settings_reload_drops_the_detected_sources(self) -> None:
        connection_sources()
        next_framework_settings.reload()
        assert connection_sources.cache_info().currsize == 0

    def test_no_module_connects_a_receiver_at_import_time(self) -> None:
        connected = [
            path
            for path in Path(next_package.__file__).parent.rglob("*.py")
            if any(
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "connect"
                for node in ast.parse(path.read_text()).body
            )
        ]
        assert connected == []
