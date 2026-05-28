from unittest.mock import MagicMock, patch

import pytest
from django import forms as django_forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.urls import NoReverseMatch

from next.forms import (
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
            "my_action",
            handler=my_handler,
            file_path=_FAKE_FILE,
            scope="shared",
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
            "my_form_action",
            form_class=MyForm,
            file_path=_FAKE_FILE,
            scope="shared",
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
            "dual_action",
            form_class=DualForm,
            handler=dual_handler,
            file_path=_FAKE_FILE,
            scope="shared",
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
            "page_action",
            handler=handler,
            file_path=page_file,
            scope="page",
        )
        meta = backend.get_meta("page_action", page_path=page_file)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_register_action_raises_on_uid_collision(self) -> None:
        """Raise ImproperlyConfigured when two distinct names share a UID."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            "alpha",
            handler=lambda: None,
            file_path=_FAKE_FILE,
            scope="shared",
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
                "beta",
                handler=lambda: None,
                file_path=_FAKE_FILE,
                scope="shared",
            )

    def test_register_action_reregistration_same_name_ok(self) -> None:
        """Re-registering the same name (e.g. reload) does not raise."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            "alpha",
            handler=lambda: None,
            file_path=_FAKE_FILE,
            scope="shared",
        )
        backend.register_action(
            "alpha",
            handler=lambda: None,
            file_path=_FAKE_FILE,
            scope="shared",
        )
        assert backend.get_meta("alpha") is not None

    def test_registry_keys_are_scope_name_tuples(self) -> None:
        """Internal registry uses (scope_key, name) tuples as keys."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            "tuple_test",
            handler=h,
            file_path=_FAKE_FILE,
            scope="shared",
        )
        keys = list(backend._registry.keys())
        assert all(isinstance(k, tuple) and len(k) == 2 for k in keys)

    def test_uid_to_name_values_are_scope_name_tuples(self) -> None:
        """_uid_to_name values are (scope_key, name) tuples."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            "uid_tuple_test",
            handler=h,
            file_path=_FAKE_FILE,
            scope="shared",
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
            "page_meta_test",
            handler=h,
            file_path=page_path,
            scope="page",
        )
        meta = backend.get_meta("page_meta_test", page_path=page_path)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_get_meta_without_page_path_finds_any_scope(self) -> None:
        """get_meta without page_path scans all registrations."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            "any_scope_test",
            handler=h,
            file_path=_FAKE_FILE,
            scope="shared",
        )
        meta = backend.get_meta("any_scope_test")
        assert meta is not None

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
            "to_clear",
            handler=lambda: None,
            file_path=_FAKE_FILE,
            scope="shared",
        )
        backend.clear_registry()
        assert backend._registry == {}
        assert backend._uid_to_name == {}


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
            "fallback_action",
            handler=h,
            file_path=page_path,
            scope="page",
        )

        original_reverse = __import__("django.urls", fromlist=["reverse"]).reverse

        def mock_reverse(name: str, **kwargs: object) -> str:
            if name == "next:form_action":
                msg = "no such url"
                raise NoReverseMatch(msg)
            return original_reverse(name, **kwargs)

        with patch("next.forms.backends.reverse", side_effect=mock_reverse):
            url = backend.get_action_url("fallback_action", page_path=page_path)
        assert "_next/form/" in url


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
