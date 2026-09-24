from __future__ import annotations

import json
import logging
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from django.test import RequestFactory, override_settings
from django.utils.functional import empty

from next.static import (
    ScriptInjectionPolicy,
    StaticAsset,
    StaticBackend,
    StaticCollector,
    StaticFilesBackend,
    StaticManager,
    default_kinds,
    default_manager,
    get_static_manager,
    reset_default_manager,
)
from next.static.collector import HEAD_CLOSE
from next.static.manager import (
    DefaultStaticManager,
    forget_manager_backend_urls,
    forget_manager_page_roots,
)
from next.static.scripts import CSRF_PAYLOAD_KEY, DEV_PAYLOAD_KEY, NextScriptBuilder
from tests.support import (
    PREFIXED_BACKENDS,
    PREFIXING_STATIC_BACKEND,
    REQUEST_RECORDING_BACKENDS,
    PrefixingStaticBackend,
    component_info,
    page_naming_one_style,
    restored_static_registries,
    static_names_resolved_by,
)


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.components import ComponentInfo


CSS_URL = "https://cdn.example.com/a.css"
JS_URL = "https://cdn.example.com/a.js"
NEXT_JS_URL = "/static/next/next.min.js"


COMPOSED_BACKENDS = {
    "STATIC_BACKENDS": [{"BACKEND": "tests.static.test_manager.ComposedStaticBackend"}]
}

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

REWRITING_BACKENDS = pytest.mark.parametrize(
    ("backends", "prefix"),
    [
        pytest.param(PREFIXED_BACKENDS, "/pfx", id="override"),
        pytest.param(COMPOSED_BACKENDS, "/composed", id="composed"),
    ],
)


class LiteralStaticBackend(StaticBackend):
    """Backend outside the staticfiles family, so every reference stays literal."""

    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Return the URL the logical name spells without asking storage."""
        del source_path
        return f"/literal/{logical_name}{default_kinds.extension(kind)}"


def _stamp_prefix(prefix: str, url: str, *, request: HttpRequest | None = None) -> str:
    if request is None or not url.startswith("/"):
        return url
    return f"{prefix}{url}"


class ComposedStaticBackend(StaticFilesBackend):
    """Backend that installs its rewrite on the instance instead of the class."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Bind the prefix into the hook the manager will find on the instance."""
        super().__init__(config)
        self.asset_url = partial(_stamp_prefix, "/composed")


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
        """The watch layer answers resolved, so a lookup pays no second resolve."""
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
        """The script tag and the preload hint read that URL through the same storage."""
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
        """The override reaches the live manager, resolver memo and all."""
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
        manager._ensure_backends()
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            manager.script_builder()
        assert manager._script_builder is not None
        manager.reload()
        assert manager._script_builder is None

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

    def test_a_caller_emptying_the_list_does_not_trigger_a_reload(self) -> None:
        manager = StaticManager()
        manager._ensure_backends()
        manager._backends.clear()
        manager._ensure_backends()
        assert manager._backends == []


class TestInjectStyles:
    def test_replaces_styles_placeholder(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        html = f"<head>{STYLES_PLACEHOLDER}</head><body/>"
        out = fresh_manager.inject(html, collector)
        assert f'<link rel="stylesheet" href="{CSS_URL}">' in out
        assert STYLES_PLACEHOLDER not in out

    def test_inline_body_wrapped_by_kind(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="", kind="css", inline="body{color:red}"))
        collector.add(StaticAsset(url="", kind="js", inline="console.log(1)"))
        html = f"<head>{STYLES_PLACEHOLDER}</head><body>{SCRIPTS_PLACEHOLDER}</body>"

        out = fresh_manager.inject(html, collector)

        assert "<style>body{color:red}</style>" in out
        assert "<script>console.log(1)</script>" in out

    def test_inline_body_verbatim_when_kind_has_no_inline_tag(
        self, fresh_manager: StaticManager
    ) -> None:
        """A kind naming no element contributes its body as it stands."""
        with restored_static_registries():
            default_kinds.register(
                "raw", extension=".raw", slot="styles", renderer="render_link_tag"
            )
            collector = StaticCollector()
            collector.add(StaticAsset(url="", kind="raw", inline="<custom>x</custom>"))
            out = fresh_manager.inject(f"<head>{STYLES_PLACEHOLDER}</head>", collector)

        assert "<custom>x</custom>" in out
        assert "<style>" not in out

    def test_empty_collector_empties_placeholder(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<head>{STYLES_PLACEHOLDER}</head>"
        out = fresh_manager.inject(html, collector)
        assert STYLES_PLACEHOLDER not in out


class TestInjectScriptsAuto:
    """AUTO policy injects next.min.js and init script before user scripts."""

    def test_script_and_init_emitted(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        collector.add_js_context("user", "alice")

        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)

        assert "/static/next/next.min.js" in out
        assert 'Next._init({"user":"alice"})' in out
        assert f'<script src="{JS_URL}"></script>' in out
        assert SCRIPTS_PLACEHOLDER not in out

    def test_next_script_comes_before_user_scripts(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)
        next_idx = out.index("/static/next/next.min.js")
        user_idx = out.index(JS_URL)
        assert next_idx < user_idx

    def test_real_request_injects_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        request = RequestFactory().get("/")
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector, request=request)
        assert '"$csrf"' in out
        assert '"header":"X-Csrftoken"' in out
        assert '"token":' in out

    def test_missing_request_omits_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)
        assert "$csrf" not in out
        assert "Next._init({})" in out


class TestInjectDevPayload:
    """The `$dev` init key follows Django `DEBUG` and is absent otherwise."""

    def inject(
        self, manager: StaticManager, collector: StaticCollector, *, debug: bool
    ) -> str:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with (
            override_settings(DEBUG=debug),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                return_value="/static/next/next.min.js",
            ),
        ):
            return manager.inject(html, collector)

    def inject_with_request(
        self, manager: StaticManager, collector: StaticCollector, *, debug: bool
    ) -> str:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        request = RequestFactory().get("/")
        with (
            override_settings(DEBUG=debug),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                return_value="/static/next/next.min.js",
            ),
        ):
            return manager.inject(html, collector, request=request)

    def test_debug_adds_dev_key(self, fresh_manager: StaticManager) -> None:
        out = self.inject(fresh_manager, StaticCollector(), debug=True)
        assert 'Next._init({"$dev":true})' in out

    def test_debug_appends_dev_key_after_user_context(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        out = self.inject(fresh_manager, collector, debug=True)
        assert 'Next._init({"user":"alice","$dev":true})' in out

    def test_debug_adds_dev_key_next_to_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        out = self.inject_with_request(fresh_manager, StaticCollector(), debug=True)
        assert '"$csrf"' in out
        assert '"$dev":true' in out

    def test_debug_leaves_collector_context_unmutated(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        out = self.inject(fresh_manager, collector, debug=True)
        assert f'"{DEV_PAYLOAD_KEY}":true' in out
        assert DEV_PAYLOAD_KEY not in collector.js_context()
        assert DEV_PAYLOAD_KEY not in collector.js_context_wire()

    def test_no_debug_omits_dev_key(self, fresh_manager: StaticManager) -> None:
        out = self.inject(fresh_manager, StaticCollector(), debug=False)
        assert "$dev" not in out
        assert "Next._init({})" in out

    def test_no_debug_omits_dev_key_with_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        out = self.inject_with_request(fresh_manager, StaticCollector(), debug=False)
        assert "$dev" not in out
        assert '"$csrf"' in out

    def test_disabled_policy_omits_dev_key(self, fresh_manager: StaticManager) -> None:
        fresh_manager._ensure_backends()
        fresh_manager._script_builder = NextScriptBuilder(
            "/static/next/next.min.js", policy=ScriptInjectionPolicy.DISABLED
        )
        out = self.inject(fresh_manager, StaticCollector(), debug=True)
        assert "$dev" not in out
        assert "Next._init" not in out

    def test_manual_policy_omits_dev_key(self, fresh_manager: StaticManager) -> None:
        fresh_manager._ensure_backends()
        fresh_manager._script_builder = NextScriptBuilder(
            "/static/next/next.min.js", policy=ScriptInjectionPolicy.MANUAL
        )
        out = self.inject(fresh_manager, StaticCollector(), debug=True)
        assert "$dev" not in out
        assert "Next._init" not in out


class _MarkSerializer:
    """Per-key serializer that wraps a value under a marker."""

    def dumps(self, value: object) -> str:
        """Return the value wrapped in a marker object as compact JSON."""
        return json.dumps({"mark": value}, separators=(",", ":"))


class TestReservedInitPayloadKeys:
    """A js-context key colliding with a reserved key loses to the framework."""

    def inject(
        self,
        manager: StaticManager,
        collector: StaticCollector,
        *,
        debug: bool,
        with_request: bool = False,
    ) -> str:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        request = RequestFactory().get("/") if with_request else None
        with (
            override_settings(DEBUG=debug),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                return_value="/static/next/next.min.js",
            ),
        ):
            return manager.inject(html, collector, request=request)

    def test_framework_csrf_payload_beats_a_user_fragment(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(CSRF_PAYLOAD_KEY, {"header": "X-App", "token": "app"})
        out = self.inject(fresh_manager, collector, debug=False, with_request=True)
        assert '"header":"X-Csrftoken"' in out
        assert "X-App" not in out
        assert out.count(f'"{CSRF_PAYLOAD_KEY}"') == 1

    def test_framework_dev_flag_beats_a_user_fragment(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(DEV_PAYLOAD_KEY, False)
        out = self.inject(fresh_manager, collector, debug=True)
        assert f'"{DEV_PAYLOAD_KEY}":true' in out
        assert f'"{DEV_PAYLOAD_KEY}":false' not in out

    def test_user_dev_key_is_dropped_without_debug(
        self, fresh_manager: StaticManager
    ) -> None:
        """A reserved key belongs to the framework even when it writes nothing."""
        collector = StaticCollector()
        collector.add_js_context(DEV_PAYLOAD_KEY, False)
        out = self.inject(fresh_manager, collector, debug=False)
        assert DEV_PAYLOAD_KEY not in out
        assert "Next._init({})" in out

    def test_user_csrf_key_is_dropped_without_a_token_minting_request(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(CSRF_PAYLOAD_KEY, {"header": "X-App", "token": "app"})
        out = self.inject(fresh_manager, collector, debug=False)
        assert CSRF_PAYLOAD_KEY not in out
        assert "X-App" not in out

    def test_a_context_without_reserved_keys_is_untouched(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        out = self.inject(fresh_manager, collector, debug=False)
        assert 'Next._init({"user":"alice"})' in out

    def test_key_serializer_override_does_not_encode_the_framework_value(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(DEV_PAYLOAD_KEY, False, serializer=_MarkSerializer())
        out = self.inject(fresh_manager, collector, debug=True)
        assert f'"{DEV_PAYLOAD_KEY}":true' in out
        assert "mark" not in out

    def test_collision_leaves_the_collector_untouched(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(CSRF_PAYLOAD_KEY, {"header": "X-App", "token": "app"})
        self.inject(fresh_manager, collector, debug=True, with_request=True)
        assert collector.js_context()[CSRF_PAYLOAD_KEY] == {
            "header": "X-App",
            "token": "app",
        }
        assert "X-App" in collector.js_context_encoded()[CSRF_PAYLOAD_KEY]

    def test_other_keys_keep_their_cached_fragments(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice", serializer=_MarkSerializer())
        out = self.inject(fresh_manager, collector, debug=True)
        assert '"user":{"mark":"alice"}' in out


class TestInjectScriptsDisabled:
    def test_disabled_policy_skips_injection(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        fresh_manager._ensure_backends()
        fresh_manager._script_builder = NextScriptBuilder(
            "/static/next/next.min.js", policy=ScriptInjectionPolicy.DISABLED
        )

        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        out = fresh_manager.inject(html, collector)

        assert "/static/next/next.min.js" not in out
        assert "Next._init" not in out
        assert JS_URL in out

    def test_next_js_options_setting_honored(
        self, fresh_manager: StaticManager
    ) -> None:
        """`NEXT_JS_OPTIONS` from user settings controls the injection policy."""
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        fresh_manager._ensure_backends()
        with (
            override_settings(
                NEXT_FRAMEWORK={"NEXT_JS_OPTIONS": {"policy": "disabled"}}
            ),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                return_value="/static/next/next.min.js",
            ),
        ):
            html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
            out = fresh_manager.inject(html, collector)

        assert "/static/next/next.min.js" not in out
        assert "Next._init" not in out
        assert JS_URL in out


class TestInjectPreloadHint:
    def test_preload_prepended_before_head_close(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<head>{HEAD_CLOSE}</head><body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)
        assert 'rel="preload"' in out
        preload_idx = out.index("preload")
        head_close_idx = out.index(HEAD_CLOSE)
        assert preload_idx < head_close_idx

    def test_no_head_close_means_no_preload(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        html = "<body></body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)
        assert 'rel="preload"' not in out

    def test_disabled_policy_skips_preload(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        fresh_manager._ensure_backends()
        fresh_manager._script_builder = NextScriptBuilder(
            "/static/next/next.min.js", policy=ScriptInjectionPolicy.DISABLED
        )
        html = f"<head>{HEAD_CLOSE}</head>"
        out = fresh_manager.inject(html, collector)
        assert 'rel="preload"' not in out


class TestInjectMissingPlaceholders:
    def test_missing_placeholders_leave_html_alone(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = "<html><body>plain</body></html>"
        out = fresh_manager.inject(html, collector)
        assert out == html


class TestInjectForwardsRequest:
    """`inject` forwards the active request to the backend rendering each tag."""

    def _inject(self, html: str, collector: StaticCollector, request) -> str:
        """Inject through a manager whose backend names the request in its tags."""
        with override_settings(NEXT_FRAMEWORK=REQUEST_RECORDING_BACKENDS):
            return StaticManager().inject(html, collector, request=request)

    def test_a_style_tag_is_rendered_under_the_active_request(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        request = RequestFactory().get("/dashboard/")

        out = self._inject(f"<head>{STYLES_PLACEHOLDER}</head>", collector, request)

        assert f'<link href="{CSS_URL}" data-request="/dashboard/">' in out

    def test_a_script_tag_is_rendered_under_the_active_request(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        request = RequestFactory().get("/dashboard/")

        out = self._inject(f"<body>{SCRIPTS_PLACEHOLDER}</body>", collector, request)

        assert f'<script src="{JS_URL}" data-request="/dashboard/"></script>' in out

    def test_a_render_without_a_request_says_so(self) -> None:
        """No request in scope is the caller's default, not a missing keyword."""
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))

        with override_settings(NEXT_FRAMEWORK=REQUEST_RECORDING_BACKENDS):
            out = StaticManager().inject(
                f"<head>{STYLES_PLACEHOLDER}</head>", collector
            )

        assert f'<link href="{CSS_URL}" data-request="none">' in out


class TestBackendRewritesEveryAssetUrl:
    """A backend that rewrites `asset_url` reaches every URL the page carries.

    The runtime bundle isn't a collected asset, so a composed hook must
    rewrite it just like a class override does.
    """

    @staticmethod
    def _inject(backends, collector, request) -> str:
        html = (
            f"<head>{STYLES_PLACEHOLDER}{HEAD_CLOSE}</head>"
            f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        )
        with (
            override_settings(NEXT_FRAMEWORK=backends),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", return_value=NEXT_JS_URL
            ),
        ):
            return StaticManager().inject(html, collector, request=request)

    def test_the_configured_backend_is_the_prefixing_one(self) -> None:
        with override_settings(NEXT_FRAMEWORK=PREFIXED_BACKENDS):
            assert isinstance(StaticManager().default_backend, PrefixingStaticBackend)

    @REWRITING_BACKENDS
    def test_runtime_script_tag_carries_the_rewritten_url(
        self, backends: dict, prefix: str
    ) -> None:
        out = self._inject(backends, StaticCollector(), RequestFactory().get("/"))
        assert f'<script src="{prefix}{NEXT_JS_URL}"></script>' in out
        assert f'<script src="{NEXT_JS_URL}"></script>' not in out

    @REWRITING_BACKENDS
    def test_preload_hint_carries_the_rewritten_url(
        self, backends: dict, prefix: str
    ) -> None:
        out = self._inject(backends, StaticCollector(), RequestFactory().get("/"))
        assert f'<link rel="preload" as="script" href="{prefix}{NEXT_JS_URL}">' in out
        assert f'href="{NEXT_JS_URL}"' not in out

    @REWRITING_BACKENDS
    def test_collected_asset_urls_carry_the_rewritten_url(
        self, backends: dict, prefix: str
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="/static/next/a.css", kind="css"))
        collector.add(StaticAsset(url="/static/next/a.js", kind="js"))
        out = self._inject(backends, collector, RequestFactory().get("/"))
        assert f'href="{prefix}/static/next/a.css"' in out
        assert f'src="{prefix}/static/next/a.js"' in out

    @REWRITING_BACKENDS
    def test_without_a_request_the_urls_stay_untouched(
        self, backends: dict, prefix: str
    ) -> None:
        out = self._inject(backends, StaticCollector(), None)
        assert f'<script src="{NEXT_JS_URL}"></script>' in out
        assert prefix not in out

    def test_disabled_policy_never_asks_the_backend_for_the_runtime_url(self) -> None:
        with override_settings(NEXT_FRAMEWORK=PREFIXED_BACKENDS):
            manager = StaticManager()
            manager._ensure_backends()
            manager._script_builder = NextScriptBuilder(
                NEXT_JS_URL, policy=ScriptInjectionPolicy.DISABLED
            )
            with mock.patch.object(
                manager.default_backend,
                "asset_url",
                wraps=manager.default_backend.asset_url,
            ) as asset_url:
                manager.inject(
                    f"<head>{HEAD_CLOSE}</head><body>{SCRIPTS_PLACEHOLDER}</body>",
                    StaticCollector(),
                    request=RequestFactory().get("/"),
                )
        assert asset_url.call_count == 0

    def test_the_runtime_url_is_resolved_once_for_the_whole_render(self) -> None:
        """The script tag and its preload hint cannot disagree, and cost one call."""
        with override_settings(NEXT_FRAMEWORK=PREFIXED_BACKENDS):
            manager = StaticManager()
            manager._ensure_backends()
            manager._script_builder = NextScriptBuilder(NEXT_JS_URL)
            with mock.patch.object(
                manager.default_backend,
                "asset_url",
                wraps=manager.default_backend.asset_url,
            ) as asset_url:
                out = manager.inject(
                    f"<head>{HEAD_CLOSE}</head><body>{SCRIPTS_PLACEHOLDER}</body>",
                    StaticCollector(),
                    request=RequestFactory().get("/"),
                )
        assert asset_url.call_args_list == [mock.call(NEXT_JS_URL, request=mock.ANY)]
        assert f'<script src="/pfx{NEXT_JS_URL}"></script>' in out
        assert f'<link rel="preload" as="script" href="/pfx{NEXT_JS_URL}">' in out


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
        """The backend hook runs first, so it never meets a URL already stamped."""
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
