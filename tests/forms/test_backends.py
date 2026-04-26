from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse

from next.forms import (
    FormActionBackend,
    FormActionFactory,
    FormActionManager,
    RegistryFormActionBackend,
    form_action_manager,
)


class TestFormActionManager:
    """FormActionManager: get_action_url, default_backend, __iter__."""

    def test_get_action_url_returns_url(self) -> None:
        """Return URL for known action."""
        url = form_action_manager.get_action_url("test_submit")
        assert url != ""
        assert "_next/form/" in url
        assert "/" in url

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
    """RegistryFormActionBackend: get_action_url, generate_urls."""

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

    def test_register_action_raises_on_uid_collision(self) -> None:
        """Raise ImproperlyConfigured when two distinct names share a UID."""
        backend = RegistryFormActionBackend()
        backend.register_action("alpha", lambda: None)
        with (
            patch(
                "next.forms.backends._make_uid",
                return_value=backend._registry["alpha"]["uid"],
            ),
            pytest.raises(ImproperlyConfigured, match="UID collision"),
        ):
            backend.register_action("beta", lambda: None)

    def test_register_action_reregistration_same_name_ok(self) -> None:
        """Re-registering the same name (e.g. reload) does not raise."""
        backend = RegistryFormActionBackend()
        backend.register_action("alpha", lambda: None)
        backend.register_action("alpha", lambda: None)
        assert "alpha" in backend._registry


class TestFormActionBackendAbstract:
    """FormActionBackend default implementations: get_meta, render_form_fragment."""

    def test_get_meta_returns_none(self) -> None:
        """Abstract backend get_meta returns None."""

        class StubBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str) -> str:
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

            def get_action_url(self, action_name: str) -> str:
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
        """If a backend constructor raises, the entry is skipped and logged."""
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_ACTION_BACKENDS": [
                {"BACKEND": "next.forms.RegistryFormActionBackend"},
            ],
        }

        def boom(_config: dict) -> None:
            msg = "boom"
            raise RuntimeError(msg)

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
