from collections.abc import Iterator

import pytest
from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern

from next.forms import (
    ActionRegistration,
    FormActionBackend,
    FormActionNotFoundError,
    RegistryFormActionBackend,
)
from next.forms.backends import ActionMeta
from next.forms.manager import form_action_manager
from next.testing import build_form_for, resolve_action_url
from tests.forms.actions import SimpleForm


class TestResolveActionUrl:
    """resolve_action_url delegates to the global manager."""

    def test_returns_url_for_registered_action(self) -> None:
        url = resolve_action_url("simple_form")
        assert "_next/form/" in url

    def test_raises_for_unknown_action(self) -> None:
        with pytest.raises(FormActionNotFoundError, match="Unknown form action"):
            resolve_action_url("nonexistent_zz")


class _NamelessMetaBackend(FormActionBackend):
    """Backend whose iter_actions yields a meta without an action name."""

    def register_action(self, registration: ActionRegistration) -> None:
        pass

    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        return ""

    def generate_urls(self) -> list[URLPattern]:
        return []

    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        return HttpResponse()

    def iter_actions(self) -> Iterator[ActionMeta]:
        yield {}


class TestBuildFormFor:
    """build_form_for instantiates the form class stored for an action."""

    def test_returns_form_with_data(self) -> None:
        form = build_form_for("simple_form", {"name": "Bob", "email": ""})
        assert isinstance(form, SimpleForm)
        assert form.is_bound
        assert form.is_valid()

    def test_raises_for_unknown_action(self) -> None:
        with pytest.raises(
            FormActionNotFoundError, match="Unknown form action"
        ) as excinfo:
            build_form_for("nonexistent_zz")
        assert excinfo.value.suggestions == ()
        assert "Closest matches" not in str(excinfo.value)

    def test_unknown_action_carries_close_match_suggestions(self) -> None:
        with pytest.raises(FormActionNotFoundError) as excinfo:
            build_form_for("simple_frm")
        assert "simple_form" in excinfo.value.suggestions
        assert "Closest matches" in str(excinfo.value)

    def test_nameless_metas_are_skipped_in_suggestions(self, monkeypatch) -> None:
        registry = RegistryFormActionBackend()
        registry.register_action(
            ActionRegistration(
                name="delete_note",
                file_path="/fake/myapp/forms.py",
                scope="shared",
                handler=lambda: None,
            )
        )
        monkeypatch.setattr(
            form_action_manager, "_backends", [_NamelessMetaBackend(), registry]
        )
        with pytest.raises(FormActionNotFoundError) as excinfo:
            build_form_for("delete_not")
        assert excinfo.value.suggestions == ("delete_note",)

    def test_raises_when_action_has_no_form_class(self) -> None:
        with pytest.raises(LookupError, match="without a form_class") as excinfo:
            build_form_for("test_no_form")  # form-less action
        message = str(excinfo.value)
        assert "handler-only" in message
        assert "resolve_action_url" in message
        assert "test client" in message

    def test_raises_for_wizard_action(self, monkeypatch) -> None:
        registry = RegistryFormActionBackend()
        registry.register_action(
            ActionRegistration(
                name="signup_wizard",
                file_path="/fake/myapp/page.py",
                scope="page",
                wizard_class=type("SignupWizardStub", (), {}),
            )
        )
        monkeypatch.setattr(form_action_manager, "_backends", [registry])
        with pytest.raises(LookupError, match="without a form_class"):
            build_form_for("signup_wizard")
