import pathlib
from pathlib import Path
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
    validated_next_form_page_path,
)
from next.forms._request_utils import _url_kwargs_from_post
from next.forms.decorators import _action_applied_to_class, action as action_decorator
from next.forms.dispatch import _form_action_context_callable
from next.forms.manager import form_action_manager


PAGE_MODULE_FOR_FORM_TESTS = (
    Path(__file__).resolve().parent.parent / "site_pages" / "page.py"
).resolve()


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

    def test_raises_type_error_when_applied_to_class(self) -> None:
        """Applying @action to a class raises TypeError immediately."""
        with pytest.raises(TypeError, match="form-less actions only"):

            @action_decorator("bad_class_action")
            class SomeClass:
                pass

    def test_class_name_recorded_in_applied_to_class_list(self) -> None:
        """When @action is applied to a class, its qualname is recorded."""
        with pytest.raises(TypeError):

            @action_decorator("recorded_class")
            class MyBadClass:
                pass

        assert any("MyBadClass" in entry for entry in _action_applied_to_class)

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

    def test_context_func_gets_url_kwargs_from_post_when_no_resolver_match(
        self,
    ) -> None:
        """context_func extracts url params from POST when resolver_match has no kwargs."""

        class FormWithId(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, id: int) -> dict:  # noqa: A002
                return {"name": f"from-{id}"}

        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_url_param_id": "42"}
        result = _form_action_context_callable(FormWithId)(request)
        assert hasattr(result, "form")
        assert result.form.initial.get("name") == "from-42"

    def test_context_func_gets_url_kwargs_from_resolver_match(self) -> None:
        """context_func uses resolver_match.kwargs when present."""

        class FormWithId(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, id: int) -> dict:  # noqa: A002
                return {"name": f"resolver-{id}"}

        request = HttpRequest()
        request.resolver_match = MagicMock()
        request.resolver_match.kwargs = {"id": 7}
        result = _form_action_context_callable(FormWithId)(request)
        assert result.form.initial.get("name") == "resolver-7"

    def test_context_func_post_url_param_non_digit_string(self) -> None:
        """context_func uses POST value as-is when not convertible to int."""

        class FormWithSlug(Form):
            slug = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, slug: str) -> dict:
                return {"slug": slug}

        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_url_param_slug": "my-slug"}
        result = _form_action_context_callable(FormWithSlug)(request)
        assert result.form.initial.get("slug") == "my-slug"


class TestUrlKwargsFromPostReserved:
    """``_url_kwargs_from_post`` skips DI-reserved param names."""

    def test_skips_url_param_request(self) -> None:
        """``_url_param_request`` is not forwarded as ``request``."""
        req = HttpRequest()
        req.method = "POST"
        req.POST = {"_url_param_request": "x", "_url_param_id": "7"}
        out = _url_kwargs_from_post(req)
        assert "request" not in out
        assert out["id"] == 7


class TestValidatedNextFormPagePath:
    """``validated_next_form_page_path`` edge cases."""

    def test_no_post_attr(self) -> None:
        """Missing ``POST`` yields None."""

        class NoPost:
            pass

        assert validated_next_form_page_path(NoPost()) is None  # type: ignore[arg-type]

    def test_next_page_not_str(self) -> None:
        """Non-string ``_next_form_page`` yields None."""
        req = HttpRequest()
        req.method = "POST"

        class WeirdPost:
            def get(self, _key: str, _default: object = None) -> object:
                return 42

        req.POST = WeirdPost()  # type: ignore[assignment]
        assert validated_next_form_page_path(req) is None

    def test_next_page_empty_after_strip(self) -> None:
        """Whitespace-only ``_next_form_page`` yields None."""
        req = HttpRequest()
        req.method = "POST"
        req.POST = {"_next_form_page": "   \n  "}
        assert validated_next_form_page_path(req) is None

    def test_resolve_raises_oserror(self, monkeypatch) -> None:
        """``Path.resolve`` raising ``OSError`` yields None."""

        def boom(self: pathlib.Path, *args: object, **kwargs: object) -> pathlib.Path:
            msg = "boom"
            raise OSError(msg)

        monkeypatch.setattr(pathlib.Path, "resolve", boom)
        req = HttpRequest()
        req.method = "POST"
        req.POST = {"_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS)}
        assert validated_next_form_page_path(req) is None

    def test_not_page_py(self, tmp_path) -> None:
        """Filename other than ``page.py`` yields None."""
        p = tmp_path / "foo.py"
        p.write_text("x=1")
        req = HttpRequest()
        req.method = "POST"
        req.POST = {"_next_form_page": str(p.resolve())}
        assert validated_next_form_page_path(req) is None

    def test_virtual_page_with_sibling_template(self, tmp_path, monkeypatch) -> None:
        """Virtual `page.py` (only `template.djx` exists alongside) is accepted."""
        page_dir = tmp_path / "project" / "leaf"
        page_dir.mkdir(parents=True)
        (page_dir / "template.djx").write_text("<p>ok</p>")
        virtual = page_dir / "page.py"
        req = HttpRequest()
        req.method = "POST"
        req.POST = {"_next_form_page": str(virtual)}
        monkeypatch.setattr("django.conf.settings.BASE_DIR", tmp_path / "project")
        result = validated_next_form_page_path(req)
        assert result == virtual.resolve()

    def test_missing_page_py_and_no_template(self, tmp_path, monkeypatch) -> None:
        """Non-existent page.py with no sibling template.djx yields None."""
        page_dir = tmp_path / "project" / "leaf"
        page_dir.mkdir(parents=True)
        virtual = page_dir / "page.py"
        req = HttpRequest()
        req.method = "POST"
        req.POST = {"_next_form_page": str(virtual)}
        monkeypatch.setattr("django.conf.settings.BASE_DIR", tmp_path / "project")
        assert validated_next_form_page_path(req) is None

    def test_base_dir_none(self, monkeypatch) -> None:
        """Missing ``settings.BASE_DIR`` yields None."""
        req = HttpRequest()
        req.method = "POST"
        req.POST = {"_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS)}
        monkeypatch.setattr("django.conf.settings.BASE_DIR", None)
        assert validated_next_form_page_path(req) is None

    def test_outside_base_dir(self, tmp_path, monkeypatch) -> None:
        """Path outside ``BASE_DIR`` yields None."""
        outside = tmp_path / "outside"
        outside.mkdir()
        page_py = outside / "page.py"
        page_py.write_text("# x")
        req = HttpRequest()
        req.method = "POST"
        req.POST = {"_next_form_page": str(page_py.resolve())}
        monkeypatch.setattr("django.conf.settings.BASE_DIR", tmp_path / "project")
        assert validated_next_form_page_path(req) is None
