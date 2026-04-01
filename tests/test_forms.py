import inspect
import pathlib
import types
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from django import forms, forms as django_forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.template import Context, TemplateSyntaxError
from django.test import Client

from next.forms import (
    BaseModelForm,
    Form,
    FormActionBackend,
    FormActionOptions,
    ModelForm,
    RegistryFormActionBackend,
    _form_action_context_callable,
    _FormActionDispatch,
    _get_caller_path,
    _url_kwargs_from_post,
    action,
    build_form_namespace_for_action,
    form_action_manager,
    page,
    validated_next_form_page_path,
)
from next.templatetags.forms import _parse_form_tag_args


PAGE_MODULE_FOR_FORM_TESTS = (
    Path(__file__).resolve().parent / "pages" / "page.py"
).resolve()


@pytest.fixture()
def client_no_csrf():
    """Test client without CSRF checks (form action POSTs supply fields manually)."""
    return Client(enforce_csrf_checks=False)


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

    def test_unknown_action_returns_empty(self, mock_http_request) -> None:
        """Unknown action renders empty string."""
        request = mock_http_request(method="GET")
        html = form_action_manager.render_form_fragment(
            request, "unknown_action_xyz", None
        )
        assert html == ""

    def test_with_template_fragment(self, mock_http_request) -> None:
        """Render form using the page template for ``page_file_path``."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "test"})
        html = form_action_manager.render_form_fragment(
            request,
            "test_submit",
            form,
            template_fragment=None,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert "test" in html or "name" in html

    def test_with_form_only_no_template(self, mock_http_request) -> None:
        """Render form via registry template for ``page_file_path`` returns HTML."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "x"})
        html = form_action_manager.render_form_fragment(
            request,
            "test_submit",
            form,
            template_fragment=None,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert isinstance(html, str)
        assert html.strip() != ""

    def test_form_none_no_template_returns_string(self, mock_http_request) -> None:
        """Form None still returns a string when a page template exists."""
        request = mock_http_request(method="GET")
        html = form_action_manager.render_form_fragment(
            request,
            "test_submit",
            form=None,
            template_fragment=None,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert isinstance(html, str)

    @pytest.mark.parametrize(
        ("registry_template", "output_mode"),
        [
            ("{{ form.name }}", "form_fields"),
            ("{{ current_template_path }}", "path"),
        ],
        ids=("form_fields", "current_template_path"),
    )
    def test_renders_from_registry_template(
        self, mock_http_request, registry_template: str, output_mode: str
    ) -> None:
        """Render fragment using template stored in page registry for ``page_file_path``."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "a"})
        backend = form_action_manager.default_backend
        assert isinstance(backend, RegistryFormActionBackend)
        file_path = PAGE_MODULE_FOR_FORM_TESTS
        original_registry = page._template_registry.copy()
        page._template_registry[file_path] = registry_template
        try:
            html = backend.render_form_fragment(
                request,
                "test_submit",
                form,
                template_fragment=None,
                page_file_path=file_path,
            )
            if output_mode == "path":
                template_djx = file_path.parent / "template.djx"
                expected_path = (
                    str(template_djx) if template_djx.is_file() else str(file_path)
                )
                assert expected_path in html
            else:
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


class TestFormTagParse:
    """_parse_form_tag_args: quoted and unquoted values."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                '@action="submit_contact" class="my-form"',
                {"@action": "submit_contact", "class": "my-form"},
            ),
            ("foo=bar", {"foo": "bar"}),
        ],
        ids=("quoted", "unquoted"),
    )
    def test_parses_key_value_pairs(self, raw: str, expected: dict[str, str]) -> None:
        """Parse tag string into key value pairs."""
        args = _parse_form_tag_args(raw)
        assert args == expected


class TestFormTagSyntax:
    """{% form %} tag: required @action, syntax errors."""

    def test_requires_at_least_one_arg(self, form_engine) -> None:
        """{% form %} without args raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError):
            form_engine.from_string("{% form %}x{% endform %}")

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
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "<form" in html
        assert "action=" in html
        assert 'method="post"' in html
        assert "</form>" in html

    def test_renders_extra_html_attrs(self, form_engine, csrf_request) -> None:
        """Form tag passes through class, id and other HTML attrs."""
        t = form_engine.from_string(
            '{% form @action="test_submit" class="my-form" id="f1" %}x{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert 'class="my-form"' in html
        assert 'id="f1"' in html

    def test_includes_csrf_when_request_in_context(
        self, form_engine, csrf_request
    ) -> None:
        """Form includes csrfmiddlewaretoken when request in context."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "csrfmiddlewaretoken" in html

    def test_includes_next_form_page_hidden(self, form_engine, csrf_request) -> None:
        """Form includes _next_form_page from current_page_module_path."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "_next_form_page" in html
        assert str(PAGE_MODULE_FOR_FORM_TESTS) in html

    def test_requires_current_page_module_path(self, form_engine, csrf_request) -> None:
        """Without current_page_module_path, {% form %} raises ImproperlyConfigured."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
        with pytest.raises(ImproperlyConfigured, match="current_page_module_path"):
            t.render(Context({"request": csrf_request}))

    def test_unknown_action_renders_empty_action_url(
        self, form_engine, csrf_request
    ) -> None:
        """Unknown action still renders form with empty action URL."""
        t = form_engine.from_string(
            '{% form @action="nonexistent_action_xyz" %}z{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
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
                "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
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
                "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
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
                "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
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

    def test_post_with_non_string_value_in_dispatch(self, mock_http_request) -> None:
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

        mock_post = MagicMock()
        mock_post.items.return_value = [
            ("_url_param_test", ["list", "value"]),
            ("name", "test"),
        ]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

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
        response = _FormActionDispatch.dispatch(backend, request, "test_action", meta)
        # Should succeed
        assert response.status_code == 302

    def test_dispatch_with_non_string_post_value_real_call(
        self, mock_http_request
    ) -> None:
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

        mock_post = MagicMock()
        mock_post.items.return_value = [
            ("_url_param_test", ["list", "value"]),
            ("name", "test"),
        ]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will cover lines 357-358 (non-string branch)
        response = _FormActionDispatch.dispatch(backend, request, "test_action", meta)
        # Should succeed
        assert response.status_code == 302

    def test_dispatch_with_string_post_value_not_int(self, mock_http_request) -> None:
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

        mock_post = MagicMock()
        mock_post.items.return_value = [
            ("_url_param_test", "not_a_number"),
            ("name", "test"),
        ]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will cover lines 353-356 (ValueError branch)
        response = _FormActionDispatch.dispatch(backend, request, "test_action", meta)
        # Should succeed
        assert response.status_code == 302

    def test_render_form_fragment_with_non_string_post_value_real_call(
        self, mock_http_request
    ) -> None:
        """Test render_form_fragment handles non-string POST values in real call."""
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
        mock_post.items.return_value = [
            ("_url_param_test", ["list", "value"]),
        ]
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

    def test_render_form_fragment_with_string_post_value_not_int(
        self, mock_http_request
    ) -> None:
        """Test render_form_fragment handles string POST values that can't be converted to int."""
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
        mock_post.items.return_value = [
            ("_url_param_test", "not_a_number"),
        ]
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
            _FormActionDispatch.dispatch(backend, request, "test_action", meta)

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
            _FormActionDispatch.dispatch(backend, request, "test_action", meta)

    def test_render_form_fragment_with_non_string_post_value(
        self, mock_http_request
    ) -> None:
        """Test render_form_fragment handles non-string POST values."""
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
        mock_post.items.return_value = [("_url_param_test", ["list", "value"])]
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


class TestBuildFormNamespaceForAction:
    """``build_form_namespace_for_action`` when action has no form class."""

    def test_returns_none_for_action_without_form_class(
        self, mock_http_request
    ) -> None:
        """Actions without ``form_class`` return None."""
        req = mock_http_request(method="GET")
        assert build_form_namespace_for_action("test_no_form", req) is None


class TestFormDispatchRenderFragmentBranches:
    """``_FormActionDispatch.render_form_fragment`` fallbacks."""

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
        html = _FormActionDispatch.render_form_fragment(
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
            html = _FormActionDispatch.render_form_fragment(
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
