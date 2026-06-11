from unittest.mock import MagicMock, patch

import pytest
from django import forms as django_forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.test import override_settings
from django.urls import NoReverseMatch, clear_script_prefix, set_script_prefix

from next.forms import (
    ActionRegistration,
    FormActionBackend,
    FormActionFactory,
    FormActionManager,
    RegistryFormActionBackend,
    form_action_manager,
)


_FAKE_FILE = "/fake/myapp/forms.py"
_FAKE_FILE_PAGE = "/fake/myapp/page.py"


class TestFormActionManager:
    """FormActionManager: get_action_url, default_backend, __iter__."""

    def test_get_action_url_returns_url(self) -> None:
        """Return URL for known action."""
        url = form_action_manager.get_action_url("simple_form")
        assert url != ""
        assert "_next/form/" in url

    def test_get_action_url_returns_url_for_form_less(self) -> None:
        """Return URL for form-less action."""
        url = form_action_manager.get_action_url("test_no_form")
        assert "_next/form/" in url

    def test_get_action_url_raises_for_unknown_action(self) -> None:
        """Raise KeyError for unknown action name."""
        with pytest.raises(KeyError, match="Unknown form action"):
            form_action_manager.get_action_url("nonexistent_action_xyz")

    def test_default_backend_is_first_backend(self) -> None:
        """Default backend is the first in the list."""
        assert form_action_manager.default_backend is form_action_manager._backends[0]

    def test_iter_yields_url_patterns(self) -> None:
        """Iteration yields URL patterns from backends."""
        patterns = list(form_action_manager)
        assert isinstance(patterns, list)
        assert len(patterns) >= 1
        assert any("_next/form" in str(p.pattern) for p in patterns)

    def test_no_backends_configured_raises_improperly_configured(
        self, settings
    ) -> None:
        """Empty DEFAULT_FORM_ACTION_BACKENDS raises ImproperlyConfigured."""
        settings.NEXT_FRAMEWORK = {"DEFAULT_FORM_ACTION_BACKENDS": []}
        manager = FormActionManager()
        with pytest.raises(ImproperlyConfigured, match="No form action backends"):
            manager.register_action(
                ActionRegistration(
                    name="orphan_action",
                    file_path=_FAKE_FILE,
                    scope="shared",
                    handler=lambda: None,
                )
            )
        with pytest.raises(ImproperlyConfigured, match="No form action backends"):
            _ = manager.default_backend


class TestRegistryFormActionBackend:
    """RegistryFormActionBackend: register_action, get_meta, generate_urls."""

    def test_get_action_url_raises_for_unknown(self) -> None:
        """Backend raises KeyError for unknown action."""
        backend = form_action_manager.default_backend
        assert isinstance(backend, RegistryFormActionBackend)
        with pytest.raises(KeyError, match="Unknown form action"):
            backend.get_action_url("nonexistent_xyz")

    def test_generate_urls_empty_when_no_actions(self) -> None:
        """Empty backend yields no URL patterns."""
        empty_backend = RegistryFormActionBackend()
        assert empty_backend.generate_urls() == []

    def test_register_action_stores_handler(self) -> None:
        """Handler is stored under (scope_key, name) key."""
        backend = RegistryFormActionBackend()

        def my_handler() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="my_action",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=my_handler,
            )
        )
        meta = backend.get_meta("my_action")
        assert meta is not None
        assert meta["handler"] is my_handler
        assert meta["form_class"] is None

    def test_register_action_stores_form_class(self) -> None:
        """form_class is stored and handler is None when only form_class given."""
        backend = RegistryFormActionBackend()

        class MyForm(django_forms.Form):
            name = django_forms.CharField()

        backend.register_action(
            ActionRegistration(
                name="my_form_action",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=MyForm,
            )
        )
        meta = backend.get_meta("my_form_action")
        assert meta is not None
        assert meta["form_class"] is MyForm
        assert meta["handler"] is None

    def test_register_action_stores_both_form_class_and_handler(self) -> None:
        """When both form_class and handler are given, both are stored in meta."""
        backend = RegistryFormActionBackend()

        class DualForm(django_forms.Form):
            name = django_forms.CharField()

        def dual_handler(request):
            pass

        backend.register_action(
            ActionRegistration(
                name="dual_action",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=DualForm,
                handler=dual_handler,
            )
        )
        meta = backend.get_meta("dual_action")
        assert meta is not None
        assert meta["form_class"] is DualForm
        assert meta["handler"] is dual_handler

    def test_register_action_page_scope_uses_file_path_as_scope_key(
        self, tmp_path
    ) -> None:
        """Page-scope action stores absolute file path as scope_key."""
        backend = RegistryFormActionBackend()
        page_file = str(tmp_path / "page.py")

        def handler() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="page_action",
                file_path=page_file,
                scope="page",
                handler=handler,
            )
        )
        meta = backend.get_meta("page_action", page_file)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_register_action_raises_on_uid_collision(self) -> None:
        """Raise ImproperlyConfigured when two distinct names share a UID."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="alpha",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        first_uid = next(iter(backend._uid_to_name))
        with (
            patch(
                "next.forms.backends._make_uid_for_action",
                return_value=first_uid,
            ),
            pytest.raises(ImproperlyConfigured, match="UID collision"),
        ):
            backend.register_action(
                ActionRegistration(
                    name="beta",
                    file_path=_FAKE_FILE,
                    scope="shared",
                    handler=lambda: None,
                )
            )

    def test_register_action_reregistration_same_name_ok(self) -> None:
        """Re-registering the same name (e.g. reload) does not raise."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="alpha",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        backend.register_action(
            ActionRegistration(
                name="alpha",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        assert backend.get_meta("alpha") is not None

    def test_registry_keys_are_scope_name_tuples(self) -> None:
        """Internal registry uses (scope_key, name) tuples as keys."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="tuple_test",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=h,
            )
        )
        keys = list(backend._registry.keys())
        assert all(isinstance(k, tuple) and len(k) == 2 for k in keys)

    def test_uid_to_name_values_are_scope_name_tuples(self) -> None:
        """_uid_to_name values are (scope_key, name) tuples."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="uid_tuple_test",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=h,
            )
        )
        values = list(backend._uid_to_name.values())
        assert all(isinstance(v, tuple) and len(v) == 2 for v in values)

    def test_get_meta_with_page_path_returns_page_scoped_action(self, tmp_path) -> None:
        """get_meta with page_path finds page-scoped actions."""
        backend = RegistryFormActionBackend()
        page_path = str(tmp_path / "page.py")

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="page_meta_test",
                file_path=page_path,
                scope="page",
                handler=h,
            )
        )
        meta = backend.get_meta("page_meta_test", page_path)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_get_meta_without_page_path_finds_any_scope(self) -> None:
        """get_meta without page_path scans all registrations."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="any_scope_test",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=h,
            )
        )
        meta = backend.get_meta("any_scope_test")
        assert meta is not None

    def test_get_meta_tolerates_dangling_name_index_entry(self) -> None:
        """A name index entry without a registry record returns None."""
        backend = RegistryFormActionBackend()
        backend._name_index["ghost"] = ("ghost_scope", "ghost")
        assert backend.get_meta("ghost") is None

    def test_get_action_url_tolerates_dangling_name_index_entry(self) -> None:
        """A name index entry without a registry record raises the not-found KeyError."""
        backend = RegistryFormActionBackend()
        backend._name_index["ghost"] = ("ghost_scope", "ghost")
        with pytest.raises(KeyError, match="Unknown form action"):
            backend.get_action_url("ghost")

    def test_dispatch_unknown_uid_returns_404(self) -> None:
        """Dispatch with unknown UID returns 404 response."""
        backend = RegistryFormActionBackend()
        req = HttpRequest()
        req.method = "POST"
        resp = backend.dispatch(req, "nonexistent_uid_xyz")
        assert resp.status_code == 404

    def test_clear_registry_empties_all_state(self) -> None:
        """clear_registry drops all registrations and UID mapping."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="to_clear",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        backend.clear_registry()
        assert backend._registry == {}
        assert backend._uid_to_name == {}


class TestNameIndexScopeFilter:
    """The name-index fallback honours registration scope for page lookups."""

    @staticmethod
    def _register(
        backend: RegistryFormActionBackend, name: str, file_path: str, scope: str
    ) -> None:
        backend.register_action(
            ActionRegistration(
                name=name,
                file_path=file_path,
                scope=scope,
                handler=lambda: None,
            )
        )

    def test_page_scoped_action_invisible_from_another_page(self, tmp_path) -> None:
        """A page-scoped name never resolves through another page's lookup."""
        backend = RegistryFormActionBackend()
        page_a = str(tmp_path / "a" / "page.py")
        page_b = str(tmp_path / "b" / "page.py")
        self._register(backend, "note_form", page_a, "page")
        assert backend.get_meta("note_form", page_b) is None
        with pytest.raises(KeyError, match="Unknown form action"):
            backend.get_action_url("note_form", page_path=page_b)

    def test_shared_action_resolves_from_any_page(self, tmp_path) -> None:
        """A shared-scope name resolves through any page's lookup."""
        backend = RegistryFormActionBackend()
        page_b = str(tmp_path / "b" / "page.py")
        self._register(backend, "shared_form", _FAKE_FILE, "shared")
        meta = backend.get_meta("shared_form", page_b)
        assert meta is not None
        assert meta["scope"] == "shared"
        url = backend.get_action_url("shared_form", page_path=page_b)
        assert "_next/form/" in url

    def test_bare_name_still_resolves_page_scoped_action(self, tmp_path) -> None:
        """A lookup without page_path keeps the unfiltered name-index fallback."""
        backend = RegistryFormActionBackend()
        page_a = str(tmp_path / "a" / "page.py")
        self._register(backend, "note_form", page_a, "page")
        meta = backend.get_meta("note_form")
        assert meta is not None
        assert meta["scope"] == "page"
        assert "_next/form/" in backend.get_action_url("note_form")

    def test_declaring_page_resolves_its_own_action(self, tmp_path) -> None:
        """The declaring page hits the exact registry key, no fallback needed."""
        backend = RegistryFormActionBackend()
        page_a = str(tmp_path / "a" / "page.py")
        self._register(backend, "note_form", page_a, "page")
        meta = backend.get_meta("note_form", page_a)
        assert meta is not None
        assert meta["scope"] == "page"
        url = backend.get_action_url("note_form", page_path=page_a)
        assert "_next/form/" in url


class TestFormActionBackendAbstract:
    """FormActionBackend default implementations: get_meta, render_form_fragment."""

    def test_get_meta_returns_none(self) -> None:
        """Abstract backend get_meta returns None."""

        class StubBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str, **kwargs: object) -> str:
                return ""

            def generate_urls(self) -> list:
                return []

            def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
                return HttpResponse()

        stub = StubBackend()
        assert stub.get_meta("any") is None

    def test_render_form_fragment_returns_empty(self) -> None:
        """Abstract backend render_form_fragment returns empty string."""

        class StubBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str, **kwargs: object) -> str:
                return ""

            def generate_urls(self) -> list:
                return []

            def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
                return HttpResponse()

        stub = StubBackend()
        req = HttpRequest()
        assert stub.render_form_fragment(req, "x", None, None) == ""


class TestFormActionManagerReloadConfig:
    """`_reload_config` reads `DEFAULT_FORM_ACTION_BACKENDS` defensively."""

    def test_non_dict_entries_are_skipped(self, settings) -> None:
        """Non-dict entries inside the list are skipped without raising."""
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_ACTION_BACKENDS": [
                "not-a-dict",
                {"BACKEND": "next.forms.RegistryFormActionBackend"},
            ],
        }
        manager = FormActionManager()
        manager._reload_config()
        assert len(manager._backends) == 1
        assert isinstance(manager._backends[0], RegistryFormActionBackend)

    def test_factory_failure_is_logged_and_skipped(self, settings, caplog) -> None:
        """If a backend constructor raises ImproperlyConfigured, the entry is skipped and logged."""
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_ACTION_BACKENDS": [
                {"BACKEND": "next.forms.RegistryFormActionBackend"},
            ],
        }

        def boom(_config: dict) -> None:
            msg = "boom"
            raise ImproperlyConfigured(msg)

        with patch.object(FormActionFactory, "create_backend", side_effect=boom):
            manager = FormActionManager()
            with caplog.at_level("ERROR"):
                manager._reload_config()
        assert manager._backends == []
        assert any(
            "Error creating form-action backend" in r.message for r in caplog.records
        )


class TestFormActionFactory:
    """`FormActionFactory.create_backend` resolves dotted paths to backends."""

    def test_explicit_backend_path(self) -> None:
        """Explicit `BACKEND` path is honoured."""
        backend = FormActionFactory.create_backend(
            {"BACKEND": "next.forms.RegistryFormActionBackend"},
        )
        assert isinstance(backend, RegistryFormActionBackend)

    def test_missing_backend_key_raises_keyerror(self) -> None:
        """Configuration without `BACKEND` is the system-check's responsibility."""
        with pytest.raises(KeyError):
            FormActionFactory.create_backend({})


class TestGetActionUrlNoReverseMatchFallback:
    """get_action_url falls back to URL_NAME_FORM_ACTION when FORM_ACTION_REVERSE_NAME fails."""

    def test_fallback_when_named_url_fails(self, tmp_path) -> None:
        """When FORM_ACTION_REVERSE_NAME raises NoReverseMatch, falls back to URL_NAME_FORM_ACTION."""
        backend = RegistryFormActionBackend()
        page_path = str(tmp_path / "page.py")

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="fallback_action",
                file_path=page_path,
                scope="page",
                handler=h,
            )
        )

        original_reverse = __import__("django.urls", fromlist=["reverse"]).reverse

        def mock_reverse(name: str, **kwargs: object) -> str:
            if name == "next:form_action":
                msg = "no such url"
                raise NoReverseMatch(msg)
            return original_reverse(name, **kwargs)

        with patch("next.forms.uid.reverse", side_effect=mock_reverse):
            url = backend.get_action_url("fallback_action", page_path=page_path)
        assert "_next/form/" in url


class TestActionUrlCache:
    """get_action_url memoises reversed URLs per uid on the backend instance."""

    @staticmethod
    def _backend_with_action() -> RegistryFormActionBackend:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="cached_action",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        return backend

    def test_second_call_skips_reverse(self) -> None:
        """The second lookup is served from the cache without reversing again."""
        backend = self._backend_with_action()
        first = backend.get_action_url("cached_action")
        with patch("next.forms.backends.reverse_form_action") as mocked:
            second = backend.get_action_url("cached_action")
        assert second == first
        mocked.assert_not_called()

    def test_clear_registry_drops_url_cache(self) -> None:
        """clear_registry resets the URL cache together with the registry."""
        backend = self._backend_with_action()
        backend.get_action_url("cached_action")
        backend.clear_registry()
        assert backend._url_cache == {}

    def test_root_urlconf_change_drops_url_cache(self) -> None:
        """Overriding ROOT_URLCONF invalidates cached URLs."""
        backend = self._backend_with_action()
        backend.get_action_url("cached_action")
        assert backend._url_cache != {}
        with override_settings(ROOT_URLCONF="next.urls"):
            assert backend._url_cache == {}

    def test_unrelated_setting_change_keeps_url_cache(self) -> None:
        """Overriding an unrelated setting leaves cached URLs in place."""
        backend = self._backend_with_action()
        url = backend.get_action_url("cached_action")
        with override_settings(APPEND_SLASH=False):
            assert backend._url_cache != {}
            assert backend.get_action_url("cached_action") == url

    def test_script_prefix_is_part_of_the_cache_key(self) -> None:
        """A request-scoped script prefix never serves another prefix's URL."""
        backend = self._backend_with_action()
        bare = backend.get_action_url("cached_action")
        try:
            set_script_prefix("/mounted/")
            prefixed = backend.get_action_url("cached_action")
        finally:
            clear_script_prefix()
        assert prefixed == f"/mounted{bare}"
        assert backend.get_action_url("cached_action") == bare


class TestManagerClearRegistries:
    """FormActionManager.clear_registries calls clear_registry on backends."""

    def test_clear_registries_calls_clear_registry(self) -> None:
        """clear_registries invokes clear_registry on backends that have it."""
        mock_backend = MagicMock()
        mock_backend.clear_registry = MagicMock()

        manager = FormActionManager(backends=[mock_backend])
        manager.clear_registries()

        mock_backend.clear_registry.assert_called_once()

    def test_clear_registries_skips_backend_without_method(self) -> None:
        """Backends without clear_registry are silently skipped."""
        mock_backend = MagicMock(spec=[])

        manager = FormActionManager(backends=[mock_backend])
        manager.clear_registries()
