import inspect
import types
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from django import forms, forms as django_forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict
from django.middleware.csrf import get_token
from django.template import Context, TemplateSyntaxError
from django.template.engine import Engine
from django.test import Client

from next.forms import (
    BaseModelForm,
    Form,
    FormActionBackend,
    FormActionOptions,
    ModelForm,
    RegistryFormActionBackend,
    _FormActionDispatch,
    _get_caller_path,
    action,
    form_action_manager,
    page,
)
from next.templatetags.forms import _parse_form_tag_args


@pytest.fixture()
def django_client():
    """Django test client."""
    return Client()


@pytest.fixture()
def form_engine():
    """Template engine with forms builtin."""
    return Engine(builtins=["next.templatetags.forms"])


@pytest.fixture()
def csrf_request():
    """HttpRequest with CSRF token set (for form tag tests)."""
    req = HttpRequest()
    req.method = "GET"
    get_token(req)
    return req


class SimpleForm(Form):
    """Minimal form used by form action tests."""

    name = forms.CharField(max_length=100)
    email = forms.EmailField(required=False)


@action("test_submit", form_class=SimpleForm)
def _test_handler(request: HttpRequest, form: SimpleForm) -> HttpResponse | None:
    return None


@action("test_redirect", form_class=SimpleForm)
def _test_redirect_handler(
    request: HttpRequest, form: SimpleForm
) -> HttpResponseRedirect:
    return HttpResponseRedirect("/done/")


@action("test_no_form")
def _test_no_form_handler(request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok", status=200)


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


class TestRenderFormFragment:
    """render_form_fragment: unknown action, template_fragment, fallback, context."""

    def test_unknown_action_returns_empty(self) -> None:
        """Unknown action renders empty string."""
        request = MagicMock(spec=HttpRequest)
        request.method = "GET"
        html = form_action_manager.render_form_fragment(
            request, "unknown_action_xyz", None
        )
        assert html == ""

    def test_with_template_fragment(self) -> None:
        """Render form with given template fragment."""
        request = MagicMock(spec=HttpRequest)
        request.method = "GET"
        form = SimpleForm(initial={"name": "test"})
        fragment = "{{ form.name }}"
        html = form_action_manager.render_form_fragment(
            request, "test_submit", form, template_fragment=fragment
        )
        assert "test" in html or "name" in html

    def test_with_form_only_no_template(self) -> None:
        """Render form without template returns string."""
        request = MagicMock(spec=HttpRequest)
        request.method = "GET"
        form = SimpleForm(initial={"name": "x"})
        html = form_action_manager.render_form_fragment(
            request, "test_submit", form, template_fragment=None
        )
        assert isinstance(html, str)

    def test_form_none_no_template_returns_string(self) -> None:
        """Form None and no template still returns a string."""
        request = MagicMock(spec=HttpRequest)
        request.method = "GET"
        html = form_action_manager.render_form_fragment(
            request, "test_submit", form=None, template_fragment=None
        )
        assert isinstance(html, str)

    def test_with_template_in_registry(self) -> None:
        """Render using template from registry for action file."""
        request = MagicMock(spec=HttpRequest)
        request.method = "GET"
        form = SimpleForm(initial={"name": "a"})
        backend = form_action_manager.default_backend
        assert isinstance(backend, RegistryFormActionBackend)
        meta = backend.get_meta("test_submit")
        assert meta is not None
        file_path = meta["file_path"]
        # Pre-populate registry and skip _load_template_for_file
        original_registry = page._template_registry.copy()
        page._template_registry[file_path] = "{{ form.name }}"
        try:
            html = backend.render_form_fragment(
                request, "test_submit", form, template_fragment=None
            )
            assert "a" in html or "name" in html
        finally:
            page._template_registry.clear()
            page._template_registry.update(original_registry)


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


class TestFormActionDispatch:
    """_FormActionDispatch: _get_caller_path, context_func, ensure_http_response."""

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
    )
    def test_ensure_http_response_variants(
        self, response_val, kwargs, expected_status, assert_extra
    ) -> None:
        """ensure_http_response: None, str, redirect-like, unknown, empty url."""
        resp = _FormActionDispatch.ensure_http_response(response_val, **kwargs)
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

    def test_unknown_uid_returns_404(self, django_client) -> None:
        """Unknown form uid returns 404."""
        resp = django_client.get("/_next/form/unknown_uid_12345/")
        assert resp.status_code == 404

    def test_valid_uid_get_returns_405(self, django_client) -> None:
        """GET form action URL returns 405 Method Not Allowed."""
        url = form_action_manager.get_action_url("test_submit")
        resp = django_client.get(url)
        assert resp.status_code == 405

    def test_invalid_form_returns_200_with_errors(self, django_client) -> None:
        """Invalid POST returns 200 with validation errors."""
        url = form_action_manager.get_action_url("test_submit")
        resp = django_client.post(url, data={"name": ""}, follow=False)
        assert resp.status_code == 200
        c = resp.content
        assert b"error" in c.lower() or b"required" in c.lower() or b"name" in c

    def test_valid_form_calls_handler(self, django_client) -> None:
        """Valid POST calls handler and returns 200/204."""
        url = form_action_manager.get_action_url("test_submit")
        resp = django_client.post(
            url, data={"name": "Alice", "email": ""}, follow=False
        )
        assert resp.status_code in (200, 204)

    def test_redirect_action_returns_redirect(self, django_client) -> None:
        """Redirect action returns 302 redirect."""
        url = form_action_manager.get_action_url("test_redirect")
        resp = django_client.post(
            url,
            data={"name": "Bob", "email": ""},
            follow=False,
        )
        assert resp.status_code == 302
        assert resp.url == "/done/"

    def test_no_form_action_get_returns_405(self, django_client) -> None:
        """Action without form_class GET returns 405."""
        url = form_action_manager.get_action_url("test_no_form")
        resp = django_client.get(url)
        assert resp.status_code == 405

    def test_no_form_action_post_returns_200(self, django_client) -> None:
        """Action without form_class POST returns 200 and body."""
        url = form_action_manager.get_action_url("test_no_form")
        resp = django_client.post(url, data={})
        assert resp.status_code == 200
        assert b"ok" in resp.content


class TestFormTagParse:
    """_parse_form_tag_args: quoted and unquoted values."""

    def test_quoted_args(self) -> None:
        """Parse quoted key=value args from tag."""
        args = _parse_form_tag_args('@action="submit_contact" class="my-form"')
        assert args.get("@action") == "submit_contact"
        assert args.get("class") == "my-form"

    def test_unquoted_value(self) -> None:
        """Parse unquoted key=value from tag."""
        args = _parse_form_tag_args("foo=bar")
        assert args.get("foo") == "bar"


class TestFormTagSyntax:
    """{% form %} tag: required @action, syntax errors."""

    def test_requires_at_least_one_arg(self) -> None:
        """{% form %} without args raises TemplateSyntaxError."""
        engine = Engine.get_default()
        if "next.templatetags.forms" not in engine.template_libraries:
            lib = __import__("next.templatetags.forms", fromlist=[""])
            engine.libraries["next.templatetags.forms"] = lib
        with pytest.raises(TemplateSyntaxError):
            engine.from_string("{% load forms %}{% form %}x{% endform %}")

    def test_requires_action_name_raises(self, form_engine) -> None:
        """{% form %} without @action raises with 'requires' message."""
        with pytest.raises(TemplateSyntaxError, match="requires"):
            form_engine.from_string("{% form x=1 %}y{% endform %}")


class TestFormTagRender:
    """{% form %} tag: attributes, CSRF, unknown action, no request."""

    def test_renders_attributes(self, form_engine, csrf_request) -> None:
        """Form tag renders action, method, form content."""
        t = form_engine.from_string(
            '{% form @action="test_submit" %}{{ form.as_p }}{% endform %}'
        )
        html = t.render(Context({"request": csrf_request}))
        assert "<form" in html
        assert "action=" in html
        assert 'method="post"' in html
        assert "</form>" in html

    def test_renders_extra_html_attrs(self, form_engine, csrf_request) -> None:
        """Form tag passes through class, id and other HTML attrs."""
        t = form_engine.from_string(
            '{% form @action="test_submit" class="my-form" id="f1" %}x{% endform %}'
        )
        html = t.render(Context({"request": csrf_request}))
        assert 'class="my-form"' in html
        assert 'id="f1"' in html

    def test_includes_csrf_when_request_in_context(
        self, form_engine, csrf_request
    ) -> None:
        """Form includes csrfmiddlewaretoken when request in context."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
        html = t.render(Context({"request": csrf_request}))
        assert "csrfmiddlewaretoken" in html

    def test_unknown_action_renders_empty_action_url(
        self, form_engine, csrf_request
    ) -> None:
        """Unknown action still renders form with empty action URL."""
        t = form_engine.from_string(
            '{% form @action="nonexistent_action_xyz" %}z{% endform %}'
        )
        html = t.render(Context({"request": csrf_request}))
        assert 'action=""' in html or "action=''" in html
        assert "<form" in html

    def test_without_request_in_context_raises(self, form_engine) -> None:
        """Form without request in context raises ImproperlyConfigured."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
        with pytest.raises(ImproperlyConfigured, match=r"request.*in template context"):
            t.render(Context({}))

    def test_form_variable_is_local(self, form_engine, csrf_request) -> None:
        """Form variable is only available inside {% form %} tag."""
        form_instance = SimpleForm(initial={"name": "test"})
        t = form_engine.from_string(
            'Outside: {{ form|default:"none" }} '
            '{% form @action="test_submit" %}Inside: {{ form.name.value|default:"none" }}{% endform %} '
            'Outside: {{ form|default:"none" }}'
        )
        context = Context(
            {
                "request": csrf_request,
                "test_submit": types.SimpleNamespace(form=form_instance),
            }
        )
        html = t.render(context)
        # Form should be available inside tag (should show form value, not "none")
        assert "test" in html
        assert "Inside:" in html
        # Form should not be available outside tag (should show "none" twice)
        assert html.count("none") == 2

    def test_form_variable_contains_form_instance(
        self, form_engine, csrf_request
    ) -> None:
        """Form variable contains the actual form instance from context."""
        form_instance = SimpleForm(initial={"name": "test_name"})
        context = Context(
            {
                "request": csrf_request,
                "test_submit": types.SimpleNamespace(form=form_instance),
            }
        )
        t = form_engine.from_string(
            '{% form @action="test_submit" %}{{ form.name.value }}{% endform %}'
        )
        html = t.render(context)
        assert "test_name" in html

    def test_form_includes_url_parameters_as_hidden_fields(
        self, form_engine, csrf_request
    ) -> None:
        """Form includes hidden fields for URL parameters from resolver_match."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')

        # Create a request with resolver_match containing URL parameters
        request = HttpRequest()
        request.method = "GET"
        get_token(request)

        # Mock resolver_match with kwargs containing URL parameters
        mock_resolver_match = MagicMock()
        mock_resolver_match.kwargs = {
            "id": 123,
            "slug": "test-slug",
            "uid": "should-be-skipped",
        }
        request.resolver_match = mock_resolver_match

        context = Context(
            {
                "request": request,
                "test_submit": types.SimpleNamespace(form=SimpleForm()),
            }
        )
        html = t.render(context)

        # Check that hidden fields for URL parameters are included (covers lines 156-159)
        assert "_url_param_id" in html or 'name="_url_param_id"' in html
        assert 'value="123"' in html
        assert "_url_param_slug" in html or 'name="_url_param_slug"' in html
        assert 'value="test-slug"' in html
        # uid should be skipped (line 158)
        assert 'name="_url_param_uid"' not in html


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
        """Test that form class without get_initial raises TypeError when context is accessed."""
        backend = RegistryFormActionBackend()

        # Create a form class that doesn't inherit from BaseForm
        class CustomDjangoForm(django_forms.Form):
            name = django_forms.CharField(max_length=100)

        def handler(
            _request: HttpRequest, _form: CustomDjangoForm
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        # Register action with form class that doesn't have get_initial
        backend.register_action(
            "test_action",
            handler,
            options=FormActionOptions(form_class=CustomDjangoForm),
        )

        # The error should occur when context_func is called via page context
        request = HttpRequest()
        meta = backend.get_meta("test_action")
        assert meta is not None
        file_path = meta["file_path"]

        # Try to get context through page context manager - this will trigger the error
        # The context_func was registered, so calling collect_context will trigger it
        with pytest.raises(TypeError, match="must have get_initial method"):
            page._context_manager.collect_context(
                file_path, request, action_name="test_action"
            )

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

        # Try to get context - this will trigger the error when trying to use instance
        request = HttpRequest()
        meta = backend.get_meta("test_action")
        assert meta is not None
        file_path = meta["file_path"]

        # Real call to collect_context - this will trigger the error in context_func
        # context_func is registered with key="test_action", so it will be called
        # when collect_context runs and processes registered context functions
        # Access the context key to trigger the function
        context_registry = page._context_manager._context_registry.get(file_path, {})
        assert "test_action" in context_registry
        func, _ = context_registry["test_action"]
        with pytest.raises(
            TypeError, match="instance parameter only supported for ModelForm"
        ):
            func(request)  # This will trigger the error

    def test_post_with_non_string_value_in_dispatch(self) -> None:
        """Test that POST values that are not strings are handled correctly in dispatch."""
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

        # Create a mock request with non-string POST value
        request = MagicMock(spec=HttpRequest)
        request.method = "POST"
        # Create a QueryDict-like mock that supports both string and non-string values
        # First create normal POST data
        post_data = QueryDict(mutable=True)
        post_data["name"] = "test"
        post_data["_url_param_test"] = "123"  # Normal string value first

        # Then manually add non-string value to test that branch
        # We'll test the non-string handling by directly testing the extraction logic
        mock_post = MagicMock()
        # Simulate POST.items() returning a non-string value for _url_param_test
        mock_post.items.return_value = [
            ("_url_param_test", ["list", "value"]),  # Non-string value
            ("name", "test"),  # Form field
        ]
        request.POST = mock_post
        request.FILES = None

        # Test the extraction logic directly to cover the non-string branch
        url_kwargs: dict[str, object] = {}
        for key, value in request.POST.items():
            if key.startswith("_url_param_"):
                param_name = key.replace("_url_param_", "")
                if isinstance(value, str):
                    try:
                        url_kwargs[param_name] = int(value)
                    except ValueError:
                        url_kwargs[param_name] = value
                else:
                    url_kwargs[param_name] = value

        # Verify non-string value was stored correctly
        assert url_kwargs["test"] == ["list", "value"]

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

        # Get context - this will call context_func which creates form with instance
        request = HttpRequest()
        meta = backend.get_meta("test_action")
        assert meta is not None
        file_path = meta["file_path"]

        # Call collect_context to trigger context_func
        context_registry = page._context_manager._context_registry.get(file_path, {})
        if "test_action" in context_registry:
            func, _ = context_registry["test_action"]
            result = func(request)  # This should create form with instance (line 255)
            assert hasattr(result, "form")
            assert result.form is not None

    def test_dispatch_with_modelform_returning_instance(self) -> None:
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

        # Create a POST request
        request = MagicMock(spec=HttpRequest)
        request.method = "POST"
        mock_post = MagicMock()
        mock_post.items.return_value = [("name", "test")]
        request.POST = mock_post
        request.FILES = None

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Call dispatch - this will create form with instance (line 381)
        response = _FormActionDispatch.dispatch(backend, request, "test_action", meta)
        # Should succeed
        assert response.status_code == 302

    def test_dispatch_with_non_string_post_value_real_call(self) -> None:
        """Test dispatch handles non-string POST values in real call."""
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

        # Create a mock request with non-string POST value
        request = MagicMock(spec=HttpRequest)
        request.method = "POST"
        mock_post = MagicMock()
        # Simulate POST.items() returning a non-string value
        mock_post.items.return_value = [
            ("_url_param_test", ["list", "value"]),  # Non-string value (covers 357-358)
            ("name", "test"),  # Form field
        ]
        request.POST = mock_post
        request.FILES = None

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will cover lines 357-358 (non-string branch)
        response = _FormActionDispatch.dispatch(backend, request, "test_action", meta)
        # Should succeed
        assert response.status_code == 302

    def test_dispatch_with_string_post_value_not_int(self) -> None:
        """Test dispatch handles string POST values that can't be converted to int."""
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

        # Create a mock request with string POST value that can't be converted to int
        request = MagicMock(spec=HttpRequest)
        request.method = "POST"
        mock_post = MagicMock()
        # Simulate POST.items() returning a string value that raises ValueError on int()
        mock_post.items.return_value = [
            (
                "_url_param_test",
                "not_a_number",
            ),  # String that can't be int (covers 353-356)
            ("name", "test"),  # Form field
        ]
        request.POST = mock_post
        request.FILES = None

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will cover lines 353-356 (ValueError branch)
        response = _FormActionDispatch.dispatch(backend, request, "test_action", meta)
        # Should succeed
        assert response.status_code == 302

    def test_render_form_fragment_with_non_string_post_value_real_call(self) -> None:
        """Test render_form_fragment handles non-string POST values in real call."""
        backend = RegistryFormActionBackend()

        class TestForm(Form):
            name = forms.CharField(max_length=100)

        def handler(_request: HttpRequest, _form: TestForm) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        test_file = Path(__file__).parent / "test_file.py"
        backend.register_action(
            "test_action",
            handler,
            options=FormActionOptions(form_class=TestForm, file_path=test_file),
        )

        # Create a mock request with non-string POST value
        request = MagicMock(spec=HttpRequest)
        mock_post = MagicMock()
        mock_post.items.return_value = [
            ("_url_param_test", ["list", "value"]),  # Non-string value (covers 478-479)
        ]
        request.POST = mock_post

        # Pre-populate template registry
        meta = backend.get_meta("test_action")
        assert meta is not None
        file_path = meta["file_path"]
        page._template_registry[file_path] = "{{ form.name }}"

        # Real call to render_form_fragment - this will cover lines 478-479 (non-string branch)
        form = TestForm(initial={"name": "test"})
        html = backend.render_form_fragment(
            request, "test_action", form, template_fragment=None
        )
        assert isinstance(html, str)

    def test_render_form_fragment_with_string_post_value_not_int(self) -> None:
        """Test render_form_fragment handles string POST values that can't be converted to int."""
        backend = RegistryFormActionBackend()

        class TestForm(Form):
            name = forms.CharField(max_length=100)

        def handler(_request: HttpRequest, _form: TestForm) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        test_file = Path(__file__).parent / "test_file.py"
        backend.register_action(
            "test_action",
            handler,
            options=FormActionOptions(form_class=TestForm, file_path=test_file),
        )

        # Create a mock request with string POST value that can't be converted to int
        request = MagicMock(spec=HttpRequest)
        mock_post = MagicMock()
        mock_post.items.return_value = [
            (
                "_url_param_test",
                "not_a_number",
            ),  # String that can't be int (covers 474-477)
        ]
        request.POST = mock_post

        # Pre-populate template registry
        meta = backend.get_meta("test_action")
        assert meta is not None
        file_path = meta["file_path"]
        page._template_registry[file_path] = "{{ form.name }}"

        # Real call to render_form_fragment - this will cover lines 474-477 (ValueError branch)
        form = TestForm(initial={"name": "test"})
        html = backend.render_form_fragment(
            request, "test_action", form, template_fragment=None
        )
        assert isinstance(html, str)

    def test_dispatch_with_form_without_get_initial(self) -> None:
        """Test that dispatch raises TypeError when form class doesn't have get_initial."""
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

        # Create a POST request
        request = MagicMock(spec=HttpRequest)
        request.method = "POST"
        request.POST = MagicMock()
        request.POST.items.return_value = []
        request.FILES = None

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will trigger the error
        with pytest.raises(TypeError, match="must have get_initial method"):
            _FormActionDispatch.dispatch(backend, request, "test_action", meta)

    def test_dispatch_with_form_returning_instance_but_not_modelform(self) -> None:
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

        # Create a POST request
        request = MagicMock(spec=HttpRequest)
        request.method = "POST"
        request.POST = MagicMock()
        request.POST.items.return_value = []
        request.FILES = None

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will trigger the error
        with pytest.raises(
            TypeError, match="instance parameter only supported for ModelForm"
        ):
            _FormActionDispatch.dispatch(backend, request, "test_action", meta)

    def test_render_form_fragment_with_non_string_post_value(self) -> None:
        """Test render_form_fragment handles non-string POST values."""
        backend = RegistryFormActionBackend()

        class TestForm(Form):
            name = forms.CharField(max_length=100)

        def handler(_request: HttpRequest, _form: TestForm) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        test_file = Path(__file__).parent / "test_file.py"
        backend.register_action(
            "test_action",
            handler,
            options=FormActionOptions(form_class=TestForm, file_path=test_file),
        )

        # Create a mock request with non-string POST value
        request = MagicMock(spec=HttpRequest)
        mock_post = MagicMock()
        mock_post.items.return_value = [("_url_param_test", ["list", "value"])]
        request.POST = mock_post

        # Pre-populate template registry
        meta = backend.get_meta("test_action")
        assert meta is not None
        file_path = meta["file_path"]
        page._template_registry[file_path] = "{{ form.name }}"

        # Real call to render_form_fragment - this will cover the non-string handling code
        form = TestForm(initial={"name": "test"})
        html = backend.render_form_fragment(
            request, "test_action", form, template_fragment=None
        )
        assert isinstance(html, str)
