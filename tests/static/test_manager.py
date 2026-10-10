from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.test import RequestFactory, override_settings
from django.utils.functional import empty

from next.static import (
    StaticBackend,
    StaticCollector,
    StaticFilesBackend,
    StaticManager,
    default_kinds,
    default_manager,
    get_static_manager,
    reset_default_manager,
)
from next.static.manager import (
    DefaultStaticManager,
    forget_manager_backend_urls,
    forget_manager_page_roots,
)
from tests.support import (
    PREFIXED_BACKENDS,
    PREFIXING_STATIC_BACKEND,
    component_info,
    page_naming_one_style,
    static_names_resolved_by,
)


if TYPE_CHECKING:
    from pathlib import Path

    from next.components import ComponentInfo


CSS_URL = "https://cdn.example.com/a.css"
LITERAL_BACKENDS = {
    "STATIC_BACKENDS": [{"BACKEND": "tests.static.test_manager.LiteralStaticBackend"}]
}

VERSIONED = {"STATIC_VERSION": "2026.9.19"}

VERSIONED_AND_PREFIXED = {
    "STATIC_VERSION": "2026.9.19",
    "STATIC_BACKENDS": [{"BACKEND": PREFIXING_STATIC_BACKEND}],
}

PAIRED_BACKENDS = {
    "STATIC_BACKENDS": [
        {"BACKEND": "next.static.StaticFilesBackend"},
        {"BACKEND": PREFIXING_STATIC_BACKEND},
    ]
}


class LiteralStaticBackend(StaticBackend):
    """Backend outside the staticfiles family, so every reference stays literal."""

    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Return the URL the logical name spells without asking storage."""
        del source_path
        return f"/literal/{logical_name}{default_kinds.extension(kind)}"


def _storage_url(name: str) -> str:
    return f"/static/{name}"


class TestEnsureBackends:
    def test_default_backend_is_static_files(
        self, fresh_manager: StaticManager
    ) -> None:
        assert isinstance(fresh_manager.default_backend, StaticFilesBackend)

    def test_backends_expose_the_configured_count(
        self, fresh_manager: StaticManager
    ) -> None:
        assert len(fresh_manager.backends) == 1

    def test_page_roots_cached(self, fresh_manager: StaticManager) -> None:
        roots1 = fresh_manager.page_roots()
        roots2 = fresh_manager.page_roots()
        assert roots1 is roots2


class TestPageRootsFollowTheRouters:
    """The trees the manager serves come from the watch layer and go with it."""

    def test_page_roots_are_taken_as_the_watch_layer_spells_them(
        self, fresh_manager: StaticManager, tmp_path: Path
    ) -> None:
        """The watch layer returns resolved roots, so a lookup resolves once."""
        spelling = tmp_path / "site" / ".." / "site"
        with mock.patch(
            "next.static.manager.get_pages_directories_for_watch",
            return_value=[spelling],
        ):
            assert fresh_manager.page_roots() == (spelling,)

    def test_forgetting_the_page_roots_reads_them_again(
        self, fresh_manager: StaticManager, tmp_path: Path
    ) -> None:
        """A tree that appeared reaches the manager, resolver memo and all."""
        first = tmp_path / "one"
        second = tmp_path / "two"
        with mock.patch(
            "next.static.manager.get_pages_directories_for_watch", return_value=[first]
        ):
            assert fresh_manager.page_roots() == (first,)
            stale_discovery = fresh_manager.discovery
        with mock.patch(
            "next.static.manager.get_pages_directories_for_watch",
            return_value=[first, second],
        ):
            assert fresh_manager.page_roots() == (first,)
            fresh_manager.forget_page_roots()

            assert fresh_manager.page_roots() == (first, second)
        assert fresh_manager.discovery is not stale_discovery


class TestForgetManagerPageRoots:
    """The module-level hook a router reload sends the manager."""

    def test_an_unbuilt_handle_is_left_alone(self, reset_default: None) -> None:
        """A manager nothing has built yet reads the trees fresh anyway."""
        forget_manager_page_roots()

        assert default_manager._wrapped is empty

    def test_a_built_manager_reads_the_trees_again(
        self, reset_default: None, tmp_path: Path
    ) -> None:
        """The live manager drops what it held without being replaced."""
        manager = get_static_manager()
        with mock.patch(
            "next.static.manager.get_pages_directories_for_watch", return_value=[]
        ):
            assert manager.page_roots() == ()

        with mock.patch(
            "next.static.manager.get_pages_directories_for_watch",
            return_value=[tmp_path],
        ):
            forget_manager_page_roots()

            assert manager.page_roots() == (tmp_path,)
        assert get_static_manager() is manager


class TestForgetManagerBackendUrls:
    """The hook a rebuilt staticfiles storage sends every configured backend."""

    def test_an_unbuilt_handle_is_left_alone(self, reset_default: None) -> None:
        """A manager nothing has built yet holds no backend and no memo."""
        forget_manager_backend_urls()

        assert default_manager._wrapped is empty

    def test_a_manifest_setting_reaches_every_backend(
        self, reset_default: None
    ) -> None:
        """Not just the first one, which is all the render pipeline reads."""
        with override_settings(NEXT_FRAMEWORK=PAIRED_BACKENDS):
            manager = get_static_manager()
            manager._ensure_backends()
            for position, backend in enumerate(manager._backends):
                backend._url_cache[("a", ".css")] = f"/static/next/a{position}.css"

            with override_settings(STATIC_URL="/assets/"):
                assert [len(backend._url_cache) for backend in manager._backends] == [
                    0,
                    0,
                ]

    def test_a_manifest_setting_also_drops_the_runtime_bundle_url(
        self, reset_default: None
    ) -> None:
        """The script tag and the preload hint read that URL from one storage."""
        manager = get_static_manager()
        before = manager.script_builder().url

        with override_settings(STATIC_URL="/assets/"):
            after = manager.script_builder().url

        assert before.startswith("/static/")
        assert after.startswith("/assets/")

    def test_an_unrelated_setting_keeps_every_memo(self, reset_default: None) -> None:
        """Only a setting that rebuilds the storage moves the URLs it answered."""
        with override_settings(NEXT_FRAMEWORK=PAIRED_BACKENDS):
            manager = get_static_manager()
            manager._ensure_backends()
            for backend in manager._backends:
                backend._url_cache[("a", ".css")] = "/static/next/a.css"

            with override_settings(LANGUAGE_CODE="fr"):
                held = [
                    backend._url_cache.get(("a", ".css"))
                    for backend in manager._backends
                ]
        assert held == ["/static/next/a.css"] * 2


class TestAppListChanges:
    """An `APP_DIRS` router routes new trees when the app list moves."""

    def test_an_app_list_change_drops_the_cached_page_roots(
        self, reset_default: None, tmp_path: Path
    ) -> None:
        """The override reaches the live manager, including its resolver memo."""
        manager = get_static_manager()
        with mock.patch(
            "next.static.manager.get_pages_directories_for_watch", return_value=[]
        ):
            assert manager.page_roots() == ()
            stale_discovery = manager.discovery

        with (
            mock.patch(
                "next.static.manager.get_pages_directories_for_watch",
                return_value=[tmp_path],
            ),
            override_settings(INSTALLED_APPS=["django.contrib.contenttypes"]),
        ):
            assert manager.page_roots() == (tmp_path,)
            assert manager.discovery is not stale_discovery

    def test_an_unrelated_setting_change_keeps_the_cached_page_roots(
        self, reset_default: None, tmp_path: Path
    ) -> None:
        """Only the app list moves what an `APP_DIRS` router reports."""
        manager = get_static_manager()
        with mock.patch(
            "next.static.manager.get_pages_directories_for_watch",
            return_value=[tmp_path],
        ):
            assert manager.page_roots() == (tmp_path,)

        with (
            mock.patch(
                "next.static.manager.get_pages_directories_for_watch", return_value=[]
            ),
            override_settings(LANGUAGE_CODE="fr"),
        ):
            assert manager.page_roots() == (tmp_path,)


class TestReloadConfig:
    def test_reload_rebuilds_backends(self) -> None:
        manager = StaticManager()
        manager._ensure_backends()
        initial = manager.default_backend
        manager.reload()
        assert manager.default_backend is not initial

    def test_reload_clears_discovery_cache(self) -> None:
        manager = StaticManager()
        _ = manager.discovery
        manager.reload()
        assert manager._discovery is None

    def test_reload_clears_script_builder(self) -> None:
        manager = StaticManager()
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            before = manager.script_builder()
            assert manager.script_builder() is before
            manager.reload()
            assert manager.script_builder() is not before

    def test_invalid_backend_falls_back(self) -> None:
        manager = StaticManager()
        with override_settings(
            NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": "builtins.dict"}]}
        ):
            manager.reload()
        assert isinstance(manager.default_backend, StaticFilesBackend)

    def test_class_outside_the_family_is_logged_and_skipped(self, caplog) -> None:
        manager = StaticManager()
        with (
            caplog.at_level(logging.ERROR, logger="next.backends"),
            override_settings(
                NEXT_FRAMEWORK={
                    "STATIC_BACKENDS": [
                        {"BACKEND": "builtins.dict"},
                        {"BACKEND": "next.static.StaticFilesBackend"},
                    ]
                }
            ),
        ):
            manager.reload()
        assert len(manager.backends) == 1
        assert "is not a StaticBackend subclass" in caplog.text

    def test_entry_without_backend_uses_the_default_class(self) -> None:
        manager = StaticManager()
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [{"OPTIONS": {"css_tag": '<link href="{url}">'}}]
            }
        ):
            manager.reload()
        backend = manager.default_backend
        assert isinstance(backend, StaticFilesBackend)
        assert backend.render_link_tag("x") == '<link href="x">'

    def test_non_dict_entries_are_ignored(self) -> None:
        manager = StaticManager()
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    "nope",
                    {"BACKEND": "next.static.StaticFilesBackend"},
                ]
            }
        ):
            manager.reload()
        assert len(manager.backends) == 1

    def test_empty_backends_seeds_default(self) -> None:
        manager = StaticManager()
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": []}):
            manager.reload()
        assert len(manager.backends) == 1


class TestBackendsLoadedOnce:
    """Settings are read once, whatever the load leaves behind."""

    def test_empty_settings_do_not_reload_on_every_access(self) -> None:
        manager = StaticManager()
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": []}):
            manager._ensure_backends()
            with mock.patch.object(
                StaticManager, "reload", autospec=True
            ) as reload_mock:
                manager._ensure_backends()
                assert len(manager.backends) == 1
        reload_mock.assert_not_called()

    def test_unusable_settings_do_not_reload_on_every_access(self) -> None:
        manager = StaticManager()
        with override_settings(
            NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": "builtins.dict"}]}
        ):
            manager._ensure_backends()
            seeded = manager.default_backend
            manager._ensure_backends()
        assert manager.default_backend is seeded

    def test_the_load_is_marked_after_the_derived_state(self) -> None:
        manager = StaticManager()
        loaded_while_resolving: list[bool] = []
        resolve = StaticManager._resolve_collector_strategies

        def spy(self: StaticManager) -> None:
            loaded_while_resolving.append(self._loaded)
            resolve(self)

        with mock.patch.object(StaticManager, "_resolve_collector_strategies", spy):
            manager._ensure_backends()
        assert loaded_while_resolving == [False]
        assert manager._loaded

    def test_a_caller_emptying_the_list_does_not_trigger_a_reload(self) -> None:
        manager = StaticManager()
        manager._ensure_backends()
        manager._backends.clear()
        manager._ensure_backends()
        assert manager._backends == []


class TestChunkUrlMemo:
    """Each chunk URL is resolved once per storage and again after a rebuild."""

    @pytest.mark.parametrize("name", ["scripts", "sse", "csrf", "poll", "dev"])
    def test_a_rebuilt_storage_resolves_it_again(
        self, fresh_manager: StaticManager, name: str
    ) -> None:
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ) as url:
            first = fresh_manager.chunk_url(name)
            fresh_manager.chunk_url(name)
            fresh_manager.forget_backend_urls()
            fresh_manager.chunk_url(name)
        assert url.call_count == 2
        assert first == f"/static/next/next.{name}.min.js"


class TestAssetUrlHook:
    """The manager hands out the URL the pipeline would render for an asset."""

    def test_identity_backend_returns_the_url_unchanged(
        self, fresh_manager: StaticManager
    ) -> None:
        url = fresh_manager.asset_url(CSS_URL, request=RequestFactory().get("/"))
        assert url == CSS_URL

    def test_identity_backend_is_never_asked(
        self, fresh_manager: StaticManager
    ) -> None:
        with mock.patch.object(
            fresh_manager.default_backend,
            "asset_url",
            wraps=fresh_manager.default_backend.asset_url,
        ) as asset_url:
            fresh_manager.asset_url(CSS_URL)
        assert asset_url.call_count == 0

    def test_rewriting_backend_stamps_the_prefix(self) -> None:
        with override_settings(NEXT_FRAMEWORK=PREFIXED_BACKENDS):
            url = StaticManager().asset_url(
                "/static/next/a.css", request=RequestFactory().get("/")
            )
        assert url == "/pfx/static/next/a.css"


class TestResolveUrlFacade:
    """The manager funnels every authored reference to the first backend."""

    def test_a_name_resolves_through_the_default_backend(
        self, fresh_manager: StaticManager
    ) -> None:
        with static_names_resolved_by({"css/theme.css": "/static/css/theme.css"}):
            assert fresh_manager.resolve_url("css/theme.css") == "/static/css/theme.css"

    def test_a_ready_url_passes_through(self, fresh_manager: StaticManager) -> None:
        assert fresh_manager.resolve_url(CSS_URL) == CSS_URL

    def test_the_default_backend_property_builds_the_list_it_reads(
        self, fresh_manager: StaticManager
    ) -> None:
        """`resolve_url` owns no lazy load of its own, so the property has to."""
        assert fresh_manager._backends == []
        assert isinstance(fresh_manager.default_backend, StaticFilesBackend)
        assert fresh_manager.resolve_url(CSS_URL) == CSS_URL

    def test_a_backend_leaving_the_hook_alone_keeps_the_reference_literal(self) -> None:
        with override_settings(NEXT_FRAMEWORK=LITERAL_BACKENDS):
            assert StaticManager().resolve_url("css/theme.css") == "css/theme.css"


class TestProjectStaticVersion:
    """`STATIC_VERSION` stamps every URL the façade hands out."""

    def test_the_project_version_reaches_a_url_the_backend_leaves_alone(self) -> None:
        with override_settings(NEXT_FRAMEWORK=VERSIONED):
            url = StaticManager().asset_url("/static/a.css")
        assert url == "/static/a.css?v=2026.9.19"

    def test_the_version_lands_on_the_rewritten_url_exactly_once(self) -> None:
        """The backend hook runs first, so it never receives a versioned URL."""
        with override_settings(NEXT_FRAMEWORK=VERSIONED_AND_PREFIXED):
            url = StaticManager().asset_url(
                "/static/a.css", request=RequestFactory().get("/")
            )
        assert url == "/pfx/static/a.css?v=2026.9.19"
        assert url.count("?v=") == 1

    def test_a_per_call_version_replaces_the_project_one(self) -> None:
        with override_settings(NEXT_FRAMEWORK=VERSIONED):
            url = StaticManager().asset_url("/static/a.css", version="rc1")
        assert url == "/static/a.css?v=rc1"

    def test_a_per_call_version_lands_without_a_project_one(
        self, fresh_manager: StaticManager
    ) -> None:
        assert fresh_manager.asset_url("/static/a.css", version=7) == (
            "/static/a.css?v=7"
        )

    def test_a_reload_reads_the_version_again(self) -> None:
        manager = StaticManager()
        assert manager.asset_url("/static/a.css") == "/static/a.css"
        with override_settings(NEXT_FRAMEWORK=VERSIONED):
            manager.reload()
            assert manager.asset_url("/static/a.css") == "/static/a.css?v=2026.9.19"


class TestWarmPlanFollowsTheStaticUrl:
    """A warm plan re-resolves its module-list URLs when `STATIC_URL` moves.

    The plan holds resolved URLs, so dropping the discovery with the backend memo is
    what carries the new prefix into a warm render rather than any file on disk.
    """

    def _warmed_manager(self, tmp_path: Path) -> StaticManager:
        manager = get_static_manager()
        manager._ensure_backends()
        manager._cached_page_roots = (tmp_path.resolve(),)
        return manager

    def test_a_warm_page_plan_emits_the_new_prefix(
        self, tmp_path: Path, reset_default: None
    ) -> None:
        page_path = page_naming_one_style(tmp_path)
        manager = self._warmed_manager(tmp_path)

        cold = StaticCollector()
        manager.discover_page_assets(page_path, cold)
        with override_settings(STATIC_URL="/assets/"):
            warm = StaticCollector()
            manager.discover_page_assets(page_path, warm)

        assert [a.url for a in cold.assets_in_slot("styles")] == ["/static/css/x.css"]
        assert [a.url for a in warm.assets_in_slot("styles")] == ["/assets/css/x.css"]

    def test_a_warm_component_plan_emits_the_new_prefix(
        self, tmp_path: Path, reset_default: None
    ) -> None:
        directory = tmp_path / "widget"
        directory.mkdir()
        module_path = directory / "component.py"
        module_path.write_text('styles = ["css/x.css"]\n')
        info = component_info(
            directory, name="widget", module=module_path, template="<div>widget</div>"
        )
        manager = self._warmed_manager(tmp_path)

        cold = StaticCollector()
        manager.discover_component_assets(info, cold)
        with override_settings(STATIC_URL="/assets/"):
            warm = StaticCollector()
            manager.discover_component_assets(info, warm)

        assert [a.url for a in cold.assets_in_slot("styles")] == ["/static/css/x.css"]
        assert [a.url for a in warm.assets_in_slot("styles")] == ["/assets/css/x.css"]


class TestDiscoveryForwarding:
    def test_discover_page_assets_delegates(
        self, tmp_path: Path, fresh_manager: StaticManager
    ) -> None:
        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        fresh_manager._cached_page_roots = (tmp_path.resolve(),)

        collector = StaticCollector()
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/index.css",
        ):
            fresh_manager.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/index.css"
        ]

    def test_discover_component_assets_delegates(
        self, composite_component: ComponentInfo, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/components/widget.css",
        ):
            fresh_manager.discover_component_assets(composite_component, collector)
        style_urls = [a.url for a in collector.assets_in_slot("styles")]
        assert "/static/next/components/widget.css" in style_urls
        assert "https://cdn.example.com/extra.css" in style_urls


class TestDefaultManagerLazy:
    def test_resolves_to_static_manager(self, reset_default: None) -> None:
        assert isinstance(default_manager.default_backend, StaticFilesBackend)

    def test_is_lazy_object_class(self) -> None:
        assert isinstance(default_manager, DefaultStaticManager)

    def test_reset_drops_wrapped(self, reset_default: None) -> None:
        _ = default_manager.default_backend  # force eval
        assert default_manager._wrapped is not empty
        reset_default_manager()
        assert default_manager._wrapped is empty

    def test_setup_is_idempotent(self, reset_default: None) -> None:
        a = default_manager.default_backend
        b = default_manager.default_backend
        assert a is b

    def test_get_static_manager_builds_and_returns_the_wrapped_instance(
        self, reset_default: None
    ) -> None:
        """The accessor hands out the live manager, not the lazy handle."""
        manager = get_static_manager()
        assert isinstance(manager, StaticManager)
        assert manager is get_static_manager()
        assert default_manager._wrapped is manager


class TestSettingChangedReload:
    """Changing NEXT_FRAMEWORK triggers a default_manager reset via next.conf."""

    def test_override_settings_resets_manager(self, reset_default: None) -> None:
        _ = default_manager.default_backend  # warm up
        assert default_manager._wrapped is not empty

        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [{"BACKEND": "next.static.StaticFilesBackend"}]
            }
        ):
            # override_settings fires setting_changed, so the first access rebuilds.
            assert isinstance(default_manager.default_backend, StaticFilesBackend)

    def test_override_settings_drops_the_cached_asset_plans(
        self, tmp_path: Path, reset_default: None
    ) -> None:
        """Asset plans die with the manager, so reloaded settings re-walk the disk."""
        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        manager = get_static_manager()
        manager._cached_page_roots = (tmp_path.resolve(),)
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/index.css",
        ):
            manager.discover_page_assets(page_path, StaticCollector())
        assert manager.discovery._page_plan_cache

        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [{"BACKEND": "next.static.StaticFilesBackend"}]
            }
        ):
            reloaded = get_static_manager()
            assert reloaded is not manager
            assert not reloaded.discovery._page_plan_cache
