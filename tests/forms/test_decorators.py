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
    FormActionOptions,
    ModelForm,
    RegistryFormActionBackend,
    _form_action_context_callable,
    _url_kwargs_from_post,
    build_form_namespace_for_action,
    validated_next_form_page_path,
)


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


class TestBaseFormGetInitial:
    """BaseForm.get_initial: default implementation and override."""

    def test_get_initial_returns_empty_dict_by_default(self) -> None:
        """Default get_initial returns empty dict."""
        request = HttpRequest()
        result = Form.get_initial(request)
        assert result == {}

    def test_get_initial_can_be_overridden(self) -> None:
        """Subclasses can override get_initial."""

        class CustomForm(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, _request: HttpRequest) -> dict:
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
                # Access request attributes
                return {"name": request.method}

        request = HttpRequest()
        request.method = "GET"
        result = RequestAwareForm.get_initial(request)
        assert result == {"name": "GET"}

    def test_basemodelform_get_initial_returns_empty_dict(self) -> None:
        """BaseModelForm.get_initial returns empty dict by default."""
        request = HttpRequest()
        result = BaseModelForm.get_initial(request)
        assert result == {}

    def test_modelform_get_initial_returns_empty_dict(self) -> None:
        """ModelForm.get_initial returns empty dict by default."""
        request = HttpRequest()
        result = ModelForm.get_initial(request)
        assert result == {}

    def test_form_class_without_get_initial_raises_error_in_context(self) -> None:
        """Form class without get_initial raises TypeError when lazy context runs."""
        backend = RegistryFormActionBackend()

        # Create a form class that doesn't inherit from BaseForm
        class CustomDjangoForm(django_forms.Form):
            name = django_forms.CharField(max_length=100)

        def handler(
            _request: HttpRequest, _form: CustomDjangoForm
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action",
            handler,
            options=FormActionOptions(form_class=CustomDjangoForm),
        )

        request = HttpRequest()
        assert backend.get_meta("test_action") is not None

        with pytest.raises(TypeError, match="must have get_initial method"):
            _form_action_context_callable(CustomDjangoForm)(request)

    def test_form_with_model_instance_but_not_modelform_raises_error(self) -> None:
        """Test that using instance parameter with non-ModelForm raises TypeError."""
        backend = RegistryFormActionBackend()

        class CustomForm(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, _request: HttpRequest) -> object:
                # Return a mock model instance
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = MagicMock()
                return mock_instance

        def handler(_request: HttpRequest, _form: CustomForm) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action", handler, options=FormActionOptions(form_class=CustomForm)
        )

        request = HttpRequest()
        assert backend.get_meta("test_action") is not None

        with pytest.raises(
            TypeError, match="instance parameter only supported for ModelForm"
        ):
            _form_action_context_callable(CustomForm)(request)

    def test_context_func_with_modelform_returning_instance(self) -> None:
        """Test context_func creates form with instance when ModelForm returns instance."""
        backend = RegistryFormActionBackend()

        # Create a simple mock model
        mock_model = MagicMock()
        mock_model._meta = MagicMock()
        mock_model._meta.get_fields.return_value = []

        class TestModelForm(ModelForm):
            name = forms.CharField(max_length=100)

            class Meta:
                model = mock_model
                fields: ClassVar[list[str]] = ["name"]

            @classmethod
            def get_initial(cls, _request: HttpRequest) -> object:
                # Return a mock model instance
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = mock_model
                return mock_instance

        def handler(
            _request: HttpRequest, _form: TestModelForm
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action", handler, options=FormActionOptions(form_class=TestModelForm)
        )

        request = HttpRequest()
        assert backend.get_meta("test_action") is not None

        result = _form_action_context_callable(TestModelForm)(request)
        assert hasattr(result, "form")
        assert result.form is not None

    def test_context_func_gets_url_kwargs_from_post_when_no_resolver_match(
        self,
    ) -> None:
        """Test context_func extracts url params from POST when resolver_match has no kwargs."""
        backend = RegistryFormActionBackend()

        class FormWithId(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, _request: HttpRequest, id: int) -> dict:  # noqa: A002
                return {"name": f"from-{id}"}

        def handler(_request: HttpRequest, form: FormWithId) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "post_params_action",
            handler,
            options=FormActionOptions(form_class=FormWithId),
        )
        assert backend.get_meta("post_params_action") is not None
        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_url_param_id": "42"}
        result = _form_action_context_callable(FormWithId)(request)
        assert hasattr(result, "form")
        assert result.form.initial.get("name") == "from-42"

    def test_context_func_gets_url_kwargs_from_resolver_match(self) -> None:
        """Test context_func uses resolver_match.kwargs when present."""
        backend = RegistryFormActionBackend()

        class FormWithId(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, _request: HttpRequest, id: int) -> dict:  # noqa: A002
                return {"name": f"resolver-{id}"}

        def handler(_request: HttpRequest, form: FormWithId) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "resolver_params_action",
            handler,
            options=FormActionOptions(form_class=FormWithId),
        )
        assert backend.get_meta("resolver_params_action") is not None
        request = HttpRequest()
        request.resolver_match = MagicMock()
        request.resolver_match.kwargs = {"id": 7}
        result = _form_action_context_callable(FormWithId)(request)
        assert result.form.initial.get("name") == "resolver-7"

    def test_context_func_post_url_param_non_digit_string(self) -> None:
        """Test context_func uses POST value as-is when not convertible to int."""
        backend = RegistryFormActionBackend()

        class FormWithSlug(Form):
            slug = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, _request: HttpRequest, slug: str) -> dict:
                return {"slug": slug}

        def handler(_request: HttpRequest, form: FormWithSlug) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "slug_action",
            handler,
            options=FormActionOptions(form_class=FormWithSlug),
        )
        assert backend.get_meta("slug_action") is not None
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
