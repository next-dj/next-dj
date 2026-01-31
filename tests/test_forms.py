"""Tests for next.forms and next.templatetags.forms."""

import inspect
from unittest.mock import MagicMock, patch

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.template import Context, TemplateSyntaxError
from django.template.engine import Engine
from django.test import Client

from next.forms import (
    Form,
    FormActionBackend,
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
        page._template_registry[file_path] = "{{ test_submit.form.name }}"
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
            '{% form @action="test_submit" %}{{ test_submit.form.as_p }}{% endform %}'
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
        with pytest.raises(ImproperlyConfigured, match="request.*in template context"):
            t.render(Context({}))
