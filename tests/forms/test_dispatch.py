import inspect
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from django import forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import (
    Form,
    FormActionDispatch,
    FormActionOptions,
    ModelForm,
    RegistryFormActionBackend,
    _get_caller_path,
    form_action_manager,
    page,
)


PAGE_MODULE_FOR_FORM_TESTS = (
    Path(__file__).resolve().parent.parent / "site_pages" / "page.py"
).resolve()


class TestFormActionDispatch:
    """FormActionDispatch: _get_caller_path, context_func, ensure_http_response."""

    def test_get_caller_path_raises_when_no_frame(self) -> None:
        """_get_caller_path raises when frame is missing."""
        with pytest.raises(RuntimeError, match="Could not determine caller file path"):
            _get_caller_path(999)

    @pytest.mark.parametrize(
        ("response_val", "kwargs", "expected_status", "assert_extra"),
        [
            (None, {}, 204, None),
            ("hello", {}, 200, lambda r: r.content == b"hello"),
            (
                type("R", (), {"url": "/target/"})(),
                {"request": HttpRequest()},
                302,
                lambda r: r.url == "/target/",
            ),
            (object(), {}, 204, None),
            (type("E", (), {"url": None})(), {}, 204, None),
        ],
        ids=("none_val", "str_val", "redirect_val", "unknown_obj", "empty_url"),
    )
    def test_ensure_http_response_variants(
        self, response_val, kwargs, expected_status, assert_extra
    ) -> None:
        """ensure_http_response: None, str, redirect-like, unknown, empty url."""
        resp = FormActionDispatch.ensure_http_response(response_val, **kwargs)
        assert resp.status_code == expected_status
        if assert_extra is not None:
            assert assert_extra(resp)

    def test_get_caller_path_raises_when_frame_becomes_none(self) -> None:
        """_get_caller_path raises when frame chain ends early."""
        frame = MagicMock()
        frame.f_globals = {"__file__": "/some/path/forms.py"}
        frame.f_back = None
        with (
            patch.object(inspect, "currentframe", return_value=frame),
            pytest.raises(RuntimeError, match="Could not determine caller"),
        ):
            _get_caller_path(0)

    def test_get_caller_path_raises_when_all_frames_are_forms_py(self) -> None:
        """_get_caller_path raises when only forms.py frames exist."""

        def make_frame(f_back: object = None) -> object:
            f = MagicMock()
            f.f_globals = {"__file__": "/some/path/forms.py"}
            f.f_back = f_back
            return f

        chain = None
        for _ in range(15):
            chain = make_frame(chain)
        with (
            patch.object(inspect, "currentframe", return_value=chain),
            pytest.raises(RuntimeError, match="Could not determine caller"),
        ):
            _get_caller_path(0)


@pytest.mark.django_db()
class TestDispatchViaClient:
    """Form action dispatch via Django test client."""

    def test_unknown_uid_returns_404(self, client_no_csrf) -> None:
        """Unknown form uid returns 404."""
        resp = client_no_csrf.get("/_next/form/unknown_uid_12345/")
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "action_name",
        ["test_submit", "test_no_form"],
        ids=("with_form_class", "without_form_class"),
    )
    def test_get_returns_405(self, client_no_csrf, action_name: str) -> None:
        """GET form action URL returns 405 Method Not Allowed."""
        url = form_action_manager.get_action_url(action_name)
        resp = client_no_csrf.get(url)
        assert resp.status_code == 405

    def test_invalid_form_returns_200_with_errors(self, client_no_csrf) -> None:
        """Invalid POST returns 200 with validation errors when _next_form_page is valid."""
        url = form_action_manager.get_action_url("test_submit")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert resp.status_code == 200
        c = resp.content
        assert b"error" in c.lower() or b"required" in c.lower() or b"name" in c

    def test_invalid_form_without_next_page_returns_400(self, client_no_csrf) -> None:
        """Invalid POST without _next_form_page returns 400."""
        url = form_action_manager.get_action_url("test_submit")
        resp = client_no_csrf.post(url, data={"name": ""}, follow=False)
        assert resp.status_code == 400

    def test_valid_form_calls_handler(self, client_no_csrf) -> None:
        """Valid POST calls handler and returns 200/204."""
        url = form_action_manager.get_action_url("test_submit")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "Alice",
                "email": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert resp.status_code in (200, 204)

    def test_redirect_action_returns_redirect(self, client_no_csrf) -> None:
        """Redirect action returns 302 redirect."""
        url = form_action_manager.get_action_url("test_redirect")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "Bob",
                "email": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert resp.status_code == 302
        assert resp.url == "/done/"

    def test_no_form_action_post_returns_200(self, client_no_csrf) -> None:
        """Action without form_class POST returns 200 and body."""
        url = form_action_manager.get_action_url("test_no_form")
        resp = client_no_csrf.post(url, data={})
        assert resp.status_code == 200
        assert b"ok" in resp.content


class TestFormDispatchRenderFragmentBranches:
    """``FormActionDispatch.render_form_fragment`` fallbacks."""

    def test_unknown_action_uses_form_as_p(self, mock_http_request) -> None:
        """Unknown action meta falls back to ``form.as_p()``."""
        backend = RegistryFormActionBackend()

        class F(Form):
            name = forms.CharField(max_length=10)

        def h(_request: HttpRequest, _form: F) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "only", handler=h, options=FormActionOptions(form_class=F)
        )
        req = mock_http_request(method="GET")
        form = F()
        html = FormActionDispatch.render_form_fragment(
            backend,
            req,
            "missing_action",
            form,
            None,
            PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert html == form.as_p()

    def test_empty_template_string_uses_form_as_p(self, mock_http_request) -> None:
        """Empty template string in registry falls back to ``form.as_p()``."""
        backend = RegistryFormActionBackend()

        class F(Form):
            name = forms.CharField(max_length=10)

        def h(_request: HttpRequest, _form: F) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "frag", handler=h, options=FormActionOptions(form_class=F)
        )
        req = mock_http_request(method="GET")
        form = F()
        original = page._template_registry.copy()
        page._template_registry[PAGE_MODULE_FOR_FORM_TESTS] = ""
        try:
            html = FormActionDispatch.render_form_fragment(
                backend,
                req,
                "frag",
                form,
                None,
                PAGE_MODULE_FOR_FORM_TESTS,
            )
            assert html == form.as_p()
        finally:
            page._template_registry.clear()
            page._template_registry.update(original)

    def test_dispatch_with_modelform_returning_instance(
        self, mock_http_request
    ) -> None:
        """Test dispatch creates form with instance when ModelForm returns instance."""
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
            _request: HttpRequest, _form: TestModelForm, **_kwargs: object
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action", handler, options=FormActionOptions(form_class=TestModelForm)
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [("name", "test")]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Call dispatch - this will create form with instance (line 381)
        response = FormActionDispatch.dispatch(backend, request, "test_action", meta)
        # Should succeed
        assert response.status_code == 302

    @pytest.mark.parametrize(
        "url_param_value",
        [["list", "value"], "not_a_number"],
        ids=["non_string", "string_not_int"],
    )
    def test_dispatch_survives_unusual_url_param_values(
        self, mock_http_request, url_param_value
    ) -> None:
        """`dispatch` accepts url_param values that are not int-convertible strings."""
        backend = RegistryFormActionBackend()

        class TestForm(Form):
            name = forms.CharField(max_length=100)

        def handler(
            _request: HttpRequest, _form: TestForm, **_kwargs: object
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action", handler, options=FormActionOptions(form_class=TestForm)
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [
            ("_url_param_test", url_param_value),
            ("name", "test"),
        ]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        response = FormActionDispatch.dispatch(backend, request, "test_action", meta)
        assert response.status_code == 302

    @pytest.mark.parametrize(
        "url_param_value",
        [["list", "value"], "not_a_number"],
        ids=["non_string", "string_not_int"],
    )
    def test_render_form_fragment_survives_unusual_url_param_values(
        self, mock_http_request, url_param_value
    ) -> None:
        """`render_form_fragment` handles url_param values that aren't int-convertible strings."""
        backend = RegistryFormActionBackend()

        class TestForm(Form):
            name = forms.CharField(max_length=100)

        def handler(_request: HttpRequest, _form: TestForm) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action",
            handler,
            options=FormActionOptions(form_class=TestForm),
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [("_url_param_test", url_param_value)]
        request = mock_http_request(POST=mock_post)

        file_path = PAGE_MODULE_FOR_FORM_TESTS
        original_registry = page._template_registry.copy()
        page._template_registry[file_path] = "{{ form.name }}"
        try:
            form = TestForm(initial={"name": "test"})
            html = backend.render_form_fragment(
                request,
                "test_action",
                form,
                template_fragment=None,
                page_file_path=file_path,
            )
            assert isinstance(html, str)
        finally:
            page._template_registry.clear()
            page._template_registry.update(original_registry)

    def test_dispatch_with_form_without_get_initial(self, mock_http_request) -> None:
        """Test that dispatch raises TypeError when form class doesn't have get_initial."""
        backend = RegistryFormActionBackend()

        # Create a form class that doesn't inherit from BaseForm
        from django import forms as django_forms

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

        post = MagicMock()
        post.items.return_value = []
        request = mock_http_request(method="POST", POST=post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will trigger the error
        with pytest.raises(TypeError, match="must have get_initial method"):
            FormActionDispatch.dispatch(backend, request, "test_action", meta)

    def test_dispatch_with_form_returning_instance_but_not_modelform(
        self, mock_http_request
    ) -> None:
        """Test that dispatch raises TypeError when Form returns instance but isn't ModelForm."""
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

        post = MagicMock()
        post.items.return_value = []
        request = mock_http_request(method="POST", POST=post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will trigger the error
        with pytest.raises(
            TypeError, match="instance parameter only supported for ModelForm"
        ):
            FormActionDispatch.dispatch(backend, request, "test_action", meta)
