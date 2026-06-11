from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from django import forms, forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import (
    BaseModelForm,
    Form,
    ModelForm,
    build_form_namespace_for_action,
)
from next.forms.decorators import action as action_decorator
from next.forms.dispatch import _form_action_context_callable
from next.forms.manager import form_action_manager
from next.forms.registration import registration_diagnostics


class TestBuildFormNamespaceForAction:
    """``build_form_namespace_for_action`` when action has no form class."""

    def test_returns_none_for_action_without_form_class(
        self, mock_http_request
    ) -> None:
        """Actions without ``form_class`` return None."""
        req = mock_http_request(method="GET")
        assert build_form_namespace_for_action("test_no_form", req) is None


class TestActionDecorator:
    """``@action("name")`` registers a form-less handler."""

    def test_registers_form_less_action(self) -> None:
        """A decorated function is registered in the backend without form_class."""

        def logout_handler(request: HttpRequest) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        action_decorator("logout_test")(logout_handler)

        meta = form_action_manager.default_backend.get_meta("logout_test")
        assert meta is not None
        assert meta["handler"] is logout_handler
        assert meta["form_class"] is None

    def test_returns_original_function(self) -> None:
        """Decorator returns the original function unchanged."""

        def my_handler(request: HttpRequest) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        result = action_decorator("return_test_handler")(my_handler)
        assert result is my_handler

    def test_applied_to_class_returns_class_without_registering(self) -> None:
        """Applying @action to a class is recorded and skips registration."""

        @action_decorator("bad_class_action")
        class SomeClass:
            pass

        assert isinstance(SomeClass, type)
        meta = form_action_manager.default_backend.get_meta("bad_class_action")
        assert meta is None

    def test_class_name_recorded_in_applied_to_class_list(self) -> None:
        """When @action is applied to a class, its qualname is recorded."""

        @action_decorator("recorded_class")
        class MyBadClass:
            pass

        assert any(
            "MyBadClass" in entry
            for entry in registration_diagnostics.action_applied_to_class
        )

    def test_raises_type_error_when_form_class_is_a_type(self) -> None:
        """Passing a class as form_class raises TypeError."""

        class MyForm(django_forms.Form):
            name = django_forms.CharField()

        with pytest.raises(TypeError, match="must be a factory callable"):
            action_decorator("bad_form_class", form_class=MyForm)(lambda: None)

    def test_scope_is_shared_for_non_anchor_file(self) -> None:
        """Handler registered from a non-anchor file gets scope='shared'."""

        def handler(request: HttpRequest) -> None:
            pass

        action_decorator("shared_scope_handler")(handler)

        meta = form_action_manager.default_backend.get_meta("shared_scope_handler")
        assert meta is not None
        assert meta["scope"] == "shared"


class TestBaseFormGetInitial:
    """BaseForm.get_initial: default implementation and override."""

    def test_get_initial_returns_empty_dict_by_default(self) -> None:
        """Default get_initial returns empty dict."""
        assert Form.get_initial() == {}

    def test_get_initial_can_be_overridden(self) -> None:
        """Subclasses can override get_initial."""

        class CustomForm(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest) -> dict:
                return {"name": "default_name"}

        request = HttpRequest()
        result = CustomForm.get_initial(request)
        assert result == {"name": "default_name"}

    def test_get_initial_receives_request(self) -> None:
        """get_initial receives request object."""

        class RequestAwareForm(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest) -> dict:
                return {"name": request.method}

        request = HttpRequest()
        request.method = "GET"
        result = RequestAwareForm.get_initial(request)
        assert result == {"name": "GET"}

    def test_basemodelform_get_initial_returns_empty_dict(self) -> None:
        """BaseModelForm.get_initial returns empty dict by default."""
        assert BaseModelForm.get_initial() == {}

    def test_modelform_get_initial_returns_empty_dict(self) -> None:
        """ModelForm.get_initial returns empty dict by default."""
        assert ModelForm.get_initial() == {}

    def test_form_class_without_get_initial_raises_error_in_context(self) -> None:
        """Form class without get_initial raises TypeError when lazy context runs."""

        class CustomDjangoForm(django_forms.Form):
            name = django_forms.CharField(max_length=100)

        with pytest.raises(TypeError, match="must have get_initial method"):
            _form_action_context_callable(CustomDjangoForm)(HttpRequest())

    def test_form_with_model_instance_but_not_modelform_raises_error(self) -> None:
        """Using instance parameter with non-ModelForm raises TypeError."""

        class CustomForm(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest) -> object:
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = MagicMock()
                return mock_instance

        request = HttpRequest()
        with pytest.raises(
            TypeError, match="instance parameter only supported for ModelForm"
        ):
            _form_action_context_callable(CustomForm)(request)

    def test_context_func_with_modelform_returning_instance(self) -> None:
        """context_func creates form with instance when ModelForm returns instance."""
        mock_model = MagicMock()
        mock_model._meta = MagicMock()
        mock_model._meta.get_fields.return_value = []

        class TestModelForm(ModelForm):
            name = forms.CharField(max_length=100)

            class Meta:
                model = mock_model
                fields: ClassVar[list[str]] = ["name"]

            @classmethod
            def get_initial(cls, request: HttpRequest) -> object:
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = mock_model
                return mock_instance

        request = HttpRequest()
        result = _form_action_context_callable(TestModelForm)(request)
        assert hasattr(result, "form")
        assert result.form is not None

    def test_context_func_gets_url_kwargs_from_origin_when_no_resolver_match(
        self,
    ) -> None:
        """context_func resolves the posted origin when resolver_match is absent."""

        class FormWithId(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, **kwargs: object) -> dict:
                return {"name": f"from-{kwargs['id']}"}

        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_next_form_origin": "/items/42/"}
        result = _form_action_context_callable(FormWithId)(request)
        assert hasattr(result, "form")
        assert result.form.initial.get("name") == "from-42"

    def test_context_func_gets_url_kwargs_from_resolver_match(self) -> None:
        """context_func uses resolver_match.kwargs when present."""

        class FormWithId(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, item_id: int) -> dict:
                return {"name": f"resolver-{item_id}"}

        request = HttpRequest()
        request.resolver_match = MagicMock()
        request.resolver_match.kwargs = {"item_id": 7}
        result = _form_action_context_callable(FormWithId)(request)
        assert result.form.initial.get("name") == "resolver-7"

    def test_context_func_origin_string_kwarg_stays_a_string(self) -> None:
        """A string URL converter value reaches get_initial untouched."""

        class FormWithName(Form):
            slug = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, name: str) -> dict:
                return {"slug": name}

        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_next_form_origin": "/groups/my-slug/"}
        result = _form_action_context_callable(FormWithName)(request)
        assert result.form.initial.get("slug") == "my-slug"
